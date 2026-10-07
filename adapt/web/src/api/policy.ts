import { api, dataMode, request, ApiError } from './client';
import {
  policySchema,
  shadowSchema,
  scenarioCatalogSchema,
  type ChannelPolicy,
  type Mode,
  type Policy,
} from './policy-contracts';
import { scenarios } from './fixtures';
import type { ScenarioKey } from './contracts';
const missingReadiness = (pool: string) => ({
  eligible: false,
  executed_decisions: 0,
  measured_outcomes: 0,
  independent_worlds: 0,
  wilson_lower: null,
  reliability: 'UNAVAILABLE' as const,
  guardrail_violations: 0,
  checks: [
    {
      id: 'EVIDENCE_MISSING',
      label: `${pool} qualification evidence`,
      passed: null,
      detail: 'No calibrated engine readiness report has been supplied.',
    },
  ],
  note: 'Zero supplied evidence is not a passing qualification. Illustrative UI outcomes do not establish engine readiness.',
});
const later = [
  [
    'S6',
    'Category demand surge',
    'Detect a positive anomaly and separate demand from advertising efficiency.',
    'Demand',
    ['demand_forecast'],
  ],
  [
    'S8',
    'Audience saturation',
    'Inspect rising retargeting frequency with flat reach.',
    'Saturation',
    ['saturation'],
  ],
  [
    'S9',
    'ROAS trap',
    'Compare margin, inventory and marginal value against trailing ROAS.',
    'Decision quality',
    ['response_curves', 'optimizer'],
  ],
  [
    'S10',
    'Seasonal demand',
    'Avoid efficiency incidents for explained seasonal variation.',
    'Classification',
    ['seasonal_baseline'],
  ],
  [
    'S11',
    'Excess inventory',
    'Inspect a clearance allocation for excess-stock SKUs.',
    'Clearance',
    ['clearance_objective'],
  ],
  [
    'S12',
    'Fatigue and demand surge',
    'Surface both drivers on the same campaign, with supplied evidence ordering.',
    'Mixed drivers',
    ['fatigue', 'demand_forecast'],
  ],
] as const;
export const policy = {
  async current(): Promise<Policy> {
    if (dataMode === 'api') return request('/policy', policySchema);
    return policySchema.parse({
      policy_version: 'fixture-policy-1',
      revision: 'fixture-readiness-1',
      note: 'Read-only frontend policy illustration. No mode changes or autonomous executions are simulated.',
      channels: ['Meta', 'Google'].map((channel) => ({
        channel,
        mode: 'APPROVE',
        execution_mode: 'MOCK',
        test_account: false,
        serves_ads: false,
        allowed_modes: [],
        simulation: missingReadiness('Simulation'),
        production: missingReadiness('Production'),
        note: 'Approve-only UI demo. Backend policy, tracking health and calibrated readiness are not connected.',
      })),
    });
  },
  async change(p: Policy, c: ChannelPolicy, mode: Mode, reason: string) {
    if (dataMode !== 'api') throw new Error('Execution modes require a connected policy engine.');
    policySchema.parse(p);
    const bound = p.channels.find((channel) => channel.channel === c.channel);
    if (!bound || JSON.stringify(bound) !== JSON.stringify(c))
      throw new Error('The requested channel must belong to the current policy revision.');
    if (!bound.allowed_modes.includes(mode) || reason.trim().length < 10)
      throw new Error('The backend must allow this mode; add a reason of at least ten characters.');
    const result = await request(
      '/policy',
      policySchema,
      {
        policy_version: p.policy_version,
        revision: p.revision,
        channel: c.channel,
        mode,
        reason: reason.trim(),
      },
      'PUT',
    );
    if (result.channels.find((x) => x.channel === c.channel)?.mode !== mode)
      throw new ApiError(
        409,
        'Policy did not confirm the requested channel mode. Refresh backend policy before retrying.',
      );
    return result;
  },
  async shadows() {
    return dataMode === 'api'
      ? request('/learning/shadow', shadowSchema)
      : shadowSchema.parse({
          status: 'NOT_AVAILABLE',
          records: [],
          note: 'No backend Observe-mode log is connected. Pending UI proposals are not substituted for recorded shadow decisions.',
        });
  },
  async scenarios() {
    return dataMode === 'api'
      ? request('/sim/scenarios', scenarioCatalogSchema)
      : scenarioCatalogSchema.parse({
          stage: 'Stage 1 · UI examples',
          note: 'Stage 1 fixtures exercise interactions only. Later scenarios need backend modules and cannot be loaded as substitutes.',
          items: [
            ...scenarios.map((s) => ({ ...s, status: 'AVAILABLE', missing_modules: [] })),
            ...later.map(([key, title, description, category, missing_modules]) => ({
              key,
              title,
              description,
              category,
              status: 'NOT_BUILT',
              missing_modules,
            })),
          ],
        });
  },
  async loadScenario(key: ScenarioKey) {
    const catalog = await policy.scenarios();
    if (!catalog.items.some((s) => s.key === key && s.status === 'AVAILABLE'))
      throw new Error('This scenario is unavailable in the current stage. Refresh the catalog.');
    // The backend must recheck required modules and unresolved execution state atomically.
    return api.scenario(key);
  },
};
