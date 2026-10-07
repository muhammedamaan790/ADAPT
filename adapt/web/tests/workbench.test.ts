import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fixtureService, resetFixtureForTests } from '../src/api/fixture-service';
import {
  anomalySchema,
  evaluationSchema,
  ledgerSchema,
  optimizerContextSchema,
} from '../src/api/workbench-contracts';
import type { AllocationInput, RecoveryInput } from '../src/api/workbench-contracts';
import { allocationErrors } from '../src/lib/allocation';

beforeEach(() => {
  vi.restoreAllMocks();
  resetFixtureForTests();
});
async function allocation(): Promise<AllocationInput> {
  const c = await fixtureService.optimizerContext();
  return {
    decision_id: c.decision_id!,
    decision_hash: c.decision_hash!,
    objective: 'PROFIT',
    policy_version: c.policy_version,
    legs: c.campaigns.map((l) => ({ budget_id: l.budget_id, after: l.after })),
  };
}
async function approved() {
  const d = (await fixtureService.decisions())[0];
  await fixtureService.approve(d.decision_id, d.decision_hash);
  const e = (await fixtureService.executions())[0];
  const input: RecoveryInput = {
    execution_id: e.execution_id,
    decision_hash: d.decision_hash,
    action: 'verify',
    reason: 'Confirmed recovery review.',
  };
  return { d, e, input };
}

