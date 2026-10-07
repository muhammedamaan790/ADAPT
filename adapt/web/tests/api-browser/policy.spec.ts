import { expect, test, type Page } from '@playwright/test';
import { testPolicy, testShadow, testCatalog } from '../policy-data';
import { fixtureDecision, fixtureEvidence, fixtureOverview } from '../../src/api/fixtures';
import { optimizerFixture } from '../../src/api/workbench-fixtures';
import type { Policy } from '../../src/api/policy-contracts';
const d = fixtureDecision('DEMO_01');
async function objectiveRoutes(page: Page, fault = '') {
  const context = { ...optimizerFixture(d), supported_objectives: ['PROFIT', 'GROWTH'] };
  let decision = d;
  await page.route('**/api/v1/**', async (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/optimizer/whatif')) {
      const input = r.request().postDataJSON();
      expect(input.decision_hash).toBe(d.decision_hash);
      return r.fulfill({
        json: {
          decision_id: d.decision_id,
          decision_hash: d.decision_hash,
          objective: fault === 'objective' ? 'PROFIT' : input.objective,
          allocated: 96000,
          unallocated: 4000,
          checks: d.checks,
          estimate_status: 'AVAILABLE',
          estimate: d.expected,
          objective_value: { label: 'Projected net revenue', value: 12000, unit: 'INR' },
          explanation: 'Synthetic backend valuation for contract testing.',
        },
      });
    }
    if (path.endsWith('/optimizer/run')) {
      expect(r.request().postDataJSON().objective).toBe('GROWTH');
      expect(r.request().headers()['idempotency-key']).toBeTruthy();
      decision = { ...d, decision_id: 'growth-test-decision', objective: 'GROWTH' };
      return r.fulfill({ json: decision });
    }
    if (path.endsWith('/modify')) return r.fulfill({ json: { ...d, objective: 'GROWTH' } }); // Original ID is not a new revision.
    const json = path.endsWith('/optimizer/context')
      ? { ...context, ...(fault === 'context' ? { decision_hash: 'stale' } : {}) }
      : path.endsWith('/decisions')
        ? [decision]
        : path.endsWith('/evidence')
          ? { ...fixtureEvidence('DEMO_01'), decision_id: decision.decision_id }
          : path.includes('/decisions/')
            ? decision
            : path.endsWith('/overview')
              ? fixtureOverview('DEMO_01', 0)
              : [];
    return r.fulfill({ json });
  });
}
test('supported objectives clear old valuations, send selected policy and navigate to its proposal', async ({
  page,
}) => {
  await objectiveRoutes(page);
  await page.goto('/optimizer');
  await page.getByRole('button', { name: 'Evaluate allocation', exact: true }).click();
  await expect(page.getByText('Backend valuation', { exact: true })).toBeVisible();
  await page.getByLabel('Evaluation objective').selectOption('GROWTH');
  await expect(page.getByText('Backend valuation', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Evaluate allocation', exact: true }).click();
  await expect(page.getByText('Projected net revenue: ₹12,000')).toBeVisible();
  await page.getByRole('button', { name: 'Run GROWTH optimizer' }).click();
  await expect(page).toHaveURL(/decisions\/growth-test-decision$/);
  await expect(page.getByText('Waiting for your approval', { exact: true })).toBeVisible();
  await expect(page.getByText('GROWTH · CHANNEL POLICY', { exact: true })).toBeVisible();
});
test('wrong objective valuation and wrong proposal context are rejected', async ({ page }) => {
  await objectiveRoutes(page, 'objective');
  await page.goto('/optimizer');
  await page.getByLabel('Evaluation objective').selectOption('GROWTH');
  await page.getByRole('button', { name: 'Evaluate allocation', exact: true }).click();
  await expect(
    page.getByText('Valuation belongs to another proposal or objective. Reload before evaluating.'),
  ).toBeVisible();
  await expect(page.getByText('Backend valuation', { exact: true })).toHaveCount(0);
  await page.unrouteAll();
  await objectiveRoutes(page, 'context');
  await page.reload();
  await expect(
    page.getByText(
      'The optimizer context does not match the current proposal. Refresh before evaluating or revising.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Evaluate allocation', exact: true })).toHaveCount(
    0,
  );
});
test('a revision response cannot reuse the original decision identity', async ({ page }) => {
  await objectiveRoutes(page);
  await page.goto('/optimizer');
  await page.getByLabel('Evaluation objective').selectOption('GROWTH');
  await page.getByRole('button', { name: 'Create revision', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm revision', exact: true }).click();
  await expect(page.getByText(/Revision identity or objective does not match/)).toBeVisible();
  await expect(page.getByRole('dialog')).toBeVisible();
});
test('mode request binds revision, requires review, retains conflicts and never executes budgets', async ({
  page,
}) => {
  let p: Policy = structuredClone(testPolicy),
    writes = 0,
    conflict = true;
  await page.route('**/api/v1/**', async (r) => {
    const path = new URL(r.request().url()).pathname;
    if (r.request().method() !== 'GET') {
      expect(path).toBe('/api/v1/policy');
      expect(r.request().method()).toBe('PUT');
      writes++;
      const body = r.request().postDataJSON();
      expect(body).toMatchObject({
        policy_version: p.policy_version,
        revision: p.revision,
        channel: 'TikTok',
        mode: 'SIMULATION_AUTONOMOUS',
        reason: 'Reviewed simulation qualification evidence.',
      });
      expect(r.request().headers()['idempotency-key']).toBeTruthy();
      expect(r.request().headers()['x-request-id']).toBeTruthy();
      if (conflict)
        return r.fulfill({
          status: 409,
          json: { detail: 'Policy evidence changed; request was not accepted.' },
        });
      p = {
        ...p,
        revision: 'test-revision-2',
        channels: p.channels.map((c) => (c.channel === 'TikTok' ? { ...c, mode: body.mode } : c)),
      };
      return r.fulfill({ json: p });
    }
    return path.endsWith('/policy')
      ? r.fulfill({ json: p })
      : path.endsWith('/learning/shadow')
        ? r.fulfill({ json: testShadow })
        : r.fulfill({
            status: 503,
            json: { detail: 'Other engine routes intentionally unavailable.' },
          });
  });
  await page.goto('/executions?section=policy');
  await expect(page.getByRole('heading', { name: 'TikTok policy' })).toBeVisible();
  await page.getByLabel('Requested execution mode').selectOption('SIMULATION_AUTONOMOUS');
  await page.getByRole('button', { name: 'Review mode change' }).click();
  const submit = page.getByRole('button', { name: 'Confirm mode request' });
  await expect(submit).toBeDisabled();
  await page.getByLabel('Mode change reason').fill('Reviewed simulation qualification evidence.');
  await expect(submit).toBeDisabled();
  await page.getByRole('checkbox').check();
  await submit.click();
  await expect(page.getByText('Policy evidence changed; request was not accepted.')).toBeVisible();
  await expect(page.getByRole('dialog')).toBeVisible();
  conflict = false;
  await submit.click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByLabel('Requested execution mode')).toHaveValue('SIMULATION_AUTONOMOUS');
  expect(writes).toBe(2);
  await page.getByLabel('Policy channel').selectOption('Google');
  await expect(page.getByText(/This test account does not serve ads/)).toBeVisible();
  await expect(
    page.getByLabel('Requested execution mode').locator('option[value="PRODUCTION_AUTONOMOUS"]'),
  ).toHaveAttribute('disabled', '');
  await expect(
    page.getByRole('region', { name: 'Production readiness' }).getByText('0 / 30 minimum'),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('Shadow world')).toBeVisible();
  await page.getByLabel('Shadow world').selectOption('REAL');
  await expect(page.getByText('Not executed', { exact: true })).toHaveCount(1);
  await expect(
    page.locator('.ledger-list').getByText('Real account context', { exact: true }),
  ).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test('invalid Google production permission and measured shadow claims fail visibly', async ({
  page,
}) => {
  const p = structuredClone(testPolicy);
  p.channels[1].allowed_modes.push('PRODUCTION_AUTONOMOUS');
  await page.route('**/api/v1/**', (r) =>
    new URL(r.request().url()).pathname.endsWith('/policy')
      ? r.fulfill({ json: p })
      : r.fulfill({
          json: { ...testShadow, records: [{ ...testShadow.records[0], measured_caa: 8000 }] },
        }),
  );
  await page.goto('/executions?section=policy');
  await expect(
    page.getByText(
      'Response contract mismatch at /policy. Ask the backend team to align the frontend schema.',
    ),
  ).toBeVisible();
  await expect(
    page.getByText(
      'Response contract mismatch at /learning/shadow. Ask the backend team to align the frontend schema.',
    ),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Review mode change' })).toHaveCount(0);
});
test('catalog availability is checked again before scenario writes and false acknowledgements fail', async ({
  page,
}) => {
  let available = true,
    writes = 0;
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/sim/scenarios'))
      return r.fulfill({
        json: {
          ...testCatalog,
          items: testCatalog.items.map((s) =>
            available ? s : { ...s, status: 'NOT_BUILT', missing_modules: ['optimizer'] },
          ),
        },
      });
    if (path.endsWith('/sim/scenario/S9')) {
      writes++;
      return r.fulfill({ json: { ok: false } });
    }
    return r.fulfill({ json: path.endsWith('/overview') ? fixtureOverview('DEMO_01', 0) : [] });
  });
  await page.goto('/scenarios');
  await expect(page.getByRole('radio')).toHaveCount(1);
  await page.getByRole('button', { name: 'Load scenario', exact: true }).click();
  available = false;
  await page.getByRole('button', { name: 'Confirm load', exact: true }).click();
  await expect(
    page
      .getByText('This scenario is unavailable in the current stage. Refresh the catalog.')
      .first(),
  ).toBeVisible();
  expect(writes).toBe(0);
  if (await page.getByRole('dialog').count())
    await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  available = true;
  await page.reload();
  await page.getByRole('button', { name: 'Load scenario', exact: true }).click();
  await page.getByRole('button', { name: 'Confirm load', exact: true }).click();
  await expect(
    page.getByRole('dialog').getByText(/Response contract mismatch at \/sim\/scenario\/S9/),
  ).toBeVisible();
  expect(writes).toBe(1);
});
test('absent policy and catalog endpoints never substitute bundled fixtures', async ({ page }) => {
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/overview')) return r.fulfill({ json: fixtureOverview('DEMO_01', 0) });
    if (path.endsWith('/executions') || path.endsWith('/events')) return r.fulfill({ json: [] });
    return r.fulfill({ status: 404, json: { detail: 'Capability endpoint not yet implemented.' } });
  });
  await page.goto('/executions?section=policy');
  await expect(page.getByText('Capability endpoint not yet implemented.')).toHaveCount(2);
  await expect(page.getByRole('heading', { name: 'Meta policy' })).toHaveCount(0);
  await page.goto('/scenarios');
  await expect(page.getByRole('button', { name: 'Load scenario', exact: true })).toBeDisabled();
  await expect(page.getByRole('radio')).toHaveCount(0);
});
