import { dataMode, request, ApiError } from './client';
import {
  confidenceBandsSchema,
  policyHistorySchema,
  sourceChecksSchema,
  sqlResultSchema,
  workspaceObjectiveSchema,
} from './completion-contracts';
import type { z } from 'zod';
import type { Objective } from './contracts';
export const completion = {
  sourceChecks: () =>
    dataMode === 'api' ? request('/data/health', sourceChecksSchema) : Promise.resolve([]),
  objective: () =>
    dataMode === 'api'
      ? request('/objective', workspaceObjectiveSchema)
      : Promise.resolve(
          workspaceObjectiveSchema.parse({
            workspace_id: 'fixture',
            objective: 'PROFIT',
            revision: 'fixture-objective-1',
            supported_objectives: ['PROFIT'],
            can_change: false,
            note: 'Fixed frontend illustration. Workspace configuration requires the backend objective service.',
          }),
        ),
  history: () =>
    dataMode === 'api'
      ? request('/policy/history', policyHistorySchema)
      : Promise.resolve(
          policyHistorySchema.parse({
            status: 'NOT_AVAILABLE',
            versions: [],
            note: 'No backend policy audit history is connected.',
          }),
        ),
  confidence: () =>
    dataMode === 'api'
      ? request('/learning/qualification', confidenceBandsSchema)
      : Promise.resolve(
          confidenceBandsSchema.parse({
            status: 'NOT_AVAILABLE',
            pools: [],
            note: 'Illustrative frontend outcomes do not provide calibrated confidence-region evidence.',
          }),
        ),
  async changeObjective(
    current: z.infer<typeof workspaceObjectiveSchema>,
    objective: Objective,
    reason: string,
  ) {
    if (
      dataMode !== 'api' ||
      !current.can_change ||
      !current.supported_objectives.includes(objective) ||
      reason.trim().length < 10
    )
      throw new Error('A supported objective, backend permission and review reason are required.');
    const next = await request(
      '/objective',
      workspaceObjectiveSchema,
      {
        workspace_id: current.workspace_id,
        revision: current.revision,
        objective,
        reason: reason.trim(),
      },
      'PUT',
    );
    if (
      next.workspace_id !== current.workspace_id ||
      next.objective !== objective ||
      next.revision === current.revision
    )
      throw new ApiError(
        409,
        'Objective acknowledgement does not match this workspace and revision. Refresh before retrying.',
      );
    return next;
  },
  async sql(query: string, signal?: AbortSignal) {
    if (dataMode !== 'api') throw new Error('SQL requires the backend read-only query service.');
    const bound = query.trim();
    if (bound.length < 3 || bound.length > 4000)
      throw new Error('Enter a query of 3–4000 characters.');
    const result = await request(
      '/copilot/sql',
      sqlResultSchema,
      { query: bound, limit: 500 },
      'POST',
      signal,
    );
    if (result.query !== bound)
      throw new ApiError(409, 'Results belong to another query. Run the current query again.');
    return result;
  },
};
