# Contract: claim atoms, guard, narrator, daily brief (Stage 2 ★, spec §11)

Modules: `backend/adapt/agent/{atoms,numbers,guard,groq_client,narrator,brief}.py`; parameters
`config/narrative.yaml`. Entry points: `narrate_incident(db, anomaly_id, client)`, `narrate_decision(db,
decision_id, client)`, `daily_brief(db, as_of, client)`, `check_answer(text, cited_ids, package)` (Copilot,
Stage 3 caller); `groq_client.from_settings(settings, db)` builds the client (`GROQ_API_KEY`, none → offline).

## Claim atoms (the single schema)
`{atom_id, evidence_ids[], entity, metric, direction, value, unit, period, comparison_basis, claim_level, qualifier,
stat_status, causal_assumptions[]}` + derived `intensity` (slight < 5% · moderate 5–15% · sharp 15–35% · severe
> 35%) and `driver`. Built by code from `intel.anomalies` (A1 relative change, A2/A3 actual vs expected, A4 ₹ CBA
impact), `intel.decompositions` (F1–F4: each lever's own change, EXACT_ACCOUNTING), `intel.diagnoses` +
`intel.evidence` (D*: supporting STRONG/WEAK drivers with their headline value), `intel.causal_estimates` (C1–C4
when ESTIMATED, C0 otherwise), and `intel.decisions` (E1–E5 expected ΔCAA / P10 / P90 / model P(loss) / calibrated,
L* legs, U1 unallocated, W* why-not). A package also carries the whitelists (entities, drivers, platforms, dates,
next_step options).

## Guard (every sentence, the headline, every Copilot answer; any violation rejects)
1. cited atoms exist and their evidence ids exist; a claim (number, direction, qualifier, driver) needs atoms
2. numbers: ±0.5% or the displayed rounding (₹1,25,000 / 125000 / ₹125k / 1.25 lakh / 1.25L / ₹1.2 Cr / 12.5e4 /
   12.5% ↔ 0.125; days by unit); numbers inside an atom's own code-built labels count as cited
3. direction against the atom of the nearest metric mention; negation within 3 tokens inverts (NOT_UP / NOT_DOWN);
   double negation (incl. "did not fail to"), conditionals and questions are rejected as unresolvable
4. intensity words must match the derived band; "significant" needs STAT/SHIFT; other evaluative words need the
   allowlist (material, notable)
5. no causal verbs at any level; QUASI-EXPERIMENTAL only as "estimated effect … under the stated assumptions";
   a named probable driver needs hedging ("evidence points to", "associated with", …)
6. drivers, SKUs, platforms, campaign ids / names, dates must be in the package
7. period phrases must agree with the cited atoms
`next_step` is `{kind ∈ VIEW_DECISION | APPROVE_REVIEW | RECONCILE | INVESTIGATE | NONE, ref_id ∈ package options}`
only; free text or amounts are rejected (T56). The headline is checked against every atom the sentences cite.

## Narrator flow
Package → (online) Groq strict json_schema, non-streaming, no tools, chain openai/gpt-oss-120b → openai/gpt-oss-20b
(filtered by GET /models), 20 s timeout, 429 backoff ×2 then the next model, cached in `ops.llm_cache` →
guard → on failure ONE retry with the violations fed back → template. The template is built from the same atoms and
must pass the same guard (a failing template raises: a bug, never displayed). Output: `{headline, sentences[{text,
atom_ids, evidence_ids, claim_levels}], next_step, source: llm:<model> | template, badge: "evidence linked · values
checked", guard_rejections, fallback_reason?, not_estimable_reason?}`, stored in `intel.narratives`. The copilot
request profile (streaming + tools, no strict schema) is a separate object and is refused by the narrator path.

## Daily brief (Stage 2)
Atoms: open incidents today, the largest incident's change, decisions awaiting approval, decisions executed and
verified, matured outcome counts by verdict. Same guard; template fallback.

## TESTS
Unit (`backend/tests/test_narrator.py`, 63): T36 (reversed direction, causal verbs), T43 (36 phrasings: negation,
double negation, conditionals, questions, intensity bands, significance, evaluative words, numbers, periods,
uncited claims), quasi-experimental form, T22 (unsupported driver / platform / SKU / id / name / date), hedging,
number normalisation, T56 (typed next_step; uncited Copilot numbers), headline guarded, template passes the guard,
Groq flow (offline, chain filtering, chips, non-streaming tool-free strict request, retry with feedback, fallback after
two rejections, 429 backoff then the next model, cache, copilot profile refused).
Integration (`integration/test_narrator_pipeline.py`): every incident, decision and the brief from a real pipeline
cycle (S1) narrate offline and pass the guard; a forged reversal of a real atom is rejected.
