"""T68 historical replay environment: a dirty / unknown code_sha, an unresolvable commit and a lockfile that does not
match the recorded hash all return REPLAY_ENV_UNAVAILABLE (never a pass from current code); a resolvable commit runs
the archived code's own replay() in a worktree against a COPY of the workspace with the artifacts linked."""

import hashlib
import json
import subprocess
from pathlib import Path

import duckdb
import pytest

from adapt.decide.archived_replay import UNAVAILABLE, replay_archived


def workspace(tmp_path, code_sha, lock_hash="L") -> Path:
    p = tmp_path / "ws" / "w.duckdb"
    p.parent.mkdir()
    (p.parent / "artifacts").mkdir()
    con = duckdb.connect(str(p))
    con.execute("CREATE SCHEMA ops")
    con.execute("CREATE TABLE ops.decision_snapshots (decision_id VARCHAR, code_sha VARCHAR, lock_hash VARCHAR, "
                "replay_environment_fingerprint VARCHAR, decision_hash VARCHAR)")
    con.execute("INSERT INTO ops.decision_snapshots VALUES ('D1', ?, ?, 'fp', 'h')", [code_sha, lock_hash])
    con.close()
    return p


class FakeRun:
    def __init__(self, tree_files=None, add_rc=0, replay_out=None):
        self.calls, self.tree_files, self.add_rc, self.replay_out = [], tree_files or {}, add_rc, replay_out

    def __call__(self, cmd, cwd, timeout=900):
        self.calls.append(cmd)
        if cmd[:3] == ["git", "worktree", "add"]:
            tree = Path(cmd[4])
            for rel, content in self.tree_files.items():
                (tree / rel).parent.mkdir(parents=True, exist_ok=True)
                (tree / rel).write_text(content)
            return subprocess.CompletedProcess(cmd, self.add_rc, "", "fatal: bad revision" if self.add_rc else "")
        if cmd[:2] == ["uv", "run"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.replay_out), "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.mark.parametrize("sha", ["abc123+dirty", "unknown", None])
def test_uncommitted_code_has_no_archived_environment(tmp_path, sha):
    run = FakeRun()
    out = replay_archived(workspace(tmp_path, sha), "D1", run=run)
    assert out["status"] == UNAVAILABLE and out["match"] is None and run.calls == []


def test_unresolvable_commit(tmp_path):
    out = replay_archived(workspace(tmp_path, "deadbeef"), "D1", run=FakeRun(add_rc=128))
    assert out["status"] == UNAVAILABLE and "cannot be resolved" in out["reason"]


def test_lockfile_must_match_the_recorded_hash(tmp_path):
    run = FakeRun(tree_files={"adapt/uv.lock": "changed lock"})
    out = replay_archived(workspace(tmp_path, "deadbeef", lock_hash="something-else"), "D1", run=run)
    assert out["status"] == UNAVAILABLE and "uv.lock" in out["reason"]
    assert not any(c[:2] == ["uv", "run"] for c in run.calls)              # nothing ran in a wrong environment


def test_archived_replay_runs_the_archived_entry_point(tmp_path):
    lock = "the archived lock"
    run = FakeRun(tree_files={"adapt/uv.lock": lock}, replay_out={"match": True, "replay_hash": "h"})
    out = replay_archived(workspace(tmp_path, "deadbeef", hashlib.sha256(lock.encode()).hexdigest()), "D1", run=run)
    assert out["status"] == "OK" and out["match"] and out["environment"]["worktree_sha"] == "deadbeef"
    kinds = [c[:3] for c in run.calls]
    assert ["uv", "sync", "--frozen"] in kinds and ["git", "worktree", "remove"] in kinds
    entry = next(c for c in run.calls if c[:2] == ["uv", "run"])
    assert entry[-1] == "D1" and Path(entry[-2]).name == "w.duckdb" and "ws" not in Path(entry[-2]).parts[-2]


def test_real_worktree_replay_on_a_clean_checkout(tmp_path):
    """End to end (git worktree + uv sync --frozen + the archived replay()). Runs only when backend/ is committed: a
    decision created from a dirty tree has, correctly, no archived environment."""
    from tests.test_decisions import make_run
    from tests.test_optimizer import T0, trap_state

    from adapt.core.db import Database
    from adapt.decide import decisions as dec
    from adapt.decide.snapshot import code_sha

    if code_sha().endswith("+dirty") or code_sha() == "unknown":
        pytest.skip("backend/ has uncommitted changes: no archived environment to replay in")
    db = Database(tmp_path / "ws" / "w.duckdb")
    state = trap_state()
    assert dec.create_decisions(db, make_run(state), state, {}, T0) == ["run-1:R"]
    db.close()
    out = replay_archived(tmp_path / "ws" / "w.duckdb", "run-1:R")
    assert out["status"] == "OK" and out["match"], out
