import { beforeEach, describe, expect, it } from 'vitest';
import { policySchema, shadowSchema, scenarioCatalogSchema } from '../src/api/policy-contracts';
import {
  objectiveSchema,
  scenarioKeys,
  fixtureScenarioKeys,
  type ScenarioKey,
} from '../src/api/contracts';
import { fixtureService, resetFixtureForTests } from '../src/api/fixture-service';
import { policy } from '../src/api/policy';
import { testPolicy, testShadow, testCatalog } from './policy-data';
beforeEach(resetFixtureForTests);
describe('Execution readiness and capability contracts', () => {
  it('accepts qualified mock evidence with separate empty production pool', () => {
    expect(policySchema.parse(testPolicy).channels[0].simulation.eligible).toBe(true);
    expect(policySchema.parse(testPolicy).channels[0].production.measured_outcomes).toBe(0);
  });
  it.each(['wilson', 'count', 'worlds', 'held-out', 'violations', 'unknown', 'missing-gate'])(
    'rejects unsafe simulation eligibility: %s',
    (fault) => {
      const p = structuredClone(testPolicy),
        r = p.channels[0].simulation;
      if (fault === 'wilson') r.wilson_lower = 0.59;
      if (fault === 'count') r.measured_outcomes = 9;
      if (fault === 'worlds') r.independent_worlds = 2;
      if (fault === 'held-out') r.reliability = 'INCONCLUSIVE';
      if (fault === 'violations') r.guardrail_violations = 1;
      if (fault === 'unknown') r.checks[0].passed = null;
      if (fault === 'missing-gate') r.checks = r.checks.filter((c) => c.id !== 'TRACKING_HEALTH');
      expect(policySchema.safeParse(p).success).toBe(false);
    },
  );
  it('rejects simulated counts reused as production qualification and live simulation permission', () => {
    const p = structuredClone(testPolicy);
    p.channels[0].production = p.channels[0].simulation;
    expect(policySchema.safeParse(p).success).toBe(false);
    p.channels[0].production = structuredClone(testPolicy.channels[0].production);
    p.channels[0].execution_mode = 'LIVE';
    expect(policySchema.safeParse(p).success).toBe(false);
  });
  it('rejects production permissions for a Google test account even with inflated counters', () => {
    const p = structuredClone(testPolicy),
      google = p.channels[1];
    google.production = { ...structuredClone(p.channels[0].simulation), measured_outcomes: 30 };
    google.allowed_modes.push('PRODUCTION_AUTONOMOUS');
    expect(policySchema.safeParse(p).success).toBe(false);
    google.test_account = false;
    google.serves_ads = true;
    expect(policySchema.safeParse(p).success).toBe(true);
    google.production.checks = google.production.checks.filter(
      (c) => c.id !== 'ADMIN_POLICY_REVIEW',
    );
    expect(policySchema.safeParse(p).success).toBe(false);
  });
  it('allows configured autonomy to lose eligibility without claiming a downgrade already occurred', () => {
    const p = structuredClone(testPolicy),
      c = p.channels[0];
    c.mode = 'SIMULATION_AUTONOMOUS';
    c.simulation.eligible = false;
    c.simulation.checks[0].passed = false;
    c.allowed_modes = ['OBSERVE', 'APPROVE'];
    expect(policySchema.safeParse(p).success).toBe(true);
  });
  it('rejects measured results in strict shadow records and unavailable populated logs', () => {
    expect(shadowSchema.safeParse(testShadow).success).toBe(true);
    expect(
      shadowSchema.safeParse({
        ...testShadow,
        records: [{ ...testShadow.records[0], measured_caa: 9000 }],
      }).success,
    ).toBe(false);
    expect(shadowSchema.safeParse({ ...testShadow, status: 'NOT_AVAILABLE' }).success).toBe(false);
  });
  it('rejects available scenarios with missing modules and duplicate scenario IDs', () => {
    expect(scenarioCatalogSchema.safeParse(testCatalog).success).toBe(true);
    expect(
      scenarioCatalogSchema.safeParse({
        ...testCatalog,
        items: [{ ...testCatalog.items[0], missing_modules: ['optimizer'] }],
      }).success,
    ).toBe(false);
    expect(
      scenarioCatalogSchema.safeParse({
        ...testCatalog,
        items: [...testCatalog.items, ...testCatalog.items],
      }).success,
    ).toBe(false);
  });
  it('does not simulate policy qualification, mode updates or shadow results', async () => {
    const p = await policy.current();
    expect(
      p.channels.every(
        (c) => !c.simulation.eligible && !c.production.eligible && !c.allowed_modes.length,
      ),
    ).toBe(true);
    await expect(
      policy.change(p, p.channels[0], 'OBSERVE', 'Reviewed readiness evidence.'),
    ).rejects.toThrow('connected policy engine');
    expect((await policy.shadows()).records).toEqual([]);
  });
  it('keeps unbuilt scenarios unavailable and cannot substitute a fixture or change state', async () => {
    const before = await fixtureService.decisions();
    const unavailable = scenarioKeys.filter((key) => !fixtureScenarioKeys.includes(key));
    for (const key of unavailable) {
      await expect(fixtureService.scenario(key as ScenarioKey)).rejects.toThrow('backend modules');
      await expect(policy.loadScenario(key as ScenarioKey)).rejects.toThrow('unavailable');
    }
    expect(await fixtureService.decisions()).toEqual(before);
    expect((await policy.scenarios()).items.filter((s) => s.status === 'NOT_BUILT')).toHaveLength(
      6,
    );
  });
  it('recognizes all objective names while only profit is available in the fixture engine', async () => {
    for (const name of objectiveSchema.options.filter((name) => name !== 'PROFIT'))
      await expect(fixtureService.runOptimizer(name)).rejects.toThrow('No PROFIT result');
    expect((await fixtureService.optimizerContext()).supported_objectives).toEqual(['PROFIT']);
  });
});
