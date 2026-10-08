"""Demo driver: inject world scenarios, advance the clock and upload CSVs through the running API.

Replaces the removed Scenario Lab screen. Signs in like the web app (session cookie + X-CSRF-Token), so every change is
recorded under the signed-in user. The password comes from ADAPT_DEMO_PASSWORD, else from
data/auth/initial_credentials.txt (never printed).

    uv run python scripts/demo_inject.py list
    uv run python scripts/demo_inject.py scenario DEMO_01
    uv run python scripts/demo_inject.py advance 3
    uv run python scripts/demo_inject.py upload ads demo_data/ads_platform_export_headers.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def password(user: str) -> str:
    if os.environ.get("ADAPT_DEMO_PASSWORD"):
        return os.environ["ADAPT_DEMO_PASSWORD"]
    creds = ROOT / "data" / "auth" / "initial_credentials.txt"
    if creds.exists():
        for line in creds.read_text().splitlines():
            if line.startswith(f"{user} ("):
                return line.split(":", 1)[1].strip()
    sys.exit(f"no password for {user}: set ADAPT_DEMO_PASSWORD (or keep data/auth/initial_credentials.txt)")


def session(base: str, user: str) -> httpx.Client:
    client = httpx.Client(base_url=base, timeout=120)
    resp = client.post("/auth/login", json={"user_id": user, "password": password(user)})
    if resp.status_code != 200:
        sys.exit(f"sign-in as {user} failed: HTTP {resp.status_code} {resp.text[:200]}")
    client.headers["X-CSRF-Token"] = resp.json()["csrf_token"]
    return client


def check(resp: httpx.Response) -> dict:
    if resp.status_code >= 400:
        sys.exit(f"HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base", default=os.environ.get("ADAPT_API_URL", "http://127.0.0.1:8000/api/v1"))
    p.add_argument("--user", default="maria", help="a manager or admin (viewers cannot change the world)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="the injectable scenarios")
    sc = sub.add_parser("scenario", help="inject one scenario into the world")
    sc.add_argument("key")
    adv = sub.add_parser("advance", help="advance the world N days (1-14); the pipeline then runs each day")
    adv.add_argument("days", type=int)
    up = sub.add_parser("upload", help="stage and confirm a CSV (ads | inventory | margins)")
    up.add_argument("type", choices=["ads", "inventory", "margins"])
    up.add_argument("file", type=Path)
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # scenario text uses − and ₹ (Windows consoles)

    client = session(a.base, a.user)
    if a.cmd == "list":
        for s in check(client.get("/sim/scenarios"))["items"]:
            print(f"{s['key']:8} {s['status']:10} {s['title']}: {s['description']}")
    elif a.cmd == "scenario":
        check(client.post(f"/sim/scenario/{a.key}"))
        print(f"{a.key} injected. Advance the clock to let it play out: demo_inject.py advance 3")
    elif a.cmd == "advance":
        check(client.post("/sim/advance", params={"days": a.days}))
        print(f"advancing {a.days} day(s); the pipeline runs in the background (watch the Command Center)")
    else:
        with open(a.file, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            sys.exit(f"{a.file} has no rows")
        suggested = check(client.post("/ingest/mapping/suggest", json={"type": a.type, "headers": list(rows[0])}))
        mapping = {k: v["header"] for k, v in suggested["mapping"].items() if v.get("header")}
        print("column mapping: " + ", ".join(f"{k} <- {h}" for k, h in mapping.items()))
        records = [{k: r[h] for k, h in mapping.items()} for r in rows]
        staged = check(client.post("/ingest/upload", json={"type": a.type, "records": records,
                                                           "source_currency": "INR",
                                                           "source_timezone": "Asia/Kolkata"}))
        done = check(client.post("/ingest/mapping/confirm", json={"import_id": staged["import_id"],
                                                                  "mapping": mapping}))
        print(f"{done['status']}: {done['row_count']} rows. {done['message']}")


if __name__ == "__main__":
    main()
