export const objective = {
  workspace_id: 'demo',
  objective: 'PROFIT',
  revision: 'objective-1',
  supported_objectives: ['PROFIT', 'GROWTH'],
  can_change: true,
  note: 'Synthetic workspace configuration for interface testing.',
};
export const history = {
  status: 'AVAILABLE',
  note: 'Synthetic audit history.',
  versions: [
    {
      version: 'policy-2',
      at: '2026-10-07T06:00:00Z',
      actor: 'Admin review',
      reason: 'Updated workspace objective after review.',
      changes: [{ field: 'objective', before: 'PROFIT', after: 'GROWTH' }],
    },
  ],
};
export const source = {
  id: 'meta_ads',
  name: 'Meta Ads',
  kind: 'ads',
  score: 80,
  status: 'YELLOW',
  freshness: 'Latest complete day',
  provenance: 'SIMULATED',
  as_of: '2026-10-07T06:00:00',
  newest_date: '2026-10-06',
  age_hours: 24,
  freshness_score: 1,
  completeness: 0.8,
  consistency: 1,
  hard_failures: [],
  checks: [{ id: 'range_check', passed: true, detail: 'Values are in the expected range.' }],
};
export const reconciliation = {
  platform_revenue: 1200,
  store_revenue: 1000,
  attribution_excess: 200,
  note: 'Synthetic conversion reconciliation.',
  window_start: '2026-09-10',
  window_end: '2026-10-07',
  platforms: [
    {
      platform: 'Meta',
      platform_conversions: 12,
      store_attributed_orders: 10,
      over_attribution: 1.2,
      over_attribution_reason: null,
      session_click_ratio: 0.8,
    },
    {
      platform: 'Google',
      platform_conversions: 2,
      store_attributed_orders: 0,
      over_attribution: null,
      over_attribution_reason: 'ZERO_DENOMINATOR',
      session_click_ratio: null,
    },
  ],
};
export const confidence = {
  status: 'AVAILABLE',
  note: 'Synthetic confidence region layout, not calibrated model evidence.',
  pools: [
    {
      world: 'SIMULATED',
      model_version: 'test-1',
      evaluated_at: '2026-10-07T06:00:00Z',
      outcome_definition:
        'Success means positive realized incremental contribution with no guardrail breach.',
      scope: 'HELD_OUT',
      world_ids: ['heldout-test'],
      regions: [
        { name: 'LOW', total: 0, successes: 0, wilson_lower: null, wilson_upper: null },
        { name: 'MID', total: 10, successes: 7, wilson_lower: 0.397, wilson_upper: 0.892 },
        { name: 'HIGH', total: 20, successes: 18, wilson_lower: 0.699, wilson_upper: 0.972 },
      ],
    },
  ],
};
