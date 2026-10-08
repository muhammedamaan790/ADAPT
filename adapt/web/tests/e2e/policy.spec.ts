import { expect, test } from '@playwright/test';
test('fixture readiness is read-only, separates outcome pools and has no fake shadow records', async ({
  page,
}) => {
  await page.goto('/executions?section=policy');
  await expect(
    page.getByRole('heading', { name: 'Execution policy & autonomy readiness' }),
  ).toBeVisible();
  await expect(
    page.getByRole('region', { name: 'Simulation readiness' }).getByText('0 / 10 minimum'),
  ).toHaveCount(2);
  await expect(
    page.getByRole('region', { name: 'Production readiness' }).getByText('0 / 30 minimum'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review mode change' })).toBeDisabled();
  await expect(page.getByRole('heading', { name: 'No recorded shadow decisions' })).toBeVisible();
  await page.getByLabel('Policy channel').selectOption('Google');
  await expect(page.getByRole('heading', { name: 'Google policy' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Policy & readiness' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
});
test('policy and objective controls fit mobile, dark theme and keyboard navigation', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of ['/executions?section=policy', '/decisions', '/inventory']) {
    await page.goto(route);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
  await page.goto('/executions?section=policy');
  await page.getByRole('button', { name: 'Switch to dark theme' }).click();
  await page.getByLabel('Policy channel').focus();
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Google policy' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
