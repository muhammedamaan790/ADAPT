"""World runtime state: sim_state.duckdb under one world-state lock (spec §2 runtime invariants).

Every state-changing request runs as one transaction under the lock and gets a durable monotonic sequence number
in world_log. Determinism means replaying the recorded committed order: replay applies the logged operations
to a fresh store and must reproduce the semantic state hash (table contents, wall-clock fields excluded; T55).
sim_truth.duckdb is a different file, written once at seeding, and is never touched here.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

# Columns that hold wall-clock time; excluded from the semantic state hash.
WALL_CLOCK_COLUMNS = frozenset({"committed_at"})

SCHEMA = """
CREATE TABLE IF NOT EXISTS world_log (
    seq BIGINT PRIMARY KEY,
    request_id VARCHAR NOT NULL UNIQUE,
    actor_id VARCHAR NOT NULL,
    operation VARCHAR NOT NULL,
    request_payload JSON NOT NULL,
    request_payload_hash VARCHAR NOT NULL,
    result JSON NOT NULL,
    result_hash VARCHAR NOT NULL,
    committed_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS world_clock (
    id INTEGER PRIMARY KEY,
    seed BIGINT NOT NULL,
    day INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS budgets_state (
    platform VARCHAR NOT NULL,
    budget_id VARCHAR NOT NULL,
    amount DOUBLE NOT NULL,
    status VARCHAR NOT NULL,
    PRIMARY KEY (platform, budget_id)
);
CREATE TABLE IF NOT EXISTS faults_state (
    platform VARCHAR NOT NULL,
    fault VARCHAR NOT NULL,
    remaining INTEGER NOT NULL,
    PRIMARY KEY (platform)
);
"""

PLATFORMS = frozenset({"google", "meta", "tiktok", "amazon"})
# Faults a mock platform can be told to produce on its next N mutations (spec §9.4: 429/400/503/timeout-after-success).
FAULTS = frozenset({"rate_limit", "unavailable", "bad_request", "timeout_after_success"})

@dataclass(frozen=True)
class WorldContext:
    """What operations may use besides the state cursor: the (immutable) truth and the day simulator.

    `simulate_day(cur, seed, day)` is None for a bare store (control-plane tests); then advance only moves the clock.
    """

    truth: Any = None
    simulate_day: Callable[[duckdb.DuckDBPyConnection, int, int], dict[str, Any]] | None = None


Operation = Callable[[duckdb.DuckDBPyConnection, dict[str, Any], WorldContext], dict[str, Any]]


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class WorldStateConflict(RuntimeError):
    """A state-changing request that is illegal in the current world state (maps to HTTP 409)."""


class UnknownEntity(LookupError):
    """The request names an entity the world does not have (maps to the platform's not-found error)."""


# ---- operations: pure functions of (state, payload); the only way state changes ----------------------
def op_reset(cur: duckdb.DuckDBPyConnection, payload: dict[str, Any], ctx: WorldContext) -> dict[str, Any]:
    """Clears every state table (the log is kept) and sets the clock. start_day < 0 begins a history run."""
    seed = int(payload["seed"])
    start_day = int(payload.get("start_day", 0))
    tables = [r[0] for r in cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' AND table_name <> 'world_log'"
    ).fetchall()]
    for table in tables:
        cur.execute(f'DELETE FROM "{table}"')
    cur.execute("INSERT INTO world_clock VALUES (1, ?, ?)", [seed, start_day])
    return {"seed": seed, "day": start_day}


def op_advance(cur: duckdb.DuckDBPyConnection, payload: dict[str, Any], ctx: WorldContext) -> dict[str, Any]:
    """Simulates days [day, day + n) and moves the clock to day + n (clock day = the next day to simulate)."""
    days = int(payload["days"])
    if days < 1:
        raise ValueError("days must be >= 1")
    row = cur.execute("SELECT seed, day FROM world_clock WHERE id = 1").fetchone()
    if row is None:
        raise WorldStateConflict("world not seeded: call reset first")
    seed, day = row
    summaries = []
    if ctx.simulate_day is not None:
        for d in range(day, day + days):
            summaries.append(ctx.simulate_day(cur, seed, d))
    cur.execute("UPDATE world_clock SET day = day + ? WHERE id = 1", [days])
    out: dict[str, Any] = {"seed": seed, "day": day + days}
    if summaries:
        out["simulated"] = summaries
    return out


def op_set_budget(cur: duckdb.DuckDBPyConnection, payload: dict[str, Any], ctx: WorldContext) -> dict[str, Any]:
    """Absolute setter (never relative), so a retried request can never double-apply."""
    amount = float(payload["amount"])
    status = str(payload.get("status", "ENABLED"))
    if amount < 0:
        raise ValueError("amount must be >= 0")
    if status not in {"ENABLED", "PAUSED"}:
        raise ValueError(f"unknown status {status}")
    cur.execute(
        "INSERT OR REPLACE INTO budgets_state VALUES (?, ?, ?, ?)",
        [payload["platform"], payload["budget_id"], amount, status],
    )
    return {"platform": payload["platform"], "budget_id": payload["budget_id"], "amount": amount, "status": status}


def op_set_fault(cur: duckdb.DuckDBPyConnection, payload: dict[str, Any], ctx: WorldContext) -> dict[str, Any]:
    platform, fault, count = payload["platform"], payload["fault"], int(payload.get("count", 1))
    if platform not in PLATFORMS or fault not in FAULTS or count < 1:
        raise ValueError(f"invalid fault {platform}/{fault}/{count}")
    cur.execute("INSERT OR REPLACE INTO faults_state VALUES (?, ?, ?)", [platform, fault, count])
    return {"platform": platform, "fault": fault, "remaining": count}


def op_platform_set_budget(
    cur: duckdb.DuckDBPyConnection, payload: dict[str, Any], ctx: WorldContext
) -> dict[str, Any]:
    """A mock-platform budget mutation. Absolute setter on an existing budget; consumes an injected fault if armed.

    The fault consumption is itself committed state, so a fault sequence replays deterministically.
    Returns {"fault": None | name, ...}; the HTTP layer maps a fault to the platform's error response.
    """
    platform, budget_id = payload["platform"], payload["budget_id"]
    row = cur.execute(
        "SELECT amount, status FROM budgets_state WHERE platform = ? AND budget_id = ?", [platform, budget_id]
    ).fetchone()
    if row is None:
        raise UnknownEntity(f"{platform} budget {budget_id} not found")
    fault_row = cur.execute("SELECT fault, remaining FROM faults_state WHERE platform = ?", [platform]).fetchone()
    fault = None
    if fault_row is not None:
        fault = fault_row[0]
        if fault_row[1] <= 1:
            cur.execute("DELETE FROM faults_state WHERE platform = ?", [platform])
        else:
            cur.execute("UPDATE faults_state SET remaining = remaining - 1 WHERE platform = ?", [platform])
    if fault in {"rate_limit", "unavailable", "bad_request"}:
        return {"fault": fault, "applied": False, "amount": row[0], "status": row[1]}
    amount = float(payload.get("amount", row[0]))
    status = str(payload.get("status", row[1]))
    if amount < 0 or status not in {"ENABLED", "PAUSED"}:
        raise ValueError(f"invalid budget mutation amount={amount} status={status}")
    cur.execute(
        "UPDATE budgets_state SET amount = ?, status = ? WHERE platform = ? AND budget_id = ?",
        [amount, status, platform, budget_id],
    )
    return {"fault": fault, "applied": True, "amount": amount, "status": status}


OPERATIONS: dict[str, Operation] = {
    "reset": op_reset,
    "advance": op_advance,
    "set_budget": op_set_budget,  # control plane: creates/sets a budget (seeding, scripted human edits)
    "set_fault": op_set_fault,
    "platform_set_budget": op_platform_set_budget,  # what a mock ad platform's mutate endpoint does
}


@dataclass(frozen=True)
class CommitResult:
    seq: int
    result: dict[str, Any]
    replayed: bool  # True when request_id was already committed and the stored result was returned


class WorldStore:
    def __init__(
        self,
        path: str | Path,
        ctx: WorldContext | None = None,
        extra_schema: str = "",
        extra_ops: dict[str, Operation] | None = None,
    ) -> None:
        self.path = str(path)
        self.ctx = ctx or WorldContext()
        self._ops = {**OPERATIONS, **(extra_ops or {})}
        self._con = duckdb.connect(self.path)
        self._lock = threading.Lock()
        self._con.execute(SCHEMA)
        if extra_schema:
            self._con.execute(extra_schema)

    def commit(self, operation: str, request_id: str, actor_id: str, payload: dict[str, Any]) -> CommitResult:
        if operation not in self._ops:
            raise KeyError(f"unknown operation {operation}")
        payload_json = canonical_json(payload)
        with self._lock:
            cur = self._con.cursor()
            try:
                prior = cur.execute(
                    "SELECT seq, operation, request_payload_hash, result FROM world_log WHERE request_id = ?",
                    [request_id],
                ).fetchone()
                if prior is not None:
                    if prior[1] != operation or prior[2] != sha256_hex(payload_json):
                        raise WorldStateConflict(f"request_id {request_id} reused with a different request")
                    return CommitResult(prior[0], json.loads(prior[3]), replayed=True)
                cur.execute("BEGIN")
                try:
                    result = self._ops[operation](cur, payload, self.ctx)
                    seq = cur.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM world_log").fetchone()[0]
                    result_json = canonical_json(result)
                    cur.execute(
                        "INSERT INTO world_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [seq, request_id, actor_id, operation, payload_json, sha256_hex(payload_json),
                         result_json, sha256_hex(result_json), datetime.now(UTC)],
                    )
                    cur.execute("COMMIT")
                except BaseException:
                    cur.execute("ROLLBACK")
                    raise
                return CommitResult(seq, result, replayed=False)
            finally:
                cur.close()

    def read(self, sql: str, params: list[Any] | None = None) -> list[tuple]:
        """Consistent read: taken under the world-state lock, so it never sees a half-applied request."""
        with self._lock:
            cur = self._con.cursor()
            try:
                return cur.execute(sql, params or []).fetchall()
            finally:
                cur.close()

    def clock(self) -> tuple[int, int] | None:
        rows = self.read("SELECT seed, day FROM world_clock WHERE id = 1")
        return (rows[0][0], rows[0][1]) if rows else None

    def log(self) -> list[dict[str, Any]]:
        rows = self.read("SELECT seq, request_id, actor_id, operation, request_payload FROM world_log ORDER BY seq")
        return [
            {"seq": r[0], "request_id": r[1], "actor_id": r[2], "operation": r[3], "payload": json.loads(r[4])}
            for r in rows
        ]

    def semantic_state_hash(self) -> str:
        """Canonical hash of every table's contents, excluding wall-clock columns. Never DuckDB file bytes."""
        with self._lock:
            cur = self._con.cursor()
            try:
                tables = [r[0] for r in cur.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
                ).fetchall()]
                digest = hashlib.sha256()
                for table in tables:
                    cols = [r[0] for r in cur.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'main' AND table_name = ? ORDER BY column_name",
                        [table],
                    ).fetchall() if r[0] not in WALL_CLOCK_COLUMNS]
                    col_sql = ", ".join(f'"{c}"' for c in cols)
                    rows = cur.execute(f'SELECT {col_sql} FROM "{table}" ORDER BY ALL').fetchall()
                    digest.update(canonical_json({"table": table, "columns": cols, "rows": rows}).encode())
                return digest.hexdigest()
            finally:
                cur.close()

    def snapshot_to(self, target: str | Path) -> None:
        """Write a consistent copy of the state file (used for the post-seeding baseline).

        DuckDB holds the file exclusively on Windows, so the connection is closed (which checkpoints) for the copy
        and reopened, all under the world lock.
        """
        with self._lock:
            self._con.close()
            try:
                shutil.copyfile(self.path, target)
            finally:
                self._con = duckdb.connect(self.path)

    def restore_from(self, baseline: str | Path) -> None:
        """Replace the whole state (log included) with a snapshot, under the world lock."""
        with self._lock:
            self._con.close()
            shutil.copyfile(baseline, self.path)
            wal = Path(self.path + ".wal")
            if wal.exists():
                wal.unlink()
            self._con = duckdb.connect(self.path)

    def close(self) -> None:
        self._con.close()


def replay(log: list[dict[str, Any]], target: WorldStore) -> None:
    """Re-apply a committed log, in sequence order, to a fresh store."""
    for entry in sorted(log, key=lambda e: e["seq"]):
        target.commit(entry["operation"], entry["request_id"], entry["actor_id"], entry["payload"])
