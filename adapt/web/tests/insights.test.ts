import { beforeEach, describe, expect, it, vi } from 'vitest';
import { parseCsv, validateImport, importFields } from '../src/lib/csv';
import { insights, askCopilot } from '../src/api/insights';
import { fixtureService, resetFixtureForTests } from '../src/api/fixture-service';
import { appHref, replaySchema, creativeScoreSchema } from '../src/api/insight-contracts';

beforeEach(resetFixtureForTests);
const mapping = (type: 'ads' | 'inventory' | 'margins') =>
  Object.fromEntries(importFields[type].map((k) => [k, k]));
describe('CSV import validation', () => {
  it('parses BOM, CRLF, embedded newlines, commas and escaped quotes', () => {
    expect(parseCsv('\uFEFFsku,note\r\nhero,"a,b\n""quoted"""\r\n')).toEqual({
      headers: ['sku', 'note'],
      rows: [['hero', 'a,b\n"quoted"']],
    });
  });
  it.each(['a,a\n1,2', 'a,b\n1', 'a,b\n"oops,2', 'a,b\n"a"oops,2', 'a,b\n'])(
    'rejects malformed CSV %s',
    (s) => expect(() => parseCsv(s)).toThrow(),
  );
  it('accepts actual zero while rejecting missing, negative and fractional counts', () => {
    const csv = parseCsv('sku,on_hand,reserved,safety_stock\nhero,0,0,0');
    expect(validateImport(csv, 'inventory', mapping('inventory')).errors).toEqual([]);
    for (const row of ['hero,,0,0', 'hero,-1,0,0', 'hero,1.5,0,0', 'hero,0,1,0'])
      expect(
        validateImport(
          parseCsv('sku,on_hand,reserved,safety_stock\n' + row),
          'inventory',
          mapping('inventory'),
        ).errors.length,
      ).toBeGreaterThan(0);
  });
  it('rejects duplicate keys, invalid dates and clicks above impressions', () => {
    const headers = importFields.ads.join(',');
    expect(
      validateImport(parseCsv(headers + '\n2026-02-30,b,Google,5,10,11'), 'ads', mapping('ads'))
        .errors,
    ).toHaveLength(2);
    expect(
      validateImport(
        parseCsv(headers + '\n2026-10-07,b,Google,5,10,2\n2026-10-07,b,Google,6,12,3'),
        'ads',
        mapping('ads'),
      ).errors.some((e) => e.includes('duplicate business key')),
    ).toBe(true);
  });
  it('rejects incomplete and reused column mappings', () => {
    const csv = parseCsv('sku,price,unit_cost\nhero,500,200');
    expect(validateImport(csv, 'margins', {}).errors).toHaveLength(1);
    expect(
      validateImport(csv, 'margins', { sku: 'sku', price: 'price', unit_cost: 'price' }).errors,
    ).toHaveLength(1);
  });
});
describe('Insight truth and lifecycle boundaries', () => {
  it('recorded intervals are ordered and P10 agrees with model loss risk', async () => {
    const d = (await fixtureService.decisions())[0];
    const c = await insights.comparison(d);
    for (const e of [
      d.expected,
      ...c.strategies.map((s) => s.estimate),
      ...c.alternatives.map((a) => a.estimate),
    ]) {
      expect(e.p10).toBeLessThanOrEqual(e.p50);
      expect(e.p50).toBeLessThanOrEqual(e.p90);
      if (e.prob_loss > 0.1) expect(e.p10).toBeLessThanOrEqual(0);
      if (e.prob_loss < 0.1) expect(e.p10).toBeGreaterThanOrEqual(0);
    }
    expect(c.strategies[0].estimate).toMatchObject({
      raw_pred: 0,
      calibrated_pred: 0,
      p10: 0,
      p50: 0,
      p90: 0,
      prob_loss: 0,
    });
  });
  it('has no invented learning samples, model artifacts or held-out uplift', async () => {
    const l = await insights.learning();
    expect(l.accuracy).toMatchObject({ sample_count: 0, mae: null });
    expect(l.uplift.rows).toEqual([]);
    expect(l.models.every((m) => m.status === 'NOT_AVAILABLE')).toBe(true);
    expect(await insights.scoreCreative('Bundle benefits creative')).toMatchObject({
      status: 'NOT_ESTIMABLE',
      score: null,
    });
  });
  it('reads feedback exactly once from the existing outcome lifecycle', async () => {
    const d = (await fixtureService.decisions())[0];
    await fixtureService.approve(d.decision_id, d.decision_hash);
    vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 5000);
    try {
      await fixtureService.advance(3);
      const l = await insights.learning();
      expect(l.accuracy.sample_count).toBe(1);
      expect(l.calibration.updates).toHaveLength(1);
      await fixtureService.advance(3);
      expect((await insights.learning()).calibration.updates).toHaveLength(1);
    } finally {
      vi.restoreAllMocks();
    }
  });
  it('fixture import stages metadata without changing engine values', async () => {
    const before = await fixtureService.overview();
    const r = await insights.importData(
      'margins',
      [{ sku: 'hero', price: 600, unit_cost: 200 }],
      mapping('margins'),
    );
    expect(r.status).toBe('STAGED');
    expect(await fixtureService.overview()).toEqual(before);
  });
  it('comparison revisions have validated caps, replay stays unavailable', async () => {
    const d = (await fixtureService.decisions())[0];
    const c = await insights.comparison(d);
    expect(c.strategies).toHaveLength(4);
    expect(c.alternatives.every((a) => a.checks.every((c) => c.passed))).toBe(true);
    expect(await insights.replay(d)).toMatchObject({ status: 'UNAVAILABLE', actual_hash: null });
    await fixtureService.scenario('S5');
    expect((await insights.comparison((await fixtureService.decisions())[0])).status).toBe(
      'NOT_ESTIMABLE',
    );
    expect(await insights.fatigue()).toEqual([]);
  });
  it('copilot is grounded and cancellation produces no answer', async () => {
    const c = await askCopilot('Why this allocation?', new AbortController().signal);
    expect(c.mode).toBe('TEMPLATE');
    expect(c.evidence.some((e) => e.href.startsWith('/decisions/'))).toBe(true);
    const abort = new AbortController();
    abort.abort();
    await expect(askCopilot('What feedback?', abort.signal)).rejects.toThrow('Cancelled');
  });
  it('rejects external evidence links and dishonest replay or creative states', () => {
    for (const href of ['https://example.com', '//example.com', 'javascript:alert(1)', '/settings'])
      expect(appHref.safeParse(href).success).toBe(false);
    expect(
      replaySchema.safeParse({
        status: 'VERIFIED',
        expected_hash: 'a',
        actual_hash: 'b',
        message: 'ok',
      }).success,
    ).toBe(false);
    expect(
      creativeScoreSchema.safeParse({ status: 'NOT_ESTIMABLE', score: 0, explanation: 'missing' })
        .success,
    ).toBe(false);
  });
});
