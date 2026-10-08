import { z } from 'zod';

// Provisional Stage 1 wire schemas. Replace/align with C6's OpenAPI when available.
const finite = z.number().finite();
const money = finite;
export const objectiveSchema = z.enum([
  'PROFIT',
  'GROWTH',
  'ACQUISITION',
  'INVENTORY_CLEARANCE',
  'MARGIN_PROTECTION',
  'BALANCED',
]);
export type Objective = z.infer<typeof objectiveSchema>;
export const provenanceSchema = z.enum([
  'PUBLIC-SAMPLE',
  'CALIBRATED',
  'SIMULATED',
  'LIVE',
  'UPLOADED',
]);
export const metricSchema = z.object({
  key: z.string(),
  label: z.string(),
  value: finite.nullable(),
  format: z.enum(['money', 'ratio', 'count']),
  change: finite.nullable(),
  reason: z.string().optional(),
  formula: z.string(),
  source: z.string(),
  available_at: z.string(),
  provenance_inputs: z.array(provenanceSchema),
});
export const pointSchema = z.object({ date: z.string(), actual: finite, baseline: finite });
export const sourceSchema = z.object({
  id: z.string(),
  name: z.string(),
  kind: z.string(),
  score: finite.min(0).max(100),
  status: z.enum(['GREEN', 'YELLOW', 'RED']),
  freshness: z.string(),
  provenance: provenanceSchema,
});
export const attentionSchema = z.object({
  id: z.string(),
  decision_id: z.string().nullable(),
  kind: z.enum(['incident', 'opportunity', 'inventory', 'outcome']),
  title: z.string(),
  description: z.string(),
  impact: money,
  label: z.string(),
});
export const overviewSchema = z.object({
  workspace: z.string(),
  decision_ts: z.string(),
  world_day: finite.int(),
  scenario: z.string(),
  brief: z.string(),
  metrics: z.array(metricSchema),
  sources: z.array(sourceSchema),
  attention: z.array(attentionSchema),
  series: z.array(pointSchema),
  loop: z.array(z.object({ label: z.string(), state: z.enum(['complete', 'current', 'waiting']) })),
  calibration: finite,
  counts: z.object({ success: finite, neutral: finite, failed: finite, inconclusive: finite }),
});
export const checkSchema = z.object({
  id: z.string(),
  label: z.string(),
  passed: z.boolean(),
  detail: z.string(),
});
// TikTok and Amazon are the Stage 2 simulated channels (opt-in per world).
export const platformSchema = z.enum(['Meta', 'Google', 'TikTok', 'Amazon']);
export const legSchema = z.object({
  platform: platformSchema,
  entity: z.string(),
  budget_id: z.string(),
  before: money.nonnegative(),
  after: money.nonnegative(),
});
export const decisionSchema = z.object({
  decision_id: z.string(),
  title: z.string(),
  summary: z.string(),
  class: z.enum(['OPTIMIZATION', 'SAFETY', 'OPERATIONAL', 'EXPLORATION']),
  type: z.string(),
  objective: objectiveSchema,
  status: z.enum([
    'DRAFT',
    'PENDING_APPROVAL',
    'APPROVED',
    'REJECTED',
    'EXPIRED',
    'SUPERSEDED',
    'EXECUTING',
    'EXECUTED',
    'PARTIAL',
    'BLOCKED',
  ]),
  trigger: z.object({ anomaly_id: z.string().nullable(), opportunity_id: z.string().nullable() }),
  legs: z.array(legSchema),
  expected: z.object({
    p10: money,
    p50: money,
    p90: money,
    prob_loss: finite.min(0).max(1),
    delta_net_revenue: money,
    raw_pred: money,
    calibrated_pred: money,
  }),
  inventory_risk_after: z.object({
    // PROJECTED_SHORTFALL: units short (Stage 1). STOCKOUT_PROBABILITY: model P(stockout) in [0, 1] (Stage 2 NB2).
    kind: z.enum(['PROJECTED_SHORTFALL', 'STOCKOUT_PROBABILITY']),
    by_sku: z.record(z.string(), finite.nonnegative()),
  }),
  unallocated: money.nonnegative(),
  reserve_floor: money.nonnegative(),
  budget_ceiling: money.nonnegative(),
  cost_of_inaction_7d: money,
  checks: z.array(checkSchema).min(1),
  evidence_ids: z.array(z.string()),
  why_not: z.array(
    z.object({ entity: z.string(), rule_id: z.string(), reason: z.string(), metric: z.string() }),
  ),
  snapshot_id: z.string(),
  decision_hash: z.string(),
  policy_version: z.string(),
  valuation_status: z.enum(['AVAILABLE', 'NOT_ESTIMABLE']).optional(),
  follows: z.string().optional(),
  provenance_inputs: z.array(provenanceSchema),
  created_at: z.string(),
  horizon_days: finite.int().positive(),
  // Stage 3: the confidence index (spec §8.4; informational, never authorizes autonomy by itself) and the autonomy
  // verdict when the decision's channels are in Simulation Autonomous mode
  confidence: z
    .object({
      overall: finite.min(0).max(1),
      band: z.enum(['HIGH', 'MEDIUM', 'LOW']),
      region: z.enum(['R1', 'R2', 'R3']),
      data_quality: finite.min(0).max(1),
      prediction_quality: finite.min(0).max(1),
      track_record: finite.min(0).max(1),
      constraint_coverage: z.boolean(),
    })
    .optional(),
  autonomy: z
    .object({
      at: z.string(),
      result: z.enum(['EXECUTED', 'DOWNGRADED']),
      gates: z.array(z.object({ id: z.string(), passed: z.boolean(), detail: z.string() })),
    })
    .optional(),
});
export const evidenceSchema = z.object({
  decision_id: z.string(),
  chart: z.array(pointSchema),
  chart_metric: z.string(),
  decomposition_kind: z.enum(['ROAS', 'NOT_APPLICABLE']),
  decomposition: z.array(z.object({ label: z.string(), value: finite })),
  decomposition_total: finite,
  drivers: z.array(
    z.object({
      id: z.string(),
      title: z.string(),
      score: finite.min(0).max(1),
      level: z.enum(['ACCOUNTING IDENTITY', 'PROBABLE DRIVER', 'WEAK EVIDENCE']),
      detail: z.string(),
      observations: z.array(z.string()),
      source: z.string(),
      available_at: z.string(),
    }),
  ),
});
export const executionSchema = z.object({
  execution_id: z.string(),
  decision_id: z.string(),
  state: z.enum([
    'PENDING',
    'EXECUTING',
    'SUCCEEDED',
    'PARTIAL',
    'COMPENSATING',
    'COMPENSATED',
    'COMPENSATION_FAILED',
    'HUMAN_RESOLUTION_REQUIRED',
    'RESOLVED_MANUALLY',
    'ACCEPTED_PARTIAL',
    'BLOCKED',
  ]),
  legs: z.array(
    legSchema.extend({
      mode: z.enum(['MOCK', 'LIVE']),
      external_state: z.enum([
        'PLANNED',
        'PREREAD_OK',
        'SENT',
        'VERIFIED',
        'UNKNOWN',
        'FAILED',
        'CONFLICT',
        'RECONCILED_VERIFIED',
        'ABANDONED',
      ]),
      sim_sync_state: z.enum([
        'NOT_REQUIRED',
        'MIRROR_PENDING',
        'MIRRORED',
        'MIRROR_FAILED',
        'MIRROR_RESOLVED_MANUALLY',
      ]),
      read_back_budget: money.nullable().optional(),
    }),
  ),
  started_at: z.string(),
  detail: z.string(),
});
export const outcomeSchema = z.object({
  outcome_id: z.string(),
  decision_id: z.string(),
  world: z.enum(['SIMULATED', 'REAL']),
  class: z.string(),
  verdict: z.enum(['SUCCESS', 'NEUTRAL', 'FAILED', 'INCONCLUSIVE']),
  predicted: money,
  measured: money,
  counterfactual: money,
  factor_before: finite,
  factor_after: finite,
  matured_at: z.string(),
  method: z.string(),
  calibration_applied: z.boolean(),
  calibration_note: z.string().optional(),
});
export const eventSchema = z.object({
  id: z.string(),
  at: z.string(),
  kind: z.string(),
  message: z.string(),
  decision_id: z.string().nullable(),
});
export const scenarioKeys = [
  'DEMO_01',
  'S1',
  'S2',
  'S3',
  'S4',
  'S5',
  'S6',
  'S7',
  'S8',
  'S9',
  'S10',
  'S11',
  'S12',
] as const;
export const fixtureScenarioKeys: readonly string[] = [
  'DEMO_01',
  'S1',
  'S2',
  'S3',
  'S4',
  'S5',
  'S7',
];
export type ScenarioKey = (typeof scenarioKeys)[number];
export type Overview = z.infer<typeof overviewSchema>;
export type Decision = z.infer<typeof decisionSchema>;
export type Evidence = z.infer<typeof evidenceSchema>;
export type Execution = z.infer<typeof executionSchema>;
export type Outcome = z.infer<typeof outcomeSchema>;
export type AppEvent = z.infer<typeof eventSchema>;
export type Metric = z.infer<typeof metricSchema>;

