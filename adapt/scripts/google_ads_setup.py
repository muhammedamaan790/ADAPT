"""Create the Google Ads TEST-account mirror of the simulated Google budgets and record the id map (spec §9.4).

  uv run python scripts/google_ads_setup.py [--limit N] [--dry-run]

Reads the simulated Google budgets from the workspace (core.budgets / core.campaigns, i.e. after the first sync),
creates one INR campaign budget per simulated budget in the test client account (shared budgets stay shared) plus a
PAUSED Search campaign attached to it (test accounts serve no ads), and writes ops.live_entity_map
(sim budget id -> live budget id) for the live adapter. Credentials come from the backend .env (OAuth desktop client +
refresh token, test manager = GOOGLE_ADS_LOGIN_CUSTOMER_ID, test client = GOOGLE_ADS_CUSTOMER_ID). The health check
must pass first: the script refuses a non-test or non-INR account. --dry-run prints the operations and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from adapt.config.settings import get_settings  # noqa: E402
from adapt.core.db import Database  # noqa: E402
from adapt.execute.google_ads_live import MAP_DDL, GoogleAdsCredentials, GoogleAdsLiveAdapter  # noqa: E402


def load_env(path: Path) -> dict:
    env = dict(os.environ)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


def operations(budgets: list[tuple], cid: str) -> list[tuple[dict, dict]]:
    """(budget create op, campaign create op) per simulated budget; the campaign references the budget by a temporary
    resource name resolved after the budget mutate."""
    out = []
    for bid, amount, shared, name in budgets:
        b = {"create": {"name": f"ADAPT mirror {bid} ({name})"[:250], "amountMicros": str(int(round(amount * 100)) *
                                                                                     10_000),
                        "deliveryMethod": "STANDARD", "explicitlyShared": bool(shared)}}
        c = {"create": {"name": f"ADAPT mirror campaign {bid}"[:250], "status": "PAUSED",
                        "advertisingChannelType": "SEARCH", "manualCpc": {},
                        "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                            "targetContentNetwork": False},
                        "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
                        "campaignBudget": None}}
        out.append((b, c))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=None, help="mirror only the first N Google budgets")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    settings = get_settings()
    db = Database(settings.workspace_db_path)
    try:
        budgets = db.query("""SELECT b.budget_id, b.current_amount_inr, b.is_shared, any_value(c.name)
                              FROM core.budgets b JOIN core.campaigns c USING (budget_id)
                              WHERE b.platform = 'google' GROUP BY 1, 2, 3 ORDER BY 1""")
        if args.limit:
            budgets = budgets[:args.limit]
        creds = GoogleAdsCredentials.from_env(load_env(ROOT / ".env"))
        ops = operations(budgets, creds.customer_id or "<customer>")
        if args.dry_run:
            print(json.dumps([{"sim_budget_id": b[0], "budget_op": o[0], "campaign_op": o[1]}
                              for b, o in zip(budgets, ops, strict=True)], indent=2))
            return 0
        adapter = GoogleAdsLiveAdapter(creds, {})
        health = adapter.health()
        if not health["ok"]:
            print(f"health check failed: {health['reason']}")
            return 1
        rows = []
        for (bid, *_), (bop, cop) in zip(budgets, ops, strict=True):
            r = adapter.http.post(adapter._url("campaignBudgets:mutate"), headers=adapter._headers(),
                                  json={"operations": [bop]})
            r.raise_for_status()
            resource = r.json()["results"][0]["resourceName"]
            cop["create"]["campaignBudget"] = resource
            r = adapter.http.post(adapter._url("campaigns:mutate"), headers=adapter._headers(),
                                  json={"operations": [cop]})
            r.raise_for_status()
            rows.append(("google", "budget", bid, resource.rsplit("/", 1)[1], creds.customer_id, datetime.now()))
            print(f"{bid} -> {resource}")

        def work(cur):
            cur.execute(MAP_DDL)
            cur.executemany("INSERT OR REPLACE INTO ops.live_entity_map VALUES (?, ?, ?, ?, ?, ?)", rows)

        db.write(work)
        print(f"mapped {len(rows)} budgets; set ADAPT_GOOGLE_EXECUTION_MODE=live to execute against the test account")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
