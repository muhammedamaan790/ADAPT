import { z } from 'zod';
import { api, dataMode, request, ApiError } from './client';
import { activateFixtureWorkspace, getActiveFixtureWorkspace } from './fixture-service';
import {
  workspaceSchema,
  workspaceListSchema,
  modelDetailSchema,
  modelActionSchema,
  evaluationEnvelopeSchema,
  archiveSchema,
  type Workspace,
  type ModelDetail,
} from './management-contracts';
import { modelSchema } from './insight-contracts';
import type { Decision } from './contracts';
const KEY = 'adapt.workspace-registry';
const fallback = {
  active_id: 'default',
  items: [
    {
      id: 'default',
      name: 'D2C workspace',
      currency: 'INR' as const,
      timezone: 'Asia/Kolkata' as const,
    },
  ],
};
export function readWorkspaceRegistry() {
  try {
    const raw = localStorage.getItem(KEY);
    const base = raw ? workspaceListSchema.parse(JSON.parse(raw)) : fallback;
    const active = getActiveFixtureWorkspace();
    return workspaceListSchema.parse({ ...base, active_id: active });
  } catch {
    return structuredClone(fallback);
  }
}
export const management = {
  async workspaces() {
    return dataMode === 'api'
      ? request('/workspaces', workspaceListSchema)
      : readWorkspaceRegistry();
  },
  async createWorkspace(name: string): Promise<Workspace> {
    const clean = workspaceSchema.shape.name.parse(name);
    if (dataMode === 'api')
      return request('/workspaces', workspaceSchema, {
        name: clean,
        currency: 'INR',
        timezone: 'Asia/Kolkata',
      });
    const registry = readWorkspaceRegistry();
    if (registry.items.length >= 20)
      throw new Error('This browser supports up to 20 demo workspaces.');
    if (registry.items.some((w) => w.name.toLowerCase() === clean.toLowerCase()))
      throw new Error('A workspace with this name already exists.');
    const created = workspaceSchema.parse({
      id: `ws-${crypto.randomUUID()}`,
      name: clean,
      currency: 'INR',
      timezone: 'Asia/Kolkata',
    });
    localStorage.setItem(KEY, JSON.stringify({ ...registry, items: [...registry.items, created] }));
    return created;
  },
  async activateWorkspace(id: string) {
    if (dataMode === 'api') {
      const r = await request(
        `/workspaces/${encodeURIComponent(id)}/activate`,
        workspaceListSchema,
        {},
      );
      if (r.active_id !== id)
        throw new ApiError(
          502,
          'Backend did not activate the requested workspace. Refresh to inspect the session context.',
        );
      return r;
    }
    const registry = readWorkspaceRegistry();
    if (!registry.items.some((w) => w.id === id))
      throw new Error('Workspace no longer exists. Refresh the list.');
    activateFixtureWorkspace(id);
    return { ...registry, active_id: id };
  },
  async models() {
    if (dataMode === 'api') return request('/models', z.array(modelSchema));
    return (await import('./insights')).insights.learning().then((l) => l.models);
  },
  async model(name: string, version: string) {
    if (dataMode !== 'api') throw new Error('No trained model artifact is connected.');
    const r = await request(
      `/models/${encodeURIComponent(name)}/${encodeURIComponent(version)}`,
      modelDetailSchema,
    );
    if (r.name !== name || r.version !== version)
      throw new ApiError(409, 'Model detail belongs to another version. Refresh the registry.');
    return r;
  },
  async modelAction(d: ModelDetail, action: 'PROMOTE' | 'ROLLBACK', reason: string) {
    if (dataMode !== 'api') throw new Error('Model changes require a connected backend.');
    if (!d.allowed_actions.includes(action) || reason.trim().length < 10)
      throw new Error('Review the backend gates and provide a reason of at least ten characters.');
    const r = await request(
      `/models/${encodeURIComponent(d.name)}/${action.toLowerCase()}`,
      modelActionSchema,
      {
        version: d.version,
        registry_revision: d.registry_revision,
        artifact_hash: d.artifact_hash,
        reason: reason.trim(),
      },
    );
    const expectedVersion = action === 'PROMOTE' ? d.version : d.rollback_version;
    if (r.name !== d.name || r.version !== expectedVersion)
      throw new ApiError(
        502,
        'Model acknowledgement has the wrong family or version. Refresh the registry.',
      );
    return r;
  },
  async evaluation() {
    if (dataMode === 'api') return request('/eval/report', evaluationEnvelopeSchema);
    return {
      status: 'NOT_AVAILABLE' as const,
      report: null,
      note: 'No precomputed eval report is connected. Load a report file to inspect its schema, or connect the backend. No benchmark results are invented.',
    };
  },
  async archive(d: Decision) {
    if (dataMode === 'api') {
      const r = await request(
        `/decisions/${encodeURIComponent(d.decision_id)}/archive`,
        archiveSchema,
      );
      if (
        r.decision_id !== d.decision_id ||
        r.decision_hash !== d.decision_hash ||
        r.snapshot_id !== d.snapshot_id
      )
        throw new ApiError(409, 'Archive belongs to a different decision. Refresh this proposal.');
      return r;
    }
    return archiveSchema.parse({
      decision_id: d.decision_id,
      decision_hash: d.decision_hash,
      snapshot_id: d.snapshot_id,
      environment_fingerprint: null,
      code_sha: null,
      lock_hash: null,
      seed: null,
      status: 'UNAVAILABLE',
      note: 'Captured UI evidence is inspectable. The engine snapshot, model artifacts, lockfile and replay environment have not been supplied.',
      artifacts: [
        {
          id: 'ui-evidence',
          kind: 'EVIDENCE',
          label: 'Visible decision evidence',
          hash: null,
          href: `/decisions/${d.decision_id}`,
          status: 'PRESENT',
        },
        {
          id: 'environment',
          kind: 'ENVIRONMENT',
          label: 'Archived execution environment',
          hash: null,
          href: null,
          status: 'MISSING',
        },
      ],
      steps: [],
    });
  },
  async switchAllowed() {
    const executions = await api.executions();
    return !executions.some(
      (e) =>
        [
          'PENDING',
          'EXECUTING',
          'PARTIAL',
          'COMPENSATING',
          'COMPENSATION_FAILED',
          'HUMAN_RESOLUTION_REQUIRED',
        ].includes(e.state) ||
        e.legs.some(
          (l) =>
            ['UNKNOWN', 'CONFLICT'].includes(l.external_state) ||
            ['MIRROR_PENDING', 'MIRROR_FAILED'].includes(l.sim_sync_state),
        ),
    );
  },
};
