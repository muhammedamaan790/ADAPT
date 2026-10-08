import { expect, test, type Page } from '@playwright/test';
import { modelTestDetail } from '../management-data';
import { fixtureOverview, fixtureDecision, fixtureEvidence } from '../../src/api/fixtures';
import type { ModelDetail } from '../../src/api/management-contracts';

async function modelBackend(page: Page, detail: ModelDetail, conflict = false) {
  const calls: { action: string; body: Record<string, unknown> }[] = [];
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/promote') || path.endsWith('/rollback')) {
      calls.push({ action: path.split('/').at(-1)!, body: r.request().postDataJSON() });
      expect(r.request().headers()['idempotency-key']).toBeTruthy();
      expect(r.request().headers()['x-request-id']).toBeTruthy();
      return conflict
        ? r.fulfill({
            status: 409,
            json: { detail: 'Registry changed; review the current champion.' },
          })
        : r.fulfill({
            json: {
              name: detail.name,
              version: detail.role === 'CHAMPION' ? detail.rollback_version : detail.version,
              registry_revision: 'registry-3',
              message: 'Backend accepted registry request',
            },
          });
    }
    if (path.endsWith('/models'))
      return r.fulfill({
        json: [
          {
            name: detail.name,
            version: detail.version,
            status: detail.role === 'CHAMPION' ? 'CHAMPION' : 'CHALLENGER',
            trained_at: detail.trained_at,
            note: 'Test backend artifact',
          },
        ],
      });
    if (path.includes('/models/')) return r.fulfill({ json: detail });
    if (path.endsWith('/overview')) return r.fulfill({ json: fixtureOverview('DEMO_01', 0) });
    return r.fulfill({ status: 503, json: { detail: 'Learning metrics unavailable.' } });
  });
  return calls;
}
test('model promotion binds reviewed artifact and revision; stale registry preserves the dialog', async ({
  page,
}) => {
  const calls = await modelBackend(page, modelTestDetail, true);
  await page.goto('/learning');
  await expect(page.getByText('Learning metrics unavailable.').first()).toBeVisible();
  await page.getByRole('button', { name: 'Request candidate promotion' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByRole('button', { name: 'Confirm registry request' })).toBeDisabled();
  await dialog.getByLabel('Registry change reason').fill('Reviewed held-out gates and coverage.');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Confirm registry request' }).click();
  await expect(dialog.getByRole('alert')).toHaveText(/Registry changed/);
  expect(calls).toEqual([
    {
      action: 'promote',
      body: {
        version: 'v2',
        registry_revision: modelTestDetail.registry_revision,
        artifact_hash: 'a'.repeat(64),
        reason: 'Reviewed held-out gates and coverage.',
      },
    },
  ]);
  await expect(dialog).toBeVisible();
});
test('model rollback requests the recorded champion revision and never locally changes the role', async ({
  page,
}) => {
  const d = {
    ...modelTestDetail,
    role: 'CHAMPION' as const,
    rollback_version: 'v1',
    allowed_actions: ['ROLLBACK'] as ModelDetail['allowed_actions'],
  };
  const calls = await modelBackend(page, d);
  await page.goto('/learning');
  await expect(page.getByRole('button', { name: 'Request candidate promotion' })).toBeDisabled();
  await page.getByRole('button', { name: 'Roll back champion' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Registry change reason').fill('Restore the recorded prior champion.');
  await dialog.getByRole('checkbox').check();
  await dialog.getByRole('button', { name: 'Confirm registry request' }).click();
  await expect(dialog).toHaveCount(0);
  expect(calls[0]).toMatchObject({
    action: 'rollback',
    body: { version: 'v2', registry_revision: d.registry_revision, artifact_hash: 'a'.repeat(64) },
  });
  await expect(page.getByText('CHAMPION', { exact: true }).last()).toBeVisible();
});
test('malformed promotable models fail contract validation before any control is enabled', async ({
  page,
}) => {
  await modelBackend(page, { ...modelTestDetail, artifact_hash: null });
  await page.goto('/learning');
  await expect(page.getByText(/Response contract mismatch/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Request candidate promotion' })).toHaveCount(0);
});
test('replay manifest refuses evidence for another decision hash', async ({ page }) => {
  const d = fixtureDecision('DEMO_01');
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    const json = path.endsWith('/archive')
      ? {
          decision_id: d.decision_id,
          decision_hash: 'wrong-hash',
          snapshot_id: d.snapshot_id,
          environment_fingerprint: 'env',
          code_sha: 'sha',
          lock_hash: 'lock',
          seed: 42,
          status: 'AVAILABLE',
          note: 'Wrong archive',
          artifacts: [],
          steps: [],
        }
      : path.endsWith('/overview')
        ? fixtureOverview('DEMO_01', 0)
        : path.endsWith('/evidence')
          ? fixtureEvidence('DEMO_01')
          : path.endsWith('/decisions')
            ? [d]
            : path.endsWith('/timeline')
              ? []
              : path.includes('/decisions/')
                ? d
                : [];
    return r.fulfill({ json });
  });
  await page.goto('/decisions');
  await page.getByRole('tab', { name: 'Alternatives', exact: true }).click();
  await page.getByRole('button', { name: 'Decision replay timeline' }).click();
  await expect(page.getByText(/Archive belongs to a different decision/)).toBeVisible();
  await expect(page.getByText('Wrong archive', { exact: true })).toHaveCount(0);
});
