// Synthetic values for contract/interaction tests only; never bundled in the application.
import type { EvaluationReport, ModelDetail } from '../src/api/management-contracts';
export const evaluationTestReport: EvaluationReport = {
  report_id: 'test-report',
  generated_at: '2026-10-07T10:00:00Z',
  code_sha: 'test-code',
  seeds: [42, 43],
  horizon_days: 7,
  budget_ceiling: 100000,
  reserve_floor: 4000,
  currency: 'INR',
  common_random_numbers: true,
  feasibility_envelope: 'Same safety gates and budget envelope in this synthetic test.',
  fairness_statement:
    'Synthetic test report: all strategy rows use paired seeds; oracle is benchmark only.',
  rows: [42, 43].flatMap((seed) =>
    (['safe-static', 'safe-contribution', 'adapt', 'oracle'] as const).map((strategy, i) => ({
      seed,
      strategy,
      realized_caa: 10000 + i * 2000,
      spend: strategy === 'oracle' ? 0 : 40000,
      stock_risk_days: 1,
      constraint_breaches: 0,
      forced_interventions: 2,
    })),
  ),
};
export const modelTestDetail: ModelDetail = {
  name: 'response-curves',
  version: 'v2',
  registry_revision: 'rev-2',
  role: 'CANDIDATE',
  artifact_hash: 'a'.repeat(64),
  training_snapshot_hash: 'b'.repeat(64),
  trained_at: '2026-10-07T09:00:00Z',
  rollback_version: null,
  promotion_reason: null,
  checks: [
    { id: 'BASELINE', label: 'Family baseline', passed: true, detail: 'Passed in test payload.' },
    {
      id: 'CHAMPION',
      label: 'Champion non-inferiority',
      passed: true,
      detail: 'Passed in test payload.',
    },
    { id: 'COVERAGE', label: 'Coverage', passed: true, detail: 'Passed in test payload.' },
  ],
  metrics: [{ label: 'MAE', candidate: 12, champion: 14, baseline: 20, unit: 'INR' }],
  allowed_actions: ['PROMOTE'],
  note: 'Synthetic model management payload for browser tests.',
};
