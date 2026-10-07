import { beforeEach, describe, expect, it } from 'vitest';
import {
  decisionSchema,
  evidenceSchema,
  overviewSchema,
  fixtureScenarioKeys,
  type ScenarioKey,
} from '../src/api/contracts';
import { fixtureDecision, fixtureEvidence, fixtureOverview } from '../src/api/fixtures';
import { fixtureService, resetFixtureForTests } from '../src/api/fixture-service';
import { money } from '../src/lib/format';

beforeEach(resetFixtureForTests);
describe('Stage 1 UI contracts and lifecycle examples', () => {
  it.each(fixtureScenarioKeys as readonly ScenarioKey[])(
    '%s values satisfy the UI wire contract',
    (key) => {
      expect(() => decisionSchema.parse(fixtureDecision(key))).not.toThrow();
      expect(() => evidenceSchema.parse(fixtureEvidence(key))).not.toThrow();
      expect(() => overviewSchema.parse(fixtureOverview(key, 0))).not.toThrow();
    },
  );
  it('golden allocation conserves the ceiling and leaves cash', () => {
    const d = fixtureDecision('DEMO_01');
    expect(d.legs.reduce((n, l) => n + l.after, 0) + d.unallocated).toBe(d.budget_ceiling);
    expect(d.legs.every((l) => Math.abs(l.after - l.before) <= l.before * 0.2)).toBe(true);
    expect(fixtureEvidence('DEMO_01').decomposition.reduce((n, x) => n + x.value, 0)).toBeCloseTo(
      -1.5,
    );
  });
  it('rejects a stale approval hash and creates no execution', async () => {
    const d = (await fixtureService.decisions())[0];
    await expect(fixtureService.approve(d.decision_id, 'stale')).rejects.toThrow('409');
    expect(await fixtureService.executions()).toHaveLength(0);
  });
  it('duplicate approval does not create two fixture executions', async () => {
    const d = (await fixtureService.decisions())[0];
    await fixtureService.approve(d.decision_id, d.decision_hash);
    await fixtureService.approve(d.decision_id, d.decision_hash);
    expect(await fixtureService.executions()).toHaveLength(1);
  });
  it('feedback is applied exactly once, against raw prediction', async () => {
    const d = (await fixtureService.decisions())[0];
    await fixtureService.approve(d.decision_id, d.decision_hash);
    const { vi } = await import('vitest');
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 5000);
    try {
      await fixtureService.advance(3);
      const first = (await fixtureService.overview()).calibration;
      expect(first).toBeCloseTo(0.8 * 0.9 + (0.2 * 5600) / 8000);
      await fixtureService.advance(3);
      expect((await fixtureService.overview()).calibration).toBe(first);
      expect(await fixtureService.outcomes()).toHaveLength(1);
    } finally {
      vi.restoreAllMocks();
    }
  });
  it('tracking freeze cannot approve and expected budget changes create no proposal', async () => {
    await fixtureService.scenario('S5');
    const d = (await fixtureService.decisions())[0];
    await expect(fixtureService.approve(d.decision_id, d.decision_hash)).rejects.toThrow('409');
    await fixtureService.scenario('S7');
    expect(await fixtureService.decisions()).toHaveLength(0);
  });
  it('displays missing and valid zero financial values differently', () => {
    expect(money(null)).toBe('—');
    expect(money(0)).toBe('₹0');
    expect(money(-1234)).toBe('−₹1,234');
  });
});
