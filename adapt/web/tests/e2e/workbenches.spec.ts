import { expect, test } from '@playwright/test';

test('anomaly investigation status, lineage and filtering persist', async ({ page }) => {
  await page.goto('/anomalies');
  await expect(page.getByRole('heading', { name: 'Anomalies', exact: true })).toBeVisible();
  await expect(page.getByText('not estimable', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Acknowledge', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Acknowledge', exact: true })).toBeDisabled();
  await page.getByLabel('Resolution reason').fill('Source health reviewed and owner informed.');
  await page.getByRole('button', { name: 'Resolve investigation', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Reopen', exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Recorded reason: Source health reviewed/)).toBeVisible();
  await page.getByLabel('Search investigations').fill('no-such-campaign');
  await expect(page.getByRole('heading', { name: 'No matching investigations' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByRole('button', { name: 'Reopen', exact: true })).toBeVisible();
});

test('unknown read-back, failed retry, settings restoration and action ledger', async ({
  page,
}) => {
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm fixture approval' }).click();
  await page.getByRole('link', { name: 'Execution & Ledger', exact: true }).click();
  await page.getByRole('button', { name: 'Inject unknown result', exact: true }).click();
  await expect(page.getByText('unknown', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Retry failed legs', exact: true })).toBeDisabled();
  await page.getByRole('link', { name: 'Scenario Lab', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Advance 3 days', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Reset workspace', exact: true })).toBeDisabled();
  await page.getByRole('link', { name: 'Execution & Ledger', exact: true }).click();
  await page.getByRole('button', { name: 'Verify platform state', exact: true }).click();
  await expect(page.getByText('succeeded', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Inject failed leg', exact: true }).click();
  await page.getByRole('button', { name: 'Verify platform state', exact: true }).click();
  await page.getByRole('button', { name: 'Retry failed legs', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Confirm recovery', exact: true })).toBeDisabled();
  await page
    .getByLabel('Recovery reason')
    .fill('Verified request failed; safe absolute budget retry.');
  await page.getByRole('button', { name: 'Confirm recovery', exact: true }).click();
  await expect(page.getByText('succeeded', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Restore prior settings', exact: true }).click();
  await page.getByLabel('Recovery reason').fill('Restore previous campaign settings after review.');
  await page.getByRole('button', { name: 'Confirm recovery', exact: true }).click();
  await expect(page.getByText('compensated', { exact: true })).toBeVisible();
  await page.getByLabel('Action type').selectOption('RESTORE_SETTINGS');
  await expect(
    page.locator('.ledger-list').getByText('restore settings', { exact: true }),
  ).toHaveCount(4);
  await page.getByRole('button', { name: 'Record reconciliation', exact: true }).click();
  await page.getByLabel('Recovery reason').fill('Read-backs match every restored prior setting.');
  await page.getByRole('button', { name: 'Confirm recovery', exact: true }).click();
  await expect(page.getByText('resolved manually', { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByText('resolved manually', { exact: true })).toBeVisible();
});

test('new workspaces fit mobile and preserve keyboard-accessible navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/v1/**', (route) =>
    route.fulfill({ status: 404, json: { detail: 'Missing backend route.' } }),
  );
  for (const path of ['/anomalies', '/decisions', '/executions', '/data']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.getByRole('button', { name: 'Open navigation' }).click();
    await expect(page.getByRole('link', { name: 'Scenario Lab', exact: true })).toBeVisible();
    await page.keyboard.press('Escape');
    if (path === '/anomalies') {
      const signal = await page.locator('.signal-change b').boundingBox();
      expect(signal!.y + signal!.height).toBeLessThanOrEqual(844);
      await expect(page.getByLabel('Search investigations')).toBeHidden();
      await page.getByRole('button', { name: /Filter investigations/ }).click();
      await expect(page.getByLabel('Search investigations')).toBeVisible();
      await page.getByRole('button', { name: /Hide filters/ }).click();
      const labelPx = await page
        .locator('.trend-chart svg')
        .evaluate(
          (svg) =>
            (Number.parseFloat(getComputedStyle(svg.querySelector('text')!).fontSize) *
              svg.getBoundingClientRect().width) /
            Number(svg.getAttribute('viewBox')!.split(' ')[2]),
        );
      expect(labelPx).toBeGreaterThanOrEqual(9);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
});
