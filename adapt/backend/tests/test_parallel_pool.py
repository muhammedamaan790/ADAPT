"""adapt.core.parallel: serial when off, the configured pool size, and a broken pool never loses a result."""

from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

from adapt.core import parallel


def test_off_runs_in_process(monkeypatch):
    monkeypatch.setenv("ADAPT_WORKERS", "0")
    assert parallel.pool() is None and parallel.submit(pow, 2, 3) is None
    assert parallel.pmap(pow, [(2, 3), (3, 2)]) == [8, 9]
    assert parallel.result(None, pow, 2, 5) == 32


def test_pool_size_from_the_environment(monkeypatch):
    monkeypatch.setenv("ADAPT_WORKERS", "3")
    assert parallel.workers() == 3
    monkeypatch.delenv("ADAPT_WORKERS")
    assert 1 <= parallel.workers() <= 8


def test_a_broken_pool_falls_back_to_the_same_value():
    dead = Future()
    dead.set_exception(BrokenProcessPool("a worker died"))
    assert parallel.result(dead, pow, 2, 10) == 1024
    done = Future()
    done.set_result(7)
    assert parallel.result(done, pow, 2, 10) == 7
