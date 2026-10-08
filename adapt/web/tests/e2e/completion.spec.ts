import { test, expect } from '@playwright/test';
test('the Ask ADAPT agent greets, answers with evidence links and survives navigation', async ({
  page,
}) => {
  await page.goto('/');
  const agent = page.locator('.ask-panel');
  await expect(agent.getByRole('heading', { name: /What would you like to know\?/ })).toBeVisible();
  await agent.getByLabel('Ask ADAPT').fill('hello');
  await page.keyboard.press('Enter');
  await expect(agent.getByText(/^Hi! I'm ADAPT's assistant/)).toBeVisible();
  await agent.getByLabel('Ask ADAPT').fill('Why this allocation?');
  await agent.getByRole('button', { name: 'Send question' }).click();
  const link = agent.getByRole('link', { name: /^Decision / }).last();
  await expect(link).toBeVisible();
  await link.click();
  await expect(page).toHaveURL(/\/decisions\//);
  await page.getByRole('link', { name: 'Command Center', exact: true }).click();
  await expect(page.locator('.ask-user').first()).toHaveText('hello');
  await page.locator('.ask-panel').getByRole('button', { name: 'New conversation' }).click();
  await expect(page.locator('.ask-msg')).toHaveCount(0);
});
test('fixture settings and confidence disclose unavailable backend evidence', async ({ page }) => {
  await page.goto('/executions?section=settings');
  await expect(page.getByLabel('Default objective')).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Review objective change' })).toBeDisabled();
  await expect(page.getByRole('heading', { name: 'No policy history supplied' })).toBeVisible();
  await page.goto('/learning');
  await expect(
    page.getByRole('heading', { name: 'No qualification evidence supplied' }),
  ).toBeVisible();
});
test('unknown routes and removed pages recover through navigation', async ({ page }) => {
  for (const path of ['/does-not-exist', '/optimizer', '/opportunities', '/connection']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
  }
  await page.getByRole('link', { name: 'Return to Command Center' }).click();
  await expect(page.getByRole('heading', { name: 'Command Center', exact: true })).toBeVisible();
});
