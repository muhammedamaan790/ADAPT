import { expect, test } from '@playwright/test';

test('inventory board filters by recommendation and explains each SKU', async ({ page }) => {
  await page.goto('/inventory');
  await expect(page.getByRole('heading', { name: 'Inventory', exact: true })).toBeVisible();
  const rows = page.locator('.inv-table tbody tr');
  // "Needs action" is the default filter: every SKU except the ones to continue as is.
  await expect(page.getByRole('button', { name: /Needs action/ })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(rows).toHaveCount(9);
  await expect(rows.first().locator('.agent-col')).toHaveText('Hold ad spend');
  await page.getByRole('button', { name: /All SKUs/ }).click();
  await expect(rows).toHaveCount(12);
  await page.getByPlaceholder('Search SKU or category').fill('serum refill');
  await expect(rows).toHaveCount(1);
  await page.getByRole('button', { name: /Serum refill pouch/ }).click();
  await expect(page.locator('.inv-detail')).toContainText('at or below the reorder point');
  await page.getByPlaceholder('Search SKU or category').fill('');
  await page.getByRole('button', { name: /Excess stock agent/ }).click();
  await expect(page.getByRole('button', { name: /Clear excess/ })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  await expect(rows).toHaveCount(2);
});
