import { expect, test } from '@playwright/test';
import { fixtureDecision, fixtureEvidence, fixtureOverview } from '../../src/api/fixtures';

test('API failure remains visible and never substitutes fixtures', async ({ page }) => {
  await page.route('**/api/v1/**', (route) =>
    route.fulfill({ status: 503, json: { detail: 'Backend unavailable for this test.' } }),
  );
  await page.goto('/');
  await expect(page.getByText('API MODE', { exact: true })).toBeVisible();
  await expect(page.getByText('Backend unavailable for this test.')).toBeVisible();
  await expect(page.getByText('FRONTEND FIXTURES', { exact: true })).toHaveCount(0);
  await expect(page.getByText('₹12.48 L', { exact: true })).toHaveCount(0);
});

test('malformed API response produces a contract error', async ({ page }) => {
  await page.route('**/api/v1/**', (route) => route.fulfill({ json: { ok: true } }));
  await page.goto('/');
  await expect(page.getByText(/Response contract mismatch/)).toBeVisible();
});

test('stale hash conflict stays in approval dialog; no execution is fabricated', async ({
  page,
}) => {
  let approvalCount = 0;
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/approve')) {
      approvalCount++;
      const body = route.request().postDataJSON();
      expect(body.decision_hash).toBe(fixtureDecision('DEMO_01').decision_hash);
      expect(route.request().headers()['idempotency-key']).toBeTruthy();
      expect(route.request().headers()['x-request-id']).toBeTruthy();
      return route.fulfill({
        status: 409,
        json: { detail: 'Approval expired: economics changed. Review a new proposal.' },
      });
    }
    const json = path.endsWith('/overview')
      ? fixtureOverview('DEMO_01', 0)
      : path.endsWith('/evidence')
        ? fixtureEvidence('DEMO_01')
        : path.endsWith('/decisions')
          ? [fixtureDecision('DEMO_01')]
          : path.includes('/decisions/')
            ? fixtureDecision('DEMO_01')
            : [];
    await route.fulfill({ json });
  });
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm approval & execute', exact: true }).click();
  await expect(
    page.getByText('Approval expired: economics changed. Review a new proposal.'),
  ).toBeVisible();
  expect(approvalCount).toBe(1);
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await expect(page.getByText('Waiting for your approval', { exact: true })).toBeVisible();
});
