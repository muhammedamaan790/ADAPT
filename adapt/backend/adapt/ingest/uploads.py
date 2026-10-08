"""Two-phase CSV normalization into isolated, idempotent DuckDB upload workspaces."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path

from rapidfuzz.fuzz import ratio

from adapt.core.db import Database
from adapt.decide.hashing import content_hash

FIELDS = {
    "ads": ["date", "budget_id", "platform", "spend", "impressions", "clicks"],
    "inventory": ["sku", "on_hand", "reserved", "safety_stock"],
    "margins": ["sku", "price", "unit_cost"],
    "cvr": ["date", "sku", "category", "channel", "clicks", "purchases"],
}
DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.csv_imports (
 import_id VARCHAR PRIMARY KEY, kind VARCHAR, records JSON, currency VARCHAR, timezone VARCHAR,
 status VARCHAR, mapping JSON, workspace_path VARCHAR, created_at TIMESTAMP
);
"""
SYNONYMS = {
    "spend": ["cost", "ad_spend"],
    "budget_id": ["campaign_id"],
    "on_hand": ["stock", "quantity"],
    "unit_cost": ["cogs", "cost_per_unit"],
    "price": ["retail_price"],
    "sku": ["product_id"],
}


def suggest(headers, kind):
    mapping = {}
    for field in FIELDS[kind]:
        scores = [(max(ratio(h.lower(), s) for s in [field, *SYNONYMS.get(field, [])]), h) for h in headers]
        score, header = max(scores, default=(0, ""))
        if score >= 75:
            mapping[field] = header
    return mapping


def stage(db, kind, records, currency, timezone):
    if kind not in FIELDS or not 1 <= len(records) <= 5000:
        raise ValueError("IMPORT_INVALID: choose a supported type and 1–5,000 rows")
    if currency != "INR" or timezone != "Asia/Kolkata":
        raise ValueError("IMPORT_INVALID: this workspace requires INR and Asia/Kolkata")
    encoded = json.dumps(records, allow_nan=False)
    if len(encoded.encode()) > 2_000_000 or any(not isinstance(r, dict) or len(r) > 40 for r in records):
        raise ValueError("IMPORT_INVALID: 2 MB / 40 columns maximum")
    iid = "csv-" + content_hash({"kind": kind, "records": records, "currency": currency, "timezone": timezone})[:24]

    def write(cur):
        cur.execute(DDL)
        cur.execute(
            "INSERT INTO ops.csv_imports VALUES (?, ?, ?, ?, ?, 'STAGED', NULL, NULL, ?) ON CONFLICT DO NOTHING",
            [iid, kind, encoded, currency, timezone, datetime.now(UTC)],
        )

    db.write(write)
    status = db.query("SELECT status FROM ops.csv_imports WHERE import_id=?", [iid])[0][0]
    return {
        "import_id": iid,
        "status": status,
        "row_count": len(records),
        "message": "Already imported" if status == "IMPORTED" else "Staged; map and confirm before import",
        "suggested_mapping": suggest(list(records[0]), kind),
    }


def normalize(kind, records, mapping):
    fields = FIELDS[kind]
    if set(mapping) != set(fields) or len(set(mapping.values())) != len(fields):
        raise ValueError("IMPORT_INVALID: map every required field to a distinct column")
    out, seen = [], set()
    for index, row in enumerate(records, start=2):
        item = {}
        for field in fields:
            value = row.get(field, row.get(mapping[field]))
            if field in {"date", "sku", "budget_id", "platform", "category", "channel"}:
                value = str(value or "").strip()
                if not value or len(value) > 150:
                    raise ValueError(f"IMPORT_INVALID: row {index}, {field} required (max 150 characters)")
            else:
                try:
                    value = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"IMPORT_INVALID: row {index}, invalid {field}") from exc
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"IMPORT_INVALID: row {index}, negative/nonfinite {field}")
                if field not in {"spend", "price", "unit_cost"} and (value != int(value) or value > 2**53 - 1):
                    raise ValueError(f"IMPORT_INVALID: row {index}, {field} must be a safe whole number")
            item[field] = value
        if kind in {"ads", "cvr"}:
            if kind == "ads" and (
                item["platform"] not in {"Meta", "Google", "TikTok", "Amazon"} or item["clicks"] > item["impressions"]
            ):
                raise ValueError(f"IMPORT_INVALID: row {index}, invalid platform or funnel")
            if kind == "cvr" and (
                item["channel"] not in {"meta", "google_search", "google_video", "tiktok", "amazon_sp"}
                or item["purchases"] > item["clicks"]
            ):
                raise ValueError(f"IMPORT_INVALID: row {index}, invalid channel or binomial observations")
            if date.fromisoformat(item["date"]).isoformat() != item["date"]:
                raise ValueError("IMPORT_INVALID: date must be YYYY-MM-DD")
            key = (
                (item["date"], item["platform"], item["budget_id"])
                if kind == "ads"
                else (item["date"], item["sku"], item["channel"])
            )
        else:
            key = item["sku"]
            if kind == "inventory" and item["reserved"] > item["on_hand"]:
                raise ValueError("IMPORT_INVALID: reserved exceeds on-hand")
        if key in seen:
            raise ValueError(f"IMPORT_INVALID: duplicate business key on row {index}")
        seen.add(key)
        out.append(item)
    return out


