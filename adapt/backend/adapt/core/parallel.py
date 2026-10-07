"""One shared process pool for the pipeline's independent, pure computations (detector entities, synthetic-control
fits, the optimizer's sensitivity scenarios). Spec §2 target: one simulated day <= 20 s on an 8-core machine.

Results are identical to the serial path: every task is a pure function of its arguments, results come back in
submission order, and each worker runs single-threaded numerics (the optimizer's determinism rule, spec §22.3).
ADAPT_WORKERS sets the pool size (default: cores - 1, at most 8); 0 or 1 runs everything in-process, which the
test suite uses by default (tests/conftest) and which is the automatic fallback if the pool cannot start.
"""

from __future__ import annotations

import atexit
import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

_pool: ProcessPoolExecutor | None = None
_lock = threading.Lock()
_SINGLE_THREAD = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}


def workers() -> int:
    raw = os.environ.get("ADAPT_WORKERS")
    if raw is not None:
        return max(int(raw), 0)
    return min(max((os.cpu_count() or 2) - 1, 1), 8)


def _init_worker() -> None:
    os.environ.update(_SINGLE_THREAD)


def pool() -> ProcessPoolExecutor | None:
    """The shared pool, started on first use; None when parallelism is off."""
    global _pool
    if workers() <= 1:
        return None
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=workers(), initializer=_init_worker)
            atexit.register(shutdown)
        return _pool


def shutdown() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


def submit(fn: Callable, *args) -> Future | None:
    """Start fn(*args) in the pool; None when parallelism is off (the caller then runs it in-process)."""
    p = pool()
    return p.submit(fn, *args) if p is not None else None


def pmap(fn: Callable, items: Iterable[tuple]) -> list:
    """[fn(*args) for args in items], in order, spread over the pool. A broken pool falls back to the serial path."""
    items = list(items)
    p = pool() if len(items) > 1 else None
    if p is None:
        return [fn(*args) for args in items]
    try:
        futures = [p.submit(fn, *args) for args in items]
        return [f.result() for f in futures]
    except BrokenProcessPool:
        shutdown()
        return [fn(*args) for args in items]


def result(future: Future | None, fn: Callable, *args):
    """The result of a submit()ted task, or fn(*args) in-process when nothing was submitted or the pool broke
    (a worker died): the same value either way, so a pool failure costs time, never correctness."""
    if future is None:
        return fn(*args)
    try:
        return future.result()
    except BrokenProcessPool:
        shutdown()
        return fn(*args)
