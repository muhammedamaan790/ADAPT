# Remaining frontend integration handoff

The new pages use `adapt/web/src/api/insights.ts` and Zod contracts in `insight-contracts.ts`. These are **proposed view-model contracts**, not implemented backend endpoints. At this snapshot the HTTP backend exposes health only. Backend owners must align envelopes, pagination, authentication and mutation semantics with OpenAPI before enabling API mode. Existing decision-loop routes remain in `frontend-api.md`.

All paths below are relative to the configured `/api/v1` base. Every response is validated; API errors never select fixture data.

| Method | Path | Response / request |
| --- | --- | --- |
| GET | `/opportunities` | `opportunitySchema[]`, ranked by backend; nullable score, feasibility, budget identity, evidence and provenance |
| GET | `/curves/{budget_id}` | `curveSchema`: supplied response points with unit and method note |
| GET | `/creatives/fatigue` | `fatigueSchema[]`: CTR movement, frequency, review state and reason |
| POST | `/creatives/score` | `{text}` → `creativeScoreSchema`; missing model output = NOT_ESTIMABLE/null |
| GET | `/learning/calibration` | Factor and update history, including before/after values and source outcome |
| GET | `/learning/accuracy` | Sample count, nullable MAE and interpretation note |
| GET | `/learning/uplift` | Availability, strategy rows with realized CAA/spend/constraint breaches and evaluation note |
| GET | `/learning/feedback` | Eligibility records by decision/outcome |
| GET | `/models` | Read-only model name/version/status/training timestamp/note |
| GET | `/data/sources` | Existing `sourceSchema[]` |
| GET | `/data/mapping-coverage` | `{coverage:0..1,unmapped:string[],note}` |
| GET | `/data/reconciliation` | Platform/store revenue, attribution excess and note |
| POST | `/ingest/upload` | Proposed JSON `{type,records,source_currency:"INR",source_timezone:"Asia/Kolkata"}` → import acknowledgement |
| POST | `/ingest/mapping/confirm` | `{import_id,mapping}` → acknowledgement with the same ID and row count |
| POST | `/decisions/{id}/simulate` | `{decision_hash}` → comparisonSchema including four strategies, sensitivity allocations, confidence interpretation and method note |
| GET | `/decisions/{id}/timeline` | `timelineSchema`: timestamped events with links to inspectable artifacts |
| GET | `/decisions/{id}/replay` | Status, expected/actual hash and explanation; hashes must agree with status and current decision |
| GET | `/decisions/{id}/snapshot` | Proposed `{decision,evidence,notice}` envelope |
| POST | `/copilot/chat` | `{message}` → agreed SSE events below |

## CSV ingest contract decisions

The browser parses a maximum 2 MB/5,000-row file, supports quoted commas/newlines/escaped quotes and validates required distinct mappings, ISO dates, platform identity, nonnegative financial values, integer counts, duplicates, clicks <= impressions and reserved <= on-hand units. It does not establish freshness, canonical IDs, permissions or engine acceptance. Backend validation remains authoritative.

The JSON upload envelope is a frontend proposal; it is **not** multipart/file upload and does not yet support a separate backend preview/errors endpoint. The client submits mapped canonical records plus the original mapping. Confirm whether this is appropriate, or adapt the service to backend multipart staging and server-returned mapping previews. Browser templates are bundled; `/ingest/templates/{type}` is not connected.

A failed mapping confirmation retains the staged import ID in memory for an explicit retry, so the same unchanged submission does not upload again. The cache holds at most ten submissions. It is not persisted across refreshes; uncertain upload responses and tab refreshes require backend status inspection and canonical deduplication before another submission. Mutation keys are unique per call, so the backend must enforce semantic idempotency for mapping confirmation. An already IMPORTED acknowledgement is reused rather than reconfirmed. The UI displays the backend acknowledgement, not a client-inferred promotion.

Fixture staging writes only file name, ID, row count and mode to browser storage. Parsed rows are not stored in localStorage or sent to the engine. This cannot change fixture metrics, sources, inventory or decisions.

## Copilot stream contract

Request uses session credentials, JSON body, request/idempotency headers, `Accept: text/event-stream`, a 30-second timeout and user cancellation. Response is capped at 128 KB. Events may use LF or CRLF, including chunk boundaries:

```text
data: {"type":"answer","reply":{"text":"Inspect the inventory gate.","evidence":[{"label":"Decision","href":"/decisions/decision-123"}],"mode":"LLM"}}

data: {"type":"done"}

```

`error` events contain a message. A full valid `answer` followed by `done` is required before displaying the reply. Partial/truncated answers, malformed JSON and unsupported evidence URLs are rejected. This transport consumes SSE but does not render token deltas. Citation routes are restricted to known internal pages; add an explicit validated URL policy if external evidence citations become required. Safe route validation alone does not establish the backend answer's factual grounding.

Fixture replies are deterministic templates over current visible overview, decisions and outcomes, visibly labelled TEMPLATE. No LLM, SQL tool, model prediction or budget action runs in this layer. Backend Copilot must implement read-only allowlisted tools, grounding and permissions; the frontend is not a sandbox for arbitrary SQL.

## Truth boundaries and remaining work

DEMO_01 comparison/fatigue examples are recorded illustrations. Alternative fixture allocations create distinct non-executable drafts through the existing modification flow. Response charts connect supplied points, not fitted splines; they are not used for valuation. The default creative scorer returns NOT_ESTIMABLE, held-out evaluation has no rows, model registry has no artifacts, and replay reports UNAVAILABLE. Snapshot export lacks an archived environment and cannot prove reproducibility.

Learning derives descriptive MAE only from available eligible optimization fixture outcomes and reads once-applied calibration transitions. Safety/inconclusive rows stay excluded. No hidden simulator truth is imported into these pages.

Data lineage currently uses the overview metric view model; dedicated `/data/health` and `/data/lineage?metric=` adapters are not yet wired. Workspace create/switch, model promotion/rollback, event-stream replacement, head-to-head evaluation execution, richer report metadata and archived replay artifacts remain follow-up work. The UI stays in PROFIT / human approval mode.
