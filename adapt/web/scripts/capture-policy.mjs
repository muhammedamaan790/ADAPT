// Synthetic response layouts, not calibrated engine evidence.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import { testPolicy, testShadow } from '../tests/policy-data.ts';
import { fixtureDecision, fixtureOverview } from '../src/api/fixtures.ts';
import { optimizerFixture } from '../src/api/workbench-fixtures.ts';
const root = '../../.impeccable/review/policy';
await mkdir(root, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE_PATH });
const captures = [],
  errors = [],
  geometry = [];
for (const [name, viewport] of [
  ['desktop', { width: 1440, height: 1000 }],
  ['mobile', { width: 390, height: 844 }],
]) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
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
      width: document.documentElement.scrollWidth,
      viewport: innerWidth,
      font: getComputedStyle(document.body).fontFamily,
    }));
    geometry.push({ file, ...measure });
    if (measure.width > measure.viewport) errors.push(`${file}: overflow`);
  }
  await page.goto('http://127.0.0.1:5173/optimizer');
  await page.getByLabel('Evaluation objective').waitFor();
  await capture('objectives-fixture');
  await page.goto('http://127.0.0.1:5173/executions?section=policy');
  await page.getByRole('heading', { name: 'No recorded shadow decisions' }).waitFor();
  await capture('policy-missing');
  if (name === 'desktop') {
    await page.getByRole('button', { name: 'Switch to dark theme' }).click();
    await capture('policy-dark');
    await page.getByRole('button', { name: 'Switch to light theme' }).click();
  }
  await page.goto('http://127.0.0.1:5173/scenarios');
  await page.getByText('Later-stage scenarios', { exact: true }).click();
  await capture('scenario-catalog');
  const d = fixtureDecision('DEMO_01');
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    const json = path.endsWith('/policy')
      ? testPolicy
      : path.endsWith('/learning/shadow')
        ? testShadow
        : path.endsWith('/optimizer/context')
          ? { ...optimizerFixture(d), supported_objectives: ['PROFIT', 'GROWTH'] }
          : path.endsWith('/optimizer/whatif')
            ? {
                decision_id: d.decision_id,
                decision_hash: d.decision_hash,
                objective: 'GROWTH',
                allocated: 96000,
                unallocated: 4000,
                estimate_status: 'AVAILABLE',
                estimate: d.expected,
                checks: d.checks,
                objective_value: { label: 'Projected net revenue', value: 12000, unit: 'INR' },
                explanation: 'Synthetic backend valuation; not verified business results.',
              }
            : path.endsWith('/decisions')
              ? [d]
              : path.endsWith('/overview')
                ? fixtureOverview('DEMO_01', 0)
                : [];
    return r.fulfill({ json });
  });
  await page.goto('http://127.0.0.1:5174/optimizer');
  await page.getByLabel('Evaluation objective').selectOption('GROWTH');
  await page.getByRole('button', { name: 'Evaluate allocation', exact: true }).click();
  await page.getByText('Backend valuation', { exact: true }).waitFor();
  await capture('growth-backend');
  await page.goto('http://127.0.0.1:5174/executions?section=policy');
  await page.getByRole('heading', { name: 'TikTok policy' }).waitFor();
  await capture('policy-qualified');
  await page.getByLabel('Requested execution mode').selectOption('SIMULATION_AUTONOMOUS');
  await page.getByRole('button', { name: 'Review mode change' }).click();
  await page
    .getByLabel('Mode change reason')
    .fill('Reviewed qualification evidence for this mock channel.');
  await capture('mode-confirm', page.getByRole('dialog'));
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.getByLabel('Policy channel').selectOption('Google');
  await capture('google-test');
  await capture(
    'shadow-log',
    page
      .locator('.panel')
      .filter({ has: page.getByRole('heading', { name: 'Observe-mode shadow decisions' }) }),
  );
  await ctx.close();
}
await browser.close();
await writeFile(
  `${root}/manifest.json`,
  JSON.stringify(
    {
      captures,
      geometry,
      errors,
      note: 'Ready-state captures use synthetic intercepted API responses. No backend qualification or real outcomes verified.',
    },
    null,
    2,
  ),
);
console.log(JSON.stringify({ captures: captures.length, errors, root }));
if (errors.length) process.exitCode = 1;
