import { z } from 'zod';
import { objectiveSchema, sourceSchema } from './contracts';

const text = z.string().min(1);
const count = z.number().int().nonnegative();
const fraction = z.number().finite().min(0).max(1);
export const sourceChecksSchema = z.array(
  sourceSchema.extend({
    as_of: text,
    newest_date: z.string().nullable(),
    age_hours: z.number().finite().nonnegative().nullable(),
    freshness_score: fraction,
    completeness: fraction,
    consistency: fraction,
    hard_failures: z.array(text),
    checks: z.array(z.object({ id: text, passed: z.boolean(), detail: text })),
  }),
);
export const workspaceObjectiveSchema = z
  .object({
    workspace_id: text,
    objective: objectiveSchema,
    revision: text,
    supported_objectives: z.array(objectiveSchema).min(1),
    can_change: z.boolean(),
    note: text,
  })
  .refine(
    (v) =>
      v.supported_objectives.includes(v.objective) &&
      new Set(v.supported_objectives).size === v.supported_objectives.length,
    'Current objective must be supported; supported objectives must be unique.',
  );
export const policyHistorySchema = z
  .object({
    status: z.enum(['AVAILABLE', 'NOT_AVAILABLE']),
    note: text,
    versions: z
      .array(
        z.object({
          version: text,
          at: z.string().datetime(),
          actor: text,
          reason: text,
          changes: z.array(z.object({ field: text, before: z.string(), after: z.string() })),
        }),
      )
      .max(500),
  })
  .refine((v) => v.status !== 'NOT_AVAILABLE' || v.versions.length === 0)
  .refine((v) => new Set(v.versions.map((r) => r.version)).size === v.versions.length);
const region = z
  .object({
    name: z.enum(['LOW', 'MID', 'HIGH']),
    total: count,
    successes: count,
    wilson_lower: fraction.nullable(),
    wilson_upper: fraction.nullable(),
  })
  .superRefine((v, ctx) => {
    if (
      v.successes > v.total ||
      (v.total === 0
        ? v.wilson_lower !== null || v.wilson_upper !== null
        : v.wilson_lower === null || v.wilson_upper === null || v.wilson_lower > v.wilson_upper)
    )
      ctx.addIssue({
        code: 'custom',
        message: 'Confidence interval and observed counts disagree.',
      });
  });
export const confidenceBandsSchema = z
  .object({
    status: z.enum(['AVAILABLE', 'NOT_AVAILABLE']),
    note: text,
    pools: z
      .array(
        z.object({
          world: z.enum(['SIMULATED', 'REAL']),
          model_version: text,
          evaluated_at: z.string().datetime(),
          outcome_definition: text,
          scope: z.enum(['WARMUP', 'HELD_OUT']),
          world_ids: z.array(text).min(1),
          regions: z
            .array(region)
            .length(3)
            .refine((v) => new Set(v.map((r) => r.name)).size === 3),
        }),
      )
      .max(20),
  })
  .refine((v) => v.status !== 'NOT_AVAILABLE' || v.pools.length === 0);
const cell = z.union([z.string().max(10000), z.number().finite(), z.boolean(), z.null()]);
export const sqlResultSchema = z
  .object({
    query: text.max(4000),
    columns: z.array(text).min(1).max(40),
    rows: z.array(z.array(cell)).max(500),
    truncated: z.boolean(),
    elapsed_ms: z.number().finite().nonnegative(),
    as_of: z.string().datetime(),
  })
  .refine(
    (v) =>
      v.rows.every((r) => r.length === v.columns.length) &&
      new Set(v.columns).size === v.columns.length,
    'SQL result columns and rows must agree.',
  );
