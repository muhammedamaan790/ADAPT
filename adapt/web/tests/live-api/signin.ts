import { expect, type Page } from '@playwright/test';

// Sign in through the real login form when the backend requires it (ADAPT_E2E_USER, default maria; ADAPT_E2E_PASSWORD).
export async function signIn(page: Page) {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
  if (!(await page.getByRole('heading', { name: 'Sign in to ADAPT' }).isVisible())) return;
  const password = process.env.ADAPT_E2E_PASSWORD;
  if (!password)
    throw new Error('The backend requires sign-in: set ADAPT_E2E_PASSWORD (and ADAPT_E2E_USER).');
  await page.getByLabel('User name').fill(process.env.ADAPT_E2E_USER || 'maria');
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in to ADAPT' })).toHaveCount(0);
}

// Headers for a direct API write from a test: the session's CSRF token (when login is on) plus the request ids.
export async function writeHeaders(page: Page, id: string): Promise<Record<string, string>> {
  const me = await page.request.get('/api/v1/auth/me');
  const csrf = me.ok() ? ((await me.json()).csrf_token as string) : '';
  return { 'X-Request-ID': id, 'Idempotency-Key': id, ...(csrf ? { 'X-CSRF-Token': csrf } : {}) };
}
