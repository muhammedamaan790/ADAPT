"""Replay a decision in its ARCHIVED code + lock environment (spec §22.9, T68). See adapt/decide/archived_replay.py.

  uv run python scripts/replay.py <decision_id> [--workspace data/workspaces/demo.duckdb]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from adapt.config.settings import get_settings  # noqa: E402
from adapt.decide.archived_replay import replay_archived  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("decision_id")
    ap.add_argument("--workspace", default=str(get_settings().workspace_db_path))
    args = ap.parse_args()
    out = replay_archived(args.workspace, args.decision_id)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("status") == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
