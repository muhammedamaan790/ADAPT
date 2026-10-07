# Stage 2 (backend): what was built, how to switch it, what is still open

Branch `stage2`. Every component below has a contract in `docs/contracts/` and tests; nothing in the frontend or the
C6 API routers was touched (one line each in `integration/test_pipeline_cycle.py`, `integration/test_decide_learn.py`,
`integration/test_execution_saga.py` and `integration/test_connectors_sync.py` changed: they asserted Stage 1-only
values).

| Spec item | Where | Contract |
|---|---|---|
| ★ Google Ads v25 live adapter + hybrid mirror | `execute/google_ads_live.py`, `execute/mirror.py`, `scripts/google_ads_setup.py` | `google_ads_live.md` |
| ★ Stage-1-scope evaluation | `evalharness/`, `world/world/truth_economics.py`, `scripts/run_eval.py` | `evaluation.md` |
| ★ Minimal causal slice (gated synthetic control) | `diagnose/causal/` | `causal.md` |
| ★ Groq narrator + claim atoms + guard (+ daily brief) | `agent/` | `narrator.md` |
| ★ Evidence: saturation + demand (+ world S6, S8, S10, S11, S12) | `diagnose/evidence.py`, `world/world/scenarios.py` | `evidence.md`, `world_scenarios.md` |
| TikTok + Amazon simulated channels | world catalog/step/reporting/mutations, `ingest/connectors/marketplaces.py`, reconcile | `connectors.md` |
| Cannibalization T matrix | `economics/cannibalization.py` | `cannibalization.md` |
| LightGBM demand + NB2 P(stockout) | `predict/demand.py`, `economics/inventory_risk.py` | `demand_inventory_risk.md` |
| Champion / challenger | `learn/governance.py` | `demand_inventory_risk.md` |
| Automatic emergency safety monitor | `policy/safety_monitor.py` (pipeline step) | `objectives_safety.md` |
| GROWTH + INVENTORY CLEARANCE + sensitivity scenarios | `decide/optimizer.py`, `decide/alternatives.py` | `objectives_safety.md` |
| Archived-environment replay (T68) | `decide/archived_replay.py`, `scripts/replay.py` | this file |

## Stage switches (set back to get Stage 1 behaviour)
- `config/inventory_risk.yaml predicate`: `STOCKOUT_PROBABILITY` (Stage 2) | `PROJECTED_SHORTFALL` (Stage 1)
- `config/cannibalization.yaml enabled`: `true` | `false` (T = 0)
- `config/objectives.yaml selected` (or `ops.objective_setting` via `set_objective`): PROFIT | GROWTH |
  INVENTORY_CLEARANCE
- `ADAPT_GOOGLE_EXECUTION_MODE`: `mock` | `live`
- world `--channels tiktok,amazon_sp` at seeding (opt-in; base worlds and their truth fingerprints are unchanged)
- `GROQ_API_KEY` (unset → deterministic templates)

## Service functions for the C6 API (none are routed yet)
narratives `agent.narrator.narrate_incident / narrate_decision`, `agent.brief.daily_brief` · causal
`intel.causal_estimates` · `execute.adapters.platform_health` (GET /platforms/health) ·
`execute.mirror.advance_world` (the only allowed advance path; 409 on SimOutOfSync), `mirror.resolve_manually`
(/reconcile?target=sim) · `decide.alternatives.set_objective / selected_objective` (GET/PUT /objective),
`choose_alternative` · `learn.governance.rollback` (POST /models/{model}/rollback), `governance.history` ·
`policy.safety_monitor` (`ops.safety_checks`, `ops.events type = safety_alert`, `ops.autonomy_pins`) ·
`decide.archived_replay.replay_archived` · evaluation report `evidence/eval.json`.

## Measured / open (honest list)
- Not run on the real seed-42 / held-out data (no Kaggle data on the build machine): the eval, the live Google smoke
  test, the 20-seed numbers and `evidence/bench.json` still have to be produced (`scripts/run_eval.py --bench`, then
  `--seeds eval`).
- Causal gates as specified refuse most single-campaign incidents in this world (daily ROAS noise ~14%; the placebo's
  rupee gate is 5% of ONE day's CBA): see `causal.md`.
- Two documented deviations: demeaned synthetic control + refit bootstrap (`causal.md`); conformalized quantiles for
  the demand model's interval (`demand_inventory_risk.md`).
- Daily cycle time will grow (sensitivity scenarios = 2 extra solves; weekly demand refit; cannibalization's O(n U²)
  evaluation): measure with `--bench` against the 20 s target.
- Autonomous execution, the autonomy ladder and warm-up worlds 901–904 are Stage 3 (the safety monitor creates
  decisions for review and raises P1 alerts; `ops.autonomy_pins` is ready for the ladder).
