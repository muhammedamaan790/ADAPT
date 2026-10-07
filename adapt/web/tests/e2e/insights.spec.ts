import { expect, test } from '@playwright/test';

test('Outcomes and Learning show the same matured feedback with class filters', async ({
  page,
}) => {
  await page.goto('/outcomes');
  await expect(page.getByRole('heading', { name: 'No matured outcomes yet' })).toBeVisible();
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm fixture approval' }).click();
  await expect(page.getByRole('button', { name: 'Executed & verified' })).toBeVisible({
    timeout: 10000,
  });
  await page.getByRole('button', { name: 'Advance 3 days', exact: true }).click();
  await page.getByRole('link', { name: 'Outcomes', exact: true }).click();
  await expect(page.getByText('Calibration applied', { exact: true })).toBeVisible();
  await page.getByLabel('Decision class').selectOption('SAFETY');
  await expect(page.getByRole('heading', { name: 'No matching outcomes' })).toBeVisible();
  await page.getByLabel('Decision class').selectOption('OPTIMIZATION');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download outcome records' }).click();
  expect((await download).suggestedFilename()).toBe('adapt-outcomes.json');
  await page.getByRole('link', { name: 'Learning', exact: true }).click();
  await expect(page.getByText('0.86×', { exact: true })).toBeVisible();
  await expect(page.getByText('Applied', { exact: true })).toBeVisible();
  await expect(page.getByText(/No held-out benchmark report/)).toBeVisible();
});
test('CSV validates and stages a local preview without importing engine data', async ({ page }) => {
  await page.goto('/data');
  await page.getByRole('button', { name: 'CSV import' }).click();
  await page.getByLabel('Import type').selectOption('inventory');
  const input = page.getByLabel('CSV file');
  await input.setInputFiles({
    name: 'invalid.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('sku,on_hand,reserved,safety_stock\nhero,1,5,0'),
  });
  await expect(page.getByText(/reserved units exceed/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Stage local preview' })).toBeDisabled();
  await input.setInputFiles({
    name: 'valid.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('sku,on_hand,reserved,safety_stock\nhero,500,20,30'),
  });
  await expect(page.getByText(/All 1 rows pass/)).toBeVisible();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Stage local preview' }).click();
  await expect(page.getByText(/No canonical data or engine state was changed/)).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: 'CSV import' }).click();
  await expect(page.getByText('valid.csv', { exact: true })).toBeVisible();
});
test('Opportunity evidence and grounded Copilot preserve missing-model states', async ({
  page,
}) => {
  await page.goto('/opportunities');
  await expect(page.getByRole('heading', { name: 'Evidence graph' })).toBeVisible();
  await page.getByLabel('Creative context').fill('Offer a refill bundle for returning customers.');
  await page.getByRole('button', { name: 'Assess creative' }).click();
  await expect(page.getByText(/Not estimable · Creative context/)).toBeVisible();
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await page.getByRole('button', { name: 'Why this allocation?' }).click();
  await expect(page.getByText('Fixture template', { exact: true })).toBeVisible();
  await page
    .getByRole('dialog')
    .getByRole('link', { name: /Decision / })
    .click();
  await expect(page.getByRole('heading', { name: 'Compare & inspect history' })).toBeVisible();
});
test('comparison, replay and sensitivity revisions remain separate from execution', async ({
  page,
}) => {
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Decision replay timeline' }).click();
  await page.getByRole('button', { name: 'Check replay availability' }).click();
  await expect(page.getByText(/No hash verification was performed/)).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download snapshot' }).click();
  expect((await download).suggestedFilename()).toContain('snapshot.json');
  await page.getByRole('button', { name: 'Comparison & sensitivity' }).click();
  await expect(page.getByRole('heading', { name: 'ROAS rank' })).toBeVisible();
  await page.getByRole('button', { name: 'Revise to conservative allocation' }).click();
  await page.getByRole('button', { name: 'Confirm sensitivity revision' }).click();
  await expect(page.getByRole('heading', { name: 'Awaiting backend valuation' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve & execute', exact: true })).toBeDisabled();
  await expect(page.getByText(/No hash verification was performed/)).toHaveCount(0);
});
test('new pages have accessible mobile navigation and no document overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('link', { name: 'Opportunity Map', exact: true }).click();
  for (const [path, name] of [
    ['/opportunities', 'Opportunity Map'],
    ['/outcomes', 'Outcomes'],
    ['/learning', 'Learning'],
    ['/data', 'Data Hub'],
  ]) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await expect(page.getByLabel('Ask Copilot')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
