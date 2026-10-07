import { z } from 'zod';
import { api, dataMode, request, apiBase, ApiError } from './client';
import { decisionSchema, type Decision } from './contracts';
import { allocationErrors } from '../lib/allocation';
import { importFields, type ImportType } from '../lib/csv';
import {
  opportunitySchema,
  curveSchema,
  fatigueSchema,
  creativeScoreSchema,
  calibrationSchema,
  accuracySchema,
  upliftSchema,
  feedbackSchema,
  modelSchema,
  dataHealthSchema,
  reconciliationSchema,
  importAckSchema,
  comparisonSchema,
  timelineSchema,
  replaySchema,
  copilotReplySchema,
  type Comparison,
  type CopilotReply,
} from './insight-contracts';

const fixture = dataMode === 'fixture';
// Retain a staged ID across a failed confirmation so an explicit retry does not upload again.
// Session-only: refresh loses this state; canonical deduplication remains backend-owned.
const stagedImports = new Map<string, z.infer<typeof importAckSchema>>();
export const insights = {
  async opportunities() {
    if (!fixture) return request('/opportunities', z.array(opportunitySchema));
    const c = await api.optimizerContext();
    const eligible =
      (await api.decisions()).find((d) => d.decision_id === c.decision_id)?.class ===
      'OPTIMIZATION';
    if (!eligible) return [];
    return c.campaigns
      .map((l, i) =>
        opportunitySchema.parse({
          id: `opportunity-${l.budget_id}`,
          budget_id: l.budget_id,
          entity: l.entity,
          platform: l.platform,
          score: l.inventory_gate === 'BLOCK' ? null : [null, 0.82, 0.67, 0.21][i],
          status: l.inventory_gate === 'BLOCK' ? 'BLOCKED' : 'FEASIBLE',
          reason: l.note,
          marginal_caa: l.marginal_caa,
          delta_budget:
            l.inventory_gate === 'BLOCK' ? 0 : Math.min(1000, Math.max(0, l.max_budget - l.before)),
          decision_id: c.decision_id,
          evidence: [
            { label: 'Allocation & policy', href: '/optimizer' },
            { label: 'Inventory evidence', href: `/decisions/${c.decision_id}` },
          ],
          provenance: ['SIMULATED'],
        }),
      )
      .sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  },
  async curve(id: string) {
    if (!fixture) return request(`/curves/${encodeURIComponent(id)}`, curveSchema);
    const c = await api.optimizerContext();
    const l = c.campaigns.find((l) => l.budget_id === id);
    if (!l) throw new Error('Budget unit no longer exists. Refresh the opportunities.');
    return curveSchema.parse({
      budget_id: id,
      label: 'Recorded illustrative response curve',
      unit: 'Contribution / day (INR)',
      points: [
        { budget: l.min_budget, contribution: 8000 },
        { budget: l.before, contribution: 11000 },
        { budget: l.max_budget, contribution: 12300 },
      ],
      reason:
        'Illustrative curve shape only. These points are not fitted model output and are not used to value revisions.',
    });
  },
  async fatigue() {
    if (!fixture) return request('/creatives/fatigue', z.array(fatigueSchema));
    const o = await api.overview();
    if (o.scenario !== 'DEMO_01') return [];
    return [
      {
        creative_id: 'creative-hero',
        name: 'Hero launch creative',
        entity: 'Hero · Prospecting',
        ctr_change: -0.4,
        frequency: 3.8,
        status: 'REVIEW' as const,
        reason: 'Recorded DEMO_01 fixture: CTR decline concentrated in the hero creative.',
      },
      {
        creative_id: 'creative-bundle',
        name: 'Bundle benefits creative',
        entity: 'Bundle · Shopping',
        ctr_change: -0.02,
        frequency: 1.9,
        status: 'STABLE' as const,
        reason: 'Recorded DEMO_01 fixture: no material click-through change.',
      },
    ];
  },
  async scoreCreative(text: string) {
    if (text.trim().length < 10)
      throw new Error('Provide at least ten characters of creative context.');
    if (!fixture) return request('/creatives/score', creativeScoreSchema, { text: text.trim() });
    return {
      status: 'NOT_ESTIMABLE' as const,
      score: null,
      explanation:
        'Creative context accepted for preview. No creative prediction model is connected; no score or performance claim has been invented.',
    };
  },
  async learning() {
    if (!fixture) {
      const [calibration, accuracy, uplift, feedback, models] = await Promise.all([
        request('/learning/calibration', calibrationSchema),
        request('/learning/accuracy', accuracySchema),
        request('/learning/uplift', upliftSchema),
        request('/learning/feedback', feedbackSchema),
        request('/models', z.array(modelSchema)),
      ]);
      return { calibration, accuracy, uplift, feedback, models };
    }
    const [overview, outcomes] = await Promise.all([api.overview(), api.outcomes()]);
    const measured = outcomes.filter(
      (o) => o.class === 'OPTIMIZATION' && o.verdict !== 'INCONCLUSIVE',
    );
    return {
      calibration: {
        factor: overview.calibration,
        updates: outcomes
          .filter((o) => o.calibration_applied)
          .map((o) => ({
            outcome_id: o.outcome_id,
            decision_id: o.decision_id,
            before: o.factor_before,
            after: o.factor_after,
            at: o.matured_at,
          })),
        note: 'Recorded UI feedback transitions only. Safety and inconclusive outcomes do not calibrate response curves.',
      },
      accuracy: {
        sample_count: measured.length,
        mae: measured.length
          ? measured.reduce((n, o) => n + Math.abs(o.measured - o.predicted), 0) / measured.length
          : null,
        note: 'Descriptive absolute error on available optimization fixture outcomes; not held-out model evaluation.',
      },
      uplift: {
        status: 'NOT_AVAILABLE' as const,
        rows: [],
        note: 'No held-out benchmark report has been supplied. Baseline or oracle uplift cannot be inferred from this UI demo.',
      },
      feedback: outcomes.map((o) => ({
        outcome_id: o.outcome_id,
        decision_id: o.decision_id,
        eligible: o.calibration_applied,
        reason: o.calibration_applied
          ? 'Fixture calibration applied once.'
          : 'Excluded from response-curve calibration.',
      })),
      models: [
        {
          name: 'Response curves',
          version: 'Unavailable',
          status: 'NOT_AVAILABLE' as const,
          trained_at: null,
          note: 'Model registry not connected.',
        },
        {
          name: 'Demand forecast',
          version: 'Unavailable',
          status: 'NOT_AVAILABLE' as const,
          trained_at: null,
          note: 'No trained artifact or promotion evidence exposed to the UI.',
        },
      ],
    };
  },
  async dataHealth() {
    if (!fixture) {
      const [sources, coverage] = await Promise.all([
        request('/data/sources', z.array(dataHealthSchema.shape.sources.element)),
        request(
          '/data/mapping-coverage',
          dataHealthSchema.pick({ coverage: true, unmapped: true, note: true }),
        ),
      ]);
      return { ...coverage, sources };
    }
    const o = await api.overview();
    return {
      sources: o.sources,
      coverage: 1,
      unmapped: [],
      note: 'Illustrative mapped-source configuration. Imported previews do not update this fixture workspace or its engine.',
    };
  },
  async reconciliation() {
    if (!fixture) return request('/data/reconciliation', reconciliationSchema);
    return reconciliationSchema.parse({
      platform_revenue: 1390000,
      store_revenue: 1248000,
      attribution_excess: 142000,
      note: 'Recorded frontend illustration. Platform attribution is reconciled against store net revenue; attribution excess is not extra sales.',
    });
  },
  async importData(
    type: ImportType,
    records: Record<string, string | number>[],
    mapping: Record<string, string>,
  ) {
    if (!records.length || importFields[type].some((k) => !mapping[k]))
      throw new Error('Validate and map the file before staging it.');
    if (!fixture) {
      const key = JSON.stringify({ type, records, mapping });
      const staged =
        stagedImports.get(key) ||
        (await request('/ingest/upload', importAckSchema, {
          type,
          records,
          source_currency: 'INR',
          source_timezone: 'Asia/Kolkata',
        }));
      if (staged.row_count !== records.length)
        throw new ApiError(
          502,
          'Backend staging row count does not match the submitted file. Inspect import status before proceeding.',
        );
      stagedImports.set(key, staged);
      if (stagedImports.size > 10) stagedImports.delete(stagedImports.keys().next().value!);
      if (staged.status === 'IMPORTED') return staged;
      const confirmed = await request('/ingest/mapping/confirm', importAckSchema, {
        import_id: staged.import_id,
        mapping,
      });
      if (confirmed.import_id !== staged.import_id || confirmed.row_count !== records.length)
        throw new ApiError(
          502,
          'Import confirmation does not match the staged file. Inspect backend import status before submitting again.',
        );
      stagedImports.set(key, confirmed);
      return confirmed;
    }
    return {
      import_id: `preview-${crypto.randomUUID()}`,
      status: 'STAGED' as const,
      row_count: records.length,
      message:
        'Validated frontend preview staged locally. No canonical data or engine state was changed.',
    };
  },
  async comparison(d: Decision): Promise<Comparison> {
    if (!fixture) {
      const result = await request(
        `/decisions/${encodeURIComponent(d.decision_id)}/simulate`,
        comparisonSchema,
        { decision_hash: d.decision_hash },
      );
      if (result.decision_id !== d.decision_id || result.decision_hash !== d.decision_hash)
        throw new ApiError(
          409,
          'Comparison belongs to a different proposal. Refresh this decision.',
        );
      return result;
    }
    if (
      d.class !== 'OPTIMIZATION' ||
      d.valuation_status === 'NOT_ESTIMABLE' ||
      (await api.overview()).scenario !== 'DEMO_01'
    )
      return {
        decision_id: d.decision_id,
        decision_hash: d.decision_hash,
        status: 'NOT_ESTIMABLE',
        strategies: [],
        alternatives: [],
        confidence: [],
        note: 'No comparison valuation has been supplied for this proposal. Comparison and sensitivity forecasts are withheld.',
      };
    const c = await api.optimizerContext();
    const variants = [
      {
        id: 'conservative',
        name: 'Conservative',
        values: [36000, 26400, 20800, 12800],
        p50: 5100,
        loss: 0.09,
      },
      {
        id: 'aggressive',
        name: 'Aggressive',
        values: [32000, 28800, 24000, 12800],
        p50: 7700,
        loss: 0.18,
      },
    ];
    const valid = d.legs.length === 4 && d.decision_id === c.decision_id;
    return comparisonSchema.parse({
      decision_id: d.decision_id,
      decision_hash: d.decision_hash,
      status: 'AVAILABLE',
      strategies: [
        {
          name: 'Static budgets',
          allocated: d.legs.reduce((n, l) => n + l.before, 0),
          estimate: {
            ...d.expected,
            raw_pred: 0,
            calibrated_pred: 0,
            p10: 0,
            p50: 0,
            p90: 0,
            prob_loss: 0,
            delta_net_revenue: 0,
          },
          reason:
            'Hold current settings: zero incremental contribution relative to the same baseline.',
        },
        {
          name: 'ROAS rank',
          allocated: 96000,
          estimate: { ...d.expected, p10: -4800, p50: -1200, p90: 3600, prob_loss: 0.64 },
          reason: 'Illustrative ROAS preference overlooks margin and marginal contribution.',
        },
        {
          name: 'Contribution rank',
          allocated: 96000,
          estimate: { ...d.expected, p10: -600, p50: 4100, p90: 8200, prob_loss: 0.22 },
          reason:
            'Illustrative trailing contribution ordering, without response-curve optimization.',
        },
        {
          name: 'ADAPT',
          allocated: d.budget_ceiling - d.unallocated,
          estimate: d.expected,
          reason: 'Recorded recommendation from this frontend example.',
        },
      ],
      alternatives: valid
        ? variants.map((v) => ({
            id: v.id,
            name: v.name,
            reason:
              'Recorded sensitivity illustration under the same input envelope; creating a revision still requires backend revaluation.',
            legs: d.legs.map((l, i) => ({ ...l, after: v.values[i] })),
            estimate: {
              ...d.expected,
              p10: v.id === 'aggressive' ? -1200 : 1200,
              p50: v.p50,
              p90: 11800,
              prob_loss: v.loss,
            },
            checks: [
              {
                id: 'INPUT_ENVELOPE',
                label: 'Recorded input envelope',
                passed:
                  allocationErrors(c, {
                    decision_id: d.decision_id,
                    decision_hash: d.decision_hash,
                    policy_version: d.policy_version,
                    objective: 'PROFIT',
                    legs: d.legs.map((l, i) => ({ budget_id: l.budget_id, after: v.values[i] })),
                  }).length === 0,
                detail: 'Backend revaluation and current policy checks remain necessary.',
              },
            ],
          }))
        : [],
      confidence: [
        {
          label: 'Driver evidence strength',
          value: 0.87,
          meaning: 'Diagnostic strength index, not probability of causation.',
        },
        {
          label: 'Forecast coverage illustration',
          value: 0.8,
          meaning: 'Recorded illustration, not measured model coverage.',
        },
      ],
      note: 'Recorded frontend model-estimate examples, not evaluated strategy performance. Compare held-out realized results only in Learning after an evaluation report exists.',
    });
  },
  async timeline(id: string) {
    if (!fixture) return request(`/decisions/${encodeURIComponent(id)}/timeline`, timelineSchema);
    const [d, events] = await Promise.all([api.decision(id), api.events()]);
    return [
      {
        id: `snapshot-${id}`,
        at: d.created_at,
        label: 'Decision snapshot',
        detail: `${d.snapshot_id} · ${d.policy_version} · ${d.decision_hash}`,
        href: `/decisions/${id}`,
      },
      ...events
        .filter((e) => e.decision_id === id)
        .reverse()
        .map((e) => ({
          id: e.id,
          at: e.at,
          label: e.kind,
          detail: e.message,
          href: ['OUTCOME'].includes(e.kind)
            ? '/outcomes'
            : e.kind === 'REVISED'
              ? `/decisions/${id}`
              : '/executions',
        })),
    ];
  },
  async replay(d: Decision) {
    if (!fixture) {
      const r = await request(
        `/decisions/${encodeURIComponent(d.decision_id)}/replay`,
        replaySchema,
      );
      if (r.expected_hash !== d.decision_hash)
        throw new ApiError(
          409,
          'Replay result belongs to a different decision hash. Refresh this proposal.',
        );
      return r;
    }
    return {
      status: 'UNAVAILABLE' as const,
      expected_hash: d.decision_hash,
      actual_hash: null,
      message:
        'Fixture events are inspectable, but no archived engine environment or replay artifacts are connected. No hash verification was performed.',
    };
  },
  async snapshot(id: string) {
    if (!fixture)
      return request(
        `/decisions/${encodeURIComponent(id)}/snapshot`,
        z.object({ decision: decisionSchema, evidence: z.unknown(), notice: z.string() }),
      );
    return {
      decision: await api.decision(id),
      evidence: await api.evidence(id),
      notice:
        'Frontend fixture snapshot only; excludes model artifacts, hidden truth and an archived replay environment.',
    };
  },
};

