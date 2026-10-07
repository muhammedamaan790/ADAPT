import type { Decision, ScenarioKey } from './contracts';
import type { Anomaly, OptimizerContext } from './workbench-contracts';
import { FIXTURE_TS, fixtureEvidence } from './fixtures';

export function anomalyFixtures(key: ScenarioKey, decisions: Decision[]): Anomaly[] {
  const d = decisions.find((d) => !['SUPERSEDED', 'REJECTED'].includes(d.status)) || decisions[0];
  const e = fixtureEvidence(key);
  const point = e.chart.at(-1)!;
  const expected = key === 'S7';
  return [
    {
      anomaly_id: `anomaly-${key}`,
      title:
        key === 'S1'
          ? 'Meta auction costs increased'
          : key === 'S3'
            ? 'Hero inventory exposure is unsafe'
            : key === 'S4'
              ? 'Price change altered the revenue funnel'
              : key === 'S5'
                ? 'Sessions diverge from platform clicks'
                : expected
                  ? 'Human budget cut classified as expected'
                  : 'Hero creative efficiency is declining',
      entity: key === 'S3' ? 'Hero · Shopping' : 'Hero · Prospecting',
      platform: key === 'S3' ? 'Google' : 'Meta',
      metric: expected ? 'Budget change' : e.chart_metric,
      kind: expected ? 'BUDGET_CHANGE' : key === 'S5' ? 'TRACKING' : 'EFFICIENCY',
      status: 'OPEN',
      direction: expected ? 'DOWN' : point.actual >= point.baseline ? 'UP' : 'DOWN',
      actual: expected ? 32000 : point.actual,
      baseline: expected ? 40000 : point.baseline,
      change: expected ? -0.2 : point.actual / point.baseline - 1,
      impact: expected ? 0 : 14800,
      impact_label: expected ? 'No efficiency incident' : 'Illustrative 7-day impact',
      detected_at: FIXTURE_TS,
      decision_id: d?.decision_id || null,
      driver: expected ? 'budget_change' : e.drivers[0].title,
      provenance_inputs: ['SIMULATED'],
      gates: expected
        ? [
            {
              id: 'CHANGE_CLASSIFIER',
              label: 'Recorded budget change',
              passed: true,
              detail: 'Classified as a budget change. Kept outside the efficiency incident count.',
            },
          ]
        : [
            {
              id: 'VOLUME',
              label: 'Volume gate',
              passed: true,
              detail: 'Illustrative sample exceeds the configured minimum.',
            },
            {
              id: 'MATERIALITY',
              label: 'Materiality gate',
              passed: true,
              detail: 'Illustrative impact exceeds the configured rupee threshold.',
            },
            {
              id: 'DEPENDENCY_HEALTH',
              label: 'Required source health',
              passed: key !== 'S5',
              detail:
                key === 'S5'
                  ? 'Tracking divergence freezes allocation.'
                  : 'Required sources are fresh in this frontend example.',
            },
          ],
      causal: {
        status: 'NOT_ESTIMABLE',
        reason:
          'No synthetic-control estimator output is available for frontend fixtures. This diagnosis is a probable driver, not identified causality.',
        effect_pct: null,
        lower_pct: null,
        upper_pct: null,
        assumptions: [
          'Control eligibility',
          'Untouched holdout',
          'Pre-fit quality',
          'Placebo and spillover gates',
        ],
      },
      resolution_reason: null,
    },
  ];
}
export function optimizerFixture(d: Decision | undefined): OptimizerContext {
  return {
    decision_id: d?.decision_id || null,
    decision_hash: d?.decision_hash || null,
    supported_objectives: ['PROFIT'],
    budget_ceiling: d?.budget_ceiling || 100000,
    reserve_floor: d?.reserve_floor || 0,
    max_daily_change: 0.2,
    policy_version: d?.policy_version || 'fixture-policy-1',
    horizon_days: 3,
    campaigns:
      d?.legs.map((l, i) => ({
        ...l,
        margin: [0.31, 0.54, 0.5, 0.46][i],
        roas: [2.3, 3.3, 3.5, 4.2][i],
        marginal_caa: [0.15, 0.62, 0.47, -0.05][i],
        inventory_gate: (i === 0 ? 'BLOCK' : 'ALLOW') as 'BLOCK' | 'ALLOW',
        min_budget: Math.ceil(l.before * 0.8),
        max_budget: Math.floor(l.before * 1.2),
        note:
          i === 0
            ? 'Projected shortfall blocks incremental scale.'
            : i === 3
              ? 'Lower model-estimated marginal contribution.'
              : 'Positive illustrative marginal contribution; stock available.',
      })) || [],
  };
}
