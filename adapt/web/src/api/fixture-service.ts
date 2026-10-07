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

const KEY = 'adapt.frontend.fixtures.v1';
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
});
let state: State = initial();
export function loadFixtureState() {
  try {
    const value = JSON.parse(localStorage.getItem(KEY) || 'null');
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
      state = value;
    }
  } catch {
    state = initial();
  }
}
const save = () => {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
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
    if (e.state === 'EXECUTING' && Date.now() - new Date(e.started_at).getTime() >= 1200) {
      e.state = 'SUCCEEDED';
      e.legs.forEach((l) => (l.external_state = 'VERIFIED'));
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
export const fixtureService = {
  async overview() {
    settle();
    const overview = fixtureOverview(state.scenario, state.day);
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
        find(a.decision_id).status === 'PENDING_APPROVAL' ||
        find(a.decision_id).status === 'BLOCKED',
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
    return clone(fixtureEvidence(state.scenario));
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
    state.executions.push({
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
        })),
      started_at: new Date().toISOString(),
      detail: 'Illustrative frontend saga. No platform request is sent.',
    });
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
    if (state.executions.some((e) => e.state === 'EXECUTING'))
      throw new Error('Finish the current execution before changing scenarios.');
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
    state = { ...initial(), seed };
    log(
      'RESET',
      `Frontend example reset; seed ${seed} reserved for backend integration (does not generate fixture values).`,
    );
    save();
  },
  async advance(days: number) {
    settle();
    if (
      state.executions.some(
        (e) =>
          e.state === 'EXECUTING' ||
          e.legs.some((l) => ['MIRROR_PENDING', 'MIRROR_FAILED'].includes(l.sim_sync_state)),
      )
    )
      throw new Error('Execution is pending or the simulation is out of sync.');
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
          state.day >= d.horizon_days,
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
};
export const resetFixtureForTests = () => {
  state = initial();
};
