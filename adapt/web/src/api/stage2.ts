import { z } from 'zod';
import { dataMode, request } from './client';
import { decisionSchema, narrativeSchema, platformsSchema, type Decision } from './contracts';

// Stage 2 endpoints: guarded narratives, platform execution health, and choosing a sensitivity scenario.
// Fixture mode has no engine behind it, so narratives and platform health are reported as unavailable.
export const stage2 = {
  narrative: (kind: 'decision' | 'incident', id: string) =>
    dataMode === 'api'
      ? request(
          `/${kind === 'decision' ? 'decisions' : 'anomalies'}/${encodeURIComponent(id)}/narrative`,
          narrativeSchema,
        )
      : Promise.resolve(null),
  platforms: () =>
    dataMode === 'api' ? request('/platforms/health', platformsSchema) : Promise.resolve(null),
  /** The backend's synonym + fuzzy field mapper: the best CSV column per required field, with its score. */
  async suggestMapping(type: string, headers: string[]) {
    if (dataMode !== 'api') return null;
    return request(
      '/ingest/mapping/suggest',
      z.object({
        type: z.string(),
        mapping: z.record(
          z.string(),
          z.object({ header: z.string().nullable(), score: z.number(), method: z.string() }),
        ),
      }),
      { type, headers },
    );
  },
  async chooseAlternative(d: Decision, alternativeId: string) {
    if (dataMode !== 'api')
      throw new Error('Choosing a risk preference requires a connected backend.');
    return request(
      `/decisions/${encodeURIComponent(d.decision_id)}/alternatives/${encodeURIComponent(alternativeId)}/choose`,
      decisionSchema,
      { decision_hash: d.decision_hash },
    );
  },
};
