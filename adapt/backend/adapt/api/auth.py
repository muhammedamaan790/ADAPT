"""Session login, roles and CSRF for the HTTP API (spec §9.5; hackathon-grade and explicitly scoped).

- Users: seeded accounts with Argon2-hashed passwords in `<data_dir>/auth/users.json`. They live outside the
  workspace file on purpose: /sim/reset copies the workspace baseline back, which must not delete accounts.
  Seed passwords come from ADAPT_SEED_PASSWORD; without it, random ones are generated once and written to
  `<data_dir>/auth/initial_credentials.txt` (data/ is git-ignored). Plain-text passwords are never stored elsewhere.
- Session: an HttpOnly, SameSite=Lax cookie signed with itsdangerous (ADAPT_SESSION_SECRET; a random per-process
  secret otherwise, so sessions end when the server restarts). Lifetime 12 h.
- CSRF: every POST/PUT except login carries `X-CSRF-Token`, an HMAC of the session id that /auth/login and /auth/me
  return in their body (the frontend keeps it in memory; it is never readable from a cookie).
- Roles: viewer < manager < admin. Reads need viewer. Read-only computations sent as POST (simulate, what-if,
  creative score, Copilot) need viewer; every state change needs manager; policy, objective, model promotion and
  rollback, and new workspaces need admin.
- Audit: every write request is appended to `<data_dir>/auth/audit.jsonl` (request id, actor, role, method, path,
  status), which also survives a workspace reset. Decision events keep their own actor in the workspace.
- Login throttling: 5 failed attempts per user name within 5 minutes lock that name for the rest of the window.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from itsdangerous import BadSignature, URLSafeTimedSerializer

COOKIE = "adapt_session"
CSRF_HEADER = "X-CSRF-Token"
SESSION_SECONDS = 12 * 3600
ROLES = {"viewer": 0, "manager": 1, "admin": 2}
SEED_USERS = [  # (user_id, display name, role)
    ("viewer", "Read-only viewer", "viewer"),
    ("maria", "Maria (Growth Manager)", "manager"),
    ("admin", "Workspace admin", "admin"),
]
MAX_FAILURES, FAILURE_WINDOW = 5, 300

PUBLIC = {("GET", "/api/v1/health"), ("POST", "/api/v1/auth/login"), ("POST", "/api/v1/auth/logout"),
          ("GET", "/api/v1/auth/me")}
VIEWER_WRITES = [r"/decisions/[^/]+/simulate", r"/optimizer/whatif", r"/creatives/score", r"/copilot/chat",
                 r"/copilot/sql", r"/ingest/mapping/suggest"]
ADMIN_WRITES = [r"/policy", r"/objective", r"/models/[^/]+/(promote|rollback)", r"/workspaces"]


def required_role(method: str, path: str) -> str:
    """The minimum role for a request on /api/v1 (PUBLIC routes are handled before this)."""
    if method in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    sub = path.removeprefix("/api/v1")
    if any(re.fullmatch(p, sub) for p in ADMIN_WRITES):
        return "admin"
    if any(re.fullmatch(p, sub) for p in VIEWER_WRITES):
        return "viewer"
    return "manager"


@dataclass(frozen=True)
class User:
    user_id: str
    display_name: str
    role: str

    def can(self, role: str) -> bool:
        return ROLES[self.role] >= ROLES[role]

    def public(self) -> dict:
        return {"user_id": self.user_id, "display_name": self.display_name, "role": self.role}


DEMO_USER = User("demo-manager", "Demo manager (auth disabled)", "manager")


class Auth:
    def __init__(self, data_dir: Path, session_secret: str | None, seed_password: str | None,
                 secure_cookie: bool = False):
        self.dir = Path(data_dir) / "auth"
        self.users_path = self.dir / "users.json"
        self.audit_path = self.dir / "audit.jsonl"
        self.secret = session_secret or secrets.token_hex(32)
        self.signer = URLSafeTimedSerializer(self.secret, salt="adapt-session")
        self.hasher = PasswordHasher()
        self.secure_cookie = secure_cookie
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._dummy_hash = self.hasher.hash(secrets.token_hex(8))  # equal-cost verify for unknown user names
        self.users = self._load_or_seed(seed_password)

    # ---- users -------------------------------------------------------------------------------------------------------
    def _load_or_seed(self, seed_password: str | None) -> dict[str, dict]:
        if self.users_path.exists():
            return json.loads(self.users_path.read_text(encoding="utf-8"))
        self.dir.mkdir(parents=True, exist_ok=True)
        users, generated = {}, []
        for uid, name, role in SEED_USERS:
            pw = seed_password or secrets.token_urlsafe(12)
            if not seed_password:
                generated.append(f"{uid} ({role}): {pw}")
            users[uid] = {"display_name": name, "role": role, "password_hash": self.hasher.hash(pw)}
        self.users_path.write_text(json.dumps(users, indent=2), encoding="utf-8")
        if generated:
            (self.dir / "initial_credentials.txt").write_text(
                "Generated once because ADAPT_SEED_PASSWORD was not set. Store these, then delete this file.\n"
                + "\n".join(generated) + "\n", encoding="utf-8")
        return users

    def login(self, user_id: str, password: str) -> User | None:
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(user_id, []) if now - t < FAILURE_WINDOW]
            self._failures[user_id] = recent
            if len(recent) >= MAX_FAILURES:
                raise PermissionError("too many failed attempts; try again in a few minutes")
        rec = self.users.get(user_id)
        try:
            ok = self.hasher.verify(rec["password_hash"] if rec else self._dummy_hash, password) and rec is not None
        except (VerifyMismatchError, InvalidHashError):
            ok = False
        if not ok:
            with self._lock:
                self._failures.setdefault(user_id, []).append(now)
            return None
        with self._lock:
            self._failures.pop(user_id, None)
        return User(user_id, rec["display_name"], rec["role"])

    # ---- sessions and CSRF -------------------------------------------------------------------------------------------
    def issue(self, user: User) -> tuple[str, str]:
        """(cookie value, CSRF token) for a fresh session."""
        sid = secrets.token_urlsafe(16)
        return self.signer.dumps({"u": user.user_id, "s": sid}), self.csrf_for(sid)

    def csrf_for(self, sid: str) -> str:
        return hmac.new(self.secret.encode(), f"csrf:{sid}".encode(), hashlib.sha256).hexdigest()

    def session(self, cookie: str | None) -> tuple[User, str] | None:
        """(user, session id) for a valid, unexpired cookie whose user still exists."""
        if not cookie:
            return None
        try:
            data = self.signer.loads(cookie, max_age=SESSION_SECONDS)
        except BadSignature:
            return None
        rec = self.users.get(data.get("u"))
        if not rec:
            return None
        return User(data["u"], rec["display_name"], rec["role"]), data["s"]

    def csrf_ok(self, sid: str, token: str | None) -> bool:
        return bool(token) and hmac.compare_digest(self.csrf_for(sid), token)

    # ---- audit -------------------------------------------------------------------------------------------------------
    def audit(self, request_id: str | None, user: User, method: str, path: str, status: int) -> None:
        line = json.dumps({"at": datetime.now(UTC).isoformat(), "request_id": request_id, "actor_id": user.user_id,
                           "role": user.role, "method": method, "path": path, "status": status})
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