export async function askCopilot(message: string, signal: AbortSignal): Promise<CopilotReply> {
  if (message.trim().length < 3 || message.length > 2000)
    throw new Error('Ask a question between 3 and 2,000 characters.');
  if (fixture) {
    const [overview, decisions, outcomes] = await Promise.all([
      api.overview(),
      api.decisions(),
      api.outcomes(),
    ]);
    const d = decisions[0];
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    let text =
      'This template assistant supports questions about budgets, inventory, risks and feedback. The backend LLM is not connected.';
    let evidence = [{ label: 'Workspace sources', href: '/data' }];
    if (/feedback|outcome|learn|accuracy/i.test(message)) {
      text = outcomes.length
        ? `${outcomes.length} fixture outcome(s) have matured. The current calibration factor is ${overview.calibration.toFixed(2)}. Review the outcome method and eligibility before interpreting it.`
        : 'No outcomes have matured. Approve and verify an eligible fixture decision, then advance its horizon to see feedback.';
      evidence = [
        { label: 'Outcome records', href: '/outcomes' },
        { label: 'Calibration history', href: '/learning' },
      ];
    } else if (d && /stock|inventory|risk|why|budget|allocat|push|campaign/i.test(message)) {
      text =
        d.valuation_status === 'NOT_ESTIMABLE'
          ? `${d.title}. This draft awaits backend valuation and cannot execute.`
          : `${d.summary} Unallocated daily budget: INR ${d.unallocated.toLocaleString('en-IN')}. Status: ${d.status}. Check the policy and inventory evidence before approving.`;
      evidence = [
        { label: `Decision ${d.decision_id}`, href: `/decisions/${d.decision_id}` },
        { label: 'Source health', href: '/data' },
      ];
    }
    return copilotReplySchema.parse({ text, evidence, mode: 'TEMPLATE' });
  }
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), 30000);
  try {
    const response = await fetch(`${apiBase}/copilot/chat`, {
      method: 'POST',
      credentials: 'include',
      signal: AbortSignal.any([signal, timeout.signal]),
      headers: {
        Accept: 'text/event-stream',
        'Content-Type': 'application/json',
        'X-Request-ID': crypto.randomUUID(),
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ message }),
    });
    if (!response.ok)
      throw new ApiError(
        response.status,
        `Copilot returned ${response.status}. Check backend availability and permissions.`,
      );
    if (!response.headers.get('content-type')?.includes('text/event-stream') || !response.body)
      throw new ApiError(502, 'Copilot must return the agreed event stream contract.');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let reply: CopilotReply | null = null;
    let bytes = 0;
    let doneEvent = false;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        bytes += value.byteLength;
        if (bytes > 128000) throw new ApiError(502, 'Copilot response exceeded the display limit.');
        buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, '\n');
        let split;
        while ((split = buffer.indexOf('\n\n')) >= 0) {
          const event = buffer.slice(0, split);
          buffer = buffer.slice(split + 2);
          const raw = event
            .split('\n')
            .filter((l) => l.startsWith('data:'))
            .map((l) => l.slice(5).trimStart())
            .join('\n');
          if (!raw) continue;
          let parsed: unknown;
          try {
            parsed = JSON.parse(raw);
          } catch {
            throw new ApiError(502, 'Malformed Copilot event.');
          }
          const result = z
            .object({
              type: z.enum(['answer', 'error', 'done']),
              reply: copilotReplySchema.optional(),
              message: z.string().optional(),
            })
            .safeParse(parsed);
          if (!result.success)
            throw new ApiError(
              502,
              'Copilot response contract mismatch: answer or evidence links are invalid. Ask again after the backend contract is corrected.',
            );
          const envelope = result.data;
          if (envelope.type === 'error')
            throw new ApiError(
              502,
              envelope.message || 'Copilot failed to generate a grounded answer.',
            );
          if (envelope.type === 'answer') {
            if (!envelope.reply)
              throw new ApiError(502, 'Copilot answer has no validated payload.');
            reply = envelope.reply;
          }
          if (envelope.type === 'done') {
            doneEvent = true;
            break;
          }
        }
        if (doneEvent) break;
      }
    } finally {
      await reader.cancel().catch(() => undefined);
    }
    if (!reply || !doneEvent)
      throw new ApiError(502, 'Copilot stream ended before a complete answer was confirmed.');
    return reply;
  } finally {
    clearTimeout(timer);
  }
}