describe('Workbenches preserve valuation and recovery boundaries', () => {
  it('contracts validate and resolving an incident records the reason', async () => {
    const a = (await fixtureService.anomalies())[0];
    expect(() => anomalySchema.parse(a)).not.toThrow();
    const context = await fixtureService.optimizerContext();
    expect(() => optimizerContextSchema.parse(context)).not.toThrow();
    await expect(fixtureService.anomalyStatus(a.anomaly_id, 'RESOLVED', '')).rejects.toThrow(
      'reason',
    );
    await fixtureService.anomalyStatus(a.anomaly_id, 'RESOLVED', 'Source values reviewed.');
    expect((await fixtureService.anomaly(a.anomaly_id)).resolution_reason).toBe(
      'Source values reviewed.',
    );
  });
  it('S7 expected budget movement is not an efficiency incident', async () => {
    await fixtureService.scenario('S7');
    const rows = await fixtureService.anomalies();
    expect(rows.filter((a) => a.kind !== 'BUDGET_CHANGE')).toHaveLength(0);
    expect(rows[0].decision_id).toBeNull();
  });
  it('rejects unsafe inputs, stale identity, missing and duplicated budget units', async () => {
    const c = await fixtureService.optimizerContext();
    const input = await allocation();
    expect(allocationErrors(c, input)).toEqual([]);
    expect(allocationErrors(c, { ...input, decision_hash: 'stale' }).join()).toContain('changed');
    expect(allocationErrors(c, { ...input, legs: input.legs.slice(1) }).join()).toContain('Every');
    expect(
      allocationErrors(c, { ...input, legs: [...input.legs, input.legs[0]] }).join(),
    ).toContain('exactly once');
    expect(
      allocationErrors(c, {
        ...input,
        legs: input.legs.map((l, i) => ({
          ...l,
          after: i === 0 ? c.campaigns[0].before + 1 : l.after,
        })),
      }).join(),
    ).toContain('inventory');
    expect(
      allocationErrors(c, {
        ...input,
        legs: input.legs.map((l, i) => ({ ...l, after: i === 0 ? NaN : l.after })),
      }).join(),
    ).toContain('whole-rupee');
    expect(allocationErrors({ ...c, reserve_floor: 10000 }, input).join()).toContain('reserve');
  });
  it('arbitrary custom allocation has no fabricated forecast or executable revision', async () => {
    const input = await allocation();
    const recorded = await fixtureService.evaluateAllocation(input);
    expect(() => evaluationSchema.parse(recorded)).not.toThrow();
    expect(recorded.estimate_status).toBe('AVAILABLE');
    input.legs[1].after -= 1000;
    const custom = await fixtureService.evaluateAllocation(input);
    expect(custom.estimate_status).toBe('NOT_ESTIMABLE');
    expect(custom.estimate).toBeNull();
    const revision = await fixtureService.modify(input);
    expect(revision.status).toBe('DRAFT');
    expect(revision.valuation_status).toBe('NOT_ESTIMABLE');
    expect((await fixtureService.decision(input.decision_id)).status).toBe('SUPERSEDED');
    await expect(
      fixtureService.approve(revision.decision_id, revision.decision_hash),
    ).rejects.toThrow('409');
    await expect(fixtureService.approve(input.decision_id, input.decision_hash)).rejects.toThrow(
      '409',
    );
    expect(await fixtureService.executions()).toHaveLength(0);
  });
  it('unknown request blocks retries/reset/advance and verification does not resend', async () => {
    const { input } = await approved();
    await fixtureService.fault('UNKNOWN');
    await expect(fixtureService.advance(3)).rejects.toThrow();
    await expect(fixtureService.reset(42)).rejects.toThrow();
    await expect(fixtureService.scenario('S7')).rejects.toThrow();
    await expect(fixtureService.recover({ ...input, action: 'retry' })).rejects.toThrow('Verify');
    const writes = (await fixtureService.ledger()).filter((l) => l.action === 'SET_BUDGET').length;
    await fixtureService.recover(input);
    const count = (await fixtureService.ledger()).length;
    await fixtureService.recover(input);
    expect((await fixtureService.ledger()).length).toBe(count);
    expect((await fixtureService.ledger()).filter((l) => l.action === 'SET_BUDGET')).toHaveLength(
      writes,
    );
    expect((await fixtureService.executions())[0].state).toBe('SUCCEEDED');
  });
  it('failed legs retry only after verification and a stale hash is rejected', async () => {
    const { input } = await approved();
    await fixtureService.fault('FAILED');
    await fixtureService.recover(input);
    await expect(
      fixtureService.recover({ ...input, decision_hash: 'stale', action: 'retry' }),
    ).rejects.toThrow('409');
    const before = (await fixtureService.ledger()).filter((l) => l.action === 'SET_BUDGET').length;
    await fixtureService.recover({ ...input, action: 'retry' });
    expect((await fixtureService.ledger()).filter((l) => l.action === 'SET_BUDGET')).toHaveLength(
      before + 1,
    );
    await expect(fixtureService.recover({ ...input, action: 'retry' })).rejects.toThrow();
  });
  it('restoration records budgets, prevents optimization feedback, and reconciliation cannot invent read-back', async () => {
    const { input } = await approved();
    await fixtureService.fault('FAILED');
    await fixtureService.recover(input);
    await expect(
      fixtureService.recover({ ...input, action: 'reconcile', final_resolution: 'COMPENSATED' }),
    ).rejects.toThrow('Restore');
    await fixtureService.recover({ ...input, action: 'rollback' });
    const e = (await fixtureService.executions())[0];
    expect(e.state).toBe('COMPENSATED');
    expect(e.legs.every((l) => l.read_back_budget === l.before)).toBe(true);
    await fixtureService.recover({
      ...input,
      action: 'reconcile',
      final_resolution: 'COMPENSATED',
    });
    expect((await fixtureService.executions())[0].state).toBe('RESOLVED_MANUALLY');
    await fixtureService.advance(3);
    expect(await fixtureService.outcomes()).toHaveLength(0);
    (await fixtureService.ledger()).forEach((l) =>
      expect(() => ledgerSchema.parse(l)).not.toThrow(),
    );
  });
  it('outcome matures relative to execution day, not workspace start', async () => {
    await fixtureService.advance(7);
    const { input } = await approved();
    await fixtureService.recover(input);
    await fixtureService.advance(1);
    expect(await fixtureService.outcomes()).toHaveLength(0);
    await fixtureService.advance(2);
    expect(await fixtureService.outcomes()).toHaveLength(1);
  });
});
