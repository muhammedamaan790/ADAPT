import { expect, test } from '@playwright/test';
import { fixtureOverview } from '../../src/api/fixtures';

test('missing proposal data does not hide the overview or fabricate a budget recommendation', async ({
  page,
}) => {
  await page.route('**/api/v1/**', (route) => {
    if (route.request().url().endsWith('/overview'))
      return route.fulfill({ json: fixtureOverview('DEMO_01', 14) });
    return route.fulfill({ status: 503, json: { detail: 'Proposal service unavailable.' } });
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Command Center', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Budget proposal unavailable' })).toBeVisible();
  await expect(
    page.locator('.home-proposal').getByRole('button', { name: 'Try again' }),
  ).toBeVisible();
  await expect(page.getByRole('slider', { name: 'Reconciled ROAS observation day' })).toBeVisible();
  await expect(page.getByText('FRONTEND FIXTURES', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Review & approve', exact: true })).toHaveCount(0);
});
