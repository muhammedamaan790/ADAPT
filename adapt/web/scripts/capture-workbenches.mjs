import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const root = '../../.impeccable/review/next';
await mkdir(root, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE_PATH });
const errors = [];
const captures = [];
for (const [name, viewport] of [
  ['desktop', { width: 1440, height: 1000 }],
  ['mobile', { width: 390, height: 844 }],
]) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  page.on('pageerror', (e) => errors.push(e.message));
  async function capture(slug) {
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(() => scrollTo(0, 0));
    await page.screenshot({ path: `${root}/${name}-${slug}.png`, fullPage: true });
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth))
      errors.push(`${name}-${slug}: overflow`);
    captures.push(`${name}-${slug}.png`);
  }
  await page.goto('http://127.0.0.1:5173/anomalies');
  await page.getByRole('heading', { name: 'Root-cause assessment' }).waitFor();
  await capture('anomalies');
  await page.goto('http://127.0.0.1:5173/optimizer');
  await page.getByRole('button', { name: 'Evaluate allocation', exact: true }).click();
  await page.getByText('Recorded valuation', { exact: true }).waitFor();
  await capture('optimizer');
  if (name === 'desktop') {
    await page.getByRole('button', { name: 'Switch to dark theme' }).click();
    await capture('optimizer-dark');
    await page.getByRole('button', { name: 'Switch to light theme' }).click();
  }
  await page.goto('http://127.0.0.1:5173/connection');
  await page.locator('.connection-list li').first().waitFor();
  await capture('connection');
  await page.goto('http://127.0.0.1:5173/decisions');
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm fixture approval' }).click();
  await page.goto('http://127.0.0.1:5173/executions');
  await page.getByRole('button', { name: 'Inject unknown result', exact: true }).click();
  await page.getByText('unknown', { exact: true }).waitFor();
  await capture('execution');
  if (name === 'desktop') {
    await page.getByRole('button', { name: 'Verify platform state', exact: true }).click();
    await page.getByText('succeeded', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Restore prior settings', exact: true }).click();
    await page
      .getByLabel('Recovery reason')
      .fill('Restore the prior settings after manager review.');
    await page.getByRole('dialog').screenshot({ path: `${root}/desktop-recovery-dialog.png` });
    captures.push('desktop-recovery-dialog.png');
  }
  await context.close();
}
await browser.close();
await writeFile(`${root}/capture.json`, JSON.stringify({ errors, captures }, null, 2));
console.log(JSON.stringify({ errors, captures }));
if (errors.length) process.exitCode = 1;
