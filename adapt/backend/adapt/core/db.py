"""Workspace database access with the single-writer invariant (spec §2).

One process owns the workspace DuckDB file. Inside that process every mutation goes through
`Database.write`, which serializes writers with a lock and wraps the work in a transaction.
Readers get their own cursor per call, so connections are never shared across threads.

A second process that tries to open the same file read-write fails at open time: DuckDB holds an
OS-level lock on the file. That is how a multi-worker deployment is rejected at boot.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

import duckdb

T = TypeVar("T")

# Order-independent SUM for money and other doubles feeding a decision. DuckDB sums DOUBLE in whatever order its
# threads finish, so the last bits of a large sum can differ between two runs on the same data (observed: attribution
# weights differing at 1e-16 moved the optimizer by Rs 200). A DECIMAL(38, 6) sum is exact integer arithmetic, so the
# result is the same every time; it is returned as DOUBLE.
DSUM_MACRO = "CREATE OR REPLACE MACRO dsum(x) AS CAST(sum(CAST(x AS DECIMAL(38, 6))) AS DOUBLE)"


class _Gate:
    """Shared/exclusive gate: queries and writes run shared; swapping the file underneath (workspace reset, baseline
    snapshot) runs exclusive, after in-flight work finishes and before any new work starts (writer preference)."""

    def __init__(self):
        self._cond = threading.Condition()
        self._active = 0
        self._swapping = False
        self._waiting = 0
        self._held = threading.local()  # re-entrant per thread: a nested query never waits behind a pending swap

    @contextmanager
    def shared(self):
        depth = getattr(self._held, "depth", 0)
        if depth == 0:
            with self._cond:
                while self._swapping or self._waiting:
                    self._cond.wait()
                self._active += 1
        self._held.depth = depth + 1
        try:
            yield
        finally:
            self._held.depth = depth
            if depth == 0:
                with self._cond:
                    self._active -= 1
                    self._cond.notify_all()

    @contextmanager
    def exclusive(self):
        with self._cond:
            self._waiting += 1
            while self._swapping or self._active:
                self._cond.wait()
            self._waiting -= 1
            self._swapping = True
        try:
            yield
        finally:
            with self._cond:
                self._swapping = False
                self._cond.notify_all()


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._gate = _Gate()
        self._connect()

    def _connect(self) -> None:
        self._con = duckdb.connect(self.path)
        self._con.execute(DSUM_MACRO)  # a catalog macro, so every cursor (a separate connection) sees it

    def swap_file(self, fn: Callable[[], None]) -> None:
        """Close the file, run fn (copy a baseline over it, or copy it out), reopen: the same Database object stays
        valid for every holder, and concurrent requests wait instead of meeting a closed connection."""
        with self._gate.exclusive():
            self._con.close()
            try:
                fn()
            finally:
                self._connect()

    def write(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        """Run `fn` inside one serialized write transaction; roll back on any error."""
        with self._gate.shared(), self._write_lock:
            cur = self._con.cursor()
            try:
                cur.execute("BEGIN TRANSACTION")
                result = fn(cur)
                cur.execute("COMMIT")
                return result
            except BaseException:
                cur.execute("ROLLBACK")
                raise
            finally:
                cur.close()

    @contextmanager
    def read(self):
        """Yield a dedicated cursor for read-only use on the calling thread."""
        with self._gate.shared():
            cur = self._con.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def query(self, sql: str, params: list | tuple | None = None) -> list[tuple]:
        with self.read() as cur:
            return cur.execute(sql, params or []).fetchall()

    def close(self) -> None:
        self._con.close()
