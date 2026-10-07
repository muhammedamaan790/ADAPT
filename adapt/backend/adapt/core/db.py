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


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(self.path)
        self._write_lock = threading.Lock()

    def write(self, fn: Callable[[duckdb.DuckDBPyConnection], T]) -> T:
        """Run `fn` inside one serialized write transaction; roll back on any error."""
        with self._write_lock:
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
