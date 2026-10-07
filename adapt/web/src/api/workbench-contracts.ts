import { z } from 'zod';
import {
  checkSchema,
  decisionSchema,
  legSchema,
  provenanceSchema,
  objectiveSchema,
} from './contracts';

export const anomalySchema = z.object({
  anomaly_id: z.string(),
  title: z.string(),
  entity: z.string(),
  platform: z.enum(['Meta', 'Google']),
  metric: z.string(),
  kind: z.enum(['EFFICIENCY', 'TRACKING', 'BUDGET_CHANGE']),
  status: z.enum(['OPEN', 'ACKNOWLEDGED', 'RESOLVED']),
  direction: z.enum(['UP', 'DOWN', 'FLAT']),
  actual: z.number().finite(),
  baseline: z.number().finite(),
  change: z.number().finite(),
  impact: z.number().finite(),
  impact_label: z.string(),
  detected_at: z.string(),
  decision_id: z.string().nullable(),
  driver: z.string(),
  provenance_inputs: z.array(provenanceSchema),
  gates: z.array(checkSchema),
  causal: z.object({
    status: z.enum(['ESTIMABLE', 'NOT_ESTIMABLE']),
    reason: z.string(),
    effect_pct: z.number().finite().nullable(),
    lower_pct: z.number().finite().nullable(),
    upper_pct: z.number().finite().nullable(),
    assumptions: z.array(z.string()),
  }),
  resolution_reason: z.string().nullable(),
});
export type Anomaly = z.infer<typeof anomalySchema>;
export { objectiveSchema } from './contracts';
export const optimizerContextSchema = z.object({
  decision_id: z.string().nullable(),
  decision_hash: z.string().nullable(),
  supported_objectives: z.array(objectiveSchema),
  objective: objectiveSchema.default('PROFIT'),
  budget_ceiling: z.number().finite().nonnegative(),
  reserve_floor: z.number().finite().nonnegative(),
  max_daily_change: z.number().min(0).max(1),
  policy_version: z.string(),
  horizon_days: z.number().int().positive(),
  campaigns: z.array(
    legSchema.extend({
      margin: z.number().finite(),
      roas: z.number().finite(),
      marginal_caa: z.number().finite().nullable(),
      inventory_gate: z.enum(['ALLOW', 'LIMIT', 'BLOCK']),
      min_budget: z.number().finite().nonnegative(),
      max_budget: z.number().finite().nonnegative(),
      note: z.string(),
    }),
  ),
});
export type OptimizerContext = z.infer<typeof optimizerContextSchema>;
export const allocationInputSchema = z.object({
  decision_id: z.string(),
  decision_hash: z.string(),
  objective: objectiveSchema,
  policy_version: z.string(),
  legs: z
    .array(z.object({ budget_id: z.string(), after: z.number().finite().int().nonnegative() }))
    .min(1),
});
export type AllocationInput = z.infer<typeof allocationInputSchema>;
export const evaluationSchema = z
  .object({
    decision_id: z.string(),
    decision_hash: z.string(),
    objective: objectiveSchema,
    objective_value: z
      .object({
        value: z.number().finite(),
        label: z.string().min(1),
        unit: z.enum(['INR', 'CUSTOMERS', 'SCORE']),
      })
      .nullable()
      .default(null),
    allocated: z.number().finite().nonnegative(),
    unallocated: z.number().finite().nonnegative(),
    checks: z.array(checkSchema).min(1),
    estimate_status: z.enum(['AVAILABLE', 'NOT_ESTIMABLE']),
    estimate: decisionSchema.shape.expected.nullable(),
    explanation: z.string(),
  })
  .refine((v) => (v.estimate_status === 'AVAILABLE') === (v.estimate !== null), {
    message: 'Estimate availability must agree with its payload',
  });
export type AllocationEvaluation = z.infer<typeof evaluationSchema>;
export const ledgerSchema = z.object({
  ledger_id: z.string(),
  execution_id: z.string(),
  decision_id: z.string(),
  at: z.string(),
  action: z.enum(['SET_BUDGET', 'VERIFY', 'RESTORE_SETTINGS', 'RECONCILE']),
  budget_id: z.string(),
  entity: z.string(),
  platform: z.enum(['Meta', 'Google']),
  before: z.number().finite(),
  after: z.number().finite(),
  mode: z.enum(['MOCK', 'LIVE']),
  request_id: z.string(),
  note: z.string(),
});
export type LedgerEntry = z.infer<typeof ledgerSchema>;
export const healthSchema = z.object({
  status: z.enum(['ok', 'degraded']),
  version: z.string(),
  workspace: z.string(),
  database: z.enum(['ok', 'error']),
  execution_modes: z.record(z.string(), z.enum(['mock', 'live'])),
  llm_mode: z.enum(['groq', 'offline']),
});
export type BackendHealth = z.infer<typeof healthSchema>;
export type RecoveryAction = 'verify' | 'retry' | 'rollback' | 'reconcile';
export type RecoveryInput = {
  execution_id: string;
  action: RecoveryAction;
  reason: string;
  decision_hash: string;
  final_resolution?: 'COMPENSATED' | 'ACCEPTED_PARTIAL' | 'BLOCKED';
};
