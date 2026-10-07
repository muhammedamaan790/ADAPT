"""Wire models of the Stage 1 API (C6). They mirror the frontend's zod schemas field for field
(`web/src/api/contracts.ts`, `workbench-contracts.ts`), so FastAPI validates every response against the contract
before it leaves the server and the generated OpenAPI is what `npm run types:generate` consumes."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_serializer

Provenance = Literal["PUBLIC-SAMPLE", "CALIBRATED", "SIMULATED", "LIVE"]
Objective = Literal["PROFIT", "GROWTH", "ACQUISITION", "INVENTORY_CLEARANCE", "MARGIN_PROTECTION", "BALANCED"]
Platform = Literal["Meta", "Google"]


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
    kind: Literal["PROJECTED_SHORTFALL"]
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


class Decision(OmitNone):
    omit_if_none = ("valuation_status", "follows")
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
