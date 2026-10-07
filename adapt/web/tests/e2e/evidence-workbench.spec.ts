import { expect, test } from '@playwright/test';

test('phone workbench filters stay visible and usable inside their disclosures', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/decisions');
  await page.locator('.decision-inbox summary').click();
  await page.getByLabel('Find a decision').fill('no-phone-match');
  await expect(page.getByText('0 matching decisions', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Decision status', { exact: true })).toBeVisible();
  await page.goto('/optimizer');
  await page.locator('.campaign-economics summary').click();
  await page.getByLabel('Campaign channel', { exact: true }).selectOption('Google');
  await page.getByLabel('Find a campaign').fill('no-phone-match');
  await expect(page.getByText('No campaigns match.', { exact: false })).toBeVisible();
  await page.goto('/opportunities');
  await page.getByLabel('Creative status', { exact: true }).selectOption('REVIEW');
  await expect(page.locator('.creative-signals li').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('decision inbox searches and preserves the selected proposal and approval action', async ({
  page,
}) => {
  await page.goto('/decisions');
  const title = await page.locator('h1').textContent();
  await page.getByText('Decision inbox', { exact: false }).click();
  await expect(page.locator('.inbox-list a[aria-current="page"]')).toContainText(title!);
  await page.getByLabel('Find a decision').fill('no-matching-proposal');
  await expect(page.getByText('0 matching decisions', { exact: true })).toBeVisible();
  await page.getByLabel('Find a decision').fill('');
  await page.getByLabel('Decision status').selectOption('PENDING_APPROVAL');
  await page.locator('.inbox-list a').first().click();
  await expect(page.getByRole('button', { name: 'Approve & execute', exact: true })).toBeEnabled();
  await expect(page.locator('.budget-movement')).toContainText('Where the budget moves');
});

test('campaign economics filters and sorts numeric margins without editing the allocation', async ({
  page,
}) => {
  await page.goto('/optimizer');
  await expect(page.locator('.budget-row input').first()).toBeVisible();
  const budgets = await page
    .locator('.budget-row input')
    .evaluateAll((inputs) => inputs.map((i) => (i as HTMLInputElement).value));
  await page.getByText('Compare campaign economics', { exact: false }).click();
  await page.getByRole('button', { name: 'Margin', exact: true }).click();
  const values = () =>
    page
      .locator('.economics-table tbody tr')
      .evaluateAll((rows) => rows.map((r) => Number(r.children[3].textContent!.replace('%', ''))));
  const descending = await values();
  expect(descending).toEqual([...descending].sort((a, b) => b - a));
  await page.getByRole('button', { name: 'Margin', exact: true }).click();
  await expect(page.locator('.economics-table th[aria-sort="ascending"]')).toContainText('Margin');
  const ascending = await values();
  expect(ascending).toEqual([...ascending].sort((a, b) => a - b));
  await page.getByLabel('Campaign channel', { exact: true }).selectOption('Meta');
  for (const row of await page.locator('.economics-table tbody tr').all())
    await expect(row).toContainText('Meta');
  await page.getByLabel('Find a campaign').fill('no-matching-campaign');
  await expect(page.getByText('No campaigns match.', { exact: false })).toBeVisible();
  expect(
    await page
      .locator('.budget-row input')
      .evaluateAll((inputs) => inputs.map((i) => (i as HTMLInputElement).value)),
  ).toEqual(budgets);
});

test('creative review filters keep measured signals distinct from model assessment', async ({
  page,
}) => {
  await page.goto('/opportunities');
  await expect(page.locator('.creative-signals li').first()).toBeVisible();
  await page.getByLabel('Creative status').selectOption('REVIEW');
  for (const row of await page.locator('.creative-signals li').all())
    await expect(row).toContainText('Needs review');
  await expect(page.locator('.creative-signals')).toContainText('CTR change');
  await page.getByLabel('Creative status').selectOption('STABLE');
  for (const row of await page.locator('.creative-signals li').all())
    await expect(row).toContainText('Stable');
  await expect(page.getByRole('button', { name: 'Assess creative', exact: true })).toBeDisabled();
});
