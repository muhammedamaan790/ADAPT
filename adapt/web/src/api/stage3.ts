import { z } from 'zod';
import { dataMode, request } from './client';

export const creativeFields = ['format', 'hook', 'cta', 'category', 'channel', 'price_band'] as const;
export type CreativeAttributes = Record<(typeof creativeFields)[number], string>;
const attributesSchema = z.object({
  status: z.enum(['AVAILABLE', 'NOT_ESTIMABLE']),
  domains: z.record(z.string(), z.array(z.string().min(1)).max(100)),
  note: z.string(),
});
const importSchema = z.object({
  import_id: z.string().regex(/^csv-[a-f0-9]{24}$/),
  type: z.enum(['ads', 'inventory', 'margins', 'cvr']),
  status: z.enum(['STAGED', 'IMPORTED']),
  row_count: z.number().int().min(1).max(5000),
  created_at: z.string().datetime(),
});
const cvrRow = z.object({
  sku: z.string(), channel: z.string(), category: z.string(), clicks: z.number().nonnegative(),
  purchases: z.number().nonnegative(), mean: z.number().min(0).max(1),
  p10: z.number().min(0).max(1), p90: z.number().min(0).max(1), prior_source: z.string(),
});
const detailSchema = z.object({
  import_id: z.string(), type: z.string(), table: z.string(), columns: z.array(z.string()).max(40),
  records: z.array(z.record(z.string(), z.union([z.string(), z.number().finite(), z.boolean(), z.null()]))).max(100),
  truncated: z.boolean(), engine_eligible: z.literal(false), missing_sources: z.array(z.string()), note: z.string(),
  cvr: z.object({status: z.enum(['AVAILABLE', 'NOT_ESTIMABLE']), rows: z.array(cvrRow), note: z.string()}).optional(),
});
export const stage3 = {
  attributes: () => dataMode === 'api' ? request('/creatives/attributes', attributesSchema) : Promise.resolve({
    status: 'NOT_ESTIMABLE' as const, domains: {} as Record<string, string[]>,
    note: 'No trained creative prior in frontend fixtures. Connect the backend to assess structured attributes.',
  }),
  imports: () => dataMode === 'api' ? request('/ingest/imports', z.array(importSchema)) : Promise.resolve([]),
  importDetail: (id: string) => request(`/ingest/imports/${encodeURIComponent(id)}`, detailSchema),
};
