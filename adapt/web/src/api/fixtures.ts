import type { Decision, Evidence, Overview, ScenarioKey } from './contracts';

// Deliberately illustrative frontend examples. Not seed-42 world output or an optimizer.
export const FIXTURE_TS = '2026-10-07T09:30:00+05:30';
export const scenarios: {
  key: ScenarioKey;
  title: string;
  description: string;
  category: string;
}[] = [
  {
    key: 'DEMO_01',
    title: 'The complete decision loop',
    description: 'Creative fatigue, a constrained hero SKU, and a feasible budget move.',
    category: 'Golden journey',
  },
  {
    key: 'S1',
    title: 'Auction pressure',
    description: 'Meta CPM rises 45% across the platform over six days.',
    category: 'Auction',
  },
  {
    key: 'S2',
    title: 'Creative fatigue',
    description: 'A creative loses 40% of its click-through rate over ten days.',
    category: 'Fatigue',
  },
  {
    key: 'S3',
    title: 'Inventory shortfall',
    description:
      'Hero SKU demand exceeds available stock. Scale is blocked; a safety cut needs review.',
    category: 'Inventory',
  },
  {
    key: 'S4',
    title: 'Price change',
    description: 'Category prices rise 18%; investigate the conversion change.',
    category: 'Price',
  },
  {
    key: 'S5',
    title: 'Tracking disruption',
    description: 'Sessions fall 60% while clicks remain flat. Freeze affected actions.',
    category: 'Tracking',
  },
  {
    key: 'S7',
    title: 'Expected budget change',
    description: 'A human budget cut is classified as a budget change, not an efficiency incident.',
    category: 'Classification',
  },
];

