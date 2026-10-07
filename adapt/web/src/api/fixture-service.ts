import {
  decisionSchema,
  executionSchema,
  outcomeSchema,
  eventSchema,
  scenarioKeys,
  type AppEvent,
  type Decision,
  type Execution,
  type Outcome,
  type ScenarioKey,
} from './contracts';
import { fixtureDecision, fixtureEvidence, fixtureOverview, FIXTURE_TS } from './fixtures';
import {
  anomalySchema,
  ledgerSchema,
  type AllocationInput,
  type AllocationEvaluation,
  type Anomaly,
  type LedgerEntry,
  type RecoveryInput,
} from './workbench-contracts';
import { anomalyFixtures, optimizerFixture } from './workbench-fixtures';
import { allocationErrors } from '../lib/allocation';

const KEY = 'adapt.frontend.fixtures.v1';
let activeWorkspace = 'default';
// Each tab keeps its own loaded context; another tab's selection must not relabel its data.
export const getActiveFixtureWorkspace = () => activeWorkspace;
const fixtureKey = () => (activeWorkspace === 'default' ? KEY : `${KEY}.${activeWorkspace}`);
type State = {
  version: 1;
  scenario: ScenarioKey;
  day: number;
  decisions: Decision[];
  executions: Execution[];
  outcomes: Outcome[];
  events: AppEvent[];
  calibration: number;
  seed: number;
  anomaly_updates: Record<string, { status: Anomaly['status']; reason: string }>;
  ledger: LedgerEntry[];
  execution_days: Record<string, number>;
};
const initial = (): State => ({
  version: 1,
  scenario: 'DEMO_01',
  day: 0,
  decisions: [fixtureDecision('DEMO_01')],
  executions: [],
  outcomes: [],
  events: [
    {
      id: 'event-initial',
      at: FIXTURE_TS,
      kind: 'READY',
      message: 'Frontend fixture workspace loaded. No engine or ad API is connected.',
      decision_id: null,
    },
  ],
  calibration: 0.9,
  seed: 42,
  anomaly_updates: {},
  ledger: [],
  execution_days: {},
});
let state: State = initial();
export function loadFixtureState() {
  state = initial();
  activeWorkspace = 'default';
  try {
    const selected = localStorage.getItem('adapt.active-workspace') || 'default';
    if (selected !== 'default') {
      const registry = JSON.parse(localStorage.getItem('adapt.workspace-registry') || 'null');
      if (
        /^[a-zA-Z0-9_-]{1,80}$/.test(selected) &&
        Array.isArray(registry?.items) &&
        registry.items.some((w: { id: string }) => w.id === selected)
      )
        activeWorkspace = selected;
    }
    const value = JSON.parse(localStorage.getItem(fixtureKey()) || 'null');
    if (
      value?.version === 1 &&
      scenarioKeys.includes(value.scenario) &&
      Array.isArray(value.decisions) &&
      Array.isArray(value.events) &&
      Array.isArray(value.executions) &&
      Array.isArray(value.outcomes) &&
      Number.isFinite(value.day) &&
      Number.isFinite(value.calibration)
    ) {
      value.decisions.forEach((d: unknown) => decisionSchema.parse(d));
      value.executions.forEach((e: unknown) => executionSchema.parse(e));
      value.outcomes.forEach((o: unknown) => outcomeSchema.parse(o));
      value.events.forEach((e: unknown) => eventSchema.parse(e));
      (value.ledger || []).forEach((e: unknown) => ledgerSchema.parse(e));
      const normalized = {
        ...value,
        ledger: value.ledger || [],
        anomaly_updates: value.anomaly_updates || {},
        execution_days: value.execution_days || {},
      };
      anomalyFixtures(normalized.scenario, normalized.decisions).forEach((a) =>
        anomalySchema.parse({
          ...a,
          status: normalized.anomaly_updates[a.anomaly_id]?.status || a.status,
        }),
      );
      state = normalized;
    }
  } catch {
    state = initial();
  }
}
const save = () => {
  try {
    localStorage.setItem(fixtureKey(), JSON.stringify(state));
  } catch {
    /* Fixture session still works without storage. */
  }
};
const log = (kind: string, message: string, decision_id: string | null = null) => {
  state.events.unshift({
    id: crypto.randomUUID(),
    at: new Date().toISOString(),
    kind,
    message,
    decision_id,
  });
  state.events = state.events.slice(0, 100);
};
const clone = <T>(value: T): T => structuredClone(value);
const find = (id: string) => {
  const d = state.decisions.find((d) => d.decision_id === id);
  if (!d) throw new Error('Decision not found. Refresh the workspace.');
  return d;
};
const settle = () => {
  state.executions.forEach((e) => {
    if (
      e.state === 'EXECUTING' &&
      e.legs.every((l) => l.external_state === 'SENT') &&
      Date.now() - new Date(e.started_at).getTime() >= 1200
    ) {
      e.state = 'SUCCEEDED';
      e.legs.forEach((l) => {
        l.external_state = 'VERIFIED';
        l.read_back_budget = l.after;
        appendLedger(
          e,
          l.budget_id,
          'VERIFY',
          l.after,
          l.after,
          'Illustrative read-back verified.',
        );
      });
      find(e.decision_id).status = 'EXECUTED';
      log(
        'VERIFIED',
        'Fixture execution completed. Read-back states are UI examples, not actual platform reads.',
        e.decision_id,
      );
      save();
    }
  });
};
const activeDecision = () =>
  state.decisions.find((d) => !['SUPERSEDED', 'REJECTED', 'EXPIRED'].includes(d.status));
