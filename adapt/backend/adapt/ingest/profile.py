"""M0 profiling gate (spec §1, "Day-1 risk task").

Profiles the raw Kaggle CSVs and writes `profile_report.json`. The gate FAILS when the backbone
(TheLook) lacks a required column or relationship with no documented fallback. Calibration sets
(Global Ads, KAG) fall back to `benchmarks.yaml` defaults instead of failing.

Usage:  uv run python -m adapt.ingest.profile --raw-dir data/raw --out data/profile_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import duckdb

from adapt.ingest.sources import GLOBAL_ADS_FIELDS, KAG_FACEBOOK, THELOOK, DatasetSpec

MAX_ORPHAN_RATE = 0.01
MIN_HISTORY_DAYS = 365
MIN_PURCHASE_USER_LINK = 0.99


def _csv(con: duckdb.DuckDBPyConnection, path: Path) -> str:
    # read_csv_auto on a quoted path; all_varchar avoids type-sniffing surprises during profiling
    return f"read_csv_auto('{path.as_posix()}', all_varchar=true, header=true)"


def _columns(con: duckdb.DuckDBPyConnection, path: Path) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {_csv(con, path)}").fetchall()]


def _profile_tables(con, ds_dir: Path, spec: DatasetSpec) -> tuple[dict, list[str], list[str]]:
    tables, failures, fallbacks = {}, [], []
    for t in spec.tables:
        path = ds_dir / t.file
        if not path.exists():
            failures.append(f"{spec.key}: missing file {t.file}")
            tables[t.file] = {"present": False}
            continue
        cols = _columns(con, path)
        rows = con.execute(f"SELECT count(*) FROM {_csv(con, path)}").fetchone()[0]
        missing = [c for c in t.required if c not in cols]
        missing_optional = {c: fb for c, fb in t.optional.items() if c not in cols}
        for c in missing:
            failures.append(f"{spec.key}: {t.file} missing required column '{c}'")
        for c, fb in missing_optional.items():
            fallbacks.append(f"{spec.key}: {t.file} lacks '{c}' -> fallback: {fb}")
        tables[t.file] = {
            "present": True,
            "rows": rows,
            "columns": cols,
            "missing_required": missing,
            "missing_optional": missing_optional,
        }
    return tables, failures, fallbacks


def _thelook_relationships(con, d: Path, tables: dict) -> tuple[dict, list[str]]:
    checks, failures = {}, []
    if all(tables.get(f, {}).get("present") for f in ("products.csv", "inventory_items.csv")):
        total, orphans = con.execute(
            f"""SELECT count(*), count(*) FILTER (WHERE p.id IS NULL)
                FROM {_csv(con, d / 'inventory_items.csv')} i
                LEFT JOIN {_csv(con, d / 'products.csv')} p ON i.product_id = p.id"""
        ).fetchone()
        rate = orphans / total if total else 1.0
        checks["inventory_product_orphan_rate"] = rate
        if rate > MAX_ORPHAN_RATE:
            failures.append(f"thelook: inventory_items.product_id orphan rate {rate:.3%} > {MAX_ORPHAN_RATE:.0%}")

    if tables.get("events.csv", {}).get("present"):
        total, linked = con.execute(
            f"""SELECT count(*), count(*) FILTER (WHERE user_id IS NOT NULL AND user_id <> '')
                FROM {_csv(con, d / 'events.csv')} WHERE event_type = 'purchase'"""
        ).fetchone()
        share = linked / total if total else 0.0
        checks["purchase_events"] = total
        checks["purchase_event_user_link_share"] = share
        if total == 0 or share < MIN_PURCHASE_USER_LINK:
            failures.append(f"thelook: purchase events linked to user_id = {share:.1%} (need >= 99%)")

    if tables.get("orders.csv", {}).get("present"):
        lo, hi = con.execute(
            f"SELECT min(TRY_CAST(created_at AS TIMESTAMP)), max(TRY_CAST(created_at AS TIMESTAMP)) "
            f"FROM {_csv(con, d / 'orders.csv')}"
        ).fetchone()
        span = (hi - lo).days if lo and hi else 0
        checks["orders_date_range"] = [str(lo), str(hi)]
        checks["orders_history_days"] = span
        if span < MIN_HISTORY_DAYS:
            failures.append(f"thelook: order history spans {span} days (< {MIN_HISTORY_DAYS})")
    return checks, failures


def _profile_global_ads(con, d: Path) -> tuple[dict, list[str]]:
    csvs = sorted(d.glob("*.csv")) if d.exists() else []
    if not csvs:
        return {"present": False}, ["global_ads: dataset absent -> fallback: benchmarks.yaml defaults"]
    path = csvs[0]
    cols = _columns(con, path)
    lower = {c.lower(): c for c in cols}
    mapping = {
        field: next((lower[s] for s in syns if s in lower), None) for field, syns in GLOBAL_ADS_FIELDS.items()
    }
    needed = ("platform", "impressions", "clicks", "spend")
    fallbacks = [
        f"global_ads: no column for '{f}' -> fallback: benchmarks.yaml defaults" for f in needed if not mapping[f]
    ]
    rows = con.execute(f"SELECT count(*) FROM {_csv(con, path)}").fetchone()[0]
    return {"present": True, "file": path.name, "rows": rows, "columns": cols, "field_mapping": mapping}, fallbacks


def profile(raw_dir: Path) -> dict:
    con = duckdb.connect()
    report: dict = {"raw_dir": str(raw_dir), "datasets": {}, "failures": [], "fallbacks": []}

    tl_dir = raw_dir / THELOOK.key
    tables, failures, fallbacks = _profile_tables(con, tl_dir, THELOOK)
    checks, rel_failures = _thelook_relationships(con, tl_dir, tables)
    report["datasets"]["thelook"] = {"label": THELOOK.label, "tables": tables, "checks": checks}
    report["failures"] += failures + rel_failures
    report["fallbacks"] += fallbacks

    ga, ga_fallbacks = _profile_global_ads(con, raw_dir / "global_ads")
    report["datasets"]["global_ads"] = ga
    report["fallbacks"] += ga_fallbacks

    kag_dir = raw_dir / KAG_FACEBOOK.key
    kag_tables, kag_fail, _ = _profile_tables(con, kag_dir, KAG_FACEBOOK)
    report["datasets"]["kag_facebook"] = {"tables": kag_tables}
    # KAG only calibrates Meta audience spread, so its problems degrade to fallbacks, never failures.
    report["fallbacks"] += [f"{m} -> fallback: benchmarks.yaml audience defaults" for m in kag_fail]

    report["gate"] = "PASS" if not report["failures"] else "FAIL"
    con.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    ap.add_argument("--out", type=Path, default=Path("data/profile_report.json"))
    args = ap.parse_args(argv)
    report = profile(args.raw_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"M0 profiling gate: {report['gate']}  ({len(report['failures'])} failures, "
          f"{len(report['fallbacks'])} fallbacks) -> {args.out}")
    for f in report["failures"]:
        print("  FAIL:", f)
    for f in report["fallbacks"]:
        print("  fallback:", f)
    return 0 if report["gate"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
