import { expect, test } from '@playwright/test';

test('home budget proposal opens the exact review and retains guarded approval', async ({
  page,
}) => {
  await page.goto('/');
  const proposal = page.locator('.home-proposal');
  await expect(
    proposal.getByRole('heading', { name: 'Budget proposal', exact: true }),
  ).toBeVisible();
  await expect(proposal.locator('.home-allocation')).toContainText('₹32,000');
  const marks = proposal.locator('.home-allocation .channel-mark img');
  await expect(marks).toHaveCount(4);
  expect(
    await marks.evaluateAll((images) =>
      images.every(
        (i) => (i as HTMLImageElement).complete && (i as HTMLImageElement).naturalWidth > 0,
      ),
    ),
  ).toBe(true);
  await proposal.getByRole('link', { name: 'Review & approve', exact: true }).click();
  await expect(page).toHaveURL(/\/decisions\/dec-demo_01-001#decision-review$/);
  await expect(page.locator('#decision-review')).toBeFocused();
  await page.getByRole('button', { name: 'Approve & execute', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Confirm fixture approval' })).toBeDisabled();
  await page.getByRole('checkbox').check();
  await expect(page.getByRole('button', { name: 'Confirm fixture approval' })).toBeEnabled();
});

test('phone budgets show both amounts without sideways scrolling and lead to review', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const proposal = page.locator('.home-proposal');
  await expect(proposal.locator('.home-allocation-mobile')).toBeVisible();
  await expect(proposal.locator('.home-allocation-scroll')).toBeHidden();
  const hero = proposal.locator('.mobile-budget-leg').first();
  await expect(hero).toContainText('₹40,000');
  await expect(hero).toContainText('₹32,000');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await proposal.getByRole('link', { name: 'Review & approve', exact: true }).click();
  await expect(page.locator('#decision-review')).toBeFocused();
  await expect(page.getByRole('button', { name: 'Approve & execute', exact: true })).toBeVisible();
});
