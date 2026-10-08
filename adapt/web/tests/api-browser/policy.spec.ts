import { expect, test } from '@playwright/test';
import { testPolicy, testShadow } from '../policy-data';
import { fixtureOverview } from '../../src/api/fixtures';
import type { Policy } from '../../src/api/policy-contracts';
test('mode request binds revision, requires review, retains conflicts and never executes budgets', async ({
  page,
}) => {
  let p: Policy = structuredClone(testPolicy),
    writes = 0,
    conflict = true;
  await page.route('**/api/v1/**', async (r) => {
    const path = new URL(r.request().url()).pathname;
    if (r.request().method() !== 'GET') {
      expect(path).toBe('/api/v1/policy');
      expect(r.request().method()).toBe('PUT');
      writes++;
      const body = r.request().postDataJSON();
      expect(body).toMatchObject({
        policy_version: p.policy_version,
        revision: p.revision,
        channel: 'TikTok',
        mode: 'SIMULATION_AUTONOMOUS',
        reason: 'Reviewed simulation qualification evidence.',
      });
      expect(r.request().headers()['idempotency-key']).toBeTruthy();
      expect(r.request().headers()['x-request-id']).toBeTruthy();
      if (conflict)
        return r.fulfill({
          status: 409,
          json: { detail: 'Policy evidence changed; request was not accepted.' },
        });
      p = {
        ...p,
        revision: 'test-revision-2',
        channels: p.channels.map((c) => (c.channel === 'TikTok' ? { ...c, mode: body.mode } : c)),
      };
      return r.fulfill({ json: p });
    }
    return path.endsWith('/policy')
      ? r.fulfill({ json: p })
      : path.endsWith('/learning/shadow')
        ? r.fulfill({ json: testShadow })
        : r.fulfill({
            status: 503,
            json: { detail: 'Other engine routes intentionally unavailable.' },
          });
  });
  await page.goto('/executions?section=policy');
  await expect(page.getByRole('heading', { name: 'TikTok policy' })).toBeVisible();
  await page.getByLabel('Requested execution mode').selectOption('SIMULATION_AUTONOMOUS');
  await page.getByRole('button', { name: 'Review mode change' }).click();
  const submit = page.getByRole('button', { name: 'Confirm mode request' });
  await expect(submit).toBeDisabled();
  await page.getByLabel('Mode change reason').fill('Reviewed simulation qualification evidence.');
  await expect(submit).toBeDisabled();
  await page.getByRole('checkbox').check();
  await submit.click();
  await expect(page.getByText('Policy evidence changed; request was not accepted.')).toBeVisible();
  await expect(page.getByRole('dialog')).toBeVisible();
  conflict = false;
  await submit.click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByLabel('Requested execution mode')).toHaveValue('SIMULATION_AUTONOMOUS');
  expect(writes).toBe(2);
  await page.getByLabel('Policy channel').selectOption('Google');
  await expect(page.getByText(/This test account does not serve ads/)).toBeVisible();
  await expect(
    page.getByLabel('Requested execution mode').locator('option[value="PRODUCTION_AUTONOMOUS"]'),
  ).toHaveAttribute('disabled', '');
  await expect(
    page.getByRole('region', { name: 'Production readiness' }).getByText('0 / 30 minimum'),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('Shadow world')).toBeVisible();
  await page.getByLabel('Shadow world').selectOption('REAL');
  await expect(page.getByText('Not executed', { exact: true })).toHaveCount(1);
  await expect(
    page.locator('.ledger-list').getByText('Real account context', { exact: true }),
  ).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test('invalid Google production permission and measured shadow claims fail visibly', async ({
  page,
}) => {
  const p = structuredClone(testPolicy);
  p.channels[1].allowed_modes.push('PRODUCTION_AUTONOMOUS');
  await page.route('**/api/v1/**', (r) =>
    new URL(r.request().url()).pathname.endsWith('/policy')
      ? r.fulfill({ json: p })
      : r.fulfill({
          json: { ...testShadow, records: [{ ...testShadow.records[0], measured_caa: 8000 }] },
        }),
  );
  await page.goto('/executions?section=policy');
  await expect(
    page.getByText(
      'Response contract mismatch at /policy. Ask the backend team to align the frontend schema.',
    ),
  ).toBeVisible();
  await expect(
    page.getByText(
      'Response contract mismatch at /learning/shadow. Ask the backend team to align the frontend schema.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review mode change' })).toHaveCount(0);
});
test('absent policy endpoints never substitute bundled fixtures', async ({ page }) => {
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/overview')) return r.fulfill({ json: fixtureOverview('DEMO_01', 0) });
    if (path.endsWith('/executions') || path.endsWith('/events')) return r.fulfill({ json: [] });
    return r.fulfill({ status: 404, json: { detail: 'Capability endpoint not yet implemented.' } });
  });
  await page.goto('/executions?section=policy');
  await expect(page.getByText('Capability endpoint not yet implemented.')).toHaveCount(2);
  await expect(page.getByRole('heading', { name: 'Meta policy' })).toHaveCount(0);
});
