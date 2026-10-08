"""Wire models of the Stage 1 API (C6). They mirror the frontend's zod schemas field for field
(`web/src/api/contracts.ts`, `workbench-contracts.ts`), so FastAPI validates every response against the contract
before it leaves the server and the generated OpenAPI is what `npm run types:generate` consumes."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_serializer

Provenance = Literal["PUBLIC-SAMPLE", "CALIBRATED", "SIMULATED", "LIVE"]
Objective = Literal["PROFIT", "GROWTH", "ACQUISITION", "INVENTORY_CLEARANCE", "MARGIN_PROTECTION", "BALANCED"]
Platform = Literal["Meta", "Google", "TikTok", "Amazon"]  # TikTok + Amazon: Stage 2 simulated channels


class OmitNone(BaseModel):
    """zod `.optional()` fields accept a missing key but not null: drop them when they are None."""
    omit_if_none: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _omit(self, handler):
        data = handler(self)
        for k in self.omit_if_none:
            if data.get(k) is None:
                data.pop(k, None)
        return data


class Metric(OmitNone):
    omit_if_none = ("reason",)
    key: str
    label: str
    value: float | None
    format: Literal["money", "ratio", "count"]
    change: float | None
    reason: str | None = None
    formula: str
    source: str
    available_at: str
    provenance_inputs: list[Provenance]


class Point(BaseModel):
    date: str
    actual: float
    baseline: float


class Source(BaseModel):
    id: str
    name: str
    kind: str
    score: float = Field(ge=0, le=100)
    status: Literal["GREEN", "YELLOW", "RED"]
    freshness: str
    provenance: Provenance


class Attention(BaseModel):
    id: str
    decision_id: str | None
    kind: Literal["incident", "opportunity", "inventory", "outcome"]
    title: str
    description: str
    impact: float
    label: str


class LoopStep(BaseModel):
    label: str
    state: Literal["complete", "current", "waiting"]


class Counts(BaseModel):
    success: float
    neutral: float
    failed: float
    inconclusive: float


class Overview(BaseModel):
    workspace: str
    decision_ts: str
    world_day: int
    scenario: str
    brief: str
    metrics: list[Metric]
    sources: list[Source]
    attention: list[Attention]
    series: list[Point]
    loop: list[LoopStep]
    calibration: float
    counts: Counts


class Check(BaseModel):
    id: str
    label: str
    passed: bool
    detail: str


class Leg(BaseModel):
    platform: Platform
    entity: str
    budget_id: str
    before: float = Field(ge=0)
    after: float = Field(ge=0)


class Expected(BaseModel):
    p10: float
    p50: float
    p90: float
    prob_loss: float = Field(ge=0, le=1)
    delta_net_revenue: float
    raw_pred: float
    calibrated_pred: float


class InventoryRisk(BaseModel):
    kind: Literal["PROJECTED_SHORTFALL", "STOCKOUT_PROBABILITY"]  # by_sku: units short | P(stockout) in [0, 1]
    by_sku: dict[str, float]


class Trigger(BaseModel):
    anomaly_id: str | None
    opportunity_id: str | None


class WhyNot(BaseModel):
    entity: str
    rule_id: str
    reason: str
    metric: str


DecisionStatus = Literal["DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "EXPIRED", "SUPERSEDED", "EXECUTING",
                         "EXECUTED", "PARTIAL", "BLOCKED"]


class Confidence(BaseModel):
    overall: float = Field(ge=0, le=1)
    band: Literal["HIGH", "MEDIUM", "LOW"]
    region: Literal["R1", "R2", "R3"]
    data_quality: float = Field(ge=0, le=1)
    prediction_quality: float = Field(ge=0, le=1)
    track_record: float = Field(ge=0, le=1)
    constraint_coverage: bool


class AutonomyGate(BaseModel):
    id: str
    passed: bool
    detail: str


class AutonomyEntry(BaseModel):
    at: str
    result: Literal["EXECUTED", "DOWNGRADED"]
    gates: list[AutonomyGate]


class Decision(OmitNone):
    omit_if_none = ("valuation_status", "follows", "confidence", "autonomy")
    decision_id: str
    title: str
    summary: str
    class_: Literal["OPTIMIZATION", "SAFETY", "OPERATIONAL", "EXPLORATION"] = Field(alias="class")
    type: str
    objective: Objective
    status: DecisionStatus
    trigger: Trigger
    legs: list[Leg]
    expected: Expected
    inventory_risk_after: InventoryRisk
    unallocated: float = Field(ge=0)
    reserve_floor: float = Field(ge=0)
    budget_ceiling: float = Field(ge=0)
    cost_of_inaction_7d: float
    checks: list[Check] = Field(min_length=1)
    evidence_ids: list[str]
    why_not: list[WhyNot]
    snapshot_id: str
    decision_hash: str
    policy_version: str
    valuation_status: Literal["AVAILABLE", "NOT_ESTIMABLE"] | None = None
    follows: str | None = None
    provenance_inputs: list[Provenance]
    created_at: str
    horizon_days: int = Field(gt=0)
    confidence: Confidence | None = None   # spec §8.4 confidence index (omitted for decisions created before it)
    autonomy: AutonomyEntry | None = None  # the auto-execute verdict on an AUTONOMOUS channel

    model_config = {"populate_by_name": True}


class Driver(BaseModel):
    id: str
    title: str
    score: float = Field(ge=0, le=1)
    level: Literal["ACCOUNTING IDENTITY", "PROBABLE DRIVER", "WEAK EVIDENCE"]
    detail: str
    observations: list[str]
    source: str
    available_at: str


class Labelled(BaseModel):
    label: str
    value: float


class EvidenceOut(BaseModel):
    decision_id: str
    chart: list[Point]
    chart_metric: str
    decomposition_kind: Literal["ROAS", "NOT_APPLICABLE"]
    decomposition: list[Labelled]
    decomposition_total: float
    drivers: list[Driver]


ExternalState = Literal["PLANNED", "PREREAD_OK", "SENT", "VERIFIED", "UNKNOWN", "FAILED", "CONFLICT",
                        "RECONCILED_VERIFIED", "ABANDONED"]
SimSync = Literal["NOT_REQUIRED", "MIRROR_PENDING", "MIRRORED", "MIRROR_FAILED", "MIRROR_RESOLVED_MANUALLY"]
SagaState = Literal["PENDING", "EXECUTING", "SUCCEEDED", "PARTIAL", "COMPENSATING", "COMPENSATED",
                    "COMPENSATION_FAILED", "HUMAN_RESOLUTION_REQUIRED", "RESOLVED_MANUALLY", "ACCEPTED_PARTIAL",
                    "BLOCKED"]


class ExecLeg(Leg):
    mode: Literal["MOCK", "LIVE"]
    external_state: ExternalState
    sim_sync_state: SimSync
    read_back_budget: float | None = None


class Execution(BaseModel):
    execution_id: str
    decision_id: str
    state: SagaState
    legs: list[ExecLeg]
    started_at: str
    detail: str


class Outcome(BaseModel):
    outcome_id: str
    decision_id: str
    world: Literal["SIMULATED", "REAL"]
    class_: str = Field(alias="class")
    verdict: Literal["SUCCESS", "NEUTRAL", "FAILED", "INCONCLUSIVE"]
    predicted: float
    measured: float
    counterfactual: float
    factor_before: float
    factor_after: float
    matured_at: str
    method: str
    calibration_applied: bool
    calibration_note: str = ""

    model_config = {"populate_by_name": True}


class Event(BaseModel):
    id: str
    at: str
    kind: str
    message: str
    decision_id: str | None


class Ack(BaseModel):
    ok: Literal[True] = True


class Causal(BaseModel):
    status: Literal["ESTIMABLE", "NOT_ESTIMABLE"]
    reason: str
    effect_pct: float | None
    lower_pct: float | None
    upper_pct: float | None
    assumptions: list[str]


class Anomaly(BaseModel):
    anomaly_id: str
    title: str
    entity: str
    platform: Platform
    metric: str
    kind: Literal["EFFICIENCY", "TRACKING", "BUDGET_CHANGE"]
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]
    direction: Literal["UP", "DOWN", "FLAT"]
    actual: float
    baseline: float
    change: float
    impact: float
    impact_label: str
    detected_at: str
    decision_id: str | None
    driver: str
    provenance_inputs: list[Provenance]
    gates: list[Check]
    causal: Causal
    resolution_reason: str | None


class ContextCampaign(Leg):
    margin: float
    roas: float
    marginal_caa: float | None
    inventory_gate: Literal["ALLOW", "LIMIT", "BLOCK"]
    min_budget: float = Field(ge=0)
    max_budget: float = Field(ge=0)
    note: str


class OptimizerContext(BaseModel):
    decision_id: str | None
    decision_hash: str | None
    supported_objectives: list[Objective]
    objective: Objective = "PROFIT"
    budget_ceiling: float = Field(ge=0)
    reserve_floor: float = Field(ge=0)
    max_daily_change: float = Field(ge=0, le=1)
    policy_version: str
    horizon_days: int = Field(gt=0)
    campaigns: list[ContextCampaign]


class AllocationLeg(BaseModel):
    budget_id: str
    after: int = Field(ge=0)


class AllocationInput(BaseModel):
    decision_id: str
    decision_hash: str
    objective: Objective
    policy_version: str
    legs: list[AllocationLeg] = Field(min_length=1)


class ObjectiveValue(BaseModel):
    value: float
    label: str = Field(min_length=1)
    unit: Literal["INR", "CUSTOMERS", "SCORE"]


class Evaluation(BaseModel):
    decision_id: str
    decision_hash: str
    objective: Objective
    objective_value: ObjectiveValue | None = None
    allocated: float = Field(ge=0)
    unallocated: float = Field(ge=0)
    checks: list[Check] = Field(min_length=1)
    estimate_status: Literal["AVAILABLE", "NOT_ESTIMABLE"]
    estimate: Expected | None
    explanation: str


class LedgerEntry(BaseModel):
    ledger_id: str
    execution_id: str
    decision_id: str
    at: str
    action: Literal["SET_BUDGET", "VERIFY", "RESTORE_SETTINGS", "RECONCILE"]
    budget_id: str
    entity: str
    platform: Platform
    before: float
    after: float
    mode: Literal["MOCK", "LIVE"]
    request_id: str
    note: str


class ScenarioItem(BaseModel):
    key: Literal["DEMO_01", "S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9", "S10", "S11", "S12"]
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1)
    status: Literal["AVAILABLE", "NOT_BUILT"]
    missing_modules: list[str]


class ScenarioCatalog(BaseModel):
    stage: str = Field(min_length=1)
    note: str = Field(min_length=1)
    items: list[ScenarioItem] = Field(max_length=13)


# ---- request bodies ---------------------------------------------------------------------------------------------
class ApproveBody(BaseModel):
    decision_hash: str
    execute: bool = True


class RejectBody(BaseModel):
    decision_hash: str
    reason: str = Field(min_length=1)


class AnomalyStatusBody(BaseModel):
    status: Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]
    reason: str = ""


class OptimizerRunBody(BaseModel):
    objective: Objective = "PROFIT"


class RecoveryBody(BaseModel):
    decision_hash: str
    reason: str = Field(min_length=1)
    final_resolution: Literal["COMPENSATED", "ACCEPTED_PARTIAL", "BLOCKED"] | None = None


# ---- insight / management / policy screens (insight-contracts.ts, management-contracts.ts, policy-contracts.ts,
# completion-contracts.ts) ------------------------------------------------------------------------------------------
class Citation(BaseModel):
    label: str
    href: str


class Opportunity(BaseModel):
    id: str
    budget_id: str
    entity: str
    platform: Platform
    score: float | None
    status: Literal["FEASIBLE", "BLOCKED", "NOT_ESTIMABLE"]
    reason: str
    marginal_caa: float | None
    delta_budget: float = Field(ge=0)
    decision_id: str | None
    evidence: list[Citation]
    provenance: list[Provenance]


class CurvePoint(BaseModel):
    budget: float = Field(ge=0)
    contribution: float


class Curve(BaseModel):
    budget_id: str
    label: str
    unit: str
    points: list[CurvePoint]
    reason: str


class Fatigue(BaseModel):
    creative_id: str
    name: str
    entity: str
    ctr_change: float
    frequency: float = Field(ge=0)
    status: Literal["REVIEW", "STABLE"]
    reason: str


class CreativeScore(BaseModel):
    status: Literal["AVAILABLE", "NOT_ESTIMABLE"]
    score: float | None
    explanation: str


class CalibrationUpdate(BaseModel):
    outcome_id: str
    decision_id: str
    before: float
    after: float
    at: str


class CalibrationOut(BaseModel):
    factor: float
    updates: list[CalibrationUpdate]
    note: str


class AccuracyOut(BaseModel):
    sample_count: int = Field(ge=0)
    mae: float | None
    note: str


class UpliftRow(BaseModel):
    strategy: str
    realized_caa: float
    spend: float = Field(ge=0)
    constraint_breaches: int = Field(ge=0)


class UpliftOut(BaseModel):
    status: Literal["AVAILABLE", "NOT_AVAILABLE"]
    rows: list[UpliftRow]
    note: str


class ModelOut(BaseModel):
    name: str
    version: str
    status: Literal["CHAMPION", "CHALLENGER", "NOT_AVAILABLE"]
    trained_at: str | None
    note: str


class FeedbackOut(BaseModel):
    outcome_id: str
    decision_id: str
    eligible: bool
    reason: str


class ModelCheck(BaseModel):
    id: str
    label: str
    passed: bool
    detail: str


class ModelMetric(BaseModel):
    label: str
    candidate: float | None
    champion: float | None
    baseline: float | None
    unit: str


class ModelDetail(BaseModel):
    name: str
    version: str
    registry_revision: str
    role: Literal["CHAMPION", "CANDIDATE", "RETIRED"]
    artifact_hash: str | None
    training_snapshot_hash: str | None
    trained_at: str | None
    rollback_version: str | None
    promotion_reason: str | None
    checks: list[ModelCheck]
    metrics: list[ModelMetric]
    allowed_actions: list[Literal["PROMOTE", "ROLLBACK"]]
    note: str


class ReadinessCheck(BaseModel):
    id: str
    label: str
    passed: bool | None
    detail: str


class Readiness(BaseModel):
    eligible: bool
    executed_decisions: int = Field(ge=0)
    measured_outcomes: int = Field(ge=0)
    independent_worlds: int = Field(ge=0)
    wilson_lower: float | None
    reliability: Literal["PASS", "FAIL", "INCONCLUSIVE", "UNAVAILABLE"]
    guardrail_violations: int = Field(ge=0)
    checks: list[ReadinessCheck]
    note: str


Mode = Literal["OBSERVE", "APPROVE", "SIMULATION_AUTONOMOUS", "PRODUCTION_AUTONOMOUS"]


class ChannelPolicy(BaseModel):
    channel: Literal["Meta", "Google", "TikTok", "Amazon"]
    mode: Mode
    execution_mode: Literal["MOCK", "LIVE"]
    test_account: bool
    serves_ads: bool
    allowed_modes: list[Mode]
    simulation: Readiness
    production: Readiness
    note: str


class PolicyOut(BaseModel):
    policy_version: str
    revision: str
    channels: list[ChannelPolicy] = Field(min_length=1)
    note: str


class PolicyChange(BaseModel):
    field: str
    before: str
    after: str


class PolicyVersion(BaseModel):
    version: str
    at: str
    actor: str
    reason: str
    changes: list[PolicyChange]


class PolicyHistory(BaseModel):
    status: Literal["AVAILABLE", "NOT_AVAILABLE"]
    note: str
    versions: list[PolicyVersion]


class WorkspaceObjective(BaseModel):
    workspace_id: str
    objective: Objective
    revision: str
    supported_objectives: list[Objective] = Field(min_length=1)
    can_change: bool
    note: str


class TimelineEntry(BaseModel):
    id: str
    at: str
    label: str
    detail: str
    href: str


class ReplayOut(BaseModel):
    status: Literal["VERIFIED", "MISMATCH", "UNAVAILABLE"]
    expected_hash: str
    actual_hash: str | None
    message: str


class SnapshotOut(BaseModel):
    decision: Decision
    evidence: EvidenceOut
    notice: str


class ArchiveArtifact(BaseModel):
    id: str
    kind: str
    label: str
    hash: str | None
    href: str | None
    status: Literal["PRESENT", "MISSING"]


class ArchiveStep(BaseModel):
    id: str
    at: str
    label: str
    detail: str
    artifact_id: str | None


class ArchiveOut(BaseModel):
    decision_id: str
    decision_hash: str
    snapshot_id: str
    environment_fingerprint: str | None
    code_sha: str | None
    lock_hash: str | None
    seed: int | None
    status: Literal["AVAILABLE", "UNAVAILABLE"]
    note: str
    artifacts: list[ArchiveArtifact]
    steps: list[ArchiveStep]


class StrategyRow(BaseModel):
    name: str
    allocated: float = Field(ge=0)
    estimate: Expected
    reason: str


class ConfidenceRow(BaseModel):
    label: str
    value: float = Field(ge=0, le=1)
    meaning: str


class Comparison(BaseModel):
    decision_id: str
    decision_hash: str
    status: Literal["AVAILABLE", "NOT_ESTIMABLE"]
    strategies: list[StrategyRow]
    alternatives: list[dict]
    confidence: list[ConfidenceRow]
    note: str


class Workspace(BaseModel):
    id: str
    name: str
    currency: Literal["INR"]
    timezone: Literal["Asia/Kolkata"]


class WorkspaceList(BaseModel):
    active_id: str
    items: list[Workspace] = Field(min_length=1)


class Envelope(BaseModel):
    """status + note + an empty payload: the contracts' NOT_AVAILABLE states for later-stage features."""
    status: Literal["AVAILABLE", "NOT_AVAILABLE"]
    note: str


