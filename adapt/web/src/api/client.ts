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
import {
  anomalySchema,
  evaluationSchema,
  healthSchema,
  ledgerSchema,
  optimizerContextSchema,
  type AllocationInput,
  type Anomaly,
  type RecoveryInput,
} from './workbench-contracts';

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
        `Response contract mismatch at ${path}. Ask the backend team to align the frontend schema.`,
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
        anomalies: () => request('/anomalies', z.array(anomalySchema)),
        anomaly: (id: string) => request(`/anomalies/${encodeURIComponent(id)}`, anomalySchema),
        anomalyStatus: (id: string, status: Anomaly['status'], reason: string) =>
          request(`/anomalies/${encodeURIComponent(id)}/status`, anomalySchema, { status, reason }),
        optimizerContext: () => request('/optimizer/context', optimizerContextSchema),
        evaluateAllocation: (input: AllocationInput) =>
          request('/optimizer/whatif', evaluationSchema, input),
        runOptimizer: () => request('/optimizer/run', decisionSchema, { objective: 'PROFIT' }),
        modify: (input: AllocationInput) =>
          request(
            `/decisions/${encodeURIComponent(input.decision_id)}/modify`,
            decisionSchema,
            input,
          ),
        ledger: () => request('/ledger', z.array(ledgerSchema)),
        fault: (type: 'UNKNOWN' | 'FAILED') => request(`/sim/fault/${type}`, ack, {}),
        recover: (input: RecoveryInput) =>
          request(
            `/executions/${encodeURIComponent(input.execution_id)}/${input.action}`,
            executionSchema,
            {
              decision_hash: input.decision_hash,
              reason: input.reason,
              ...(input.final_resolution ? { final_resolution: input.final_resolution } : {}),
            },
          ),
      };

export const apiBase = base;
export const readinessEndpoints = [
  { name: 'Command Center', path: '/overview', schema: overviewSchema },
  { name: 'Decisions', path: '/decisions', schema: z.array(decisionSchema) },
  { name: 'Anomalies', path: '/anomalies', schema: z.array(anomalySchema) },
  { name: 'Optimizer context', path: '/optimizer/context', schema: optimizerContextSchema },
  { name: 'Executions', path: '/executions', schema: z.array(executionSchema) },
  { name: 'Ledger', path: '/ledger', schema: z.array(ledgerSchema) },
];
export async function checkConnection() {
  const probes = [
    { name: 'Backend health', path: '/health', schema: healthSchema },
    ...readinessEndpoints,
  ];
  return Promise.all(
    probes.map(async (probe) => {
      try {
        const payload = await request(probe.path, probe.schema as z.ZodType<unknown>);
        return {
          name: probe.name,
          path: probe.path,
          status: 'READY',
          detail: 'Response matches the frontend contract.',
          health: probe.path === '/health' ? healthSchema.parse(payload) : null,
        };
      } catch (error) {
        const e = error instanceof ApiError ? error : new ApiError(0, String(error));
        return {
          name: probe.name,
          path: probe.path,
          status:
            e.status === 404
              ? 'MISSING'
              : [401, 403].includes(e.status)
                ? 'AUTH_REQUIRED'
                : e.status === 502
                  ? 'CONTRACT_MISMATCH'
                  : e.status === 0
                    ? 'OFFLINE'
                    : 'ERROR',
          detail: e.message,
          health: null,
        };
      }
    }),
  );
}
