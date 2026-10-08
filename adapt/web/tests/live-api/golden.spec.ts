import { expect, test, type Page } from '@playwright/test';
import { signIn, writeHeaders } from './signin';

// Live Stage 1 journey against the REAL backend (API mode, world service + adapt-api running, seed 42 at day 0).
async function jobStart(page: Page): Promise<string | null> {
  return (await (await page.request.get('/api/v1/pipeline/status')).json()).started_at;
}

async function waitForNewJob(page: Page, previousStart: string | null) {
  // the UI's request may still be in flight: wait for a NEW job (new start time), then for it to finish
  for (let i = 0; i < 600; i++) {
    const s = await (await page.request.get('/api/v1/pipeline/status')).json();
    if (s.started_at !== previousStart && !s.busy) {
      expect(s.state).toBe('completed');
      return s;
    }
    await page.waitForTimeout(2000);
  }
  throw new Error('pipeline job did not finish');
}

test('live golden journey: evidence → approve → verified → advance → outcome → S3 review → reset', async ({
  page,
}) => {
  await signIn(page);
  // Command Center on real data
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Command Center', exact: true })).toBeVisible();
  await expect(page.getByText('API MODE', { exact: true })).toBeVisible();
  await expect(page.getByText('FRONTEND FIXTURES', { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Response contract mismatch/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Lineage for Net revenue (7d)', exact: true }).click();
  await expect(page.getByText('GMV − discounts − refunds (tax excluded)')).toBeVisible();
  await page.keyboard.press('Escape');

  // the top decision: evidence, why-not, checks, unallocated
  const link = page.locator('a[href^="/decisions/"]').first();
  await link.click();
  await expect(page.getByText(/unallocated/).first()).toBeVisible();
  await page.getByRole('tab', { name: 'Alternatives', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Why not the obvious alternative?' }),
  ).toBeVisible();
  await page.getByRole('tab', { name: 'Checks & details', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Policy checks' })).toBeVisible();
  await page.screenshot({
    path: `${process.env.PW_OUT || 'test-results/live'}/live_decision.png`,
    fullPage: true,
  });

  // approve the displayed hash and execute; every leg verified by read-back
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm approval & execute', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Executed & verified' })).toBeVisible({
    timeout: 60000,
  });
  const verified = await page.getByText('verified', { exact: true }).count();
  expect(verified).toBeGreaterThan(0);
  console.log('verified legs shown:', verified);

  // advance 3 days; the backend measures the outcome in its pipeline jobs
  const before3 = await jobStart(page);
  await page.getByRole('button', { name: 'Advance 3 days', exact: true }).click();
  await waitForNewJob(page, before3);
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Prediction meets the outcome' })).toBeVisible({
    timeout: 60000,
  });
  await page.getByText('Measurement & feedback details', { exact: true }).click();
  console.log(
    'outcome:',
    (await page.locator('.outcome-panel').innerText()).replace(/\s+/g, ' ').slice(0, 400),
  );
  await page.screenshot({
    path: `${process.env.PW_OUT || 'test-results/live'}/live_outcome.png`,
    fullPage: true,
  });

  // S3 (loaded through the API; the UI no longer has a Scenario Lab) -> a safety proposal that needs
  // review; reject it with a reason
  const load = await page.request.post('/api/v1/sim/scenario/S3', {
    data: {},
    headers: await writeHeaders(page, 'live-s3-load'),
  });
  expect(load.status()).toBe(200);
  const before2 = await jobStart(page);
  const adv = await page.request.post('/api/v1/sim/advance?days=2', {
    data: {},
    headers: await writeHeaders(page, 'live-s3'),
  });
  expect(adv.status()).toBe(200);
  await waitForNewJob(page, before2);
  const decisions = await (await page.request.get('/api/v1/decisions')).json();
  const safety = decisions.find(
    (d: { class: string; status: string }) =>
      d.class === 'SAFETY' && d.status === 'PENDING_APPROVAL',
  );
  console.log('safety proposal:', safety?.title);
  expect(safety).toBeTruthy();
  await page.goto(`/decisions/${safety.decision_id}`);
  await page.getByRole('button', { name: 'Reject with a reason' }).click();
  await page
    .getByLabel('Reason for rejection')
    .fill('Restock confirmed by the warehouse for tomorrow.');
  await page.getByRole('button', { name: 'Confirm rejection' }).click();
  await expect(page.getByRole('button', { name: 'Proposal rejected' })).toBeDisabled({
    timeout: 30000,
  });

  // reset: world and workspace back to day 0
  const reset = await page.request.post('/api/v1/sim/reset?seed=42', {
    data: {},
    headers: await writeHeaders(page, 'live-reset'),
  });
  expect(reset.status()).toBe(200);
  const ov = await (await page.request.get('/api/v1/overview')).json();
  expect(ov.world_day).toBe(0);
  expect(ov.counts.success + ov.counts.neutral + ov.counts.failed + ov.counts.inconclusive).toBe(0);
});
