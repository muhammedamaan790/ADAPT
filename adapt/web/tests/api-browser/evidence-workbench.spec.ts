import { expect, test } from '@playwright/test';
import { fixtureDecision, fixtureOverview } from '../../src/api/fixtures';
import { signedMoney } from '../../src/lib/format';

test('outcome comparison retains signed values and a common zero for losses, gains and zeros', async ({
  page,
}) => {
  let predicted = -400;
  let measured = 200;
  await page.route('**/api/v1/**', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/overview')) return route.fulfill({ json: fixtureOverview('DEMO_01', 17) });
    if (path.endsWith('/decisions')) return route.fulfill({ json: [fixtureDecision('DEMO_01')] });
    if (path.endsWith('/outcomes'))
      return route.fulfill({
        json: [
          {
            outcome_id: 'outcome-signed',
            decision_id: fixtureDecision('DEMO_01').decision_id,
            world: 'SIMULATED',
            class: 'OPTIMIZATION',
            verdict: 'INCONCLUSIVE',
            predicted,
            measured,
            counterfactual: 1000,
            factor_before: 1,
            factor_after: 1,
            matured_at: '2026-10-08T10:00:00+05:30',
            method: 'Mocked signed-value test; not causal evidence.',
            calibration_applied: false,
          },
        ],
      });
    return route.fulfill({ status: 503, json: { detail: 'Service not required by this test.' } });
  });
  for (const pair of [
    [-400, 200],
    [-400, -100],
    [200, -400],
    [0, 0],
  ]) {
    [predicted, measured] = pair;
    await page.goto('/outcomes');
    const rows = page.locator('.outcome-comparison-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.nth(0)).toContainText(signedMoney(predicted));
    await expect(rows.nth(1)).toContainText(signedMoney(measured));
    const geometry = await rows.evaluateAll((nodes) =>
      nodes.map((node) => {
        const track = node.querySelector('.signed-track')!.getBoundingClientRect();
        const zero = node.querySelector('.zero-line')!.getBoundingClientRect();
        const bar = node
          .querySelector('.signed-track > i:not(.zero-line)')!
          .getBoundingClientRect();
        return {
          zero: zero.x,
          left: bar.x,
          right: bar.right,
          width: bar.width,
          trackLeft: track.x,
          trackRight: track.right,
        };
      }),
    );
    for (let i = 0; i < geometry.length; i++) {
      const g = geometry[i],
        value = pair[i];
      expect(g.left).toBeGreaterThanOrEqual(g.trackLeft - 1);
      expect(g.right).toBeLessThanOrEqual(g.trackRight + 1);
      if (value < 0) expect(Math.abs(g.right - g.zero)).toBeLessThan(1);
      else expect(Math.abs(g.left - g.zero)).toBeLessThan(1);
      if (value === 0) expect(g.width).toBe(0);
    }
  }
});
