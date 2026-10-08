import { expect, test } from '@playwright/test';
import { fixtureOverview, fixtureDecision, fixtureEvidence } from '../../src/api/fixtures';

test('remaining APIs fail visibly without fixture substitution', async ({ page }) => {
  await page.route('**/api/v1/**', (r) =>
    r.fulfill({ status: 503, json: { detail: 'Insight service unavailable.' } }),
  );
  for (const path of ['/outcomes', '/learning', '/data']) {
    await page.goto(path);
    await expect(page.getByText('Insight service unavailable.').first()).toBeVisible();
    await expect(page.getByText('FRONTEND FIXTURES', { exact: true })).toHaveCount(0);
  }
});
test('Ask ADAPT streams tool steps, shows checked answers with history and rejects unsafe or truncated ones', async ({
  page,
}) => {
  let response = '';
  const bodies: { message: string; history: { role: string; content: string }[] }[] = [];
  await page.route('**/api/v1/**', (r) => {
    const url = r.request().url();
    if (url.endsWith('/copilot/chat')) {
      expect(r.request().method()).toBe('POST');
      expect(r.request().headers()['idempotency-key']).toBeTruthy();
      bodies.push(r.request().postDataJSON());
      return r.fulfill({ contentType: 'text/event-stream', body: response });
    }
    if (url.endsWith('/overview')) return r.fulfill({ json: fixtureOverview('DEMO_01', 14) });
    return r.fulfill({ status: 503, json: { detail: 'Backend unavailable.' } });
  });
  await page.goto('/');
  const agent = page.locator('.ask-panel');
  const ask = async (q: string) => {
    await agent.getByLabel('Ask ADAPT').fill(q);
    await agent.getByRole('button', { name: 'Send question' }).click();
  };
  const payload = {
    text: 'Inspect the **inventory gate** before increasing spend.\n- Hero SKU is short by 42 units',
    evidence: [{ label: 'Inventory evidence', href: '/decisions/d-1' }],
    mode: 'LLM',
    model: 'openai/gpt-oss-120b',
    verified: true,
    unverified: [],
    tools: ['get_decision'],
  };
  const event = (e: object) => `data: ${JSON.stringify(e)}\r\n\r\n`;
  response =
    event({ type: 'status', text: 'Opening the decision' }) +
    event({ type: 'answer', reply: payload }) +
    event({ type: 'done' });
  await ask('Why inventory risk?');
  await expect(agent.locator('.ask-bot strong')).toHaveText('inventory gate');
  await expect(agent.locator('.ask-bot li')).toHaveText('Hero SKU is short by 42 units');
  await expect(agent.getByRole('link', { name: /Inventory evidence/ })).toHaveAttribute(
    'href',
    '/decisions/d-1',
  );
  await expect(agent.getByText('Figures checked against your data')).toBeVisible();
  expect(bodies[0]).toEqual({ message: 'Why inventory risk?', history: [] });

  response =
    event({
      type: 'answer',
      reply: {
        ...payload,
        text: 'Spend is ₹9,99,999.',
        verified: false,
        unverified: ['₹9,99,999'],
      },
    }) + event({ type: 'done' });
  await ask('And spend?');
  await expect(agent.getByText('Not found in data: ₹9,99,999')).toBeVisible();
  expect(bodies[1].history).toEqual([
    { role: 'user', content: 'Why inventory risk?' },
    { role: 'assistant', content: payload.text },
  ]);

  response =
    event({
      type: 'answer',
      reply: {
        ...payload,
        text: 'UNSAFE ANSWER',
        evidence: [{ label: 'Unsafe', href: 'https://evil.example' }],
      },
    }) + event({ type: 'done' });
  await ask('Unsafe link test');
  await expect(agent.locator('.ask-error').last()).toContainText('contract mismatch');
  await expect(agent.getByText('UNSAFE ANSWER', { exact: true })).toHaveCount(0);

  response = event({ type: 'answer', reply: { ...payload, text: 'TRUNCATED ANSWER' } });
  await ask('Truncated stream test');
  await expect(agent.locator('.ask-error').last()).toContainText('ended before it was complete');
  await expect(agent.getByText('TRUNCATED ANSWER', { exact: true })).toHaveCount(0);

  response = event({ type: 'error', message: 'The assistant failed: model offline' });
  await ask('Error event test');
  await expect(agent.locator('.ask-error').last()).toContainText('model offline');
  await agent.getByRole('button', { name: 'Ask again' }).last().click();
  expect(bodies.at(-1)!.message).toBe('Error event test');
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