class ShadowOut(Envelope):
    records: list[dict]


class QualificationOut(Envelope):
    pools: list[dict]


class EvalReportOut(Envelope):
    report: dict | None


class SimulateBody(BaseModel):
    decision_hash: str


class CopilotTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class CopilotBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[CopilotTurn] = Field(default_factory=list, max_length=40)


# ---- Stage 2 wiring --------------------------------------------------------------------------------------------------
class ObjectiveChangeBody(BaseModel):
    workspace_id: str
    revision: str
    objective: Objective
    reason: str = Field(min_length=10, max_length=500)


class ModelActionBody(BaseModel):
    version: str
    registry_revision: str
    artifact_hash: str | None = None
    reason: str = Field(min_length=10, max_length=500)


class ModelActionOut(BaseModel):
    name: str
    version: str
    registry_revision: str
    message: str


class ChooseAlternativeBody(BaseModel):
    decision_hash: str


class NarrativeSentence(BaseModel):
    text: str
    atom_ids: list[str]
    evidence_ids: list[str]
    claim_levels: list[str]


class NextStep(BaseModel):
    kind: Literal["VIEW_DECISION", "APPROVE_REVIEW", "RECONCILE", "INVESTIGATE", "NONE"]
    ref_id: str | None


class NarrativeOut(BaseModel):
    """A guarded narrative (spec §11): LLM prose whose every number, direction and entity was checked against the
    deterministic claim atoms, or the offline template. `badge` is never a blanket "verified"."""
    kind: Literal["incident", "decision", "brief"]
    ref_id: str
    headline: str
    sentences: list[NarrativeSentence]
    next_step: NextStep
    source: str
    badge: str
    fallback_reason: str | None = None
    not_estimable_reason: str | None = None


