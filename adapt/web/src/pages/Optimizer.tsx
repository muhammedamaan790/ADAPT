import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, RotateCcw } from 'lucide-react';
import { api, dataMode } from '../api/client';
import { useAction, useDecisions } from '../hooks/workspace';
import type {
  OptimizerContext,
  AllocationInput,
  AllocationEvaluation,
} from '../api/workbench-contracts';
import { allocationErrors } from '../lib/allocation';
import { objectives } from '../lib/objectives';
import type { Objective } from '../api/contracts';
import {
  Badge,
  Empty,
  ErrorState,
  InlineError,
  Loading,
  Modal,
  PolicyCheck,
  SectionTitle,
} from '../components/ui';
import { money, percent, signedMoney } from '../lib/format';

export function Optimizer() {
  const context = useQuery({ queryKey: ['optimizer-context'], queryFn: api.optimizerContext });
  const decisions = useDecisions();
  if (context.isPending || decisions.isPending)
    return <Loading label="Loading allocation constraints" />;
  if (context.error || decisions.error)
    return (
      <ErrorState
        error={context.error || decisions.error!}
        retry={() => {
          void context.refetch();
          void decisions.refetch();
        }}
      />
    );
  const c = context.data!;
  const decision = decisions.data?.find((d) => d.decision_id === c.decision_id);
  if (
    decision &&
    (decision.decision_hash !== c.decision_hash || decision.objective !== c.objective)
  )
    return (
      <ErrorState
        error={
          new Error(
            'The optimizer context does not match the current proposal. Refresh before evaluating or revising.',
          )
        }
        retry={() => {
          void context.refetch();
          void decisions.refetch();
        }}
      />
    );
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Optimizer</h1>
          <p>Compare an allocation, inspect the constraints, and submit a separate revision.</p>
        </div>
        <Badge tone="accent">{c.objective.replaceAll('_', ' ')} PROPOSAL</Badge>
      </div>
      {!c.campaigns.length || !decision || decision.class !== 'OPTIMIZATION' ? (
        <section className="panel">
          <Empty title="No optimization proposal available">
            This scenario needs an operational or safety response, or has no open proposal.
          </Empty>
          <Link className="button secondary" to="/scenarios">
            Choose an optimization example <ArrowRight size={15} />
          </Link>
        </section>
      ) : (
        <AllocationEditor
          key={`${c.decision_id}:${c.decision_hash}`}
          context={c}
          canRevise={decision.status === 'PENDING_APPROVAL'}
        />
      )}
    </>
  );
}

