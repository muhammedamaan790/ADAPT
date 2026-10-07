"""Database.swap_file (workspace reset): concurrent queries wait for the swap instead of meeting a closed
connection (the live journey saw 500s from /overview polled during a 1.2 GB baseline copy), and see the new file."""

import shutil
import threading
import time

from adapt.core.db import Database


def test_queries_during_a_swap_wait_and_then_read_the_restored_file(tmp_path):
    path, base = tmp_path / "ws.duckdb", tmp_path / "ws.baseline.duckdb"
    db = Database(path)
    db.write(lambda c: c.execute("CREATE TABLE t AS SELECT 1 AS v"))
    db.swap_file(lambda: shutil.copyfile(path, base))           # snapshot: baseline holds v = 1
    db.write(lambda c: c.execute("UPDATE t SET v = 2"))
    seen, errors, stop = [], [], threading.Event()

    def poll():
        while not stop.is_set():
            try:
                seen.append(db.query("SELECT v FROM t")[0][0])
            except Exception as exc:  # noqa: BLE001 - any failure is the bug under test
                errors.append(repr(exc))

    readers = [threading.Thread(target=poll) for _ in range(4)]
    for t in readers:
        t.start()
    time.sleep(0.05)
    db.swap_file(lambda: (time.sleep(0.2), shutil.copyfile(base, path)))  # a slow copy, like the real baseline
    time.sleep(0.05)
    stop.set()
    for t in readers:
        t.join()
    assert not errors
    assert seen[-1] == 1 and 2 in seen
    assert db.query("SELECT dsum(v) FROM t")[0][0] == 1.0       # the macro is back after reopening
    db.close()


def test_a_nested_query_never_waits_behind_a_pending_swap(tmp_path):
    db = Database(tmp_path / "ws.duckdb")
    started, done = threading.Event(), threading.Event()

    def nested():
        with db.read() as cur:
            started.set()
            time.sleep(0.2)                                     # a swap starts waiting meanwhile
            cur.execute("SELECT 1")
            assert db.query("SELECT 2")[0][0] == 2              # re-entrant: no deadlock
        done.set()

    t = threading.Thread(target=nested)
    t.start()
    started.wait()
    db.swap_file(lambda: None)
    t.join(5)
    assert done.is_set()
    db.close()