const findExecution = (id: string) => {
  const e = state.executions.find((e) => e.execution_id === id);
  if (!e) throw new Error('Execution not found. Refresh its state.');
  return e;
};
function appendLedger(
  e: Execution,
  budgetId: string,
  action: LedgerEntry['action'],
  before: number,
  after: number,
  note: string,
) {
  const leg = e.legs.find((l) => l.budget_id === budgetId)!;
  state.ledger.unshift({
    ledger_id: crypto.randomUUID(),
    execution_id: e.execution_id,
    decision_id: e.decision_id,
    at: new Date().toISOString(),
    action,
    budget_id: budgetId,
    entity: leg.entity,
    platform: leg.platform,
    before,
    after,
    mode: 'MOCK',
    request_id: crypto.randomUUID(),
    note: `Frontend fixture only. ${note}`,
  });
}
const currentAnomalies = () =>
  anomalyFixtures(state.scenario, state.decisions).map((a) => ({
    ...a,
    status: state.anomaly_updates[a.anomaly_id]?.status || a.status,
    resolution_reason: state.anomaly_updates[a.anomaly_id]?.reason || null,
  }));
const hasUnresolved = () =>
  state.executions.some(
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
export const fixtureService = {
  async overview() {
    settle();
    const overview = fixtureOverview(state.scenario, state.day);
    if (activeWorkspace === 'default') overview.workspace = 'D2C workspace';
    try {
      const registry = JSON.parse(localStorage.getItem('adapt.workspace-registry') || 'null');
      const workspace = registry?.items?.find(
        (w: { id: string; name: string }) => w.id === activeWorkspace,
      );
      if (
        typeof workspace?.name === 'string' &&
        workspace.name.trim().length >= 2 &&
        workspace.name.length <= 60
      )
        overview.workspace = workspace.name;
    } catch {
      /* Default label remains when no registry is stored. */
    }
    overview.calibration = state.calibration;
    const completed = state.decisions.some((d) => d.status === 'EXECUTED');
    const matured = state.outcomes.length > 0;
    overview.loop.forEach((l, i) => {
      if (completed && i <= 5) l.state = 'complete';
      if (completed && i === 6) l.state = matured ? 'complete' : 'current';
    });
    overview.attention = overview.attention.filter(
      (a) =>
        !a.decision_id ||
        state.decisions.some(
          (d) =>
            d.decision_id === a.decision_id && ['PENDING_APPROVAL', 'BLOCKED'].includes(d.status),
        ),
    );
    state.outcomes.forEach((o) => {
      overview.counts[o.verdict.toLowerCase() as keyof typeof overview.counts]++;
      overview.attention.push({
        id: o.outcome_id,
        decision_id: o.decision_id,
        kind: 'outcome',
        title: 'Outcome measured · forecast updated',
        description: 'Illustrative feedback applied once to the next forecast',
        impact: o.measured,
        label: 'Example measured ΔCAA',
      });
    });
    overview.attention.sort((a, b) => Math.abs(b.impact) - Math.abs(a.impact));
    return clone(overview);
  },
  async decisions() {
    settle();
    return clone(state.decisions);
  },
  async decision(id: string) {
    settle();
    return clone(find(id));
  },
  async evidence(id: string) {
    find(id);
    return clone({ ...fixtureEvidence(state.scenario), decision_id: id });
  },
  async executions() {
    settle();
    return clone(state.executions);
  },
  async outcomes() {
    return clone(state.outcomes);
  },
  async events() {
    settle();
    return clone(state.events);
  },
  async approve(id: string, hash: string) {
    const d = find(id);
    if (d.decision_hash !== hash)
      throw new Error('409: Decision changed. Review the latest proposal before approving.');
    if (d.status === 'EXECUTING' || d.status === 'EXECUTED') return clone(d);
    if (d.status !== 'PENDING_APPROVAL' || d.checks.some((c) => !c.passed))
      throw new Error('409: This decision is not eligible for approval.');
    d.status = 'EXECUTING';
    const execution: Execution = {
      execution_id: crypto.randomUUID(),
      decision_id: id,
      state: 'EXECUTING',
      legs: d.legs
        .filter((l) => l.before !== l.after)
        .map((l) => ({
          ...l,
          mode: 'MOCK',
          external_state: 'SENT',
          sim_sync_state: 'NOT_REQUIRED',
          read_back_budget: null,
        })),
      started_at: new Date().toISOString(),
      detail: 'Illustrative frontend saga. No platform request is sent.',
    };
    state.executions.push(execution);
    state.execution_days[id] = state.day;
    execution.legs.forEach((l) =>
      appendLedger(
        execution,
        l.budget_id,
        'SET_BUDGET',
        l.before,
        l.after,
        'Budget request sent in the UI example; not actual platform spend.',
      ),
    );
    log('APPROVED', 'Human approval bound to the displayed fixture hash.', id);
    log('EXECUTING', 'Fixture budget legs sent to the UI execution example.', id);
    save();
    return clone(d);
  },
  async reject(id: string, hash: string, reason: string) {
    const d = find(id);
    if (hash !== d.decision_hash || d.status !== 'PENDING_APPROVAL')
      throw new Error('409: Decision is no longer pending.');
    if (reason.trim().length < 5) throw new Error('Give a reason with at least five characters.');
    d.status = 'REJECTED';
    log('REJECTED', reason.trim(), id);
    save();
    return clone(d);
  },
  async scenario(key: ScenarioKey) {
    if (hasUnresolved()) throw new Error('Finish the current execution before changing scenarios.');
    state = {
      ...initial(),
      scenario: key,
      seed: state.seed,
      decisions: key === 'S7' ? [] : [fixtureDecision(key)],
      events: [],
    };
    log('SCENARIO', `${key} frontend example loaded. Values are not simulator output.`);
    save();
  },
  async reset(seed: number) {
    if (hasUnresolved())
      throw new Error('Resolve execution uncertainty before resetting this workspace.');
    state = { ...initial(), seed };
    log(
      'RESET',
      `Frontend example reset; seed ${seed} reserved for backend integration (does not generate fixture values).`,
    );
    save();
  },
  async advance(days: number) {
    settle();
    if (hasUnresolved()) throw new Error('Execution is pending or the simulation is out of sync.');
    state.day += days;
    log(
      'ADVANCE',
      `Fixture clock advanced ${days} day${days === 1 ? '' : 's'} to day ${state.day}.`,
    );
    state.decisions
      .filter(
        (d) =>
          d.status === 'EXECUTED' &&
          !state.outcomes.some((o) => o.decision_id === d.decision_id) &&
          state.day - (state.execution_days[d.decision_id] || 0) >= d.horizon_days,
      )
      .forEach((d) => {
        const optimization = d.class === 'OPTIMIZATION';
        const before = state.calibration;
        const measured = optimization ? 5600 : 1800;
        const after = optimization
          ? Math.max(
              0.25,
              Math.min(1.5, 0.8 * before + (0.2 * measured) / Math.max(d.expected.raw_pred, 1000)),
            )
          : before;
        state.calibration = after;
        state.outcomes.push({
          outcome_id: crypto.randomUUID(),
          decision_id: d.decision_id,
          world: 'SIMULATED',
          class: d.class,
          verdict: 'SUCCESS',
          predicted: d.expected.calibrated_pred,
          measured,
          counterfactual: 42000,
          factor_before: before,
          factor_after: after,
          matured_at: new Date().toISOString(),
          method: optimization
            ? 'Illustrative forecast-counterfactual outcome. Not observed simulator evidence or causal proof.'
            : 'Illustrative avoided-loss safety outcome. Does not calibrate response curves.',
          calibration_applied: optimization,
        });
        log(
          'OUTCOME',
          `${d.class} example matured; feedback ${optimization ? 'applied exactly once' : 'excluded from curve calibration'}.`,
          d.decision_id,
        );
      });
    save();
  },
  async anomalies() {
    return clone(currentAnomalies());
  },
  async anomaly(id: string) {
    const a = currentAnomalies().find((a) => a.anomaly_id === id);
    if (!a) throw new Error('Investigation no longer exists in this scenario.');
    return clone(a);
  },
  async anomalyStatus(id: string, status: Anomaly['status'], reason: string) {
    if (!currentAnomalies().some((a) => a.anomaly_id === id))
      throw new Error('Investigation not found.');
    if (status === 'RESOLVED' && reason.trim().length < 5)
      throw new Error('Add a resolution reason with at least five characters.');
    state.anomaly_updates[id] = { status, reason: reason.trim() };
    log('INCIDENT_STATUS', `${id} marked ${status}. ${reason.trim()}`);
    save();
    return clone(currentAnomalies().find((a) => a.anomaly_id === id)!);
  },
  async optimizerContext() {
    return clone(optimizerFixture(activeDecision()));
  },
  async evaluateAllocation(input: AllocationInput): Promise<AllocationEvaluation> {
    const context = optimizerFixture(activeDecision());
    const errors = allocationErrors(context, input);
    if (errors.length) throw new Error(errors.join(' '));
    const d = find(input.decision_id);
    const known =
      input.legs.every((l) => d.legs.find((x) => x.budget_id === l.budget_id)?.after === l.after) &&
      d.valuation_status !== 'NOT_ESTIMABLE';
    const allocated = input.legs.reduce((n, l) => n + l.after, 0);
    return {
      decision_id: d.decision_id,
      decision_hash: d.decision_hash,
      objective: 'PROFIT',
      allocated,
      unallocated: d.budget_ceiling - allocated,
      checks: [
        {
          id: 'BROWSER_INPUT',
          label: 'Allocation input bounds',
          passed: true,
          detail:
            'Whole-rupee budgets, daily change, mapped inventory blocks and reserve floor pass input checks.',
        },
        {
          id: 'MODEL_VALUATION',
          label: 'Model valuation',
          passed: known,
          detail: known
            ? 'Pre-recorded illustrative forecast for this exact fixture allocation.'
            : 'Custom fixture allocations have no engine valuation. Approval stays unavailable.',
        },
      ],
      estimate_status: known ? 'AVAILABLE' : 'NOT_ESTIMABLE',
      estimate: known ? clone(d.expected) : null,
      explanation: known
        ? 'Recorded frontend example only. No optimizer was run.'
        : 'The UI accepts custom allocations but does not invent their profit or risk. Connect portfolio_economics to value them.',
    };
  },
  async runOptimizer() {
    const d = activeDecision();
    if (!d || d.class !== 'OPTIMIZATION' || !['PENDING_APPROVAL', 'DRAFT'].includes(d.status))
      throw new Error('No eligible optimization proposal in this scenario.');
    return clone(d);
  },
  async modify(input: AllocationInput) {
    const context = optimizerFixture(activeDecision());
    const errors = allocationErrors(context, input);
    if (errors.length) throw new Error(errors.join(' '));
    const d = find(input.decision_id);
    if (d.status !== 'PENDING_APPROVAL' || d.class !== 'OPTIMIZATION')
      throw new Error('409: Only a pending optimization proposal can be revised.');
    if (input.legs.every((l) => d.legs.find((x) => x.budget_id === l.budget_id)?.after === l.after))
      throw new Error('Change a budget before creating a revision.');
    const newId = `${d.decision_id}-rev-${crypto.randomUUID().slice(0, 6)}`;
    const revision: Decision = {
      ...clone(d),
      decision_id: newId,
      follows: d.decision_id,
      title: 'Revised allocation · awaiting model valuation',
      decision_hash: `fixture-${newId}`,
      snapshot_id: `fixture-snapshot-${newId}`,
      status: 'DRAFT',
      valuation_status: 'NOT_ESTIMABLE',
      legs: d.legs.map((l) => ({
        ...l,
        after: input.legs.find((x) => x.budget_id === l.budget_id)!.after,
      })),
      unallocated: d.budget_ceiling - input.legs.reduce((n, l) => n + l.after, 0),
      expected: {
        p10: 0,
        p50: 0,
        p90: 0,
        prob_loss: 0,
        delta_net_revenue: 0,
        raw_pred: 0,
        calibrated_pred: 0,
      },
      summary:
        'User revision recorded as a draft. Forecasts, inventory projection and policy approval require backend revaluation; they have not been recomputed by the browser.',
      checks: [
        {
          id: 'MODEL_VALUATION',
          label: 'Backend revaluation required',
          passed: false,
          detail:
            'This custom fixture draft has no model output, updated inventory envelope or executable policy approval.',
        },
      ],
      created_at: new Date().toISOString(),
    };
    d.status = 'SUPERSEDED';
    state.decisions.unshift(revision);
    log('REVISED', 'Pending proposal superseded by a separate non-executable draft.', newId);
    save();
    return clone(revision);
  },
  async ledger() {
    settle();
    return clone(state.ledger);
  },
  async fault(type: 'UNKNOWN' | 'FAILED') {
    settle();
    const e = state.executions.at(-1);
    if (!e || !['EXECUTING', 'SUCCEEDED'].includes(e.state))
      throw new Error('Approve a fixture decision before injecting an execution fault.');
    if (state.outcomes.some((o) => o.decision_id === e.decision_id))
      throw new Error('Use a fresh execution before its outcome matures.');
    e.legs[0].external_state = type;
    e.legs[0].read_back_budget = type === 'UNKNOWN' ? null : e.legs[0].before;
    e.state = type === 'UNKNOWN' ? 'EXECUTING' : 'PARTIAL';
    find(e.decision_id).status = type === 'UNKNOWN' ? 'EXECUTING' : 'PARTIAL';
    e.detail = `${type} frontend fault example. No platform calls are made.`;
    log('FAULT', `Illustrative ${type} leg injected; world advance is blocked.`, e.decision_id);
    save();
  },
  async recover(input: RecoveryInput) {
    const e = findExecution(input.execution_id);
    const d = find(e.decision_id);
    if (input.decision_hash !== d.decision_hash)
      throw new Error('409: Execution identity changed. Refresh before recovering.');
    if (input.action === 'verify') {
      e.legs
        .filter((l) => l.external_state === 'UNKNOWN' || l.external_state === 'SENT')
        .forEach((l) => {
          l.external_state = 'VERIFIED';
          l.read_back_budget = l.after;
          appendLedger(
            e,
            l.budget_id,
            'VERIFY',
            l.after,
            l.after,
            'Unknown example resolved by fixture read-back. No repeat budget request.',
          );
        });
      if (
        e.legs.every((l) => l.external_state === 'VERIFIED') &&
        ['EXECUTING', 'PARTIAL'].includes(e.state)
      ) {
        e.state = 'SUCCEEDED';
        d.status = 'EXECUTED';
      }
    } else {
      if (input.reason.trim().length < 5) throw new Error('A recovery reason is required.');
      if (e.legs.some((l) => ['UNKNOWN', 'CONFLICT', 'SENT'].includes(l.external_state)))
        throw new Error('409: Verify external state before any recovery mutation.');
      if (input.action === 'retry') {
        if (!['PARTIAL', 'HUMAN_RESOLUTION_REQUIRED'].includes(e.state))
          throw new Error('Retry is unavailable in this execution state.');
        if (!e.legs.some((l) => l.external_state === 'FAILED'))
          throw new Error('No verified failed leg is eligible for retry.');
        e.legs
          .filter((l) => l.external_state === 'FAILED')
          .forEach((l) => {
            appendLedger(
              e,
              l.budget_id,
              'SET_BUDGET',
              l.read_back_budget ?? l.before,
              l.after,
              'Verified failed leg retried as an absolute budget set.',
            );
            l.external_state = 'VERIFIED';
            l.read_back_budget = l.after;
          });
        e.state = 'SUCCEEDED';
        d.status = 'EXECUTED';
      } else if (input.action === 'rollback') {
        if (!['SUCCEEDED', 'PARTIAL', 'ACCEPTED_PARTIAL'].includes(e.state))
          throw new Error('Settings restoration is unavailable in this state.');
        e.legs.forEach((l) => {
          appendLedger(
            e,
            l.budget_id,
            'RESTORE_SETTINGS',
            l.read_back_budget ?? l.after,
            l.before,
            input.reason,
          );
          l.read_back_budget = l.before;
        });
        e.state = 'COMPENSATED';
        d.status = 'PARTIAL';
      } else {
        if (!['PARTIAL', 'HUMAN_RESOLUTION_REQUIRED', 'COMPENSATED'].includes(e.state))
          throw new Error('No eligible execution is available for reconciliation.');
        if (input.final_resolution !== 'COMPENSATED')
          throw new Error('This fixture supports only a verified compensated resolution.');
        if (e.legs.some((l) => l.read_back_budget !== l.before))
          throw new Error(
            'Restore prior settings and verify every read-back before recording compensated reconciliation.',
          );
        e.legs.forEach((l) => {
          appendLedger(e, l.budget_id, 'RECONCILE', l.before, l.before, input.reason);
        });
        e.state = 'RESOLVED_MANUALLY';
        d.status = 'PARTIAL';
      }
    }
    log(
      'RECOVERY',
      `${input.action} applied to the frontend example. ${input.reason}`,
      d.decision_id,
    );
    save();
    return clone(e);
  },
};
export const resetFixtureForTests = () => {
  state = initial();
  activeWorkspace = 'default';
};
export function activateFixtureWorkspace(id: string) {
  if (!/^[a-zA-Z0-9_-]{1,80}$/.test(id)) throw new Error('Invalid workspace identity.');
  settle();
  if (hasUnresolved())
    throw new Error('409: Resolve the current execution before switching workspaces.');
  // Commit storage before changing the in-memory identity. No silent switch on storage failure.
  localStorage.setItem(fixtureKey(), JSON.stringify(state));
  localStorage.setItem('adapt.active-workspace', id);
  activeWorkspace = id;
  state = initial();
  loadFixtureState();
}
