// Synthetic server responses for frontend verification only. Never imported by app source.
import type { Policy } from '../src/api/policy-contracts';
import { fixtureDecision } from '../src/api/fixtures.ts';
const checks = [
  'TRACKING_HEALTH',
  'EXECUTION_HEALTH',
  'CHAMPION_MODELS',
  'ADMIN_POLICY_REVIEW',
].map((id) => ({
  id,
  label: id.replaceAll('_', ' '),
  passed: true,
  detail: 'Synthetic passing server gate for testing.',
}));
const unavailable = {
  eligible: false,
  executed_decisions: 0,
  measured_outcomes: 0,
  independent_worlds: 0,
  wilson_lower: null,
  reliability: 'UNAVAILABLE' as const,
  guardrail_violations: 0,
  checks: [],
  note: 'No matured real outcome evidence supplied.',
};
export const testPolicy: Policy = {
  policy_version: 'test-policy-1',
  revision: 'test-revision-1',
  note: 'Synthetic contract response; not evidence of engine qualification.',
  channels: [
    {
      channel: 'TikTok',
      mode: 'APPROVE',
      execution_mode: 'MOCK',
      test_account: false,
      serves_ads: false,
      allowed_modes: ['OBSERVE', 'APPROVE', 'SIMULATION_AUTONOMOUS'],
      simulation: {
        ...unavailable,
        eligible: true,
        executed_decisions: 12,
        measured_outcomes: 12,
        independent_worlds: 3,
        wilson_lower: 0.7,
        reliability: 'PASS',
        checks,
        note: 'Synthetic region qualification over worlds 901–903; held-out world 904 PASS.',
      },
      production: unavailable,
      note: 'Mock channel with synthetic simulation evidence only.',
    },
    {
      channel: 'Google',
      mode: 'APPROVE',
      execution_mode: 'LIVE',
      test_account: true,
      serves_ads: false,
      allowed_modes: ['OBSERVE', 'APPROVE'],
      simulation: unavailable,
      production: unavailable,
      note: 'Test-account execution does not serve advertising impressions.',
    },
  ],
};
export const testShadow = {
  status: 'AVAILABLE',
  note: 'Synthetic unexecuted forecasts only.',
  records: (['SIMULATED', 'REAL'] as const).map((world, i) => ({
    id: `shadow-${i}`,
    decision_id: fixtureDecision('DEMO_01').decision_id,
    decision_hash: fixtureDecision('DEMO_01').decision_hash,
    channel: i ? 'Google' : 'TikTok',
    world,
    at: '2026-10-07T10:00:00Z',
    method: 'FORECAST_ONLY',
    expected: fixtureDecision('DEMO_01').expected,
    guardrail_breaches: [],
    note: 'Synthetic forecast; no observed uplift or matured outcome.',
  })),
};
export const testCatalog = {
  stage: 'Synthetic later-stage server',
  note: 'Availability is a test contract, not an implemented optimizer claim.',
  items: [
    {
      key: 'S9',
      title: 'ROAS trap',
      description: 'Inspect contribution and marginal value against ROAS.',
      category: 'Decision quality',
      status: 'AVAILABLE',
      missing_modules: [],
    },
  ],
};
