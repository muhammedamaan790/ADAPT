import { expect, test } from '@playwright/test';

test('Outcomes and Learning show the same matured feedback with class filters', async ({
  page,
}) => {
  await page.goto('/outcomes');
  await expect(page.getByRole('heading', { name: 'No matured outcomes yet' })).toBeVisible();
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm fixture approval' }).click();
  await expect(page.getByRole('button', { name: 'Executed & verified' })).toBeVisible({
    timeout: 10000,
  });
  await page.getByRole('button', { name: 'Advance 3 days', exact: true }).click();
  await page.getByRole('link', { name: 'Outcomes', exact: true }).click();
  await expect(page.getByText('Calibration applied', { exact: true })).toBeVisible();
  await page.getByLabel('Decision class').selectOption('SAFETY');
  await expect(page.getByRole('heading', { name: 'No matching outcomes' })).toBeVisible();
  await page.getByLabel('Decision class').selectOption('OPTIMIZATION');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download outcome records' }).click();
  expect((await download).suggestedFilename()).toBe('adapt-outcomes.json');
  await page.getByRole('link', { name: 'Learning', exact: true }).click();
  await expect(page.getByText('0.86×', { exact: true })).toBeVisible();
  await expect(page.getByText('Applied', { exact: true })).toBeVisible();
  await expect(page.getByText(/No held-out benchmark report/)).toBeVisible();
});
test('Ask ADAPT links an allocation answer to the decision evidence', async ({ page }) => {
  await page.goto('/');
  const agent = page.locator('.ask-panel');
  await agent.getByRole('button', { name: 'What is waiting for my approval?' }).click();
  await expect(agent.locator('.ask-user')).toHaveText('What is waiting for my approval?');
  await expect(agent.getByText(/needs the backend with GROQ_API_KEY/)).toBeVisible();
  await agent.getByLabel('Ask ADAPT').fill('Why this allocation?');
  await page.keyboard.press('Enter');
  await agent
    .getByRole('link', { name: /^Decision / })
    .last()
    .click();
  await expect(page.getByRole('heading', { name: 'Compare & inspect history' })).toBeVisible();
});
test('comparison, replay and sensitivity revisions remain separate from execution', async ({
  page,
}) => {
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Decision replay timeline' }).click();
  await page.getByRole('button', { name: 'Check replay availability' }).click();
  await expect(page.getByText(/No hash verification was performed/)).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download snapshot' }).click();
  expect((await download).suggestedFilename()).toContain('snapshot.json');
  await page.getByRole('button', { name: 'Comparison & sensitivity' }).click();
  await expect(page.getByRole('heading', { name: 'ROAS rank' })).toBeVisible();
  await page.getByRole('button', { name: 'Revise to conservative allocation' }).click();
  await page.getByRole('button', { name: 'Confirm sensitivity revision' }).click();
  await expect(page.getByRole('heading', { name: 'Awaiting backend valuation' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Approve & execute', exact: true })).toBeDisabled();
  await expect(page.getByText(/No hash verification was performed/)).toHaveCount(0);
});
test('pages and the agent fit mobile with accessible navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('link', { name: 'Outcomes', exact: true }).click();
  for (const [path, name] of [
    ['/outcomes', 'Outcomes'],
    ['/learning', 'Learning'],
    ['/data', 'Data Hub'],
    ['/', 'Command Center'],
  ]) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
  }
  await page.getByLabel('Ask ADAPT').fill('What feedback has matured?');
  await page.getByRole('button', { name: 'Send question' }).click();
  await expect(page.locator('.ask-bot')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