function AllocationEditor({
  context: c,
  canRevise,
}: {
  context: OptimizerContext;
  canRevise: boolean;
}) {
  const navigate = useNavigate();
  const [budgets, setBudgets] = useState<Record<string, string>>(() =>
    Object.fromEntries(c.campaigns.map((l) => [l.budget_id, String(l.after)])),
  );
  const [evaluation, setEvaluation] = useState<AllocationEvaluation | null>(null);
  const [objective, setObjective] = useState<Objective>(c.objective);
  const [confirm, setConfirm] = useState(false);
  const input: AllocationInput = {
    decision_id: c.decision_id!,
    decision_hash: c.decision_hash!,
    policy_version: c.policy_version,
    objective,
    legs: c.campaigns.map((l) => ({
      budget_id: l.budget_id,
      after: budgets[l.budget_id]?.trim() === '' ? NaN : Number(budgets[l.budget_id]),
    })),
  };
  const errors = allocationErrors(c, input);
  const total = input.legs.reduce((n, l) => n + (Number.isFinite(l.after) ? l.after : 0), 0);
  const changed =
    objective !== c.objective ||
    input.legs.some((l) => l.after !== c.campaigns.find((x) => x.budget_id === l.budget_id)?.after);
  const evaluate = useAction(async (v: AllocationInput) => {
    const result = await api.evaluateAllocation(v);
    setEvaluation(result);
    return result;
  });
  const revise = useAction(api.modify);
  const run = useAction((selected: Objective) => api.runOptimizer(selected));
  const editing = (id: string, value: string) => {
    setBudgets((b) => ({ ...b, [id]: value }));
    setEvaluation(null);
    evaluate.reset();
    revise.reset();
  };
  const reset = () => {
    setBudgets(Object.fromEntries(c.campaigns.map((l) => [l.budget_id, String(l.after)])));
    setEvaluation(null);
    evaluate.reset();
  };
  const busy = evaluate.isPending || revise.isPending || run.isPending;
  return (
    <>
      <div className="workbench-stats">
        <div>
          <span>Daily budget ceiling</span>
          <strong>{money(c.budget_ceiling)}</strong>
        </div>
        <div>
          <span>Allocated / day</span>
          <strong>{money(total)}</strong>
        </div>
        <div>
          <span>Unallocated / day</span>
          <strong>{money(c.budget_ceiling - total)}</strong>
        </div>
      </div>
      <div className="optimizer-layout">
        <section className="panel">
          <SectionTitle title="Allocation workbench">
            <button className="button tertiary" onClick={reset} disabled={busy}>
              <RotateCcw size={14} /> Reset allocation
            </button>
          </SectionTitle>
          <p className="section-description">
            Input checks run here. Profit, loss risk, inventory projections and executable policy
            gates belong to the backend.
          </p>
          <label className="field objective-picker">
            Evaluation objective
            <select
              value={objective}
              disabled={busy}
              onChange={(e) => {
                setObjective(e.target.value as Objective);
                setEvaluation(null);
                evaluate.reset();
                revise.reset();
                run.reset();
              }}
            >
              {Object.entries(objectives).map(([id, o]) => (
                <option
                  key={id}
                  value={id}
                  disabled={!c.supported_objectives.includes(id as Objective)}
                >
                  {o.label}
                  {c.supported_objectives.includes(id as Objective) ? '' : ' · backend unavailable'}
                </option>
              ))}
            </select>
          </label>
          <p className="workbench-copy">
            {objectives[objective].description} This selection applies to a what-if or new proposal;
            it does not change the workspace default or execute budgets.
          </p>
          <div className="budget-editor">
            {c.campaigns.map((l) => (
              <article className="budget-row" key={l.budget_id}>
                <div>
                  <div className="badge-row">
                    <Badge>{l.platform}</Badge>
                    <Badge tone={l.inventory_gate === 'BLOCK' ? 'danger' : 'neutral'}>
                      Inventory {l.inventory_gate.toLowerCase()}
                    </Badge>
                  </div>
                  <h3>{l.entity}</h3>
                  <p>{l.note}</p>
                  <dl className="budget-signals">
                    <div>
                      <dt>Margin</dt>
                      <dd>{percent(l.margin)}</dd>
                    </div>
                    <div>
                      <dt>ROAS</dt>
                      <dd>{l.roas.toFixed(2)}×</dd>
                    </div>
                    <div>
                      <dt>Marginal CAA</dt>
                      <dd>
                        {l.marginal_caa === null
                          ? 'Unavailable'
                          : `${l.marginal_caa.toFixed(2)} / ₹1`}
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="budget-input">
                  <span>Current {money(l.before)} / day</span>
                  <label className="field">
                    {l.entity} proposed budget
                    <input
                      type="number"
                      step="1"
                      min={l.min_budget}
                      max={l.max_budget}
                      value={budgets[l.budget_id]}
                      disabled={busy}
                      onChange={(e) => editing(l.budget_id, e.target.value)}
                      aria-invalid={errors.some((e) => e.includes(l.entity))}
                    />
                  </label>
                  <small>
                    {money(l.min_budget)}–{money(l.max_budget)} · whole rupees
                  </small>
                </div>
              </article>
            ))}
          </div>
          {errors.length > 0 && (
            <div className="input-errors" role="alert">
              <strong>Resolve these input checks</strong>
              <ul>
                {errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            </div>
          )}
          <div className="workbench-actions">
            <button
              className="button primary"
              disabled={busy || errors.length > 0}
              onClick={() => {
                setEvaluation(null);
                evaluate.mutate(input);
              }}
            >
              {evaluate.isPending ? 'Evaluating…' : 'Evaluate allocation'}
            </button>
            <button
              className="button secondary"
              disabled={busy || !canRevise || !changed || errors.length > 0}
              onClick={() => {
                revise.reset();
                setConfirm(true);
              }}
            >
              Create revision
            </button>
          </div>
          <InlineError error={evaluate.error} />
          {!canRevise && (
            <p className="caption">
              Only a pending optimization proposal can be revised. Review the current decision
              status.
            </p>
          )}
        </section>
        <aside>
          <section className="panel">
            <SectionTitle title="Active constraints">
              <Badge>{c.policy_version}</Badge>
            </SectionTitle>
            <dl className="constraint-list">
              <div>
                <dt>Objective</dt>
                <dd>{objectives[objective].label}</dd>
              </div>
              <div>
                <dt>Daily change cap</dt>
                <dd>{percent(c.max_daily_change)}</dd>
              </div>
              <div>
                <dt>Reserve floor</dt>
                <dd>{money(c.reserve_floor)}</dd>
              </div>
              <div>
                <dt>Evaluation horizon</dt>
                <dd>{c.horizon_days} days</dd>
              </div>
            </dl>
            <p className="caption">
              Only objectives reported by the current backend are selectable. Constraints and
              objective-specific values are calculated by the engine.
            </p>
            <button
              className="button secondary full-width"
              disabled={busy || !canRevise || !c.supported_objectives.includes(objective)}
              onClick={() =>
                run.mutate(objective, { onSuccess: (d) => navigate(`/decisions/${d.decision_id}`) })
              }
            >
              {dataMode === 'fixture'
                ? 'Inspect recorded recommendation'
                : `Run ${objective.replaceAll('_', ' ')} optimizer`}
            </button>
            <InlineError error={run.error} />
          </section>
          <section className="panel" aria-live="polite">
            <SectionTitle title="Allocation evaluation" />
            {!evaluation ? (
              <p className="workbench-copy">
                Evaluate the displayed budgets to inspect their valuation. Editing any budget clears
                the previous result.
              </p>
            ) : (
              <>
                <Badge tone={evaluation.estimate_status === 'AVAILABLE' ? 'accent' : 'warning'}>
                  {evaluation.estimate_status === 'AVAILABLE'
                    ? dataMode === 'fixture'
                      ? 'Recorded valuation'
                      : 'Backend valuation'
                    : 'Not estimable'}
                </Badge>
                {evaluation.estimate && (
                  <dl className="constraint-list">
                    <div>
                      <dt>Median ΔCAA</dt>
                      <dd>{signedMoney(evaluation.estimate.p50)}</dd>
                    </div>
                    <div>
                      <dt>P10–P90</dt>
                      <dd>
                        {money(evaluation.estimate.p10)}–{money(evaluation.estimate.p90)}
                      </dd>
                    </div>
                    <div>
                      <dt>Bootstrap P(loss)</dt>
                      <dd>{percent(evaluation.estimate.prob_loss)}</dd>
                    </div>
                  </dl>
                )}
                <p className="workbench-copy">{evaluation.explanation}</p>
                {evaluation.objective_value ? (
                  <p className="notice">
                    {evaluation.objective_value.label}:{' '}
                    {evaluation.objective_value.unit === 'INR'
                      ? money(evaluation.objective_value.value)
                      : `${evaluation.objective_value.value.toLocaleString('en-IN')} ${evaluation.objective_value.unit.toLowerCase()}`}
                  </p>
                ) : (
                  objective !== 'PROFIT' && (
                    <p className="notice">
                      The selected objective value was not supplied. CAA above remains a financial
                      risk measure, not a substitute objective score.
                    </p>
                  )
                )}
                {evaluation.checks.map((check) => (
                  <PolicyCheck key={check.id} {...check} />
                ))}
              </>
            )}
          </section>
        </aside>
      </div>
      {confirm && (
        <Modal
          title="Create a separate revision"
          close={() => {
            if (!revise.isPending) setConfirm(false);
          }}
        >
          <p className="modal-description">
            This supersedes the pending proposal. The original stays in the decision history.{' '}
            {dataMode === 'fixture'
              ? 'Custom fixture revisions are drafts awaiting backend valuation and cannot be approved.'
              : 'The backend must revalue the allocation and validate the current decision hash.'}
          </p>
          <p className="notice">Allocated: {money(total)} / day · no execution starts here.</p>
          <InlineError error={revise.error} />
          <div className="modal-actions">
            <button
              className="button secondary"
              disabled={revise.isPending}
              onClick={() => setConfirm(false)}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={revise.isPending}
              onClick={() =>
                revise.mutate(input, {
                  onSuccess: (d) => {
                    setConfirm(false);
                    navigate(`/decisions/${d.decision_id}`);
                  },
                })
              }
            >
              {revise.isPending ? 'Creating…' : 'Confirm revision'}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
