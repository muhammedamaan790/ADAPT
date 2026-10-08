"""Live smoke test against the Google Ads TEST account (spec §15 "Live smoke", §21 evidence): mutate -> GAQL verify ->
restore -> verify, through the same live adapter the saga uses. Writes a redacted ledger excerpt to
evidence/google_live_smoke.json (no tokens, no secrets: the adapter's redaction filter applies).

  uv run python scripts/google_live_smoke.py [--budget <sim budget id>] [--delta 100]

Needs the backend .env (OAuth client + refresh token + test manager / client ids) and a mapped budget
(scripts/google_ads_setup.py). Refuses a non-test or non-INR account (the health check). Test accounts serve no ads.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from adapt.config.settings import get_settings  # noqa: E402
from adapt.core.db import Database  # noqa: E402
from adapt.execute.adapters import redact  # noqa: E402
from adapt.execute.google_ads_live import (  # noqa: E402
    GoogleAdsCredentials,
    GoogleAdsLiveAdapter,
    load_env,
    load_mapping,
)

OUT = ROOT / "evidence" / "google_live_smoke.json"


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def verify(ad: GoogleAdsLiveAdapter, budget: str, want: float, polls: int = 5) -> dict:
    """Separate GAQL read-backs until the amount matches (eventual consistency), never the request payload."""
    seen = None
    for k in range(polls):
        seen = ad.read(budget)["amount_inr"]
        if abs(seen - want) <= ad.tolerance():
            return {"verified": True, "observed_inr": seen, "polls": k + 1}
        time.sleep(1.0 * (k + 1))
    return {"verified": False, "observed_inr": seen, "polls": polls}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--budget", help="simulated budget id (default: the first mapped one)")
    ap.add_argument("--delta", type=float, default=100.0, help="INR added for the test mutation")
    args = ap.parse_args()
    db = Database(get_settings().workspace_db_path)
    try:
        mapping = load_mapping(db)
    finally:
        db.close()
    if not mapping:
        print("no mapped budget: run scripts/google_ads_setup.py first")
        return 1
    budget = args.budget or sorted(mapping)[0]
    ad = GoogleAdsLiveAdapter(GoogleAdsCredentials.from_env(load_env(ROOT / ".env")), mapping)
    health = ad.health(force=True)
    log = {"started_at": now(), "account": ad.creds.customer_id, "api_version": ad.version,
           "health": {k: health[k] for k in ("ok", "checks", "reason")}, "budget": budget,
           "live_budget": mapping[budget], "steps": []}
    if not health["ok"]:
        print(f"health check failed: {health['reason']}")
        return 1
    before = ad.read(budget)["amount_inr"]
    target = round(before + args.delta, 2)
    log["steps"].append({"at": now(), "step": "PRE_READ", "observed_inr": before})
    res = ad.set_budget(budget, target, request_id=f"smoke-set-{int(time.time())}")
    log["steps"].append({"at": now(), "step": "SET", "desired_inr": target, "ok": res.ok, "http": res.http_status,
                         "request": redact(res.request), "response": redact(res.response)})
    v1 = verify(ad, budget, target)
    log["steps"].append({"at": now(), "step": "VERIFY_SET", **v1})
    res2 = ad.set_budget(budget, before, request_id=f"smoke-restore-{int(time.time())}")
    log["steps"].append({"at": now(), "step": "RESTORE", "desired_inr": before, "ok": res2.ok,
                         "http": res2.http_status, "request": redact(res2.request), "response": redact(res2.response)})
    v2 = verify(ad, budget, before)
    log["steps"].append({"at": now(), "step": "VERIFY_RESTORE", **v2})
    log["passed"] = bool(res.ok and v1["verified"] and res2.ok and v2["verified"])
    log["finished_at"] = now()
    text = json.dumps(log, indent=2, default=str)
    lowered = text.lower()
    if any(s in lowered for s in ("refresh_token", "client_secret", "access_token", "bearer ")):
        print("refusing to write evidence: a secret-like field survived redaction")
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(json.dumps({"passed": log["passed"], "budget": budget, "before": before, "set": target,
                      "verify_set": v1, "verify_restore": v2}, indent=2))
    return 0 if log["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
