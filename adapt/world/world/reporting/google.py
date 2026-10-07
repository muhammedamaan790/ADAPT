"""Mock Google Ads API v25 search (REST shape) with a GAQL subset for reporting and read-back verification.

POST /google/v25/customers/{cid}/googleAds:search  {"query": "...", "pageToken": "..."}
Grammar: SELECT f[, f...] FROM resource [WHERE cond [AND cond...]] [ORDER BY ...] [LIMIT n]
  cond: field BETWEEN 'a' AND 'b' | field (=|!=|>|>=|<|<=) value
Resources: customer, campaign_budget, campaign, ad_group, ad_group_ad. Metrics: impressions, clicks, cost_micros,
conversions (platform-claimed, incl. view-through), conversions_value. With segments.date -> one row per day,
without -> totals over the filtered days. Rows with zero impressions are omitted (as Google does).
int64 values are JSON strings, doubles are numbers; money in the account currency (INR) micros.
campaign_budget and customer work on a bare store (no seeded world) so budget read-back can be tested alone.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from world.accounts import BRAND_TIMEZONE, GOOGLE_CURRENCY
from world.mutations.google import MICROS, google_error
from world.reporting.common import date_of, decode_cursor, encode_cursor, reportable_days

router = APIRouter(prefix="/google/v25", tags=["mock-google-reporting"])
PAGE_SIZE = 10_000

QUERY_RE = re.compile(
    r"^\s*SELECT\s+(?P<fields>.+?)\s+FROM\s+(?P<resource>\w+)"
    r"(?:\s+WHERE\s+(?P<where>.+?))?(?:\s+ORDER\s+BY\s+(?P<order>.+?))?(?:\s+LIMIT\s+(?P<limit>\d+))?\s*$",
    re.IGNORECASE | re.DOTALL,
)
BETWEEN_RE = re.compile(r"(?P<f>[a-z_][\w.]*)\s+BETWEEN\s+'(?P<a>[^']*)'\s+AND\s+'(?P<b>[^']*)'", re.IGNORECASE)
COND_RE = re.compile(r"^\s*(?P<f>[a-z_][\w.]*)\s*(?P<op>!=|>=|<=|=|>|<)\s*(?P<v>'[^']*'|[\w.-]+)\s*$", re.IGNORECASE)

METRICS = {"metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions",
           "metrics.conversions_value"}
RESOURCE_FIELDS = {
    "customer": {"customer.id", "customer.descriptive_name", "customer.currency_code", "customer.time_zone"},
    "campaign_budget": {"campaign_budget.id", "campaign_budget.resource_name", "campaign_budget.amount_micros",
                        "campaign_budget.explicitly_shared", "campaign_budget.name", "campaign_budget.status"},
    "campaign": {"campaign.id", "campaign.name", "campaign.status", "campaign.resource_name",
                 "campaign.campaign_budget", "campaign.advertising_channel_type", "segments.date"} | METRICS,
    "ad_group": {"ad_group.id", "ad_group.name", "ad_group.status", "campaign.id", "campaign.name",
                 "segments.date"} | METRICS,
    "ad_group_ad": {"ad_group_ad.ad.id", "ad_group_ad.ad.name", "ad_group_ad.status", "ad_group.id",
                    "campaign.id", "campaign.name", "segments.date"} | METRICS,
}
FILTERABLE = {"segments.date", "campaign.id", "campaign_budget.id", "ad_group.id", "ad_group_ad.ad.id",
              "campaign.status", "metrics.impressions"}
INT64 = {"id", "amountMicros", "impressions", "clicks", "costMicros"}
CHANNEL_TYPE = {"google_search": "SEARCH", "google_video": "VIDEO"}


class SearchRequest(BaseModel):
    query: str
    pageToken: str | None = None


class GaqlError(ValueError):
    pass


def _lower_camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(p.title() for p in rest)


def camel(field: str) -> str:
    """campaign_budget.amount_micros -> campaignBudget.amountMicros"""
    return ".".join(_lower_camel(part) for part in field.split("."))


def _set(row: dict, field: str, value) -> None:
    parts = camel(field).split(".")
    node = row
    for p in parts[:-1]:
        node = node.setdefault(p, {})
    leaf = parts[-1]
    node[leaf] = str(value) if leaf in INT64 and value is not None else value


def _parse(query: str) -> tuple[list[str], str, list[tuple[str, str, object]], int | None]:
    m = QUERY_RE.match(query)
    if m is None:
        raise GaqlError("query must be SELECT ... FROM resource [WHERE ...] [ORDER BY ...] [LIMIT n]")
    resource = m["resource"].lower()
    if resource not in RESOURCE_FIELDS:
        raise GaqlError(f"unsupported resource {resource}")
    fields = [f.strip().lower() for f in m["fields"].split(",") if f.strip()]
    unknown = [f for f in fields if f not in RESOURCE_FIELDS[resource]]
    if unknown:
        raise GaqlError(f"unrecognized fields: {', '.join(unknown)}")
    conds: list[tuple[str, str, object]] = []
    where = m["where"] or ""
    for b in BETWEEN_RE.finditer(where):
        conds.append((b["f"].lower(), "between", (b["a"], b["b"])))
    rest = BETWEEN_RE.sub(" ", where).strip()
    for part in [p for p in re.split(r"\s+AND\s+", rest, flags=re.IGNORECASE) if p.strip()]:
        c = COND_RE.match(part)
        if c is None:
            raise GaqlError(f"unsupported condition: {part.strip()}")
        conds.append((c["f"].lower(), c["op"], c["v"].strip("'")))
    for f, _, _ in conds:
        if f not in FILTERABLE:
            raise GaqlError(f"unsupported filter field {f}")
    return fields, resource, conds, int(m["limit"]) if m["limit"] else None


def _date_bounds(conds) -> tuple[date | None, date | None]:
    since = until = None
    for f, op, v in conds:
        if f != "segments.date":
            continue
        if op == "between":
            since, until = date.fromisoformat(v[0]), date.fromisoformat(v[1])
        elif op == "=":
            since = until = date.fromisoformat(v)
        elif op in (">=", ">"):
            since = date.fromisoformat(v) if op == ">=" else date.fromordinal(date.fromisoformat(v).toordinal() + 1)
        elif op in ("<=", "<"):
            until = date.fromisoformat(v) if op == "<=" else date.fromordinal(date.fromisoformat(v).toordinal() - 1)
        else:
            raise GaqlError(f"unsupported operator {op} on segments.date")
    return since, until


def _passes(conds, values: dict[str, object]) -> bool:
    ops: dict[str, Callable[[float, float], bool]] = {
        "=": lambda a, b: a == b, "!=": lambda a, b: a != b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
    for f, op, v in conds:
        if f == "segments.date" or f not in values:
            continue
        actual = values[f]
        target = float(v) if isinstance(actual, (int, float)) else str(v)
        if not ops[op](actual, target):
            return False
    return True


@router.post("/customers/{customer_id}/googleAds:search")
def search(customer_id: str, body: SearchRequest, request: Request) -> JSONResponse:
    store = request.app.state.store
    truth = store.ctx.truth
    try:
        fields, resource, conds, limit = _parse(body.query)
        offset = decode_cursor(body.pageToken)
    except (GaqlError, ValueError) as exc:
        return google_error(400, "INVALID_ARGUMENT", str(exc))

    rows: list[dict] = []
    if resource == "customer":
        rows.append({"customer.id": customer_id, "customer.descriptive_name": "ADAPT Test Client",
                     "customer.currency_code": GOOGLE_CURRENCY, "customer.time_zone": BRAND_TIMEZONE})
    elif resource == "campaign_budget":
        shared = {b for b, members in truth.catalog.budgets().items() if len(members) > 1} if truth else set()
        for budget_id, amount, status in store.read(
                "SELECT budget_id, amount, status FROM budgets_state WHERE platform = 'google' ORDER BY budget_id"):
            rows.append({"campaign_budget.id": budget_id,
                         "campaign_budget.resource_name": f"customers/{customer_id}/campaignBudgets/{budget_id}",
                         "campaign_budget.amount_micros": round(amount * MICROS),
                         "campaign_budget.explicitly_shared": budget_id in shared,
                         "campaign_budget.name": f"{'Shared' if budget_id in shared else 'Budget'} {budget_id}",
                         "campaign_budget.status": status})
    else:
        if truth is None:
            return JSONResponse(status_code=503, content={"detail": "world not seeded: reporting is unavailable"})
        try:
            rows = _entity_rows(store, truth, customer_id, resource, fields, conds)
        except ValueError as exc:
            return google_error(400, "INVALID_ARGUMENT", str(exc))

    rows = [r for r in rows if _passes(conds, r)]
    if limit is not None:
        rows = rows[:limit]
    page = rows[offset:offset + PAGE_SIZE]
    results = []
    for r in page:
        out: dict = {}
        for f in fields:
            _set(out, f, r.get(f))
        results.append(out)
    payload: dict = {"results": results, "fieldMask": ",".join(camel(f) for f in fields)}
    if offset + PAGE_SIZE < len(rows):
        payload["nextPageToken"] = encode_cursor(offset + PAGE_SIZE)
    return JSONResponse(payload)


def _entity_rows(store, truth, customer_id: str, resource: str, fields: list[str], conds) -> list[dict]:
    campaigns = {c.campaign_id: c for c in truth.catalog.campaigns if c.platform == "google"}
    adsets = {a.adset_id: a for a in truth.catalog.adsets if a.campaign_id in campaigns}
    ads = {cr.creative_id: cr for cr in truth.catalog.creatives if cr.campaign_id in campaigns}
    paused = {r[0] for r in store.read("SELECT campaign_id FROM campaign_status WHERE status = 'PAUSED'")}

    def attrs(cid: str, asid: str | None = None, adid: str | None = None) -> dict:
        c = campaigns[cid]
        status = "PAUSED" if cid in paused else "ENABLED"
        row = {"campaign.id": cid, "campaign.name": c.name, "campaign.status": status,
               "campaign.resource_name": f"customers/{customer_id}/campaigns/{cid}",
               "campaign.campaign_budget": f"customers/{customer_id}/campaignBudgets/{c.budget_id}",
               "campaign.advertising_channel_type": CHANNEL_TYPE[c.channel], "campaign_budget.id": c.budget_id}
        if asid:
            row.update({"ad_group.id": asid, "ad_group.name": f"{c.name} | {adsets[asid].audience}",
                        "ad_group.status": status})
        if adid:
            cr = ads[adid]
            row.update({"ad_group_ad.ad.id": adid, "ad_group_ad.ad.name": f"{cr.format} · {cr.hook}",
                        "ad_group_ad.status": status})
        return row

    wants_metrics = any(f in METRICS for f in fields) or any(f.startswith("metrics.") for f, _, _ in conds)
    by_day = "segments.date" in fields
    if not wants_metrics and not by_day:
        if resource == "campaign":
            return [attrs(cid) for cid in sorted(campaigns)]
        if resource == "ad_group":
            return [attrs(a.campaign_id, a.adset_id) for a in sorted(adsets.values(), key=lambda x: x.adset_id)]
        return [attrs(cr.campaign_id, cr.adset_id, cr.creative_id) for cr in sorted(ads.values(),
                                                                                       key=lambda x: x.creative_id)]

    since, until = _date_bounds(conds)
    lo, hi = reportable_days(store, truth, since, until)
    key = {"campaign": "campaign_id", "ad_group": "adset_id", "ad_group_ad": "creative_id"}[resource]
    group = f"day, {key}" if by_day else key
    facts = store.read(f"""
        SELECT {'day' if by_day else 'NULL'}, {key}, any_value(campaign_id), any_value(adset_id),
               sum(impressions), sum(clicks), sum(spend_inr), sum(conversions), sum(conversion_value_inr)
        FROM fact_ad_creative_daily WHERE platform = 'google' AND day BETWEEN ? AND ?
        GROUP BY {group} HAVING sum(impressions) > 0 ORDER BY {group}
    """, [lo, hi])
    rows = []
    for day, ident, cid, asid, imps, clicks, spend, conv, value in facts:
        row = attrs(cid, asid if resource != "campaign" else None, ident if resource == "ad_group_ad" else None)
        if by_day:
            row["segments.date"] = date_of(truth, day).isoformat()
        row.update({"metrics.impressions": int(imps), "metrics.clicks": int(clicks),
                    "metrics.cost_micros": round(spend * MICROS), "metrics.conversions": float(conv),
                    "metrics.conversions_value": round(float(value), 2)})
        rows.append(row)
    return rows
