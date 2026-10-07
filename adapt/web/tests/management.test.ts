import { beforeEach, describe, expect, it, vi } from 'vitest';
import { management, readWorkspaceRegistry } from '../src/api/management';
import { fixtureService, loadFixtureState, resetFixtureForTests } from '../src/api/fixture-service';
import {
  evaluationEnvelopeSchema,
  evaluationReportSchema,
  modelDetailSchema,
  workspaceListSchema,
  archiveSchema,
} from '../src/api/management-contracts';
import { evaluationTestReport, modelTestDetail } from './management-data';
beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => storage.get(k) || null,
    setItem: (k: string, v: string) => storage.set(k, v),
    removeItem: (k: string) => storage.delete(k),
  });
  resetFixtureForTests();
});
describe('Isolated frontend workspaces', () => {
  it('does not relabel a loaded tab when another tab selects a different workspace', async () => {
    const w = await management.createWorkspace('Other tab');
    localStorage.setItem('adapt.active-workspace', w.id);
    expect(readWorkspaceRegistry().active_id).toBe('default');
    expect((await fixtureService.overview()).workspace).toBe('D2C workspace');
    loadFixtureState();
    expect(readWorkspaceRegistry().active_id).toBe(w.id);
    expect((await fixtureService.overview()).workspace).toBe('Other tab');
  });
  it('rejects a corrupted selected-workspace registry without using an invalid storage key', async () => {
    localStorage.setItem('adapt.active-workspace', 'not-registered');
    localStorage.setItem('adapt.workspace-registry', '{broken');
    loadFixtureState();
    expect(readWorkspaceRegistry().active_id).toBe('default');
    expect((await fixtureService.overview()).scenario).toBe('DEMO_01');
  });
  it('creates explicitly and restores independent scenario, clock and calibration', async () => {
    await fixtureService.scenario('S5');
    await fixtureService.advance(7);
    const before = await fixtureService.overview();
    const w = await management.createWorkspace('Refill demo');
    expect(readWorkspaceRegistry().active_id).toBe('default');
    await management.activateWorkspace(w.id);
    expect(await fixtureService.overview()).toMatchObject({
      world_day: 0,
      scenario: 'DEMO_01',
      workspace: 'Refill demo',
      calibration: 0.9,
    });
    await fixtureService.scenario('S1');
    await management.activateWorkspace('default');
    expect(await fixtureService.overview()).toEqual(before);
    await management.activateWorkspace(w.id);
    loadFixtureState();
    expect((await fixtureService.overview()).scenario).toBe('S1');
  });
  it('blocks switching during an unresolved execution without losing current identity', async () => {
    const w = await management.createWorkspace('Another demo');
    const d = (await fixtureService.decisions())[0];
    await fixtureService.approve(d.decision_id, d.decision_hash);
    await expect(management.activateWorkspace(w.id)).rejects.toThrow(
      'Resolve the current execution',
    );
    expect(readWorkspaceRegistry().active_id).toBe('default');
    expect(await fixtureService.executions()).toHaveLength(1);
  });
  it('rejects duplicate names and fails visibly when storage is unavailable', async () => {
    await management.createWorkspace('Second demo');
    await expect(management.createWorkspace(' second DEMO ')).rejects.toThrow('already exists');
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => {
      throw new Error('Storage unavailable');
    });
    await expect(management.createWorkspace('Third demo')).rejects.toThrow('Storage unavailable');
    expect(readWorkspaceRegistry().items).toHaveLength(2);
  });
});
describe('Management truth contracts', () => {
  it('requires complete paired rows and coherent report availability', () => {
    expect(evaluationReportSchema.safeParse(evaluationTestReport).success).toBe(true);
    expect(
      evaluationReportSchema.safeParse({
        ...evaluationTestReport,
        rows: evaluationTestReport.rows.slice(1),
      }).success,
    ).toBe(false);
    expect(
      evaluationReportSchema.safeParse({
        ...evaluationTestReport,
        rows: [...evaluationTestReport.rows, evaluationTestReport.rows[0]],
      }).success,
    ).toBe(false);
    expect(
      evaluationReportSchema.safeParse({ ...evaluationTestReport, seeds: [42, 42] }).success,
    ).toBe(false);
    expect(
      evaluationEnvelopeSchema.safeParse({ status: 'AVAILABLE', report: null, note: 'missing' })
        .success,
    ).toBe(false);
  });
  it('does not grant promotion or rollback when artifacts/gates/history are missing', () => {
    expect(modelDetailSchema.safeParse(modelTestDetail).success).toBe(true);
    for (const replacement of [
      { artifact_hash: null },
      { checks: [] },
      { checks: [{ id: 'FAIL', label: 'Failed', passed: false, detail: 'Rejected' }] },
      { allowed_actions: ['ROLLBACK'] },
    ])
      expect(modelDetailSchema.safeParse({ ...modelTestDetail, ...replacement }).success).toBe(
        false,
      );
  });
  it('has no fake registry changes, evaluation results or archived replay proof', async () => {
    expect(await management.evaluation()).toMatchObject({ status: 'NOT_AVAILABLE', report: null });
    await expect(
      management.modelAction(modelTestDetail, 'PROMOTE', 'Test promotion reason'),
    ).rejects.toThrow('connected backend');
    const d = (await fixtureService.decisions())[0];
    const archive = await management.archive(d);
    expect(archive.status).toBe('UNAVAILABLE');
    expect(archive.environment_fingerprint).toBeNull();
    expect(archive.steps).toEqual([]);
    expect(archiveSchema.safeParse({ ...archive, status: 'AVAILABLE' }).success).toBe(false);
  });
  it('rejects missing active workspace and broken archive artifact references', async () => {
    expect(
      workspaceListSchema.safeParse({
        active_id: 'missing',
        items: [{ id: 'default', name: 'Default', currency: 'INR', timezone: 'Asia/Kolkata' }],
      }).success,
    ).toBe(false);
    const archive = await management.archive((await fixtureService.decisions())[0]);
    expect(
      archiveSchema.safeParse({
        ...archive,
        steps: [
          {
            id: 'a',
            at: '2026-10-07T09:00:00Z',
            label: 'Step',
            detail: 'Missing',
            artifact_id: 'not-there',
          },
        ],
      }).success,
    ).toBe(false);
  });
});
