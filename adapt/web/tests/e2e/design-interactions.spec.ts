import { test, expect } from '@playwright/test';

test('daily comparison is usable with a keyboard and preserves both values', async ({ page }) => {
  await page.goto('/');
  const slider = page.getByRole('slider', { name: 'Reconciled ROAS observation day' });
  await expect(slider).toHaveValue('13');
  await slider.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(slider).toHaveValue('12');
  await expect(slider).toHaveAttribute('aria-valuetext', '2026-10-06: actual 2.48, baseline 3.85');
  await page.keyboard.press('Home');
  await expect(slider).toHaveValue('0');
  await page.getByText('View chart data', { exact: true }).click();
  await expect(page.getByRole('cell', { name: '2026-09-24', exact: true })).toBeVisible();
});

test('metric lineage stays inside a narrow viewport and Escape dismisses it', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/');
  const control = page.getByRole('button', { name: 'Lineage for Net revenue', exact: true });
  await control.click();
  const popover = page.locator('.lineage:popover-open');
  await expect(popover).toBeVisible();
  const box = await popover.boundingBox();
  expect(box!.x).toBeGreaterThanOrEqual(0);
  expect(box!.x + box!.width).toBeLessThanOrEqual(375);
  await page.keyboard.press('Escape');
  await expect(control).toHaveAttribute('aria-expanded', 'false');
});

test('mobile navigation exposes every workflow and restores focus after closing', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const menu = page.getByRole('button', { name: 'Open navigation' });
  await menu.click();
  const dialog = page.getByRole('dialog', { name: 'Navigation' });
  await expect(dialog.getByRole('link')).toHaveCount(11);
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(menu).toBeFocused();
  await menu.click();
  await dialog.getByRole('link', { name: 'Data Hub', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Data Hub', exact: true })).toBeVisible();
  await expect(dialog).toHaveCount(0);
});
