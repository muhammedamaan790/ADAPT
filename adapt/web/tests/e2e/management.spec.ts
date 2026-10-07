import { expect, test } from '@playwright/test';
import { evaluationTestReport } from '../management-data';
test('workspace creation and switching isolate demo state and restore prior decisions', async ({
  page,
}) => {
  await page.goto('/scenarios');
  await page.getByRole('radio', { name: /S5/ }).check();
  await page.getByRole('button', { name: 'Load scenario', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm load', exact: true }).click();
  await page.goto('/data?section=workspaces');
  await page.getByLabel('New workspace name').fill('Refill brand');
  await page.getByRole('button', { name: 'Create local demo' }).click();
  await expect(page.getByText(/Created Refill brand/)).toBeVisible();
  await page.getByRole('button', { name: 'Switch to Refill brand' }).click();
  await page.getByRole('button', { name: 'Confirm workspace switch' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { name: 'Command Center', exact: true })).toBeVisible();
  await expect(page.locator('.workspace strong')).toHaveText('Refill brand');
  await page.goto('/decisions');
  await expect(page.getByRole('button', { name: 'Approve & execute', exact: true })).toBeEnabled();
  await page.goto('/data?section=workspaces');
  await page.getByRole('button', { name: 'Switch to D2C workspace' }).click();
  await page.getByRole('button', { name: 'Confirm workspace switch' }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.goto('/decisions');
  await expect(page.getByRole('button', { name: 'Execution blocked' })).toBeDisabled();
});
test('unknown execution blocks switching and missing models stay unavailable', async ({ page }) => {
  await page.goto('/data?section=workspaces');
  await page.getByLabel('New workspace name').fill('Isolated demo');
  await page.getByRole('button', { name: 'Create local demo' }).click();
  await expect(page.getByText(/Created Isolated demo/)).toBeVisible();
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm fixture approval' }).click();
  await page.goto('/executions');
  await page.getByRole('button', { name: 'Inject unknown result', exact: true }).click();
  await page.goto('/data?section=workspaces');
  await expect(page.getByRole('button', { name: 'Switch to Isolated demo' })).toBeDisabled();
  await expect(page.getByText(/Resolve or verify the current execution/)).toBeVisible();
  await page.goto('/learning');
  await expect(page.getByText(/Promotion and rollback are unavailable/)).toBeVisible();
  await expect(page.getByRole('button', { name: /Inspect Response curves/ })).toBeDisabled();
});
test('precomputed report upload validates paired rows, filters seeds and preserves unavailable zero-spend ratios', async ({
  page,
}) => {
  await page.goto('/scenarios');
  await page.getByRole('button', { name: 'Head-to-Head', exact: true }).click();
  await expect(page.getByText(/No precomputed eval report is connected/)).toBeVisible();
  await page.getByLabel('Precomputed evaluation JSON').setInputFiles({
    name: 'invalid.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify({ ...evaluationTestReport, rows: [] })),
  });
  await expect(page.getByRole('alert')).toHaveText(/Report contract mismatch/);
  await expect(page.getByRole('table')).toHaveCount(0);
  await page.getByLabel('Precomputed evaluation JSON').setInputFiles({
    name: 'eval.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(evaluationTestReport)),
  });
  await expect(
    page.getByText(/provenance and results have not been independently verified/),
  ).toBeVisible();
  await expect(page.getByRole('row', { name: /oracle/ })).toHaveText(/Unavailable/);
  await page.getByLabel('Evaluation seed').selectOption('42');
  await expect(page.getByRole('row', { name: /safe-static/ })).toContainText('₹10,000');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export displayed report' }).click();
  expect((await download).suggestedFilename()).toBe('adapt-evaluation-report.json');
  await page.getByRole('button', { name: 'Clear uploaded report' }).click();
  await expect(page.getByRole('table')).toHaveCount(0);
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
  await page.goto('/data?section=workspaces');
  await expect(page.getByLabel('New workspace name')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.goto('/scenarios');
  await page.getByRole('button', { name: 'Head-to-Head', exact: true }).click();
  await page.getByLabel('Precomputed evaluation JSON').setInputFiles({
    name: 'eval.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(evaluationTestReport)),
  });
  await expect(page.getByRole('table')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
