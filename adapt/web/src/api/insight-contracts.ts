import { z } from 'zod';
import {
  checkSchema,
  decisionSchema,
  legSchema,
  sourceSchema,
  provenanceSchema,
} from './contracts';

const finite = z.number().finite();
export const appHref = z
  .string()
  .refine(
    (s) =>
      /^\/(decisions(?:\/[^/?#]+)?|anomalies|optimizer|executions|outcomes|learning|data|opportunities|scenarios|connection)(?:\?[^#]*)?$/.test(
        s,
      ),
    'Evidence links must point to a known app route.',
  );
export const citationSchema = z.object({ label: z.string(), href: appHref });
export const opportunitySchema = z.object({
  id: z.string(),
  budget_id: z.string(),
  entity: z.string(),
  platform: z.enum(['Meta', 'Google']),
  score: finite.nullable(),
  status: z.enum(['FEASIBLE', 'BLOCKED', 'NOT_ESTIMABLE']),
  reason: z.string(),
  marginal_caa: finite.nullable(),
  delta_budget: finite.nonnegative(),
  decision_id: z.string().nullable(),
  evidence: z.array(citationSchema),
  provenance: z.array(provenanceSchema),
});
export const curveSchema = z.object({
  budget_id: z.string(),
  label: z.string(),
  unit: z.string(),
  points: z.array(z.object({ budget: finite.nonnegative(), contribution: finite })),
  reason: z.string(),
});
export const fatigueSchema = z.object({
  creative_id: z.string(),
  name: z.string(),
  entity: z.string(),
  ctr_change: finite,
  frequency: finite.nonnegative(),
  status: z.enum(['REVIEW', 'STABLE']),
  reason: z.string(),
});
export const creativeScoreSchema = z
  .object({
    status: z.enum(['AVAILABLE', 'NOT_ESTIMABLE']),
    score: finite.min(0).max(1).nullable(),
    explanation: z.string(),
  })
  .refine((v) => (v.status === 'AVAILABLE') === (v.score !== null));
export const calibrationSchema = z.object({
  factor: finite,
  updates: z.array(
    z.object({
      outcome_id: z.string(),
      decision_id: z.string(),
      before: finite,
      after: finite,
      at: z.string(),
    }),
  ),
  note: z.string(),
});
export const accuracySchema = z.object({
  sample_count: z.number().int().nonnegative(),
  mae: finite.nonnegative().nullable(),
  note: z.string(),
});
export const upliftSchema = z.object({
  status: z.enum(['AVAILABLE', 'NOT_AVAILABLE']),
  rows: z.array(
    z.object({
      strategy: z.string(),
      realized_caa: finite,
      spend: finite.nonnegative(),
      constraint_breaches: z.number().int().nonnegative(),
    }),
  ),
  note: z.string(),
});
export const modelSchema = z.object({
  name: z.string(),
  version: z.string(),
  status: z.enum(['CHAMPION', 'CHALLENGER', 'NOT_AVAILABLE']),
  trained_at: z.string().nullable(),
  note: z.string(),
});
export const feedbackSchema = z.array(
  z.object({
    outcome_id: z.string(),
    decision_id: z.string(),
    eligible: z.boolean(),
    reason: z.string(),
  }),
);
export const dataHealthSchema = z.object({
  sources: z.array(sourceSchema),
  coverage: finite.min(0).max(1),
  unmapped: z.array(z.string()),
  note: z.string(),
});
export const reconciliationSchema = z.object({
  platform_revenue: finite,
  store_revenue: finite,
  attribution_excess: finite,
  note: z.string(),
  window_start: z.string().optional(),
  window_end: z.string().optional(),
  platforms: z
    .array(
      z.object({
        platform: z.string().min(1),
        platform_conversions: finite.nonnegative(),
        store_attributed_orders: z.number().int().nonnegative(),
        over_attribution: finite.nonnegative().nullable(),
        over_attribution_reason: z.string().nullable().optional(),
        session_click_ratio: finite.nonnegative().nullable(),
      }),
    )
    .optional(),
});
export const importAckSchema = z.object({
  import_id: z.string(),
  status: z.enum(['STAGED', 'IMPORTED']),
  row_count: z.number().int().nonnegative(),
  message: z.string(),
});
export const comparisonSchema = z.object({
  decision_id: z.string(),
  decision_hash: z.string(),
  status: z.enum(['AVAILABLE', 'NOT_ESTIMABLE']),
  strategies: z.array(
    z.object({
      name: z.string(),
      allocated: finite.nonnegative(),
      estimate: decisionSchema.shape.expected,
      reason: z.string(),
    }),
  ),
  alternatives: z.array(
    z.object({
      id: z.string(),
      name: z.string(),
      reason: z.string(),
      legs: z.array(legSchema),
      estimate: decisionSchema.shape.expected,
      checks: z.array(checkSchema),
    }),
  ),
  confidence: z.array(
    z.object({ label: z.string(), value: finite.min(0).max(1), meaning: z.string() }),
  ),
  note: z.string(),
});
export const timelineSchema = z.array(
  z.object({
    id: z.string(),
    at: z.string(),
    label: z.string(),
    detail: z.string(),
    href: appHref,
  }),
);
export const replaySchema = z
  .object({
    status: z.enum(['VERIFIED', 'MISMATCH', 'UNAVAILABLE']),
    expected_hash: z.string(),
    actual_hash: z.string().nullable(),
    message: z.string(),
  })
  .refine((v) =>
    v.status === 'UNAVAILABLE'
      ? v.actual_hash === null
      : v.actual_hash !== null && (v.status === 'VERIFIED') === (v.actual_hash === v.expected_hash),
  );
export const copilotReplySchema = z.object({
  text: z.string().min(1),
  evidence: z.array(citationSchema),
  mode: z.enum(['TEMPLATE', 'LLM']),
});
export type Opportunity = z.infer<typeof opportunitySchema>;
export type Comparison = z.infer<typeof comparisonSchema>;
export type CopilotReply = z.infer<typeof copilotReplySchema>;
