import { z } from 'zod';
import { appHref } from './insight-contracts';
const id = z.string().regex(/^[a-zA-Z0-9_-]{1,80}$/);
const finite = z.number().finite();
export const workspaceSchema = z.object({
  id,
  name: z.string().trim().min(2).max(60),
  currency: z.literal('INR'),
  timezone: z.literal('Asia/Kolkata'),
});
export const workspaceListSchema = z
  .object({ active_id: id, items: z.array(workspaceSchema).min(1).max(20) })
  .refine(
    (v) =>
      new Set(v.items.map((w) => w.id)).size === v.items.length &&
      v.items.some((w) => w.id === v.active_id),
    'Workspace IDs must be unique and include the active workspace.',
  );
export const modelDetailSchema = z
  .object({
    name: z.string().min(1),
    version: z.string().min(1),
    registry_revision: z.string().min(1),
    role: z.enum(['CHAMPION', 'CANDIDATE', 'RETIRED']),
    artifact_hash: z
      .string()
      .regex(/^[a-f0-9]{64}$/)
      .nullable(),
    training_snapshot_hash: z.string().min(1).nullable(),
    trained_at: z.string().datetime().nullable(),
    rollback_version: z.string().min(1).nullable(),
    promotion_reason: z.string().nullable(),
    checks: z.array(
      z.object({ id: z.string(), label: z.string(), passed: z.boolean(), detail: z.string() }),
    ),
    metrics: z.array(
      z.object({
        label: z.string(),
        candidate: finite.nullable(),
        champion: finite.nullable(),
        baseline: finite.nullable(),
        unit: z.string(),
      }),
    ),
    allowed_actions: z.array(z.enum(['PROMOTE', 'ROLLBACK'])),
    note: z.string(),
  })
  .refine(
    (v) =>
      !v.allowed_actions.includes('PROMOTE') ||
      (v.role === 'CANDIDATE' &&
        v.artifact_hash !== null &&
        v.checks.length > 0 &&
        v.checks.every((c) => c.passed)),
    'Promotable candidates require an artifact and passing backend gates.',
  )
  .refine(
    (v) =>
      !v.allowed_actions.includes('ROLLBACK') ||
      (v.role === 'CHAMPION' && v.rollback_version !== null),
    'Rollback requires a recorded previous champion.',
  );
export const modelActionSchema = z.object({
  name: z.string(),
  version: z.string(),
  registry_revision: z.string(),
  message: z.string(),
});
export const strategies = ['safe-static', 'safe-contribution', 'adapt', 'oracle'] as const;
export const evaluationReportSchema = z
  .object({
    report_id: z.string().min(1),
    generated_at: z.string().datetime(),
    code_sha: z.string().min(1),
    seeds: z.array(z.number().int().min(0).max(2147483647)).min(1).max(100),
    horizon_days: z.number().int().min(1).max(365),
    budget_ceiling: finite.positive(),
    reserve_floor: finite.nonnegative(),
    currency: z.literal('INR'),
    common_random_numbers: z.boolean(),
    feasibility_envelope: z.string().min(1),
    fairness_statement: z.string().min(1),
    rows: z
      .array(
        z.object({
          seed: z.number().int().nonnegative(),
          strategy: z.enum(strategies),
          realized_caa: finite,
          spend: finite.nonnegative(),
          stock_risk_days: z.number().int().nonnegative(),
          constraint_breaches: z.number().int().nonnegative(),
          forced_interventions: z.number().int().nonnegative(),
        }),
      )
      .max(400),
    // headline results from scripts/run_eval.py: primary paired CI, oracle capture, detection, diagnosis, safety
    summary: z.record(z.string(), z.unknown()).optional(),
  })
  .strict()
  .superRefine((r, ctx) => {
    if (r.reserve_floor > r.budget_ceiling)
      ctx.addIssue({ code: 'custom', message: 'Reserve exceeds budget ceiling.' });
    if (new Set(r.seeds).size !== r.seeds.length)
      ctx.addIssue({ code: 'custom', message: 'Duplicate report seeds.' });
    const keys = new Set<string>();
    for (const row of r.rows) {
      const key = `${row.seed}:${row.strategy}`;
      if (keys.has(key) || !r.seeds.includes(row.seed) || row.stock_risk_days > r.horizon_days)
        ctx.addIssue({
          code: 'custom',
          message: 'Duplicate, unexpected or inconsistent strategy row.',
        });
      keys.add(key);
    }
    for (const seed of r.seeds)
      for (const strategy of strategies)
        if (!keys.has(`${seed}:${strategy}`))
          ctx.addIssue({ code: 'custom', message: `Missing paired row ${seed}:${strategy}.` });
  });
export const evaluationEnvelopeSchema = z
  .object({
    status: z.enum(['AVAILABLE', 'NOT_AVAILABLE']),
    report: evaluationReportSchema.nullable(),
    note: z.string(),
  })
  .refine(
    (v) => (v.status === 'AVAILABLE') === (v.report !== null),
    'Availability must match the supplied report.',
  );
export const archiveSchema = z
  .object({
    decision_id: id,
    decision_hash: z.string().min(1),
    snapshot_id: z.string().min(1),
    environment_fingerprint: z.string().min(1).nullable(),
    code_sha: z.string().min(1).nullable(),
    lock_hash: z.string().min(1).nullable(),
    seed: z.number().int().min(0).max(2147483647).nullable(),
    status: z.enum(['AVAILABLE', 'UNAVAILABLE']),
    note: z.string(),
    artifacts: z.array(
      z.object({
        id: z.string().min(1),
        kind: z.string().min(1),
        label: z.string().min(1),
        hash: z.string().min(1).nullable(),
        href: appHref.nullable(),
        status: z.enum(['PRESENT', 'MISSING']),
      }),
    ),
    steps: z.array(
      z.object({
        id: z.string().min(1),
        at: z.string().datetime(),
        label: z.string(),
        detail: z.string(),
        artifact_id: z.string().nullable(),
      }),
    ),
  })
  .refine(
    (v) =>
      new Set(v.artifacts.map((a) => a.id)).size === v.artifacts.length &&
      new Set(v.steps.map((s) => s.id)).size === v.steps.length &&
      v.steps.every(
        (s) => s.artifact_id === null || v.artifacts.some((a) => a.id === s.artifact_id),
      ),
    'Replay steps must reference a unique supplied artifact.',
  )
  .refine(
    (v) =>
      v.status !== 'AVAILABLE' ||
      (v.environment_fingerprint !== null && v.code_sha !== null && v.lock_hash !== null),
    'Available archives require environment identities.',
  );
export type Workspace = z.infer<typeof workspaceSchema>;
export type EvaluationReport = z.infer<typeof evaluationReportSchema>;
export type ModelDetail = z.infer<typeof modelDetailSchema>;
