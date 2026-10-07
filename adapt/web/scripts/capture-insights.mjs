import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
const root = '../../.impeccable/review/remaining';
await mkdir(root, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE_PATH });
const errors = [],
  captures = [];
for (const [name, viewport] of [
  ['desktop', { width: 1440, height: 1000 }],
  ['mobile', { width: 390, height: 844 }],
]) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  page.on('pageerror', (e) => errors.push(e.message));
  async function capture(slug, locator) {
    await page.evaluate(() => document.fonts.ready);
    if (!locator) await page.evaluate(() => scrollTo(0, 0));
    const path = `${root}/${name}-${slug}.png`;
    if (locator) await locator.screenshot({ path });
    else await page.screenshot({ path, fullPage: true });
    captures.push(`${name}-${slug}.png`);
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth))
      errors.push(`${name}-${slug}: overflow`);
  }
  await page.goto('http://127.0.0.1:5173/opportunities');
  await page.getByRole('heading', { name: 'Evidence graph' }).waitFor();
  await page.getByRole('heading', { name: 'Response curve', exact: true }).waitFor();
  await page.locator('.response-curve').waitFor();
  await capture('opportunities');
  await page.goto('http://127.0.0.1:5173/data');
  await page.getByRole('heading', { name: 'Metric lineage' }).waitFor();
  await page.locator('.connection-list li').first().waitFor();
  await capture('sources');
  await page.getByRole('button', { name: 'CSV import' }).click();
  await page.getByLabel('Import type').selectOption('inventory');
  await page.getByLabel('CSV file').setInputFiles({
    name: 'inventory.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('sku,on_hand,reserved,safety_stock\nhero,500,20,30'),
  });
  await page.getByText(/All 1 rows pass/).waitFor();
  await capture('csv');
  await page.goto('http://127.0.0.1:5173/decisions');
  await page.getByRole('button', { name: 'Comparison & sensitivity' }).click();
  await page.getByRole('heading', { name: 'ROAS rank' }).waitFor();
  await capture(
    'comparison',
    page
      .locator('.panel')
      .filter({ has: page.getByRole('heading', { name: 'Compare & inspect history' }) }),
  );
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm fixture approval' }).click();
  await page.getByRole('button', { name: 'Executed & verified' }).waitFor();
  await page.getByRole('button', { name: 'Advance 3 days', exact: true }).click();
  await page.getByRole('heading', { name: 'Prediction meets the outcome' }).waitFor();
  await page.goto('http://127.0.0.1:5173/outcomes');
  await page.getByText('Calibration applied', { exact: true }).waitFor();
  await capture('outcomes');
  await page.goto('http://127.0.0.1:5173/learning');
  await page.getByText('0.86×', { exact: true }).waitFor();
  await capture('learning');
  if (name === 'desktop') {
    await page.getByRole('button', { name: 'Switch to dark theme' }).click();
    await capture('learning-dark');
    await page.getByRole('button', { name: 'Switch to light theme' }).click();
  }
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  await page.getByRole('button', { name: 'What feedback has matured?' }).click();
  await page.getByText('Fixture template', { exact: true }).waitFor();
  await capture('copilot', page.getByRole('dialog'));
  await context.close();
}
await browser.close();
await writeFile(`${root}/capture.json`, JSON.stringify({ errors, captures }, null, 2));
console.log(JSON.stringify({ errors, captures }));
if (errors.length) process.exitCode = 1;
