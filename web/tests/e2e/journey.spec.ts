import { expect, test } from '@playwright/test';

test('golden frontend journey: evidence → approve → verified → outcome → feedback', async ({
  page,
}) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Command Center', exact: true })).toBeVisible();
  await expect(page.getByText('FRONTEND FIXTURES', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Lineage for Net revenue', exact: true }).click();
  await expect(page.getByText('GMV − discounts − refunds (tax excluded)')).toBeVisible();
  await page.getByRole('link', { name: /Hero campaign is losing efficiency/ }).click();
  await expect(
    page.getByRole('heading', { name: 'Why not the obvious alternative?' }),
  ).toBeVisible();
  await expect(page.getByText('₹4,000 unallocated', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Confirm fixture approval' })).toBeDisabled();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm fixture approval' }).click();
  await expect(page.getByRole('button', { name: 'Executed & verified' })).toBeVisible({
    timeout: 10000,
  });
  await expect(page.getByText('verified', { exact: true })).toHaveCount(4);
  await page.getByRole('button', { name: 'Advance 3 days', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Prediction meets the outcome' })).toBeVisible();
  await page.getByText('Measurement & feedback details', { exact: true }).click();
  await expect(page.getByText(/Factor updated once: 0.90 → 0.86/)).toBeVisible();
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Prediction meets the outcome' })).toBeVisible();
  await page.getByRole('link', { name: 'Scenario Lab', exact: true }).click();
  await expect(page.getByText('0.86×', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Advance 3 days', exact: true }).click();
  await expect(page.getByText('0.86×', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Reset workspace', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm reset', exact: true }).click();
  await expect(page.getByText('0.90×', { exact: true })).toBeVisible();
});

test('tracking freezes approval; S7 produces an empty state', async ({ page }) => {
  await page.goto('/scenarios');
  await page.getByRole('radio', { name: /S5/ }).check();
  await page.getByRole('button', { name: 'Load scenario', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm load', exact: true }).click();
  await page.getByRole('link', { name: 'Decision Center', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Execution blocked' })).toBeDisabled();
  await expect(page.getByText('Resolve tracking first', { exact: true })).toBeVisible();
  await expect(page.getByText('Model P(loss)', { exact: true })).toHaveCount(0);
  await page.getByRole('link', { name: 'Scenario Lab', exact: true }).click();
  await page.getByRole('radio', { name: /S7/ }).check();
  await page.getByRole('button', { name: 'Load scenario', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm load', exact: true }).click();
  await page.getByRole('link', { name: 'Decision Center', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'No decisions to review' })).toBeVisible();
});

test('rejection requires a reason and survives navigation', async ({ page }) => {
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Reject with a reason' }).click();
  await expect(page.getByRole('button', { name: 'Confirm rejection' })).toBeDisabled();
  await page
    .getByLabel('Reason for rejection')
    .fill('Need an inventory refresh before allocation.');
  await page.getByRole('button', { name: 'Confirm rejection' }).click();
  await expect(page.getByRole('button', { name: 'Proposal rejected' })).toBeDisabled();
  await expect(page.getByText('Rejected · no execution started', { exact: true })).toBeVisible();
  await expect(page.getByText('Waiting for your approval', { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole('button', { name: 'Proposal rejected' })).toBeDisabled();
});

test('mobile routes have no page overflow and theme persists', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of ['/', '/decisions', '/scenarios']) {
    await page.goto(route);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
    if (route === '/decisions') {
      await expect(page.locator('.allocation-mobile')).toBeVisible();
      await expect(
        page.locator('.allocation-mobile').getByText('₹32,000', { exact: true }),
      ).toBeVisible();
      await expect(
        page.locator('.allocation-mobile dd').filter({ hasText: '−₹8,000' }),
      ).toBeVisible();
    }
  }
  await page.getByRole('button', { name: 'Switch to dark theme' }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Switch to light theme' })).toBeVisible();
});
