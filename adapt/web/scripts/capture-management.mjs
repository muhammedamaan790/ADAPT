// Run with Node 22 --experimental-strip-types. Test artifacts are synthetic, not benchmark evidence.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { evaluationTestReport, modelTestDetail } from '../tests/management-data.ts';
import { fixtureDecision, fixtureEvidence, fixtureOverview } from '../src/api/fixtures.ts';
const root = '../../.impeccable/review/management';
await mkdir(root, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE_PATH });
const errors = [],
  captures = [],
  geometry = [];
for (const [name, viewport] of [
  ['desktop', { width: 1440, height: 1000 }],
  ['mobile', { width: 390, height: 844 }],
]) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.on('pageerror', (e) => errors.push(`${name}: ${e.message}`));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  async function capture(slug, locator) {
    await page.evaluate(() => document.fonts.ready);
    if (!locator) await page.evaluate(() => scrollTo(0, 0));
    const file = `${name}-${slug}.png`;
    if (locator) await locator.screenshot({ path: `${root}/${file}` });
    else await page.screenshot({ path: `${root}/${file}`, fullPage: true });
    captures.push(file);
    const measure = await page.evaluate(() => ({
      viewport: innerWidth,
      width: document.documentElement.scrollWidth,
      font: getComputedStyle(document.body).fontFamily,
      panelPadding: getComputedStyle(document.querySelector('.panel')).padding,
    }));
    geometry.push({ file, ...measure });
    if (measure.width > measure.viewport) errors.push(`${file}: document overflow`);
  }
  await page.goto('http://127.0.0.1:5173/data?section=workspaces');
  await page.getByLabel('New workspace name').fill('Refill brand');
  await page.getByRole('button', { name: 'Create local demo' }).click();
  await page.getByText(/Created Refill brand/).waitFor();
  await capture('workspaces');
  await page.getByRole('button', { name: 'Switch to Refill brand' }).click();
  await capture('workspace-confirm', page.getByRole('dialog'));
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  if (name === 'desktop') {
    await page.getByRole('button', { name: 'Switch to dark theme' }).click();
    await capture('workspaces-dark');
    await page.getByRole('button', { name: 'Switch to light theme' }).click();
  }
  await page.goto('http://127.0.0.1:5173/scenarios');
  await page.getByRole('button', { name: 'Head-to-Head', exact: true }).click();
  await page.getByText(/No precomputed eval report/).waitFor();
  await capture('evaluation-empty');
  await page.getByLabel('Precomputed evaluation JSON').setInputFiles({
    name: 'synthetic-test-report.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(evaluationTestReport)),
  });
  await page.getByRole('table').waitFor();
  await page.getByText('Report provenance & evaluation envelope', { exact: true }).click();
  await capture('evaluation-report');
  await page.goto('http://127.0.0.1:5173/learning');
  await page.getByText(/Promotion and rollback are unavailable/).waitFor();
  await capture('models-missing');
  await page.goto('http://127.0.0.1:5173/decisions');
  await page.getByRole('button', { name: 'Decision replay timeline' }).click();
  await page.getByText(/No full archived processing trace/).waitFor();
  await page.getByText('Snapshot and reproducibility identities', { exact: true }).click();
  await capture(
    'archive-missing',
    page
      .locator('.panel')
      .filter({ has: page.getByRole('heading', { name: 'Compare & inspect history' }) }),
  );
  // Separate API-mode app, using intercepted test data only. Backend learning can fail independently.
  const d = fixtureDecision('DEMO_01');
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    const json = path.endsWith('/models')
      ? [
          {
            name: modelTestDetail.name,
            version: modelTestDetail.version,
            status: 'CHALLENGER',
            trained_at: modelTestDetail.trained_at,
            note: 'Synthetic test backend model; no fitted artifact verified.',
          },
        ]
      : path.includes('/models/')
        ? modelTestDetail
        : path.endsWith('/archive')
          ? {
              decision_id: d.decision_id,
              decision_hash: d.decision_hash,
              snapshot_id: d.snapshot_id,
              environment_fingerprint: 'test-env-fingerprint',
              code_sha: 'test-code',
              lock_hash: 'test-lock-hash',
              seed: 42,
              status: 'AVAILABLE',
              note: 'Synthetic archived backend contract for layout inspection only.',
              artifacts: [
                {
                  id: 'snapshot',
                  kind: 'SNAPSHOT',
                  label: 'Decision snapshot',
                  hash: 'a'.repeat(64),
                  href: `/decisions/${d.decision_id}`,
                  status: 'PRESENT',
                },
              ],
              steps: [
                {
                  id: 'snapshot-step',
                  at: '2026-10-07T09:00:00Z',
                  label: 'Snapshot materialized',
                  detail: 'Observed inputs frozen in an immutable snapshot.',
                  artifact_id: 'snapshot',
                },
              ],
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
                    : null;
    return json === null
      ? r.fulfill({
          status: 503,
          json: { detail: 'Learning metrics unavailable in this contract capture.' },
        })
      : r.fulfill({ json });
  });
  await page.goto('http://127.0.0.1:5174/learning');
  await page.getByRole('button', { name: 'Request candidate promotion' }).waitFor();
  await page.getByText('Artifact identity', { exact: true }).click();
  const modelPanel = page
    .locator('.panel')
    .filter({ has: page.getByRole('heading', { name: 'Model registry & controls' }) });
  await capture('models-ready');
  await page.getByRole('button', { name: 'Request candidate promotion' }).click();
  await page
    .getByLabel('Registry change reason')
    .fill('Reviewed artifact, coverage and family baseline.');
  await page.getByRole('checkbox').check();
  await capture('model-confirm', page.getByRole('dialog'));
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.goto('http://127.0.0.1:5174/decisions');
  await page.getByRole('button', { name: 'Decision replay timeline' }).click();
  await page.getByRole('heading', { name: 'Full archived processing trace' }).waitFor();
  await page.getByText('Snapshot and reproducibility identities', { exact: true }).click();
  await capture(
    'archive-ready',
    page
      .locator('.panel')
      .filter({ has: page.getByRole('heading', { name: 'Compare & inspect history' }) }),
  );
  await context.close();
}
await browser.close();
await writeFile(
  `${root}/capture-checks.json`,
  JSON.stringify({ captures, errors, geometry }, null, 2),
);
console.log(JSON.stringify({ captures: captures.length, errors }));
if (errors.length) process.exitCode = 1;
