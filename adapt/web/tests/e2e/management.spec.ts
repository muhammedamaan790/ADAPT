import { expect, test } from '@playwright/test';
test('an unknown execution result stays unresolved and missing models stay unavailable', async ({
  page,
}) => {
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm fixture approval' }).click();
  await page.goto('/executions');
  await page.getByRole('button', { name: 'Inject unknown result', exact: true }).click();
  await page.goto('/scenarios');
  await expect(page.getByRole('button', { name: 'Advance 3 days', exact: true })).toBeDisabled();
  await page.goto('/learning');
  await expect(page.getByText(/Promotion and rollback are unavailable/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Inspect Response curves/ })).toBeDisabled();
});
test('replay manifest distinguishes captured evidence from absent archived environment', async ({
  page,
}) => {
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Decision replay timeline' }).click();
  await expect(
    page.getByRole('heading', { name: 'Archived evidence & environment' }),
  ).toBeVisible();
  await expect(page.getByText(/No full archived processing trace/)).toBeVisible();
  await page.getByText('Snapshot and reproducibility identities', { exact: true }).click();
  await expect(page.getByText('Environment fingerprint', { exact: true })).toBeVisible();
  await expect(page.getByText('Not supplied', { exact: true })).toHaveCount(4);
});
test('management controls fit mobile without document overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ['/scenarios', '/learning', '/data']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
});