class PlatformHealth(BaseModel):
    platform: str
    mode: Literal["MOCK", "LIVE"]
    ok: bool
    label: str
    reason: str | None = None
    checks: dict[str, bool] = {}


class SimDivergence(BaseModel):
    leg_id: str
    budget_id: str
    amount: float
    state: Literal["MIRROR_PENDING", "MIRROR_FAILED"]


class PlatformsOut(BaseModel):
    platforms: list[PlatformHealth]
    sim_out_of_sync: list[SimDivergence]


class PolicyModeBody(BaseModel):
    policy_version: str
    revision: str
    channel: Literal["Meta", "Google", "TikTok", "Amazon"]
    mode: Literal["OBSERVE", "APPROVE", "SIMULATION_AUTONOMOUS", "PRODUCTION_AUTONOMOUS"]
    reason: str = Field(min_length=10, max_length=500)


class SqlBody(BaseModel):
    query: str = Field(min_length=3, max_length=4000)
    limit: int = Field(500, ge=1, le=500)


class SqlResult(BaseModel):
    query: str
    columns: list[str] = Field(min_length=1, max_length=40)
    rows: list[list[str | float | int | bool | None]]
    truncated: bool
    elapsed_ms: float = Field(ge=0)
    as_of: str


class ImportAck(BaseModel):
    import_id: str
    status: Literal["STAGED", "IMPORTED"]
    row_count: int = Field(ge=0)
    message: str


class UploadBody(BaseModel):
    type: Literal["ads", "inventory", "margins"]
    records: list[dict] = Field(min_length=1, max_length=5000)
    source_currency: str
    source_timezone: str


class ConfirmBody(BaseModel):
    import_id: str
    mapping: dict[str, str]


class MappingSuggestBody(BaseModel):
    type: Literal["ads", "inventory", "margins"]
    headers: list[str] = Field(min_length=1, max_length=200)


class CreativeScoreBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
