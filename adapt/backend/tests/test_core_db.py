import threading

import duckdb
import pytest

from adapt.core.db import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "ws.duckdb")
    d.write(lambda c: c.execute("CREATE TABLE t (id INTEGER, v INTEGER)"))
    yield d
    d.close()


def test_write_commits(db):
    db.write(lambda c: c.execute("INSERT INTO t VALUES (1, 10)"))
    assert db.query("SELECT v FROM t WHERE id = 1") == [(10,)]


def test_write_rolls_back_on_error(db):
    def bad(c):
        c.execute("INSERT INTO t VALUES (2, 20)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        db.write(bad)
    assert db.query("SELECT count(*) FROM t WHERE id = 2") == [(0,)]


def test_concurrent_writes_are_serialized(db):
    db.write(lambda c: c.execute("INSERT INTO t VALUES (0, 0)"))

    def increment(c):
        (v,) = c.execute("SELECT v FROM t WHERE id = 0").fetchone()
        c.execute("UPDATE t SET v = ? WHERE id = 0", [v + 1])

    threads = [threading.Thread(target=lambda: db.write(increment)) for _ in range(20)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    # Without the writer lock, read-modify-write races would lose increments.
    assert db.query("SELECT v FROM t WHERE id = 0") == [(20,)]


def test_second_process_cannot_open_same_file_for_writing(tmp_path):
    """The single-writer invariant across processes is enforced by DuckDB's file lock."""
    import subprocess
    import sys

    path = tmp_path / "locked.duckdb"
    d = Database(path)
    try:
        code = f"import duckdb; duckdb.connect(r'{path}')"
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert proc.returncode != 0
        assert "lock" in proc.stderr.lower()
    finally:
        d.close()


def test_read_cursor_is_independent(db):
    with db.read() as cur:
        assert isinstance(cur, duckdb.DuckDBPyConnection)
        assert cur.execute("SELECT 42").fetchone() == (42,)
