// Intercepted API examples exercise presentation; none are engine qualification evidence.
import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import {
  objective,
  history,
  source,
  reconciliation,
  confidence,
} from '../tests/completion-data.ts';
import { fixtureOverview } from '../src/api/fixtures.ts';
const root = '../../.impeccable/review/completion';
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
    await page.evaluate(() => scrollTo(0, 0));
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
  await page.goto('http://127.0.0.1:5173/executions?section=settings');
  await page.getByRole('heading', { name: 'No policy history supplied' }).waitFor();
  await capture('settings-fixture');
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/copilot/sql'))
      return r.fulfill({
        json: {
          query: r.request().postDataJSON().query,
          columns: ['campaign', 'revenue', 'missing'],
          rows: [['Very long campaign / synthetic query evidence', 1200, null]],
          truncated: false,
          elapsed_ms: 8,
          as_of: '2026-10-07T06:00:00Z',
        },
      });
    const json = path.endsWith('/overview')
      ? fixtureOverview('DEMO_01', 0)
      : path.endsWith('/objective')
        ? objective
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
  await page.goto('http://127.0.0.1:5174/executions?section=settings');
  await page.getByLabel('Default objective').waitFor();
  await capture('settings-api');
  await page.getByLabel('Default objective').selectOption('GROWTH');
  await page.getByRole('button', { name: 'Review objective change' }).click();
  await page.getByLabel('Change reason').fill('Reviewed growth target with the team.');
  await capture('objective-review', page.getByRole('dialog'));
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  await page.goto('http://127.0.0.1:5174/data');
  await page.getByText('Inspect Meta Ads checks', { exact: true }).click();
  await page.getByText('1.20×', { exact: true }).waitFor();
  await capture('data-details');
  await page.goto('http://127.0.0.1:5174/learning');
  await page.getByText('18 / 20', { exact: true }).waitFor();
  await capture(
    'confidence',
    page
      .locator('section')
      .filter({ has: page.getByRole('heading', { name: 'Confidence-region evidence' }) }),
  );
  await page.goto('http://127.0.0.1:5174/');
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await page.getByRole('button', { name: 'SQL inspection' }).click();
  await page.getByRole('button', { name: 'Run read-only query' }).click();
  await page.getByRole('cell', { name: 'NULL', exact: true }).waitFor();
  await page
    .getByRole('region', { name: 'SQL results; scroll to inspect all rows and columns' })
    .focus();
  await capture('sql', page.getByRole('dialog'));
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.goto('http://127.0.0.1:5174/executions?section=settings');
  await page.getByLabel('Default objective').waitFor();
  await page.getByRole('button', { name: 'Switch to dark theme' }).click();
  await capture('settings-dark');
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await page.getByRole('button', { name: 'SQL inspection' }).click();
  await page.getByRole('button', { name: 'Run read-only query' }).click();
  await page.getByRole('cell', { name: 'NULL', exact: true }).waitFor();
  await page
    .getByRole('region', { name: 'SQL results; scroll to inspect all rows and columns' })
    .focus();
  await capture('sql-dark', page.getByRole('dialog'));
  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.goto('http://127.0.0.1:5174/learning');
  await page.getByText('18 / 20', { exact: true }).waitFor();
  await capture(
    'confidence-dark',
    page
      .locator('section')
      .filter({ has: page.getByRole('heading', { name: 'Confidence-region evidence' }) }),
  );
  await page.route('**/src/components/Copilot.tsx*', (r) => r.abort());
  await page.goto('http://127.0.0.1:5173/');
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await page.getByRole('dialog', { name: 'Copilot unavailable' }).waitFor();
  await capture('copilot-recovery', page.getByRole('dialog'));
  await page.getByRole('button', { name: 'Close assistant' }).click();
  await ctx.close();
}
await browser.close();
await writeFile(
  `${root}/capture-report.json`,
  JSON.stringify({ captures, errors, geometry }, null, 2),
);
if (errors.length) throw new Error(errors.join('\n'));
console.log(`Captured ${captures.length} views without document overflow or page errors.`);
