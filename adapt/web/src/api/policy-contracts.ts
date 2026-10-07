import { z } from 'zod';
import { scenarioKeys, decisionSchema } from './contracts';
const text = z.string().min(1);
const count = z.number().int().nonnegative();
export const channelSchema = z.enum(['Meta', 'Google', 'TikTok', 'Amazon']);
export const modeSchema = z.enum([
  'OBSERVE',
  'APPROVE',
  'SIMULATION_AUTONOMOUS',
  'PRODUCTION_AUTONOMOUS',
]);
export type Mode = z.infer<typeof modeSchema>;
const check = z.object({ id: text, label: text, passed: z.boolean().nullable(), detail: text });
const readiness = z.object({
  eligible: z.boolean(),
  executed_decisions: count,
  measured_outcomes: count,
  independent_worlds: count,
  wilson_lower: z.number().min(0).max(1).nullable(),
  reliability: z.enum(['PASS', 'FAIL', 'INCONCLUSIVE', 'UNAVAILABLE']),
  guardrail_violations: count,
  checks: z.array(check),
  note: text,
});
const ready = (r: z.infer<typeof readiness>, production: boolean) =>
  r.eligible &&
  r.executed_decisions >= 10 &&
  r.measured_outcomes >= (production ? 30 : 10) &&
  (production || r.independent_worlds >= 3) &&
  (r.wilson_lower ?? -1) >= 0.6 &&
  r.reliability === 'PASS' &&
  r.guardrail_violations === 0 &&
  r.checks.length > 0 &&
  r.checks.every((c) => c.passed === true) &&
  [
    'TRACKING_HEALTH',
    'EXECUTION_HEALTH',
    ...(production ? ['CHAMPION_MODELS', 'ADMIN_POLICY_REVIEW'] : []),
  ].every((id) => r.checks.some((c) => c.id === id && c.passed === true));
export const channelPolicySchema = z
  .object({
    channel: channelSchema,
    mode: modeSchema,
    execution_mode: z.enum(['MOCK', 'LIVE']),
    test_account: z.boolean(),
    serves_ads: z.boolean(),
    allowed_modes: z.array(modeSchema),
    simulation: readiness,
    production: readiness,
    note: text,
  })
  .superRefine((c, ctx) => {
    if (new Set(c.allowed_modes).size !== c.allowed_modes.length)
      ctx.addIssue({ code: 'custom', message: 'Duplicate allowed modes.' });
    for (const r of [c.simulation, c.production])
      if (new Set(r.checks.map((x) => x.id)).size !== r.checks.length)
        ctx.addIssue({ code: 'custom', message: 'Duplicate readiness checks.' });
    if (
      (c.simulation.eligible && !ready(c.simulation, false)) ||
      (c.production.eligible && !ready(c.production, true))
    )
      ctx.addIssue({
        code: 'custom',
        message: 'Eligibility lacks measured outcomes and qualification evidence.',
      });
    if (
      c.allowed_modes.includes('SIMULATION_AUTONOMOUS') &&
      (c.execution_mode !== 'MOCK' || !ready(c.simulation, false))
    )
      ctx.addIssue({
        code: 'custom',
        message: 'Simulation autonomy requires a qualified mock channel.',
      });
    if (
      (c.production.eligible || c.allowed_modes.includes('PRODUCTION_AUTONOMOUS')) &&
      (c.execution_mode !== 'LIVE' || c.test_account || !c.serves_ads || !ready(c.production, true))
    )
      ctx.addIssue({
        code: 'custom',
        message: 'Production autonomy requires serving live-account evidence.',
      });
  });
export const policySchema = z
  .object({
    policy_version: text,
    revision: text,
    channels: z.array(channelPolicySchema).min(1),
    note: text,
  })
  .refine(
    (p) => new Set(p.channels.map((c) => c.channel)).size === p.channels.length,
    'Duplicate channel policy.',
  );
export type Policy = z.infer<typeof policySchema>;
export type ChannelPolicy = z.infer<typeof channelPolicySchema>;
export const shadowSchema = z
  .object({
    status: z.enum(['AVAILABLE', 'NOT_AVAILABLE']),
    note: text,
    records: z
      .array(
        z
          .object({
            id: text,
            decision_id: text,
            decision_hash: text,
            channel: channelSchema,
            world: z.enum(['SIMULATED', 'REAL']),
            at: z.string().datetime(),
            method: z.literal('FORECAST_ONLY'),
            expected: decisionSchema.shape.expected.nullable(),
            guardrail_breaches: z.array(text),
            note: text,
          })
          .strict(),
      )
      .max(1000),
  })
  .refine(
    (v) => v.status !== 'NOT_AVAILABLE' || v.records.length === 0,
    'Unavailable shadow log cannot contain results.',
  )
  .refine(
    (v) => new Set(v.records.map((r) => r.id)).size === v.records.length,
    'Duplicate shadow record.',
  );
export const scenarioCatalogSchema = z
  .object({
    stage: z.string().min(1),
    note: text,
    items: z
      .array(
        z
          .object({
            key: z.enum(scenarioKeys),
            title: text,
            description: text,
            category: text,
            status: z.enum(['AVAILABLE', 'NOT_BUILT']),
            missing_modules: z.array(text),
          })
          .refine(
            (v) => v.status !== 'AVAILABLE' || v.missing_modules.length === 0,
            'Available scenario cannot have missing modules.',
          ),
      )
      .max(13),
  })
  .refine(
    (v) => new Set(v.items.map((s) => s.key)).size === v.items.length,
    'Duplicate scenario IDs.',
  );
