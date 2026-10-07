"""Suite-wide defaults: API tests run with login disabled (they act as the demo manager; test_auth.py turns it on)
and pipeline maths runs in-process (test_parallel.py checks the pooled path gives identical results)."""

import os

os.environ.setdefault("ADAPT_AUTH_ENABLED", "false")
os.environ.setdefault("ADAPT_WORKERS", "0")
