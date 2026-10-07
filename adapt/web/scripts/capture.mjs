import { chromium } from '@playwright/test';
import { mkdir } from 'node:fs/promises';

await mkdir('../../.impeccable/review', { recursive: true });
const browser = await chromium.launch({
  ...(process.env.CHROMIUM_EXECUTABLE_PATH
    ? { executablePath: process.env.CHROMIUM_EXECUTABLE_PATH }
    : {}),
});
const errors = [];
for (const [name, viewport] of [
  ['desktop', { width: 1440, height: 1000 }],
  ['mobile', { width: 390, height: 844 }],
]) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.on('pageerror', (e) => errors.push(e.message));
  for (const [route, slug] of [
    ['/', 'command'],
    ['/decisions', 'decision'],
    ['/scenarios', 'scenario'],
  ]) {
    await page.goto(`http://127.0.0.1:5173${route}`);
    await page.getByRole('heading', { level: 1 }).waitFor();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `../../.impeccable/review/${name}-${slug}.png`, fullPage: true });
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth);
    if (overflow) errors.push(`${name} ${slug} overflow`);
  }
  if (name === 'desktop') {
    await page.getByRole('button', { name: 'Switch to dark theme' }).click();
    await page.goto('http://127.0.0.1:5173');
    await page.getByRole('heading', { level: 1 }).waitFor();
    await page.screenshot({ path: '../../.impeccable/review/desktop-dark.png', fullPage: true });
    await page.getByRole('button', { name: 'Switch to light theme' }).click();
    await page.goto('http://127.0.0.1:5173/decisions');
    await page.getByRole('button', { name: 'Reject with a reason' }).click();
    await page
      .getByLabel('Reason for rejection')
      .fill('Visual review: rejected proposal should show no execution.');
    await page.getByRole('button', { name: 'Confirm rejection' }).click();
    await page.getByText('Rejected · no execution started', { exact: true }).waitFor();
    await page
      .locator('#execution')
      .screenshot({ path: '../../.impeccable/review/desktop-rejected.png' });
    await page.goto('http://127.0.0.1:5173/scenarios');
    await page.getByRole('radio', { name: /S5/ }).check();
    await page.getByRole('button', { name: 'Load scenario', exact: true }).click();
    await page.getByRole('button', { name: 'Confirm load', exact: true }).click();
    await page.goto('http://127.0.0.1:5173/decisions');
    await page.getByText('Resolve tracking first', { exact: true }).waitFor();
    await page.screenshot({
      path: '../../.impeccable/review/desktop-tracking.png',
      fullPage: true,
    });
  } else {
    await page.goto('http://127.0.0.1:5173/decisions');
    await page.locator('.allocation-mobile').waitFor();
    await page
      .locator('.allocation-mobile')
      .screenshot({ path: '../../.impeccable/review/mobile-allocation.png' });
  }
  await context.close();
}
await browser.close();
console.log(JSON.stringify({ errors, captures: 10 }));
if (errors.length) process.exitCode = 1;
