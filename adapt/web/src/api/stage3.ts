import { z } from 'zod';
import { dataMode, request } from './client';
import { creativeScoreSchema } from './insight-contracts';

export const creativeFields = ['format', 'hook', 'cta', 'category', 'channel'] as const;
export type CreativeField = (typeof creativeFields)[number];

const attributesSchema = z.object({
  status: z.enum(['AVAILABLE', 'NOT_ESTIMABLE']),
  domains: z.record(z.string(), z.array(z.string().min(1)).max(200)),
  note: z.string(),
});
const uploadType = z.enum(['ads', 'inventory', 'margins']);
const importSchema = z.object({
  import_id: z.string().regex(/^imp-[a-f0-9]{16}$/),
  type: uploadType,
  status: z.enum(['STAGED', 'IMPORTED']),
  row_count: z.number().int().min(0).max(5000),
  staged_at: z.string(),
  workspace_id: z.string().nullable(),
});
const detailSchema = z.object({
  import_id: z.string(),
  type: uploadType,
  workspace_id: z.string(),
  table: z.string(),
  columns: z.array(z.string()).max(40),
  records: z
    .array(z.record(z.string(), z.union([z.string(), z.number().finite(), z.boolean(), z.null()])))
    .max(100),
  truncated: z.boolean(),
  row_count: z.number().int().min(0),
  engine_eligible: z.literal(false),
  missing_sources: z.array(z.string()),
  note: z.string(),
});
export type UploadImport = z.infer<typeof importSchema>;

export const stage3 = {
  attributes: () =>
    dataMode === 'api'
      ? request('/creatives/attributes', attributesSchema)
      : Promise.resolve({
          status: 'NOT_ESTIMABLE' as const,
          domains: {} as Record<string, string[]>,
          note: 'No trained creative prior in frontend fixtures. Connect the backend to assess structured attributes.',
        }),
  scoreAttributes: (attributes: Partial<Record<CreativeField, string>>) =>
    request('/creatives/score', creativeScoreSchema, { attributes }),
  imports: () =>
    dataMode === 'api' ? request('/ingest/imports', z.array(importSchema)) : Promise.resolve([]),
  importDetail: (id: string) => request(`/ingest/imports/${encodeURIComponent(id)}`, detailSchema),
};
