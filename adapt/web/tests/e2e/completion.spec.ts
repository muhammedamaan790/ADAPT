import { test, expect } from '@playwright/test';
test('failed Copilot module retains workspace navigation and a closeable recovery dialog', async ({
  page,
}) => {
  await page.route('**/src/components/Copilot.tsx*', (r) => r.abort());
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await expect(page.getByRole('dialog', { name: 'Copilot unavailable' })).toBeVisible();
  await page.getByRole('button', { name: 'Close assistant' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await page.getByRole('link', { name: 'Decision Center', exact: true }).click();
  await expect(page).toHaveURL(/\/decisions$/);
  await expect(page.getByRole('button', { name: 'Approve & execute', exact: true })).toBeVisible();
});
test('fixture settings and confidence disclose unavailable backend evidence', async ({ page }) => {
  await page.goto('/executions?section=settings');
  await expect(page.getByLabel('Default objective')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Review objective change' })).toBeDisabled();
  await expect(page.getByRole('heading', { name: 'No policy history supplied' })).toBeVisible();
  await page.goto('/learning');
  await expect(
    page.getByRole('heading', { name: 'No qualification evidence supplied' }),
  ).toBeVisible();
});
test('fixture SQL tool is disabled and unknown routes recover through navigation', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await page.getByRole('button', { name: 'SQL inspection' }).click();
  await expect(page.getByRole('button', { name: 'Run read-only query' })).toBeDisabled();
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.goto('/does-not-exist');
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
  await page.getByRole('link', { name: 'Return to Command Center' }).click();
  await expect(page.getByRole('heading', { name: 'Command Center', exact: true })).toBeVisible();
});
