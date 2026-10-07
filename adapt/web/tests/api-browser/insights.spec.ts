import { expect, test } from '@playwright/test';
import { fixtureOverview, fixtureDecision, fixtureEvidence } from '../../src/api/fixtures';

test('remaining APIs fail visibly without fixture substitution', async ({ page }) => {
  await page.route('**/api/v1/**', (r) =>
    r.fulfill({ status: 503, json: { detail: 'Insight service unavailable.' } }),
  );
  for (const path of ['/opportunities', '/outcomes', '/learning', '/data']) {
    await page.goto(path);
    await expect(page.getByText('Insight service unavailable.').first()).toBeVisible();
    await expect(page.getByText('FRONTEND FIXTURES', { exact: true })).toHaveCount(0);
  }
});
test('SSE Copilot accepts a complete grounded response and rejects unsafe or truncated answers', async ({
  page,
}) => {
  let response = '';
  let chats = 0;
  await page.route('**/api/v1/**', (r) => {
    if (r.request().url().endsWith('/copilot/chat')) {
      chats++;
      expect(r.request().method()).toBe('POST');
      expect(r.request().headers()['idempotency-key']).toBeTruthy();
      return r.fulfill({ contentType: 'text/event-stream', body: response });
    }
    return r.fulfill({ status: 503, json: { detail: 'Backend unavailable.' } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open Copilot' }).click();
  const payload = {
    text: 'Inspect the inventory gate before increasing spend.',
    evidence: [{ label: 'Inventory evidence', href: '/decisions/d-1' }],
    mode: 'LLM',
  };
  response = `data: ${JSON.stringify({ type: 'answer', reply: payload })}\r\n\r\ndata: {"type":"done"}\r\n\r\n`;
  await page.getByLabel('Ask Copilot').fill('Why inventory risk?');
  await page.getByRole('button', { name: 'Ask question', exact: true }).click();
  await expect(page.getByText(payload.text, { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Inventory evidence' })).toHaveAttribute(
    'href',
    '/decisions/d-1',
  );
  response = `data: ${JSON.stringify({ type: 'answer', reply: { ...payload, text: 'UNSAFE ANSWER', evidence: [{ label: 'Unsafe', href: 'https://evil.example' }] } })}\n\ndata: {"type":"done"}\n\n`;
  await page.getByLabel('Ask Copilot').fill('Unsafe link test');
  await page.getByRole('button', { name: 'Ask question', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('alert')).toBeVisible();
  await expect(page.getByText('UNSAFE ANSWER', { exact: true })).toHaveCount(0);
  response = `data: ${JSON.stringify({ type: 'answer', reply: { ...payload, text: 'TRUNCATED ANSWER' } })}\n\n`;
  await page.getByLabel('Ask Copilot').fill('Truncated stream test');
  await page.getByRole('button', { name: 'Ask question', exact: true }).click();
  await expect(page.getByText(/stream ended before a complete answer/)).toBeVisible();
  await expect(page.getByText('TRUNCATED ANSWER', { exact: true })).toHaveCount(0);
  expect(chats).toBe(3);
});
test('failed import confirmation retries the staged ID without uploading twice', async ({
  page,
}) => {
  let uploads = 0;
  let confirmations = 0;
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/ingest/upload')) {
      uploads++;
      return r.fulfill({
        json: {
          import_id: 'import-test',
          status: 'STAGED',
          row_count: 1,
          message: 'Staged by backend.',
        },
      });
    }
    if (path.endsWith('/ingest/mapping/confirm')) {
      confirmations++;
      expect(r.request().postDataJSON().import_id).toBe('import-test');
      return confirmations === 1
        ? r.fulfill({
            status: 503,
            json: { detail: 'Confirmation service unavailable; retry same import.' },
          })
        : r.fulfill({
            json: {
              import_id: 'import-test',
              status: 'IMPORTED',
              row_count: 1,
              message: 'Backend import confirmed.',
            },
          });
    }
    if (path.endsWith('/overview')) return r.fulfill({ json: fixtureOverview('DEMO_01', 0) });
    return r.fulfill({ status: 503, json: { detail: 'Source service unavailable.' } });
  });
  await page.goto('/data');
  await page.getByRole('button', { name: 'CSV import' }).click();
  await page.getByLabel('Import type').selectOption('margins');
  await page.getByLabel('CSV file').setInputFiles({
    name: 'margin.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('sku,price,unit_cost\nhero,500,200'),
  });
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Submit validated import' }).click();
  await expect(
    page.getByText('Confirmation service unavailable; retry same import.'),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Submit validated import' }).click();
  await expect(page.getByText(/Backend import confirmed/)).toBeVisible();
  expect(uploads).toBe(1);
  expect(confirmations).toBe(2);
});
test('replay verification is rejected when it belongs to a different proposal hash', async ({
  page,
}) => {
  const d = fixtureDecision('DEMO_01');
  await page.route('**/api/v1/**', (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith('/replay'))
      return r.fulfill({
        json: {
          status: 'VERIFIED',
          expected_hash: 'another-hash',
          actual_hash: 'another-hash',
          message: 'Replay succeeded.',
        },
      });
    const json = path.endsWith('/overview')
      ? fixtureOverview('DEMO_01', 0)
      : path.endsWith('/evidence')
        ? fixtureEvidence('DEMO_01')
        : path.endsWith('/decisions')
          ? [d]
          : path.endsWith('/timeline')
            ? []
            : path.includes('/decisions/')
              ? d
              : [];
    return r.fulfill({ json });
  });
  await page.goto('/decisions');
  await page.getByRole('button', { name: 'Decision replay timeline' }).click();
  await page.getByRole('button', { name: 'Check replay availability' }).click();
  await expect(page.getByText(/Replay result belongs to a different decision hash/)).toBeVisible();
  await expect(page.getByText('Replay succeeded.')).toHaveCount(0);
});
