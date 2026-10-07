"""Eval-only ground-truth listener (spec §2): a second server on 127.0.0.1, started only when WORLD_EVAL_MODE=1.

It shares the world store with the public listener (same process, so DuckDB's single-writer lock is respected) but
is a separate FastAPI app on a separate port. Every request needs the per-run token (header X-Eval-Token),
generated at startup and written to `<world_dir>/.eval_token` for the evalharness process to read. Docker compose
never publishes this port and the ADAPT container is not on its network.

Routes: /truth/gt_incidents, /truth/scenarios, /truth/campaigns, /truth/lost_demand?from_day&to_day
"""

from __future__ import annotations

import json
import secrets
import threading
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException

from world.state import WorldStore

TOKEN_FILE = ".eval_token"


def create_truth_app(store: WorldStore, token: str) -> FastAPI:
    app = FastAPI(title="ADAPT world truth (eval only)", docs_url=None, redoc_url=None, openapi_url=None)

    def require_token(x_eval_token: str | None = Header(default=None)) -> None:
        if x_eval_token is None or not secrets.compare_digest(x_eval_token, token):
            raise HTTPException(status_code=401, detail="eval token required")

    auth = [Depends(require_token)]

    @app.get("/truth/gt_incidents", dependencies=auth)
    def gt_incidents() -> list[dict]:
        rows = store.read("SELECT incident_id, activation_id, scenario, driver, entity_ids, metric_set, direction, "
                          "injection_start_day, onset_day, injection_end_day, magnitude FROM gt_incidents "
                          "ORDER BY incident_id")
        keys = ["incident_id", "activation_id", "scenario", "driver", "entity_ids", "metric_set", "direction",
                "injection_start_day", "onset_day", "injection_end_day", "magnitude"]
        out = []
        for r in rows:
            d = dict(zip(keys, r, strict=True))
            for k in ("entity_ids", "metric_set", "direction"):
                d[k] = json.loads(d[k])
            out.append(d)
        return out

    @app.get("/truth/scenarios", dependencies=auth)
    def scenarios() -> list[dict]:
        rows = store.read("SELECT activation_id, scenario_key, start_day, end_day, params FROM scenario_activation "
                          "ORDER BY activation_id")
        return [{"activation_id": a, "key": k, "start_day": s, "end_day": e, "params": json.loads(p)}
                for a, k, s, e, p in rows]

    @app.get("/truth/campaigns", dependencies=auth)
    def campaigns() -> list[dict]:
        truth = store.ctx.truth
        return [asdict(t) for t in truth.campaigns.values()] if truth else []

    @app.get("/truth/lost_demand", dependencies=auth)
    def lost_demand(from_day: int = -10_000, to_day: int = 10_000) -> list[dict]:
        rows = store.read("SELECT day, sku, channel, campaign_id, units FROM fact_lost_demand "
                          "WHERE day BETWEEN ? AND ? ORDER BY day, sku, channel, campaign_id", [from_day, to_day])
        return [{"day": d, "sku": s, "channel": c, "campaign_id": cid, "units": u} for d, s, c, cid, u in rows]

    return app


class TruthListener:
    """Runs the truth app with uvicorn in a background thread, bound to loopback only."""

    def __init__(self, store: WorldStore, world_dir: Path, port: int) -> None:
        self.token = secrets.token_urlsafe(32)
        self.token_path = Path(world_dir) / TOKEN_FILE
        self.token_path.write_text(self.token, encoding="utf-8")
        config = uvicorn.Config(create_truth_app(store, self.token), host="127.0.0.1", port=port, log_level="warning",
                                lifespan="off")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, name="world-truth-listener", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)
        if self.token_path.exists():
            self.token_path.unlink()
