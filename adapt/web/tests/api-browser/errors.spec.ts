import { expect, test } from '@playwright/test';
import { fixtureDecision, fixtureEvidence, fixtureOverview } from '../../src/api/fixtures';
import { optimizerFixture } from '../../src/api/workbench-fixtures';

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

test('new API workspaces fail visibly when their routes are absent', async ({ page }) => {
  await page.route('**/api/v1/**', (route) =>
    route.fulfill({ status: 404, json: { detail: 'Frontend route not implemented by backend.' } }),
  );
  for (const path of ['/anomalies', '/optimizer', '/executions']) {
    await page.goto(path);
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByText('Frontend route not implemented by backend.')).toBeVisible();
    await expect(page.getByText('FRONTEND FIXTURES', { exact: true })).toHaveCount(0);
  }
});

test('custom allocation requests retain identity, headers and stale-revision conflict', async ({
  page,
}) => {
  let revisions = 0;
  await page.route('**/api/v1/**', async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/modify')) {
      revisions++;
      const body = route.request().postDataJSON();
      expect(body.decision_hash).toBe(fixtureDecision('DEMO_01').decision_hash);
      expect(body.legs.find((l: { budget_id: string }) => l.budget_id === 'g-bundle').after).toBe(
        27800,
      );
      expect(route.request().headers()['idempotency-key']).toBeTruthy();
      expect(route.request().headers()['x-request-id']).toBeTruthy();
      return route.fulfill({
        status: 409,
        json: { detail: 'Proposal changed. Reload before revising.' },
      });
    }
    if (path.endsWith('/optimizer/whatif')) {
      const d = fixtureDecision('DEMO_01');
      return route.fulfill({
        json: {
          decision_id: d.decision_id,
          decision_hash: d.decision_hash,
          objective: 'PROFIT',
          allocated: 96000,
          unallocated: 4000,
          checks: d.checks,
          estimate_status: 'AVAILABLE',
          estimate: d.expected,
          explanation: 'Intercepted backend response for frontend contract testing.',
        },
      });
    }
    const json = path.endsWith('/optimizer/context')
      ? optimizerFixture(fixtureDecision('DEMO_01'))
      : path.endsWith('/decisions')
        ? [fixtureDecision('DEMO_01')]
        : path.endsWith('/overview')
          ? fixtureOverview('DEMO_01', 0)
          : [];
    return route.fulfill({ json });
  });
  await page.goto('/optimizer');
  await page.getByRole('button', { name: 'Evaluate allocation', exact: true }).click();
  await expect(page.getByText('Backend valuation', { exact: true })).toBeVisible();
  await expect(page.getByText('Recorded valuation', { exact: true })).toHaveCount(0);
  await page.getByLabel('Bundle · Shopping proposed budget', { exact: true }).fill('27800');
  await page.getByRole('button', { name: 'Create revision', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm revision', exact: true }).click();
  await expect(page.getByText('Proposal changed. Reload before revising.')).toBeVisible();
  await expect(page.getByRole('dialog')).toBeVisible();
  expect(revisions).toBe(1);
});
