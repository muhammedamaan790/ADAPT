"""CSV upload (Stage 3; spec §1 "Ingestion modes" 2, §12 /ingest/*): templates, a rapidfuzz + synonym field mapper,
server-side validation (never trusting the browser's), staging, confirm, and a NEW workspace file per upload.

- Types: ads (date, budget_id, platform, spend, impressions, clicks), orders (date, order_id, sku, quantity,
  net_revenue), inventory (sku, on_hand, reserved, safety_stock), margins (sku, price, unit_cost); amounts in INR,
  dates Asia/Kolkata (the only accepted source currency / timezone).
- suggest_mapping(): for each required field, the best header by exact synonym, then rapidfuzz token-set similarity
  (score >= 80), each header used once; the score is returned so the UI shows how sure it is.
- stage(): validates every record against the data contract (types, ranges, whole numbers, clicks <= impressions,
  reserved <= on hand, valid dates, platform, duplicate business keys); any error -> 422 with the row list, nothing
  written. Valid records are staged in the registry file with a content hash; the same file staged twice gives the same
  import id (idempotent).
- confirm(import_id, mapping): writes the typed table into a NEW workspace DuckDB `upload-<import id>.duckdb`
  (stg.upload_<type>, provenance UPLOADED, available_at = confirm time) plus a data-contract report, and registers it.
  Upload workspaces are for review in the Data Hub; the decision engine keeps running on the world-backed demo workspace
  (an engine run needs every source: ads, orders, inventory, pricing), which the import message states.
- A file staged for a brand workspace (`target`) is confirmed into that workspace by the caller's `sink` instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path

from rapidfuzz import fuzz

FIELDS = {
    "ads": ["date", "budget_id", "platform", "spend", "impressions", "clicks"],
    "orders": ["date", "order_id", "sku", "quantity", "net_revenue"],
    "inventory": ["sku", "on_hand", "reserved", "safety_stock"],
    "margins": ["sku", "price", "unit_cost"],
}
OPTIONAL = {"ads": ["conversion_value"]}  # mapped when the file has it; empty cells are stored as NULL
TEXT = {"date", "sku", "budget_id", "platform", "order_id"}
WHOLE = {"impressions", "clicks", "on_hand", "reserved", "safety_stock", "quantity"}
PLATFORMS = {"Meta", "Google"}
SYNONYMS = {
    "date": ["day", "report date", "date start", "stat time day", "segments date"],
    "budget_id": ["campaign budget", "budget", "budget id", "campaign budget id", "campaign id"],
    "platform": ["channel", "network", "source", "publisher"],
    "spend": ["cost", "amount spent", "spend inr", "cost inr", "cost micros"],
    "conversion_value": ["conv value", "conv. value", "conversion value", "conversions value", "all conv value",
                         "purchase conversion value", "website purchases conversion value", "purchases value",
                         "total conversion value", "revenue", "roas value"],
    "impressions": ["impr", "impr.", "views served"],
    "clicks": ["link clicks", "clicks all"],
    "order_id": ["order", "order id", "order number", "order no", "name"],
    "quantity": ["qty ordered", "units", "units sold", "lineitem quantity", "quantity ordered"],
    "net_revenue": ["revenue", "net sales", "sales", "total", "line total", "net revenue inr"],
    "sku": ["product id", "item id", "variant sku", "asin", "product code"],
    "on_hand": ["stock", "on hand", "quantity on hand", "qty", "inventory", "units in stock"],
    "reserved": ["allocated", "committed", "reserved qty"],
    "safety_stock": ["safety stock", "min stock", "buffer"],
    "price": ["selling price", "list price", "mrp", "retail price"],
    "unit_cost": ["cogs", "cost price", "unit cost", "landed cost"],
}
MIN_SCORE = 80.0
MAX_ROWS = 5000


class UploadError(ValueError):
    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or []


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def template(kind: str) -> dict:
    if kind not in FIELDS:
        raise UploadError(f"unknown import type {kind}; types: {', '.join(FIELDS)}")
    fields = FIELDS[kind] + OPTIONAL.get(kind, [])
    return {"type": kind, "columns": FIELDS[kind], "optional": OPTIONAL.get(kind, []),
            "synonyms": {f: SYNONYMS.get(f, []) for f in fields},
            "csv": ",".join(FIELDS[kind]) + "\n", "currency": "INR", "timezone": "Asia/Kolkata"}


def suggest_mapping(kind: str, headers: list[str]) -> dict:
    """{field: {header, score, method}} for every required and optional field; header None when nothing scores
    >= 80."""
    t = template(kind)
    fields = t["columns"] + t["optional"]
    used: set[str] = set()
    out = {}
    for f in fields:
        best = (None, 0.0, "none")
        for h in headers:
            if h in used:
                continue
            n = _norm(h)
            if n in (_norm(f), *map(_norm, SYNONYMS.get(f, []))):
                best = (h, 100.0, "exact" if n == _norm(f) else "synonym")
                break
            score = max(fuzz.token_set_ratio(n, _norm(c)) for c in (f, *SYNONYMS.get(f, [])))
            if score > best[1]:
                best = (h, float(score), "fuzzy")
        if best[0] is not None and best[1] >= MIN_SCORE:
            used.add(best[0])
            out[f] = {"header": best[0], "score": best[1], "method": best[2]}
        else:
            out[f] = {"header": None, "score": best[1], "method": "none"}
    return out


def validate(kind: str, records: list[dict]) -> list[dict]:
    """The data contract, server side. Returns typed rows; raises UploadError listing every row error."""
    fields = template(kind)["columns"]
    if not records:
        raise UploadError("the file has no rows")
    if len(records) > MAX_ROWS:
        raise UploadError(f"at most {MAX_ROWS} rows per upload")
    errors, rows, seen = [], [], set()
    for i, r in enumerate(records, start=2):
        missing = [f for f in fields if f not in r]
        if missing:
            errors.append(f"row {i}: missing {', '.join(missing)}")
            continue
        row = {}
        for f in fields:
            v = r[f]
            if f in TEXT:
                s = str(v).strip()
                if not s:
                    errors.append(f"row {i}: {f} is required")
                row[f] = s
                continue
            try:
                x = float(v)
            except (TypeError, ValueError):
                errors.append(f"row {i}: {f} must be a number")
                continue
            if not (x == x and abs(x) != float("inf")) or x < 0:
                errors.append(f"row {i}: {f} must be a finite nonnegative number")
            if f in WHOLE and x != int(x):
                errors.append(f"row {i}: {f} must be a whole number")
            row[f] = int(x) if f in WHOLE and x == int(x) else x
        for f in OPTIONAL.get(kind, []):
            raw = r.get(f)
            if raw is None or str(raw).strip() == "":
                row[f] = None
                continue
            try:
                x = float(raw)
            except (TypeError, ValueError):
                errors.append(f"row {i}: {f} must be a number")
                continue
            if not (x == x and abs(x) != float("inf")) or x < 0:
                errors.append(f"row {i}: {f} must be a finite nonnegative number")
            row[f] = x
        if kind in ("ads", "orders") and all(f in row for f in fields):
            if kind == "ads" and row["platform"] not in PLATFORMS:
                errors.append(f"row {i}: platform must be Meta or Google")
            try:
                if date.fromisoformat(row["date"]).isoformat() != row["date"]:
                    raise ValueError
            except ValueError:
                errors.append(f"row {i}: date must be a valid YYYY-MM-DD date")
            if isinstance(row.get("clicks"), (int, float)) and isinstance(row.get("impressions"), (int, float)) \
                    and row["clicks"] > row["impressions"]:
                errors.append(f"row {i}: clicks exceed impressions")
        if kind == "inventory" and all(f in row for f in fields) and row["reserved"] > row["on_hand"]:
            errors.append(f"row {i}: reserved units exceed on-hand stock")
        key = ((row.get("date"), row.get("platform"), row.get("budget_id")) if kind == "ads" else
               (row.get("order_id"), row.get("sku")) if kind == "orders" else row.get("sku"))
        if key in seen:
            errors.append(f"row {i}: duplicate business key {key}")
        seen.add(key)
        rows.append(row)
    if errors:
        raise UploadError(f"{len(errors)} data-contract violation(s)", errors[:50])
    return rows


class Registry:
    """data/workspaces/uploads.json: staged + imported uploads and their workspace files."""

    def __init__(self, workspaces_dir: Path):
        self.dir = Path(workspaces_dir)
        self.path = self.dir / "uploads.json"

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"uploads": {}}

    def save(self, reg: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(reg, indent=2, default=str), encoding="utf-8")


def stage(registry: Registry, kind: str, records: list[dict], currency: str, timezone: str,
          target: str | None = None) -> dict:
    """`target`: the brand workspace the file is for; part of the import id, so one file can go into several."""
    if currency != "INR" or timezone != "Asia/Kolkata":
        raise UploadError("uploads must be in INR and Asia/Kolkata (the brand currency and timezone)")
    rows = validate(kind, records)
    payload = {"type": kind, "rows": rows, **({"target": target} if target else {})}
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    import_id = f"imp-{digest[:16]}"
    reg = registry.load()
    existing = reg["uploads"].get(import_id)
    if existing:
        return {"import_id": import_id, "status": existing["status"], "row_count": len(rows),
                "message": "this exact file was already " + existing["status"].lower()}
    reg["uploads"][import_id] = {"type": kind, "rows": rows, "status": "STAGED", "staged_at": datetime.now(),
                                 "sha256": digest, **({"target": target} if target else {})}
    registry.save(reg)
    return {"import_id": import_id, "status": "STAGED", "row_count": len(rows),
            "message": f"{len(rows)} {kind} rows passed the data contract and are staged; confirm the mapping to "
                       + ("import them into this workspace" if target else "import them into a new workspace")}


def confirm(registry: Registry, import_id: str, mapping: dict, actor: str, sink=None) -> dict:
    """`sink(kind, rows, import_id, actor) -> message` writes a brand-workspace upload; without a target the rows go
    into a new review workspace file."""
    import duckdb

    reg = registry.load()
    up = reg["uploads"].get(import_id)
    if up is None:
        raise UploadError(f"no staged upload {import_id}")
    fields = template(up["type"])["columns"]
    allowed = set(fields) | set(OPTIONAL.get(up["type"], []))
    if any(f not in mapping or not mapping[f] for f in fields) or set(mapping) - allowed:
        raise UploadError(f"the mapping must name a column for every field: {', '.join(fields)}")
    if up["status"] == "IMPORTED":
        return {"import_id": import_id, "status": "IMPORTED", "row_count": len(up["rows"]),
                "message": f"already imported into workspace {up['workspace_id']}"}
    if up.get("target"):
        if sink is None:
            raise UploadError(f"this file was staged for workspace {up['target']}; switch to it to confirm")
        message = sink(up["type"], up["rows"], import_id, actor)
        up.update(status="IMPORTED", workspace_id=up["target"], imported_at=datetime.now(), mapping=mapping,
                  actor=actor)
        reg["uploads"][import_id] = up
        registry.save(reg)
        return {"import_id": import_id, "status": "IMPORTED", "row_count": len(up["rows"]), "message": message}
    ws_id = f"upload-{import_id[4:]}"
    path = registry.dir / f"{ws_id}.duckdb"
    now = datetime.now()
    con = duckdb.connect(str(path))
    try:
        cols = ", ".join(f"{f} {'VARCHAR' if f in TEXT else ('BIGINT' if f in WHOLE else 'DOUBLE')}" for f in fields)
        con.execute("CREATE SCHEMA IF NOT EXISTS stg; CREATE SCHEMA IF NOT EXISTS ops")
        con.execute(f"CREATE OR REPLACE TABLE stg.upload_{up['type']} ({cols}, _provenance VARCHAR, "
                    "_import_id VARCHAR, available_at TIMESTAMP, source_currency VARCHAR, source_timezone VARCHAR)")
        con.executemany(f"INSERT INTO stg.upload_{up['type']} VALUES ({', '.join('?' * (len(fields) + 5))})",
                        [[r[f] for f in fields] + ["UPLOADED", import_id, now, "INR", "Asia/Kolkata"]
                         for r in up["rows"]])
        con.execute("CREATE OR REPLACE TABLE ops.upload_manifest (import_id VARCHAR, type VARCHAR, rows BIGINT, "
                    "sha256 VARCHAR, mapping JSON, imported_at TIMESTAMP, actor VARCHAR)")
        con.execute("INSERT INTO ops.upload_manifest VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [import_id, up["type"], len(up["rows"]), up["sha256"], json.dumps(mapping), now, actor])
    finally:
        con.close()
    up.update(status="IMPORTED", workspace_id=ws_id, imported_at=now, mapping=mapping, actor=actor)
    reg["uploads"][import_id] = up
    registry.save(reg)
    return {"import_id": import_id, "status": "IMPORTED", "row_count": len(up["rows"]),
            "message": f"imported {len(up['rows'])} {up['type']} rows into the new workspace {ws_id} (provenance "
                       "UPLOADED); the decision engine keeps running on the demo workspace until every required "
                       "source (ads, orders, inventory, pricing) is uploaded"}


def upload_workspaces(registry: Registry) -> list[dict]:
    return [{"id": u["workspace_id"], "name": f"Upload {iid[4:12]} ({u['type']})"[:60], "currency": "INR",
             "timezone": "Asia/Kolkata"}
            for iid, u in sorted(registry.load()["uploads"].items())
            if u.get("status") == "IMPORTED" and not u.get("target")]