// Guarded narrative (spec §11): prose whose numbers, directions and entities were checked against claim atoms.
export const narrativeSchema = z.object({
  kind: z.enum(['incident', 'decision', 'brief']),
  ref_id: z.string(),
  headline: z.string(),
  sentences: z.array(
    z.object({
      text: z.string(),
      atom_ids: z.array(z.string()),
      evidence_ids: z.array(z.string()),
      claim_levels: z.array(z.string()),
    }),
  ),
  next_step: z.object({
    kind: z.enum(['VIEW_DECISION', 'APPROVE_REVIEW', 'RECONCILE', 'INVESTIGATE', 'NONE']),
    ref_id: z.string().nullable(),
  }),
  source: z.string(),
  badge: z.string(),
  fallback_reason: z.string().nullable().optional(),
  not_estimable_reason: z.string().nullable().optional(),
});
export const platformsSchema = z.object({
  platforms: z.array(
    z.object({
      platform: z.string(),
      mode: z.enum(['MOCK', 'LIVE']),
      ok: z.boolean(),
      label: z.string(),
      reason: z.string().nullable().optional(),
      checks: z.record(z.string(), z.boolean()),
    }),
  ),
  sim_out_of_sync: z.array(
    z.object({
      leg_id: z.string(),
      budget_id: z.string(),
      amount: finite,
      state: z.enum(['MIRROR_PENDING', 'MIRROR_FAILED']),
    }),
  ),
});
export type Narrative = z.infer<typeof narrativeSchema>;
export type Platforms = z.infer<typeof platformsSchema>;
