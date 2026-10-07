import { z } from 'zod';
import {
  decisionSchema,
  evidenceSchema,
  executionSchema,
  eventSchema,
  outcomeSchema,
  overviewSchema,
  type ScenarioKey,
  type Objective,
} from './contracts';
import { fixtureService, loadFixtureState } from './fixture-service';
import { sourceSchema } from './contracts';
import {
  calibrationSchema,
  accuracySchema,
  upliftSchema,
  feedbackSchema,
  modelSchema,
  reconciliationSchema,
  dataHealthSchema,
  opportunitySchema,
  fatigueSchema,
} from './insight-contracts';
import { workspaceListSchema } from './management-contracts';
import { policySchema, shadowSchema, scenarioCatalogSchema } from './policy-contracts';
import {
  confidenceBandsSchema,
  sourceChecksSchema,
  workspaceObjectiveSchema,
  policyHistorySchema,
} from './completion-contracts';
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
let csrfToken = '';
let onAuthRequired: (() => void) | null = null;
/** The session's CSRF token (from /auth/login or /auth/me); sent on every write. Kept in memory only. */
export function setCsrfToken(token: string) {
  csrfToken = token;
}
/** Called when the API answers 401, so the login gate can show the sign-in form again. */
export function setAuthRequiredHandler(handler: (() => void) | null) {
  onAuthRequired = handler;
}
export function writeHeaders(): Record<string, string> {
  return {
    'Content-Type': 'application/json',
    'X-Request-ID': crypto.randomUUID(),
    'Idempotency-Key': crypto.randomUUID(),
    ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
  };
}
export async function request<T>(
  path: string,
  schema: z.ZodType<T>,
  body?: unknown,
  writeMethod: 'POST' | 'PUT' = 'POST',
  signal?: AbortSignal,
): Promise<T> {
  const mutation = body !== undefined;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`${base}${path}`, {
      method: mutation ? writeMethod : 'GET',
      credentials: 'include',
      signal: signal ? AbortSignal.any([controller.signal, signal]) : controller.signal,
      headers: {
        Accept: 'application/json',
        ...(mutation ? writeHeaders() : {}),
      },
      ...(mutation ? { body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      if (response.status === 401 && !path.startsWith('/auth/')) onAuthRequired?.();
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
const ack = z.object({ ok: z.literal(true) });
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
        evaluateAllocation: async (input: AllocationInput) => {
          const result = await request('/optimizer/whatif', evaluationSchema, input);
          if (
            result.decision_id !== input.decision_id ||
            result.decision_hash !== input.decision_hash ||
            result.objective !== input.objective
          )
            throw new ApiError(
              409,
              'Valuation belongs to another proposal or objective. Reload before evaluating.',
            );
          return result;
        },
        runOptimizer: async (objective: Objective = 'PROFIT') => {
          const d = await request('/optimizer/run', decisionSchema, { objective });
          if (d.objective !== objective)
            throw new ApiError(
              409,
              'Optimizer returned another objective. Review the current proposal.',
            );
          return d;
        },
        modify: async (input: AllocationInput) => {
          const d = await request(
            `/decisions/${encodeURIComponent(input.decision_id)}/modify`,
            decisionSchema,
            input,
          );
          if (
            d.objective !== input.objective ||
            d.decision_id === input.decision_id ||
            d.follows !== input.decision_id
          )
            throw new ApiError(
              409,
              'Revision identity or objective does not match this proposal. Refresh decision history before retrying.',
            );
          return d;
        },
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
  { name: 'Outcomes', path: '/outcomes', schema: z.array(outcomeSchema) },
  { name: 'Event polling', path: '/events', schema: z.array(eventSchema) },
  { name: 'Opportunities', path: '/opportunities', schema: z.array(opportunitySchema) },
  { name: 'Creative fatigue', path: '/creatives/fatigue', schema: z.array(fatigueSchema) },
  { name: 'Data sources', path: '/data/sources', schema: z.array(sourceSchema) },
  { name: 'Source checks', path: '/data/health', schema: sourceChecksSchema },
  {
    name: 'SKU mapping coverage',
    path: '/data/mapping-coverage',
    schema: dataHealthSchema.pick({ coverage: true, unmapped: true, note: true }),
  },
  {
    name: 'Attribution reconciliation',
    path: '/data/reconciliation',
    schema: reconciliationSchema,
  },
  { name: 'Workspaces', path: '/workspaces', schema: workspaceListSchema },
  { name: 'Calibration', path: '/learning/calibration', schema: calibrationSchema },
  { name: 'Prediction accuracy', path: '/learning/accuracy', schema: accuracySchema },
  { name: 'Strategy uplift', path: '/learning/uplift', schema: upliftSchema },
  { name: 'Feedback', path: '/learning/feedback', schema: feedbackSchema },
  { name: 'Model registry', path: '/models', schema: z.array(modelSchema) },
  { name: 'Execution policy', path: '/policy', schema: policySchema },
  { name: 'Policy history', path: '/policy/history', schema: policyHistorySchema },
  { name: 'Workspace objective', path: '/objective', schema: workspaceObjectiveSchema },
  { name: 'Shadow decisions', path: '/learning/shadow', schema: shadowSchema },
  {
    name: 'Confidence qualification',
    path: '/learning/qualification',
    schema: confidenceBandsSchema,
  },
  { name: 'Scenario capabilities', path: '/sim/scenarios', schema: scenarioCatalogSchema },
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
