"""Per-channel autonomy mode (spec §9.2): OBSERVE (recommend only, shadow-recorded) | APPROVE (a human approves, then
the system executes; the default) | AUTONOMOUS (executes only fully passing decisions; the rest drop to Approve).

The mode map is an EXACT staleness input (decide/fingerprint.py): changing any channel's mode expires every pending
decision. Enabling AUTONOMOUS is gated by the readiness checklist in policy/autonomy.py (T38).
"""

from __future__ import annotations

from datetime import datetime

MODES = ("OBSERVE", "APPROVE", "AUTONOMOUS")
CHANNELS = ("meta", "google", "tiktok", "amazon")

DDL = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.channel_modes (
    channel VARCHAR PRIMARY KEY, mode VARCHAR NOT NULL, set_at TIMESTAMP, actor VARCHAR, reason VARCHAR
);
"""


def channel_modes(db) -> dict[str, str]:
    if not db.query("SELECT 1 FROM information_schema.tables WHERE table_schema = 'ops' "
                    "AND table_name = 'channel_modes'"):
        return dict.fromkeys(CHANNELS, "APPROVE")
    stored = dict(db.query("SELECT channel, mode FROM ops.channel_modes"))
    return {c: stored.get(c, "APPROVE") for c in CHANNELS}


def write_mode(db, channel: str, mode: str, actor: str, at: datetime, reason: str) -> None:
    if channel not in CHANNELS or mode not in MODES:
        raise ValueError(f"unknown channel/mode {channel}/{mode}")

    def work(cur):
        cur.execute(DDL)
        cur.execute("INSERT OR REPLACE INTO ops.channel_modes VALUES (?, ?, ?, ?, ?)",
                    [channel, mode, at, actor, reason])

    db.write(work)
