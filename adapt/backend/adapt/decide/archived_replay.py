"""Historical replay in the ARCHIVED environment (spec §22.9 P0, T68): replay must run the exact source and dependency
environment identified by the decision's stored code_sha and lock_hash; running current code against historical
hashes is not a valid replay.

  uv run python scripts/replay.py <decision_id> [--workspace data/workspaces/demo.duckdb]

1. read the snapshot's code_sha / lock_hash / replay_environment_fingerprint
2. a dirty ("+dirty") or unknown code_sha cannot be reconstructed -> REPLAY_ENV_UNAVAILABLE
3. `git worktree add --detach <tmp> <code_sha>`; an unresolvable commit (rewritten history) -> REPLAY_ENV_UNAVAILABLE
4. the worktree's uv.lock must hash to the recorded lock_hash -> else REPLAY_ENV_UNAVAILABLE
5. `uv sync --frozen` in the worktree (the uv cache makes this fast), then the archived code's own
   adapt.decide.decisions.replay() runs in that environment against a COPY of the workspace (the live file keeps its
   single writer; the content-addressed artifacts are linked, never copied or moved) and must reproduce decision_hash
The result names the environment it ran in. A Docker image digest is an optional alternative (not built).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
UNAVAILABLE = "REPLAY_ENV_UNAVAILABLE"
ENTRY = ("import json, sys; from adapt.core.db import Database; from adapt.decide.decisions import replay; "
         "db = Database(sys.argv[1]); print(json.dumps(replay(db, sys.argv[2]), default=str)); db.close()")


def _run(cmd: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def snapshot_env(workspace: Path, decision_id: str) -> dict:
    import duckdb

    con = duckdb.connect(str(workspace), read_only=True)
    try:
        row = con.execute("SELECT code_sha, lock_hash, replay_environment_fingerprint, decision_hash FROM "
                          "ops.decision_snapshots WHERE decision_id = ?", [decision_id]).fetchone()
    finally:
        con.close()
    if row is None:
        raise KeyError(f"no snapshot for {decision_id}")
    return {"code_sha": row[0], "lock_hash": row[1], "fingerprint": row[2], "decision_hash": row[3]}


def replay_archived(workspace: str | Path, decision_id: str, repo: Path = REPO, run=_run) -> dict:
    workspace = Path(workspace).resolve()
    env = snapshot_env(workspace, decision_id)
    out = {"decision_id": decision_id, "code_sha": env["code_sha"], "original_hash": env["decision_hash"]}
    sha = env["code_sha"] or "unknown"
    if sha == "unknown" or sha.endswith("+dirty"):
        return {**out, "match": None, "status": UNAVAILABLE,
                "reason": "the decision was created from uncommitted or unknown code: no archived environment exists"}
    tmp = Path(tempfile.mkdtemp(prefix="adapt-replay-"))
    tree = tmp / "src"
    try:
        add = run(["git", "worktree", "add", "--detach", str(tree), sha], repo)
        if add.returncode != 0:
            return {**out, "match": None, "status": UNAVAILABLE,
                    "reason": f"commit {sha} cannot be resolved: {add.stderr.strip()[:200]}"}
        lock = tree / "adapt" / "uv.lock" if (tree / "adapt" / "uv.lock").exists() else tree / "uv.lock"
        project = lock.parent
        got = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else None
        if got != env["lock_hash"]:
            return {**out, "match": None, "status": UNAVAILABLE,
                    "reason": f"the archived uv.lock hash {str(got)[:12]} != recorded {str(env['lock_hash'])[:12]}"}
        sync = run(["uv", "sync", "--frozen"], project)
        if sync.returncode != 0:
            return {**out, "match": None, "status": UNAVAILABLE, "reason": f"uv sync --frozen: {sync.stderr[-300:]}"}
        ws = tmp / "workspace" / workspace.name                     # a copy: the live file keeps its single writer
        ws.parent.mkdir(parents=True)
        shutil.copy(workspace, ws)
        (ws.parent / "artifacts").symlink_to(workspace.parent / "artifacts", target_is_directory=True)
        res = run(["uv", "run", "--frozen", "python", "-c", ENTRY, str(ws), decision_id], project)
        if res.returncode != 0:
            return {**out, "match": False, "status": "REPLAY_FAILED", "reason": res.stderr[-500:]}
        r = json.loads(res.stdout.strip().splitlines()[-1])
        return {**out, **r, "status": "OK" if r.get("match") else "MISMATCH",
                "environment": {"worktree_sha": sha, "uv_lock": env["lock_hash"], "fingerprint": env["fingerprint"]}}
    finally:
        run(["git", "worktree", "remove", "--force", str(tree)], repo)
        shutil.rmtree(tmp, ignore_errors=True)
