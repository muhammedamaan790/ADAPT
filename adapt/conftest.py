"""Suite-wide defaults: API tests run with login disabled (they act as the demo manager; test_auth.py turns it on)
and pipeline maths runs in-process (test_parallel.py checks the pooled path gives identical results)."""

import os

os.environ.setdefault("ADAPT_AUTH_ENABLED", "false")
os.environ.setdefault("ADAPT_WORKERS", "0")
# Hermetic: a developer's backend .env (Groq key, live Google mode) must never reach the suite. Process variables
# override .env in Settings, so these pin the offline narrator and mock execution; tests that need either pass
# their own settings or credentials explicitly.
os.environ["GROQ_API_KEY"] = ""
os.environ["ADAPT_GOOGLE_EXECUTION_MODE"] = "mock"