def confirm(db, iid, mapping, root):
    db.write(lambda cur: cur.execute(DDL))
    found = db.query("SELECT kind, records, status, mapping FROM ops.csv_imports WHERE import_id=?", [iid])
    if not found:
        raise ValueError("IMPORT_NOT_FOUND: stage the file first")
    kind, records, status, old_mapping = found[0]
    records = json.loads(records)
    if status == "IMPORTED":
        if json.loads(old_mapping) != mapping:
            raise ValueError("IMPORT_CONFLICT: confirmed mappings are immutable")
        return {
            "import_id": iid,
            "status": status,
            "row_count": len(records),
            "message": "Already imported; no duplicate rows",
        }
    rows = normalize(kind, records, mapping)
    # iid is looked up in our staging table; still refuse path characters before opening a file.
    if not iid.startswith("csv-") or len(iid) != 28 or not all(c in "0123456789abcdef" for c in iid[4:]):
        raise ValueError("IMPORT_INVALID: invalid staged identifier")
    target = Path(root) / f"{iid}.duckdb"
    imported = Database(target)
    now = datetime.now(UTC).replace(tzinfo=None)
    try:

        def load(cur):
            cur.execute("CREATE SCHEMA IF NOT EXISTS uploads")
            cur.execute(
                "CREATE TABLE IF NOT EXISTS uploads.records (business_key VARCHAR PRIMARY KEY, kind VARCHAR, "
                "payload JSON, provenance VARCHAR, available_at TIMESTAMP)"
            )
            for item in rows:
                cur.execute(
                    "INSERT INTO uploads.records VALUES (?, ?, ?, 'LIVE', ?) ON CONFLICT DO NOTHING",
                    [content_hash(item), kind, json.dumps(item), now],
                )
            cur.execute("CREATE SCHEMA IF NOT EXISTS stg")
            columns = ", ".join(
                f'"{f}" '
                + (
                    "DATE"
                    if f == "date"
                    else "DOUBLE"
                    if f in {"spend", "price", "unit_cost"}
                    else "BIGINT"
                    if f in {"impressions", "clicks", "purchases", "on_hand", "reserved", "safety_stock"}
                    else "VARCHAR"
                )
                for f in FIELDS[kind]
            )
            cur.execute(
                f'CREATE TABLE IF NOT EXISTS stg."csv_{kind}" ({columns}, provenance VARCHAR, available_at TIMESTAMP)'
            )
            # Immutable content-addressed workspace: a retry replaces exactly the same normalized rows.
            cur.execute(f'DELETE FROM stg."csv_{kind}"')
            for item in rows:
                cur.execute(
                    f'INSERT INTO stg."csv_{kind}" VALUES ({", ".join("?" for _ in range(len(FIELDS[kind]) + 2))})',
                    [*[item[f] for f in FIELDS[kind]], "LIVE", now],
                )

        imported.write(load)
    finally:
        imported.close()
    db.write(
        lambda cur: cur.execute(
            "UPDATE ops.csv_imports SET status='IMPORTED', mapping=?, workspace_path=? WHERE import_id=?",
            [json.dumps(mapping), str(target), iid],
        )
    )
    return {
        "import_id": iid,
        "status": "IMPORTED",
        "row_count": len(rows),
        "message": "Validated records saved in a separate upload workspace. Active simulated budgets were not "
        "changed. Uploaded facts are inspectable; full engine decisions require all source dependencies.",
    }


def imports(db):
    db.write(lambda cur: cur.execute(DDL))
    return [
        {
            "import_id": iid,
            "type": kind,
            "status": status,
            "row_count": len(json.loads(records)),
            "created_at": at.isoformat() + "Z",
        }
        for iid, kind, status, records, at in db.query(
            "SELECT import_id, kind, status, records, created_at FROM ops.csv_imports ORDER BY created_at DESC"
        )
    ]


def detail(db, iid):
    found = db.query("SELECT kind, status, workspace_path FROM ops.csv_imports WHERE import_id=?", [iid])
    if not found or found[0][1] != "IMPORTED":
        raise ValueError("IMPORT_NOT_FOUND: no confirmed workspace")
    kind, _, path = found[0]
    imported = Database(path)
    try:
        rows = imported.query(f'SELECT * FROM stg."csv_{kind}" LIMIT 101')
        names = [*FIELDS[kind], "provenance", "available_at"]
        out = [
            {
                k: v.isoformat() + "Z" if isinstance(v, datetime) else v.isoformat() if isinstance(v, date) else v
                for k, v in zip(names, row, strict=True)
            }
            for row in rows[:100]
        ]
        result = {
            "import_id": iid,
            "type": kind,
            "table": f"stg.csv_{kind}",
            "columns": names,
            "records": out,
            "truncated": len(rows) > 100,
            "engine_eligible": False,
            "missing_sources": [
                "store orders/refunds",
                "campaign-to-SKU mapping",
                "historical reports",
                "current external budget state",
            ],
            "note": "Isolated uploaded facts; source completeness blocks execution. Provenance is a source label, not authenticity verification.",
        }
        if kind == "cvr":
            from adapt.predict.cvr_bayes import fit_rows

            all_rows = [
                json.loads(payload) | {"available_at": at}
                for payload, at in imported.query(
                    "SELECT payload, available_at FROM uploads.records ORDER BY business_key"
                )
            ]
            result["cvr"] = fit_rows(all_rows, datetime.now(UTC).replace(tzinfo=None))
        return result
    finally:
        imported.close()
