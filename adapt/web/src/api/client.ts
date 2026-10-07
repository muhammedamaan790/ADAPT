import { z } from 'zod';
import {
  decisionSchema,
  evidenceSchema,
  executionSchema,
  eventSchema,
  outcomeSchema,
  overviewSchema,
  type ScenarioKey,
} from './contracts';
import { fixtureService, loadFixtureState } from './fixture-service';

export const dataMode = import.meta.env.VITE_DATA_MODE === 'api' ? 'api' : 'fixture';
const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');
if (dataMode === 'fixture') loadFixtureState();
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}
export async function request<T>(path: string, schema: z.ZodType<T>, body?: unknown): Promise<T> {
  const mutation = body !== undefined;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`${base}${path}`, {
      method: mutation ? 'POST' : 'GET',
      credentials: 'include',
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...(mutation
          ? {
              'Content-Type': 'application/json',
              'X-Request-ID': crypto.randomUUID(),
              'Idempotency-Key': crypto.randomUUID(),
            }
          : {}),
      },
      ...(mutation ? { body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      const content = await response.json().catch(() => ({}));
      const detail =
        typeof content.detail === 'string'
          ? content.detail
          : `API returned ${response.status}. Refresh and review the current state.`;
      throw new ApiError(response.status, detail);
    }
    const parsed = schema.safeParse(await response.json());
    if (!parsed.success)
      throw new ApiError(
        502,
        `Response contract mismatch at ${path}. Ask the backend team to align the Stage 1 schema.`,
      );
    return parsed.data;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(
      0,
      error instanceof Error && error.name === 'AbortError'
        ? 'API timed out. Refresh to verify state before retrying a mutation.'
        : 'Cannot reach the backend. Check FastAPI and the API URL. Fixture mode has not been substituted.',
    );
  } finally {
    clearTimeout(timer);
  }
}
const ack = z.object({ ok: z.boolean() });
export const api =
  dataMode === 'fixture'
    ? fixtureService
    : {
        overview: () => request('/overview', overviewSchema),
        decisions: () => request('/decisions', z.array(decisionSchema)),
        decision: (id: string) => request(`/decisions/${encodeURIComponent(id)}`, decisionSchema),
        evidence: (id: string) =>
          request(`/decisions/${encodeURIComponent(id)}/evidence`, evidenceSchema),
        executions: () => request('/executions', z.array(executionSchema)),
        outcomes: () => request('/outcomes', z.array(outcomeSchema)),
        // Provisional polling read endpoint; C6 may replace this with /events/stream SSE.
        events: () => request('/events', z.array(eventSchema)),
        approve: (id: string, hash: string) =>
          request(`/decisions/${encodeURIComponent(id)}/approve`, decisionSchema, {
            decision_hash: hash,
            execute: true,
          }),
        reject: (id: string, hash: string, reason: string) =>
          request(`/decisions/${encodeURIComponent(id)}/reject`, decisionSchema, {
            decision_hash: hash,
            reason,
          }),
        scenario: (key: ScenarioKey) => request(`/sim/scenario/${key}`, ack, {}),
        reset: (seed: number) => request(`/sim/reset?seed=${seed}`, ack, {}),
        advance: (days: number) => request(`/sim/advance?days=${days}`, ack, {}),
      };
