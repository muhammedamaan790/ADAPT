import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  confidenceBandsSchema,
  sourceChecksSchema,
  workspaceObjectiveSchema,
  policyHistorySchema,
  sqlResultSchema,
} from '../src/api/completion-contracts';
import { dateTime } from '../src/lib/format';
import { confidence, source, objective, history } from './completion-data';
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});
describe('completion evidence contracts', () => {
  it('accepts actual backend source-health field names and normalized scores', () => {
    expect(sourceChecksSchema.parse([source])[0].completeness).toBe(0.8);
    expect(sourceChecksSchema.safeParse([{ ...source, completeness: 80 }]).success).toBe(false);
  });
  it('requires supported current objectives and unique audit versions', () => {
    expect(workspaceObjectiveSchema.parse(objective).objective).toBe('PROFIT');
    expect(
      workspaceObjectiveSchema.safeParse({ ...objective, supported_objectives: ['GROWTH'] })
        .success,
    ).toBe(false);
    expect(
      policyHistorySchema.safeParse({
        ...history,
        versions: [history.versions[0], history.versions[0]],
      }).success,
    ).toBe(false);
  });
  it('rejects impossible sample counts, empty fabricated intervals and unavailable evidence', () => {
    expect(confidenceBandsSchema.parse(confidence).pools).toHaveLength(1);
    const pool = confidence.pools[0];
    for (const altered of [
      { ...pool.regions[1], successes: 11 },
      { ...pool.regions[0], wilson_lower: 0.6 },
      { ...pool.regions[1], wilson_lower: 0.95, wilson_upper: 0.8 },
    ])
      expect(
        confidenceBandsSchema.safeParse({
          ...confidence,
          pools: [{ ...pool, regions: [altered, pool.regions[2], pool.regions[0]] }],
        }).success,
      ).toBe(false);
    expect(
      confidenceBandsSchema.safeParse({ ...confidence, status: 'NOT_AVAILABLE' }).success,
    ).toBe(false);
  });
  it('bounds SQL results, rejecting extra columns and non-scalar values', () => {
    const result = {
      query: 'SELECT x',
      columns: ['x'],
      rows: [[null], [true], [1], ['<script>']],
      truncated: false,
      elapsed_ms: 5,
      as_of: '2026-10-07T06:00:00Z',
    };
    expect(sqlResultSchema.parse(result).rows).toHaveLength(4);
    for (const rows of [[[1, 2]], [[{}]], Array.from({ length: 501 }, () => [1])])
      expect(sqlResultSchema.safeParse({ ...result, rows }).success).toBe(false);
  });
  it('formats workspace timestamps in Kolkata regardless of machine timezone and tolerates invalid dates', () => {
    expect(dateTime('2026-10-07T06:00:00Z')).toMatch(/11:30/);
    expect(dateTime('2026-10-07T06:00:00')).toMatch(/06:00/);
    expect(dateTime('bad')).toBe('Timestamp unavailable');
  });
});
describe('API acknowledgement binding', () => {
  it('rejects an objective update acknowledged for another workspace', async () => {
    vi.stubEnv('VITE_DATA_MODE', 'api');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            ...objective,
            workspace_id: 'other',
            objective: 'GROWTH',
            revision: 'objective-2',
          }),
        ),
      ),
    );
    const { completion } = await import('../src/api/completion');
    await expect(
      completion.changeObjective(
        workspaceObjectiveSchema.parse(objective),
        'GROWTH',
        'Reviewed growth strategy',
      ),
    ).rejects.toThrow(/acknowledgement/);
  });
  it('rejects SQL results for a different query', async () => {
    vi.stubEnv('VITE_DATA_MODE', 'api');
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            query: 'SELECT other',
            columns: ['x'],
            rows: [[1]],
            truncated: false,
            elapsed_ms: 1,
            as_of: '2026-10-07T06:00:00Z',
          }),
        ),
      ),
    );
    const { completion } = await import('../src/api/completion');
    await expect(completion.sql('SELECT current')).rejects.toThrow(/another query/);
  });
});