export const trend = Array.from({ length: 14 }, (_, i) => ({
  date: `2026-09-${String(24 + i > 30 ? 24 + i - 30 : 24 + i).padStart(2, '0')}`.replace(
    i > 6 ? '2026-09-' : 'never',
    '2026-10-',
  ),
  baseline: [3.6, 3.7, 3.5, 3.8, 3.65, 3.7, 3.8, 3.75, 3.8, 3.9, 3.8, 3.7, 3.85, 3.8][i],
  actual: [3.55, 3.75, 3.45, 3.85, 3.6, 3.65, 3.78, 3.4, 3.12, 2.95, 2.72, 2.55, 2.48, 2.3][i],
}));
function scenarioTrend(key: ScenarioKey) {
  if (key === 'S5')
    return trend.map((p, i) => ({ ...p, baseline: 0.86, actual: i < 7 ? 0.85 : 0.34 }));
  if (key === 'S7') return trend.map((p) => ({ ...p, actual: p.baseline * 0.99 }));
  if (key === 'S1')
    return trend.map((p, i) => ({ ...p, actual: i < 8 ? p.baseline : p.baseline / 1.45 }));
  if (key === 'S4')
    return trend.map((p, i) => ({ ...p, actual: i < 8 ? p.baseline : p.baseline * 0.88 * 1.18 }));
  return trend;
}
const checks = [
  {
    id: 'BUDGET_CEILING',
    label: 'Budget ceiling & reserve',
    passed: true,
    detail: '₹96,000 allocated ≤ ₹1,00,000 ceiling. ₹4,000 remains unallocated; reserve floor ₹0.',
  },
  {
    id: 'DAILY_CHANGE',
    label: 'Daily change limit',
    passed: true,
    detail: 'Every leg stays within ±20% of its daily anchor.',
  },
  {
    id: 'INVENTORY_GATE',
    label: 'Inventory exposure',
    passed: true,
    detail: 'Hero scaling blocked. Proposed allocation reduces projected stock shortfall.',
  },
  {
    id: 'DEPENDENCY_HEALTH',
    label: 'Required data health',
    passed: true,
    detail: 'Ads, store, attribution, inventory and SKU economics are available.',
  },
  {
    id: 'EXECUTION_STATE',
    label: 'Execution state & reservations',
    passed: true,
    detail:
      'No unresolved external state or reserved entity. Revalidated by backend before execution.',
  },
];
export function fixtureDecision(key: ScenarioKey): Decision {
  const safety = key === 'S3';
  const blocked = key === 'S5';
  return {
    decision_id: `dec-${key.toLowerCase()}-001`,
    title: safety
      ? 'Reduce exposure to the hero SKU'
      : blocked
        ? 'Investigate the tracking gap'
        : 'Move spend toward stronger contribution',
    summary: safety
      ? 'Reduce the Google hero budget by 20%. Keep the released ₹6,000 unallocated. A remaining 12-unit shortfall still needs replenishment.'
      : blocked
        ? 'Freeze budget changes on the affected channel until store and session reporting reconcile. No mutation is proposed.'
        : 'Reduce the fatigued Meta campaign, scale the bundle and refill campaigns, and retain ₹4,000 rather than force an unprofitable allocation.',
    class: safety ? 'SAFETY' : blocked ? 'OPERATIONAL' : 'OPTIMIZATION',
    type: blocked ? 'tracking_alert' : safety ? 'decrease_budget' : 'reallocate',
    objective: 'PROFIT',
    status: blocked ? 'BLOCKED' : 'PENDING_APPROVAL',
    trigger: { anomaly_id: `anomaly-${key}`, opportunity_id: null },
    legs: blocked
      ? []
      : safety
        ? [
            {
              platform: 'Google',
              entity: 'Hero · Shopping',
              budget_id: 'g-hero',
              before: 30000,
              after: 24000,
            },
          ]
        : [
            {
              platform: 'Meta',
              entity: 'Hero · Prospecting',
              budget_id: 'm-hero',
              before: 40000,
              after: 32000,
            },
            {
              platform: 'Google',
              entity: 'Bundle · Shopping',
              budget_id: 'g-bundle',
              before: 24000,
              after: 28800,
            },
            {
              platform: 'Meta',
              entity: 'Refill · Retargeting',
              budget_id: 'm-refill',
              before: 20000,
              after: 22400,
            },
            {
              platform: 'Google',
              entity: 'Brand · Search',
              budget_id: 'g-brand',
              before: 16000,
              after: 12800,
            },
          ],
    expected: blocked
      ? {
          p10: 0,
          p50: 0,
          p90: 0,
          prob_loss: 0,
          delta_net_revenue: 0,
          raw_pred: 0,
          calibrated_pred: 0,
        }
      : {
          p10: safety ? 400 : 1800,
          p50: safety ? 2400 : 7200,
          p90: safety ? 4200 : 11200,
          prob_loss: 0.14,
          delta_net_revenue: safety ? -9000 : 14600,
          raw_pred: safety ? 2400 : 8000,
          calibrated_pred: safety ? 2400 : 7200,
        },
    inventory_risk_after: {
      kind: 'PROJECTED_SHORTFALL',
      by_sku: { 'HERO-001': safety ? 12 : 0, 'BUNDLE-004': 0, 'REFILL-006': 0 },
    },
    unallocated: safety ? 6000 : 4000,
    reserve_floor: 0,
    budget_ceiling: 100000,
    cost_of_inaction_7d: safety ? 4200 : 14800,
    checks: blocked
      ? checks
          .filter((c) => ['DEPENDENCY_HEALTH', 'EXECUTION_STATE'].includes(c.id))
          .map((c) =>
            c.id === 'DEPENDENCY_HEALTH'
              ? {
                  ...c,
                  passed: false,
                  detail: 'Tracking gap unresolved. Budget mutations remain frozen.',
                }
              : c,
          )
      : checks.map((c) =>
          safety && c.id === 'INVENTORY_GATE'
            ? {
                ...c,
                detail:
                  'BLOCK SCALE. Maximum feasible reduction leaves 12 units of projected shortfall; human review required.',
              }
            : safety && c.id === 'BUDGET_CEILING'
              ? {
                  ...c,
                  detail:
                    'Portfolio spend ₹94,000. ₹6,000 released and unallocated; reserve floor ₹0.',
                }
              : c,
        ),
    evidence_ids: ['e-fatigue', 'e-inventory'],
    why_not: blocked
      ? [
          {
            entity: 'Change budgets during a tracking gap',
            rule_id: 'TRACKING_FREEZE',
            reason:
              'Store and session reporting must reconcile before another budget decision can be generated. Allocation forecasts are withheld while required data is unreliable.',
            metric: 'Sessions −60% · clicks stable · affected channel frozen',
          },
        ]
      : [
          {
            entity: 'Hero · Shopping',
            rule_id: 'INVENTORY_BLOCK_SCALE',
            reason:
              'A high historical ROAS does not create stock. Incremental scale exceeds the hero SKU inventory envelope.',
            metric: 'ROAS 5.2× · projected shortfall 42 units before action',
          },
          {
            entity: 'Keep every rupee deployed',
            rule_id: 'NONPOSITIVE_MARGINAL_CAA',
            reason:
              'No remaining feasible receiver has a positive model-estimated marginal contribution after constraints.',
            metric: `${safety ? '₹6,000' : '₹4,000'} remains unallocated`,
          },
        ],
    snapshot_id: 'fixture-snapshot-001',
    decision_hash: `fixture-${key}-immutable-v1`,
    policy_version: 'fixture-policy-1',
    provenance_inputs: ['SIMULATED'],
    created_at: FIXTURE_TS,
    horizon_days: 3,
  };
}
export function fixtureEvidence(key: ScenarioKey): Evidence {
  const driver =
    key === 'S1'
      ? {
          title: 'Auction pressure',
          detail:
            'CPM rose across matched Meta campaigns while creative click-through rates stayed stable.',
          observations: [
            'CPM +45% across Meta',
            'Click-through rate stable',
            'Multiple campaigns move together',
          ],
        }
      : key === 'S3'
        ? {
            title: 'Inventory constraint',
            detail:
              'Projected hero demand exceeds available units. The mapped campaigns cannot safely scale.',
            observations: [
              '42-unit projected shortfall before action',
              'Confirmed inbound insufficient',
              'Non-exposed products remain available',
            ],
          }
        : key === 'S4'
          ? {
              title: 'Price change',
              detail:
                'An 18% category price increase coincides with lower conversion. This is evidence, not causal identification.',
              observations: [
                'Category price +18%',
                'Conversion rate −12%',
                'Price timestamp precedes reporting change',
              ],
            }
          : key === 'S5'
            ? {
                title: 'Tracking disruption',
                detail:
                  'Session reporting fell without a corresponding decline in platform clicks. Diagnose the data gap before allocating.',
                observations: [
                  'GA4 sessions −60%',
                  'Platform clicks stable',
                  'Store reconciliation incomplete',
                ],
              }
            : {
                title: 'Creative fatigue',
                detail:
                  'Declining click-through rate is concentrated in the hero creative. Two sibling creatives remain stable; auction pressure is weaker.',
                observations: [
                  'Hero creative CTR −40%',
                  'Sibling creatives within ±4%',
                  'Spend remains above the volume gate',
                ],
              };
  const chart = scenarioTrend(key);
  const delta = chart.at(-1)!.actual - chart.at(-1)!.baseline;
  const decomposition =
    key === 'S1'
      ? [
          { label: 'CTR', value: 0 },
          { label: 'CVR', value: 0 },
          { label: 'AOV', value: 0 },
          { label: 'CPM', value: delta },
        ]
      : key === 'S4'
        ? [
            { label: 'CTR', value: 0 },
            { label: 'CVR', value: -0.456 },
            { label: 'AOV', value: delta + 0.456 },
            { label: 'CPM', value: 0 },
          ]
        : key === 'S3'
          ? [
              { label: 'CTR', value: 0 },
              { label: 'CVR', value: delta },
              { label: 'AOV', value: 0 },
              { label: 'CPM', value: 0 },
            ]
          : [
              { label: 'CTR', value: -0.91 },
              { label: 'CVR', value: -0.35 },
              { label: 'AOV', value: 0.09 },
              { label: 'CPM', value: -0.33 },
            ];
  return {
    decision_id: fixtureDecision(key).decision_id,
    chart,
    chart_metric: key === 'S5' ? 'Session / click ratio' : 'Reconciled ROAS',
    decomposition_kind: key === 'S5' || key === 'S7' ? 'NOT_APPLICABLE' : 'ROAS',
    decomposition: key === 'S5' || key === 'S7' ? [] : decomposition,
    decomposition_total: key === 'S5' || key === 'S7' ? 0 : delta,
    drivers: [
      {
        id: 'e-fatigue',
        ...driver,
        score: 0.87,
        level: 'PROBABLE DRIVER',
        source: 'Ads reporting + creative registry',
        available_at: FIXTURE_TS,
      },
      {
        id: 'e-inventory',
        title: 'Inventory limits the alternative',
        score: 0.76,
        level: 'PROBABLE DRIVER',
        detail:
          'The high-ROAS hero SKU has a projected stock shortfall. Scaling this campaign is blocked, even if its trailing return looks attractive.',
        observations: [
          'Inventory exposure gate: BLOCK SCALE',
          '42 units short before action',
          'Safety stock included in calculation',
        ],
        source: 'ERP inventory + SKU mapping',
        available_at: FIXTURE_TS,
      },
    ],
  };
}
export function fixtureOverview(key: ScenarioKey, day: number): Overview {
  const metric = (
    key: string,
    label: string,
    value: number,
    format: 'money' | 'ratio' | 'count',
    change: number | null,
    formula: string,
    source: string,
  ) => ({
    key,
    label,
    value,
    format,
    change,
    formula,
    source,
    available_at: FIXTURE_TS,
    provenance_inputs: ['SIMULATED' as const],
  });
  const d = fixtureDecision(key);
  return {
    workspace: 'D2C demo workspace',
    decision_ts: FIXTURE_TS,
    world_day: day,
    scenario: key,
    brief:
      key === 'S7'
        ? 'The spend decline follows a recorded human budget cut. It is classified as a budget change; no efficiency incident or allocation proposal is open.'
        : key === 'S5'
          ? 'A tracking gap needs investigation. Affected budget changes are frozen until reporting reconciles.'
          : 'Hero campaign efficiency is falling. A lower-risk budget move is ready for review, but the highest-ROAS alternative is limited by inventory.',
    metrics: [
      metric(
        'net_revenue',
        'Net revenue',
        1248000,
        'money',
        -0.062,
        'GMV − discounts − refunds (tax excluded)',
        'Store orders / reconciled revenue',
      ),
      metric(
        'spend',
        'Ad spend',
        350000,
        'money',
        0.018,
        'Σ normalized platform spend',
        'Meta + Google reporting',
      ),
      metric(
        'caa',
        'Contribution after ads',
        198400,
        'money',
        -0.124,
        'Net revenue − COGS − shipping − fees − ad spend',
        'Store + SKU economics + ads',
      ),
      metric(
        'mer',
        'MER',
        1248000 / 350000,
        'ratio',
        -0.079,
        'Total net revenue / total ad spend',
        'Reconciled store + ads',
      ),
      metric(
        'poas',
        'POAS',
        548400 / 350000,
        'ratio',
        -0.093,
        'Attributed contribution before ads / ad spend',
        'Attribution + SKU economics + ads',
      ),
      metric(
        'inventory',
        'At-risk SKUs',
        2,
        'count',
        null,
        'Count of SKUs with projected demand above available − safety stock',
        'ERP + seasonal-naive demand',
      ),
    ],
    sources: [
      {
        id: 'meta',
        name: 'Meta Ads',
        kind: 'Advertising',
        score: 98,
        status: 'GREEN',
        freshness: '4 min ago',
        provenance: 'SIMULATED',
      },
      {
        id: 'google',
        name: 'Google Ads',
        kind: 'Advertising',
        score: 99,
        status: 'GREEN',
        freshness: '3 min ago',
        provenance: 'SIMULATED',
      },
      {
        id: 'store',
        name: 'Store',
        kind: 'Commerce',
        score: 99,
        status: 'GREEN',
        freshness: '2 min ago',
        provenance: 'SIMULATED',
      },
      {
        id: 'ga4',
        name: 'GA4',
        kind: 'Events',
        score: key === 'S5' ? 41 : 97,
        status: key === 'S5' ? 'RED' : 'GREEN',
        freshness: key === 'S5' ? 'Gap detected' : '5 min ago',
        provenance: 'SIMULATED',
      },
      {
        id: 'erp',
        name: 'Inventory',
        kind: 'ERP',
        score: 96,
        status: 'GREEN',
        freshness: '8 min ago',
        provenance: 'SIMULATED',
      },
      {
        id: 'econ',
        name: 'SKU economics',
        kind: 'Margins',
        score: 100,
        status: 'GREEN',
        freshness: '12 min ago',
        provenance: 'SIMULATED',
      },
    ],
    attention:
      key === 'S7'
        ? []
        : ([
            {
              id: 'att-1',
              decision_id: d.decision_id,
              kind: 'incident',
              title:
                key === 'S5'
                  ? 'Session reporting diverges from clicks'
                  : key === 'S3'
                    ? 'Hero SKU has a projected stock shortfall'
                    : key === 'S1'
                      ? 'Meta auction costs are increasing'
                      : key === 'S4'
                        ? 'Category price change affects conversion'
                        : 'Hero campaign is losing efficiency',
              description:
                key === 'S5'
                  ? 'Tracking issue · affected channel frozen'
                  : 'Probable driver identified · human review required',
              impact: 14800,
              label: '7-day cost of inaction',
            },
            {
              id: 'att-2',
              decision_id: d.decision_id,
              kind: 'opportunity',
              title: 'Bundle has room to scale',
              description: 'Higher margin, available inventory, positive marginal contribution',
              impact: 7200,
              label: 'Model-estimated ΔCAA',
            },
            {
              id: 'att-3',
              decision_id: d.decision_id,
              kind: 'inventory',
              title: 'High ROAS, limited stock',
              description: 'Hero · Shopping rejected for incremental scale',
              impact: 4200,
              label: 'Estimated exposure',
            },
          ].sort((a, b) => b.impact - a.impact) as Overview['attention']),
    series: key === 'S5' ? trend.map((p) => ({ ...p, actual: p.baseline })) : scenarioTrend(key),
    loop: ['Ingest', 'Detect', 'Diagnose', 'Recommend', 'Approve', 'Execute', 'Learn'].map(
      (label, i) => ({
        label,
        state:
          key === 'S7'
            ? i < 2
              ? ('complete' as const)
              : ('waiting' as const)
            : i < 4
              ? ('complete' as const)
              : i === 4
                ? ('current' as const)
                : ('waiting' as const),
      }),
    ),
    calibration: 0.9,
    counts: { success: 0, neutral: 0, failed: 0, inconclusive: 0 },
  };
}
