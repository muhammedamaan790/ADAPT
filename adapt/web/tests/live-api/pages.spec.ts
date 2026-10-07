import { expect, test } from '@playwright/test';

// Every page of the app against the REAL backend: no load error, no contract mismatch, no fixture data.
const routes = [
  '/',
  '/decisions',
  '/anomalies',
  '/optimizer',
  '/executions',
  '/outcomes',
  '/opportunities',
  '/learning',
  '/data',
  '/scenarios',
  '/connection',
];

for (const route of routes) {
  test(`page ${route} loads from the live API`, async ({ page }) => {
    await page.goto(route);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.waitForTimeout(4000);
    const alerts = (await page.getByRole('alert').allInnerTexts()).filter((t) => t.trim());
    console.log(
      `${route}: ${alerts.length ? alerts.join(' | ').replace(/\s+/g, ' ').slice(0, 300) : 'no alerts'}`,
    );
    await expect(page.getByText(/Response contract mismatch/)).toHaveCount(0);
    await expect(page.getByText(/couldn.t load this view/i)).toHaveCount(0);
    await expect(page.getByText('FRONTEND FIXTURES', { exact: true })).toHaveCount(0);
  });
}
