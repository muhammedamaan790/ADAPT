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
