"""World-state lock, sequence log, idempotent requests and deterministic replay (T55)."""

import threading

import pytest

from world.state import WorldStateConflict, WorldStore, replay


@pytest.fixture
def store(tmp_path):
    s = WorldStore(tmp_path / "sim_state.duckdb")
    s.commit("reset", "r0", "test", {"seed": 42})
    yield s
    s.close()


def test_reset_and_advance_move_the_clock(store):
    assert store.clock() == (42, 0)
    store.commit("advance", "r1", "test", {"days": 3})
    assert store.clock() == (42, 3)


def test_advance_before_seeding_is_a_conflict(tmp_path):
    s = WorldStore(tmp_path / "empty.duckdb")
    with pytest.raises(WorldStateConflict):
        s.commit("advance", "r1", "test", {"days": 1})
    assert s.log() == []  # failed requests leave no log entry
    s.close()


def test_failed_operation_rolls_back_and_consumes_no_sequence(store):
    with pytest.raises(ValueError):
        store.commit("set_budget", "bad", "test", {"platform": "google", "budget_id": "B1", "amount": -5})
    store.commit("set_budget", "ok", "test", {"platform": "google", "budget_id": "B1", "amount": 500})
    assert [e["seq"] for e in store.log()] == [1, 2]
    assert store.read("SELECT amount FROM budgets_state") == [(500.0,)]


def test_duplicate_request_id_applies_once(store):
    first = store.commit("advance", "dup", "test", {"days": 1})
    second = store.commit("advance", "dup", "test", {"days": 1})
    assert (first.replayed, second.replayed) == (False, True)
    assert first.result == second.result and first.seq == second.seq
    assert store.clock() == (42, 1)


def test_request_id_reused_with_different_payload_is_rejected(store):
    store.commit("advance", "x", "test", {"days": 1})
    with pytest.raises(WorldStateConflict):
        store.commit("advance", "x", "test", {"days": 2})


def test_concurrent_requests_serialize_and_replay_to_the_same_semantic_hash(store, tmp_path):
    def worker(t: int) -> None:
        for i in range(10):
            if i % 3 == 0:
                store.commit("advance", f"adv-{t}-{i}", f"actor{t}", {"days": 1})
            else:
                store.commit("set_budget", f"bud-{t}-{i}", f"actor{t}",
                             {"platform": "meta", "budget_id": f"B{i % 4}", "amount": 100 * t + i})

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    log = store.log()
    assert [e["seq"] for e in log] == list(range(1, len(log) + 1))  # contiguous, monotonic
    assert len(log) == 1 + 8 * 10
    assert store.clock() == (42, 8 * 4)  # i in {0, 3, 6, 9} advance

    fresh = WorldStore(tmp_path / "replayed.duckdb")
    replay(log, fresh)
    assert fresh.semantic_state_hash() == store.semantic_state_hash()
    fresh.close()


def test_semantic_hash_ignores_wall_clock_but_sees_content(tmp_path):
    a = WorldStore(tmp_path / "a.duckdb")
    b = WorldStore(tmp_path / "b.duckdb")
    for s in (a, b):
        s.commit("reset", "r0", "t", {"seed": 1})
    assert a.semantic_state_hash() == b.semantic_state_hash()  # committed_at differs, hash does not
    b.commit("set_budget", "r1", "t", {"platform": "google", "budget_id": "B1", "amount": 10})
    assert a.semantic_state_hash() != b.semantic_state_hash()
    a.close()
    b.close()
