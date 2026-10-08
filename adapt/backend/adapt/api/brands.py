"""Brand workspaces: a named workspace that starts empty and fills only from the CSVs uploaded into it.

- Registry: data/workspaces/brands.json {active, items[{id, name, created_at}]}; each workspace is its own DuckDB file
  `<id>.duckdb` next to the engine workspace. `active` None means the simulated-world engine workspace.
- Uploads land in stg.upload_<type>; a row whose business key (ads: date+platform+budget; orders: order+sku; inventory
  and margins: sku) was uploaded before is replaced, so a corrected file updates the numbers instead of double counting.
- The Command Center reads brand_overview(): every metric is 0 until a file is uploaded, then computed from the uploads
  alone. A metric whose inputs are missing (e.g. contribution without margins) is null with the missing upload named.
  No engine runs here: decisions, executions and outcomes stay empty (every view guards on its tables existing).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

from adapt.api.views import _rel, has
from adapt.core.db import Database
from adapt.ingest.csv_upload import FIELDS, TEXT, WHOLE

PREFIX = "brand-"
MAX_BRANDS = 19  # plus the engine workspace: the frontend lists at most 20
KEYS = {"ads": ["date", "platform", "budget_id"], "orders": ["order_id", "sku"], "inventory": ["sku"],
        "margins": ["sku"]}
LABELS = {"ads": "Ad spend CSV", "orders": "Orders CSV", "inventory": "Inventory CSV", "margins": "SKU margins CSV"}


class BrandError(ValueError):
    pass


class Brands:
    def __init__(self, workspaces_dir: Path):
        self.dir = Path(workspaces_dir)
        self.path = self.dir / "brands.json"
        self._dbs: dict[str, Database] = {}
        self._lock = threading.Lock()

    # ---- registry ---------------------------------------------------------------------------------------------------
    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"active": None, "items": []}

    def _save(self, reg: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(reg, indent=2), encoding="utf-8")

    def items(self) -> list[dict]:
        return self.load()["items"]

    def get(self, ws_id: str) -> dict | None:
        return next((w for w in self.items() if w["id"] == ws_id), None)

    def create(self, name: str) -> dict:
        name = name.strip()
        with self._lock:
            reg = self.load()
            if len(reg["items"]) >= MAX_BRANDS:
                raise BrandError(f"at most {MAX_BRANDS} workspaces; nothing was created")
            if any(w["name"].lower() == name.lower() for w in reg["items"]):
                raise BrandError("a workspace with this name already exists")
            slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "workspace"
            item = {"id": f"{PREFIX}{slug}-{secrets.token_hex(3)}", "name": name,
                    "created_at": datetime.now().isoformat(timespec="seconds")}
            reg["items"].append(item)
            self._save(reg)
        self.db(item["id"])  # create the empty file now, so the workspace exists before its first upload
        return item

    def active(self) -> dict | None:
        reg = self.load()
        return next((w for w in reg["items"] if w["id"] == reg.get("active")), None)

    def activate(self, ws_id: str | None) -> None:
        with self._lock:
            reg = self.load()
            reg["active"] = ws_id
            self._save(reg)

    # ---- databases --------------------------------------------------------------------------------------------------
    def db(self, ws_id: str) -> Database:
        with self._lock:
            if ws_id not in self._dbs:
                self._dbs[ws_id] = Database(self.dir / f"{ws_id}.duckdb")
            return self._dbs[ws_id]

    def active_db(self) -> Database | None:
        ws = self.active()
        return self.db(ws["id"]) if ws else None

    def close(self) -> None:
        with self._lock:
            for d in self._dbs.values():
                d.close()
            self._dbs.clear()


def import_rows(db: Database, kind: str, rows: list[dict], import_id: str, actor: str) -> dict:
    """Append validated rows to stg.upload_<kind>, replacing earlier rows with the same business key."""
    fields = FIELDS[kind]
    cols = ", ".join(f"{f} {'VARCHAR' if f in TEXT else ('BIGINT' if f in WHOLE else 'DOUBLE')}" for f in fields)
    table = f"stg.upload_{kind}"
    match = " AND ".join(f"n.{k} = t.{k}" for k in KEYS[kind])
    now = datetime.now()

    def write(cur):
        cur.execute("CREATE SCHEMA IF NOT EXISTS stg; CREATE SCHEMA IF NOT EXISTS ops")
        cur.execute(f"CREATE TABLE IF NOT EXISTS {table} ({cols}, _import_id VARCHAR, available_at TIMESTAMP)")
        cur.execute("CREATE TABLE IF NOT EXISTS ops.uploads (import_id VARCHAR, type VARCHAR, rows BIGINT, "
                    "replaced BIGINT, imported_at TIMESTAMP, actor VARCHAR)")
        cur.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * (len(fields) + 2))})",
                        [[r[f] for f in fields] + [import_id, now] for r in rows])
        older = f"""FROM {table} t WHERE t._import_id <> ? AND EXISTS
                    (SELECT 1 FROM {table} n WHERE n._import_id = ? AND {match})"""
        replaced = cur.execute(f"SELECT count(*) {older}", [import_id, import_id]).fetchone()[0]
        cur.execute(f"DELETE {older}", [import_id, import_id])
        cur.execute("INSERT INTO ops.uploads VALUES (?, ?, ?, ?, ?, ?)",
                    [import_id, kind, len(rows), replaced, now, actor])
        return int(replaced)

    replaced = db.write(write)
    return {"rows": len(rows), "replaced": replaced}


# ---- the Command Center for a brand workspace ------------------------------------------------------------------------
def _num(db: Database, sql: str, params: list | None = None) -> float:
    v = db.query(sql, params or [])[0][0]
    return float(v or 0)


def _window(db: Database, kinds: set[str], last: date, days: int, offset: int = 0) -> dict:
    hi = last - timedelta(days=offset)
    lo = hi - timedelta(days=days - 1)
    rng = [lo, hi]
    w = {"revenue": 0.0, "spend": 0.0, "cogs": None, "has_data": False}
    if "orders" in kinds:
        w["revenue"] = _num(db, "SELECT sum(net_revenue) FROM stg.upload_orders "
                                "WHERE CAST(date AS DATE) BETWEEN ? AND ?", rng)
        if "margins" in kinds:
            w["cogs"] = _num(db, """SELECT sum(o.quantity * m.unit_cost) FROM stg.upload_orders o
                                    JOIN stg.upload_margins m USING (sku)
                                    WHERE CAST(o.date AS DATE) BETWEEN ? AND ?""", rng)
    if "ads" in kinds:
        w["spend"] = _num(db, "SELECT sum(spend) FROM stg.upload_ads WHERE CAST(date AS DATE) BETWEEN ? AND ?", rng)
    w["has_data"] = bool(w["revenue"] or w["spend"])
    return w


def _ratio(num: float | None, den: float) -> float | None:
    if num is None:
        return None
    if not num:
        return 0.0
    return num / den if den else None


def brand_overview(db: Database, ws: dict) -> dict:
    kinds = {k for k in FIELDS if has(db, "stg", f"upload_{k}")}
    uploads = (db.query("SELECT type, count(*), sum(rows), max(imported_at) FROM ops.uploads GROUP BY type")
               if has(db, "ops", "uploads") else [])
    last_upload = max((u[3] for u in uploads), default=None)
    as_of = last_upload.isoformat(timespec="seconds") if last_upload else ws["created_at"]
    dated = [k for k in ("orders", "ads") if k in kinds]
    last = max((db.query(f"SELECT max(CAST(date AS DATE)) FROM stg.upload_{k}")[0][0] for k in dated),
               default=None)
    empty = {"revenue": 0.0, "spend": 0.0, "cogs": None, "has_data": False}
    cur = _window(db, kinds, last, 7) if last else empty
    prev = _window(db, kinds, last, 7, 7) if last else empty
    ch = (lambda a, b: _rel(a, b) if prev["has_data"] and a is not None and b is not None else None)  # noqa: E731

    def cba(w):
        return None if w["cogs"] is None else w["revenue"] - w["cogs"]

    def caa(w):
        c = cba(w)
        return None if c is None else c - w["spend"]

    def why(*needed):
        missing = [k for k in needed if k not in kinds]
        return f"NEEDS_{'_AND_'.join(m.upper() for m in missing)}_UPLOAD" if missing else None

    nothing = not kinds
    inv_risk = (_num(db, "SELECT count(*) FROM stg.upload_inventory WHERE on_hand - reserved < safety_stock")
                if "inventory" in kinds else 0.0)
    spec = [
        ("net_revenue", "Net revenue (7d)", cur["revenue"], "money", ch(cur["revenue"], prev["revenue"]),
         "Σ order net revenue, last 7 days of the uploads", "stg.upload_orders", None),
        ("spend", "Ad spend (7d)", cur["spend"], "money", ch(cur["spend"], prev["spend"]),
         "Σ platform spend (INR), last 7 days of the uploads", "stg.upload_ads", None),
        ("caa", "Contribution after ads (7d)", caa(cur), "money", ch(caa(cur), caa(prev)),
         "net revenue − units × unit cost − ad spend", "stg.upload_orders + upload_margins + upload_ads",
         why("orders", "margins", "ads")),
        ("mer", "MER (7d)", _ratio(cur["revenue"], cur["spend"]), "ratio",
         ch(_ratio(cur["revenue"], cur["spend"]), _ratio(prev["revenue"], prev["spend"])),
         "net revenue / ad spend", "stg.upload_orders + upload_ads", why("orders", "ads")),
        ("poas", "POAS (7d)", _ratio(cba(cur), cur["spend"]), "ratio",
         ch(_ratio(cba(cur), cur["spend"]), _ratio(cba(prev), prev["spend"])),
         "contribution before ads / ad spend (blended: uploads carry no attribution)",
         "stg.upload_orders + upload_margins + upload_ads", why("orders", "margins", "ads")),
        ("inventory_risk_skus", "SKUs below safety stock", inv_risk, "count", None,
         "count of SKUs with on hand − reserved < safety stock", "stg.upload_inventory", None),
    ]
    metrics = []
    for key, label, value, fmt, change, formula, source, reason in spec:
        if nothing:
            value, change, reason = 0.0, None, None
        elif value is None:
            reason = reason or "ZERO_DENOMINATOR"
        metrics.append({"key": key, "label": label, "value": value, "format": fmt, "change": change,
                        "reason": reason, "formula": formula, "source": source, "available_at": as_of,
                        "provenance_inputs": ["UPLOADED"]})
    series = []
    if "orders" in kinds and last:
        daily = dict(db.query("""SELECT CAST(date AS DATE), sum(net_revenue) FROM stg.upload_orders
                                 WHERE CAST(date AS DATE) > ? GROUP BY 1""", [last - timedelta(days=28)]))
        for i in range(13, -1, -1):
            d = last - timedelta(days=i)
            prior = [daily.get(d - timedelta(days=k), 0.0) for k in range(1, 8)]
            series.append({"date": d.isoformat(), "actual": float(daily.get(d, 0.0)),
                           "baseline": float(sum(prior) / 7)})
    sources = []
    for kind, files, _rows, at in sorted(uploads):
        newest = (db.query(f"SELECT max(date) FROM stg.upload_{kind}")[0][0] if kind in ("ads", "orders") else None)
        current = int(_num(db, f"SELECT count(*) FROM stg.upload_{kind}"))
        sources.append({"id": f"upload_{kind}", "name": LABELS[kind], "kind": "upload", "score": 100.0,
                        "status": "GREEN", "provenance": "UPLOADED",
                        "freshness": f"{current} rows from {files} file{'s' if files != 1 else ''}"
                                     + (f", newest day {newest}" if newest else "")
                                     + f", uploaded {at:%d %b %H:%M}"})
    if nothing:
        brief = ("Empty workspace: every number is 0. Upload ads, orders, inventory or margins CSVs to fill it; "
                 "each upload updates the numbers here.")
    else:
        missing = [k for k in ("ads", "orders", "margins", "inventory") if k not in kinds]
        brief = (f"Built only from your uploads ({', '.join(sorted(kinds))})."
                 + (f" Upload {', '.join(missing)} to complete the picture." if missing else "")
                 + " Decisions need the simulated world workspace, which runs the engine.")
    return {"workspace": ws["name"], "decision_ts": as_of, "world_day": 0, "scenario": "NONE", "brief": brief,
            "metrics": metrics, "sources": sources, "attention": [], "series": series,
            "loop": [{"label": "Upload data", "state": "waiting" if nothing else "complete"},
                     {"label": "Metrics", "state": "waiting" if nothing else "current"},
                     {"label": "Decisions", "state": "waiting"}],
            "calibration": 0.0, "counts": {"success": 0, "neutral": 0, "failed": 0, "inconclusive": 0}}
