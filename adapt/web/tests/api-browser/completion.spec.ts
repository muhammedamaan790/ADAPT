import { test, expect, type Page } from '@playwright/test';
import { fixtureOverview } from '../../src/api/fixtures';
import { objective, history, source, reconciliation, confidence } from '../completion-data';
async function routes(page: Page, failure = '') {
  let current = { ...objective };
  let writes = 0;
  await page.route('**/api/v1/**', async (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/objective') && r.request().method() === 'PUT') {
      writes++;
      const input = r.request().postDataJSON();
      expect(input.revision).toBe('objective-1');
      expect(input.workspace_id).toBe('demo');
      expect(input.reason.length).toBeGreaterThan(10);
      if (failure === 'conflict')
        return r.fulfill({
          status: 409,
          json: { detail: 'Workspace revision changed. Refresh settings.' },
        });
      current = { ...current, objective: input.objective, revision: 'objective-2' };
      return r.fulfill({ json: current });
    }
    if (path.endsWith('/copilot/sql')) {
      const input = r.request().postDataJSON();
      expect(input.limit).toBe(500);
      if (failure === 'sql')
        return r.fulfill({
          status: 403,
          json: { detail: 'Only allowlisted SELECT statements are permitted.' },
        });
      return r.fulfill({
        json: {
          query: input.query,
          columns: ['campaign', 'revenue', 'missing'],
          rows: [['<script>alert(1)</script>', 1200, null]],
          truncated: false,
          elapsed_ms: 8,
          as_of: '2026-10-07T06:00:00Z',
        },
      });
    }
    const json = path.endsWith('/overview')
      ? fixtureOverview('DEMO_01', 0)
      : path.endsWith('/objective')
        ? current
        : path.endsWith('/policy/history')
          ? history
          : path.endsWith('/data/sources')
            ? [source]
            : path.endsWith('/data/health')
              ? [source]
              : path.endsWith('/data/mapping-coverage')
                ? { coverage: 1, unmapped: [], note: 'Synthetic coverage.' }
                : path.endsWith('/data/reconciliation')
                  ? reconciliation
                  : path.endsWith('/learning/qualification')
                    ? confidence
                    : [];
    return r.fulfill({ json });
  });
  return () => writes;
}
test('workspace objective change is reviewed, bound and refreshed', async ({ page }) => {
  const writes = await routes(page);
  await page.goto('/executions?section=settings');
  await page.getByLabel('Default objective').selectOption('GROWTH');
  await page.getByRole('button', { name: 'Review objective change' }).click();
  await expect(page.getByRole('button', { name: 'Confirm objective change' })).toBeDisabled();
  await page.getByLabel('Change reason').fill('Reviewed growth target with the team.');
  await page.getByRole('button', { name: 'Confirm objective change' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByText('objective-2', { exact: true })).toBeVisible();
  expect(writes()).toBe(1);
  await expect(
    page.getByText('Updated workspace objective after review.', { exact: false }),
  ).toBeVisible();
});
test('objective conflict preserves review and does not invent success', async ({ page }) => {
  const writes = await routes(page, 'conflict');
  await page.goto('/executions?section=settings');
  await page.getByLabel('Default objective').selectOption('GROWTH');
  await page.getByRole('button', { name: 'Review objective change' }).click();
  await page.getByLabel('Change reason').fill('Reviewed growth target with the team.');
  await page.getByRole('button', { name: 'Confirm objective change' }).click();
  await expect(page.getByText('Workspace revision changed. Refresh settings.')).toBeVisible();
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(writes()).toBe(1);
});
test('Data Hub renders canonical source checks and platform reconciliation', async ({ page }) => {
  await routes(page);
  await page.goto('/data');
  await page.getByText('Inspect Meta Ads checks', { exact: true }).click();
  await expect(page.getByText('Values are in the expected range.')).toBeVisible();
  await expect(page.getByText('80.0%', { exact: true })).toBeVisible();
  await expect(page.getByText('1.20×', { exact: true })).toBeVisible();
  await expect(page.getByText('ZERO_DENOMINATOR', { exact: true })).toBeVisible();
});
test('confidence evidence shows held-out scope, counts and missing intervals', async ({ page }) => {
  await routes(page);
  await page.goto('/learning');
  await expect(page.getByText('HELD OUT', { exact: true })).toBeVisible();
  await expect(page.getByText('18 / 20', { exact: true })).toBeVisible();
  await expect(page.getByText('69.9%–97.2%', { exact: true })).toBeVisible();
  await expect(page.getByText('Not estimable', { exact: true })).toHaveCount(2);
});
test('SQL inspection binds submitted query and renders untrusted cells as text', async ({
  page,
}) => {
  await routes(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await page.getByRole('button', { name: 'SQL inspection' }).click();
  await page.getByRole('button', { name: 'Run read-only query' }).click();
  await expect(
    page.getByRole('cell', { name: '<script>alert(1)</script>', exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('cell', { name: 'NULL', exact: true })).toBeVisible();
  const results = page.getByRole('region', {
    name: 'SQL results; scroll to inspect all rows and columns',
  });
  await results.focus();
  await expect(results).toBeFocused();
  await page.getByLabel('Read-only SQL').fill('SELECT other');
  await expect(page.getByRole('table')).toHaveCount(0);
});
test('SQL refusal displays backend reason without fabricated results', async ({ page }) => {
  await routes(page, 'sql');
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await page.getByRole('button', { name: 'SQL inspection' }).click();
  await page.getByRole('button', { name: 'Run read-only query' }).click();
  await expect(page.getByText('Only allowlisted SELECT statements are permitted.')).toBeVisible();
  await expect(page.getByRole('table')).toHaveCount(0);
});
