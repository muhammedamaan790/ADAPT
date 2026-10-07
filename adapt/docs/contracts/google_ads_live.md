# Contract: Google Ads v25 live adapter + hybrid mirror (Stage 2 ★, spec §9.4, §22.5 mirror table)

Modules: `backend/adapt/execute/{google_ads_live,mirror}.py`, hooks in `execute/saga.py` (`Runner._verified`),
`execute/adapters.py` (`build_adapters(..., settings, db)`, `platform_health`), the pipeline's verify step (mirror
retrier), `scripts/google_ads_setup.py` (test-account budgets + `ops.live_entity_map`), `scripts/google_oauth_token.py`
(refresh token).

## Mode (fixed at startup, never an automatic fallback)
`ADAPT_GOOGLE_EXECUTION_MODE = mock | live` (env) overrides `platforms.yaml execution_mode.google`. `live` builds
`GoogleAdsLiveAdapter` (needs the workspace for its id map); Meta stays mock (sandbox out of scope).

## Live adapter
- REST, same wire format as the world mock: `POST /v25/customers/{cid}/campaignBudgets:mutate` (absolute
  `amountMicros`, multiples of 10,000) and verification by a SEPARATE GAQL `googleAds:search` read.
- OAuth desktop client + refresh token (`oauth2.googleapis.com/token`), `login-customer-id` = test manager. The
  developer-token header is sent only if `GOOGLE_ADS_DEVELOPER_TOKEN` is set; it is redacted from the ledger either way.
- Health (once, before the first call): credentials present, token refresh, API version answers (404 = sunset /
  misconfigured, T23), customer accessible, `customer.test_account = true` (this build refuses a serving account),
  currency INR. Failure → `AdapterUnavailable` → the leg is BLOCKED, the decision preserved, nothing sent anywhere.
- Sim budget id → live budget id via `ops.live_entity_map`; unmapped → BLOCKED.
- `platform_health(adapters)` = the `/platforms/health` payload; the UI badge is "LIVE · Google Ads (test account) —
  performance data simulated".

## Hybrid mirror (exec.saga_legs.sim_sync_state + exec.leg_mirror; history `MIRROR:*` in exec.saga_transitions)
NOT_REQUIRED (mock legs) · MIRROR_PENDING → MIRRORED · MIRROR_PENDING → MIRROR_FAILED (after 1 h of idempotent
absolute-set retries) · MIRROR_FAILED → MIRROR_RESOLVED_MANUALLY (manager; a FRESH live read-back must confirm the
verified amount first). Started by the saga when a LIVE leg turns VERIFIED (immediate verify, read-after-write,
background re-verify or crash recovery); retried by the pipeline's verify step. A mirror problem never compensates
or rolls back the verified Google change; the saga stays SUCCEEDED and the decision EXECUTED (T41).
`mirror.advance_world(...)` is the only path ADAPT uses to advance the world; it raises `SimOutOfSync` (HTTP 409
"sim divergence pending") while any leg is MIRROR_PENDING / MIRROR_FAILED.

## Setup
`uv run python scripts/google_oauth_token.py` (refresh token into .env) → sync the workspace once →
`uv run python scripts/google_ads_setup.py [--limit N] [--dry-run]` creates one INR budget (shared stays shared) +
one PAUSED Search campaign per simulated Google budget in the test client account and writes the id map →
`ADAPT_GOOGLE_EXECUTION_MODE=live`. The script refuses a non-test / non-INR account.
**Not run against a real account in this build** (no credentials on the dev machine): every behaviour above is tested
against a recorded-fixture fake of the REST API; the live smoke test (mutate → GAQL verify → restore) is manual.

## TESTS (`integration/test_google_live.py`)
health checks (test account, INR, sunset version, revoked refresh token, missing credentials) · hybrid happy path:
verified live, mirrored into the world, mock Meta leg NOT_REQUIRED, verification is a separate GAQL read after the
mutate (anti-hollow) · T41 mirror down → MIRROR_PENDING, Google untouched, world advance refused, retry → MIRRORED ·
T41 > 1 h → MIRROR_FAILED → manual resolution (viewer refused; refused while the live read-back disagrees) ·
T23 / T30 auth / version / non-test / unmapped → BLOCKED, zero mutations, never VERIFIED, never mirrored ·
timeout-after-success on live → read-after-write, one mutation · redaction (access token, refresh token, developer
token never persisted) · `build_adapters` live mode reads the id map.
