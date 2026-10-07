import { lazy, Suspense, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  FileCheck2,
  Info,
  LockKeyhole,
  Loader2,
  ShieldCheck,
} from 'lucide-react';
import { api, dataMode } from '../api/client';
import type { Decision, Outcome } from '../api/contracts';
import {
  useAction,
  useDecisions,
  useExecutions,
  useOutcomes,
  useOverview,
} from '../hooks/workspace';
import {
  Badge,
  Disclosure,
  Empty,
  ErrorState,
  InlineError,
  Loading,
  Modal,
  PolicyCheck,
  SectionTitle,
  Status,
} from '../components/ui';
const DecisionInsights = lazy(() =>
  import('../components/DecisionInsights').then((m) => ({ default: m.DecisionInsights })),
);
import { TrendChart, Waterfall } from '../components/charts';
import { dateTime, humanStatus, money, percent, signedMoney } from '../lib/format';

export function DecisionCenter() {
  const { id: routeId } = useParams();
  const decisions = useDecisions();
  const id = routeId || decisions.data?.[0]?.decision_id;
  const detail = useQuery({
    queryKey: ['decision', id],
    queryFn: () => api.decision(id!),
    enabled: !!id,
    refetchInterval: 1500,
  });
  const evidence = useQuery({
    queryKey: ['evidence', id],
    queryFn: () => api.evidence(id!),
    enabled: !!id,
  });
  const executions = useExecutions();
  const outcomes = useOutcomes();
  const overview = useOverview();
  const [dialog, setDialog] = useState<{ kind: 'approve' | 'reject'; decision: Decision } | null>(
    null,
  );
  const [confirmed, setConfirmed] = useState(false);
  const [reason, setReason] = useState('');
  const approve = useAction((d: Decision) => api.approve(d.decision_id, d.decision_hash));
  const reject = useAction((v: { d: Decision; reason: string }) =>
    api.reject(v.d.decision_id, v.d.decision_hash, v.reason),
  );
  const advance = useAction(async () => {
    await api.advance(3);
  });
  const close = () => {
    setDialog(null);
    setConfirmed(false);
    setReason('');
    approve.reset();
    reject.reset();
  };
  const open = (kind: 'approve' | 'reject', d: Decision) => {
    approve.reset();
    reject.reset();
    setConfirmed(false);
    setDialog({ kind, decision: structuredClone(d) });
  };
  if (decisions.isPending) return <Loading label="Loading decisions" />;
  if (decisions.error)
    return <ErrorState error={decisions.error} retry={() => void decisions.refetch()} />;
  if (!id)
    return (
      <>
        <div className="page-heading">
          <div>
            <h1>Decision Center</h1>
            <p>Evidence, policy and action in one place.</p>
          </div>
        </div>
        <section className="panel">
          <Empty title="No decisions to review">
            This workspace has no open proposal. Run a scenario to investigate a new example.
          </Empty>
          <div className="empty-action">
            <Link className="button secondary" to="/scenarios">
              Open Scenario Lab <ArrowRight size={15} />
            </Link>
          </div>
        </section>
      </>
    );
  if (detail.isPending || evidence.isPending) return <Loading label="Loading decision evidence" />;
  if (detail.error || evidence.error || !detail.data || !evidence.data)
    return (
      <ErrorState
        error={detail.error || evidence.error || new Error('Decision evidence unavailable')}
        retry={() => {
          void detail.refetch();
          void evidence.refetch();
        }}
      />
    );
  const d = detail.data;
  const e = evidence.data;
  const execution = executions.data?.find((x) => x.decision_id === d.decision_id);
  const outcome = outcomes.data?.find((o) => o.decision_id === d.decision_id);
  const canApprove =
    d.status === 'PENDING_APPROVAL' &&
    d.checks.every((c) => c.passed) &&
    !executions.isError &&
    !executions.isPending;
  const outOfSync = execution?.legs.some((l) =>
    ['MIRROR_PENDING', 'MIRROR_FAILED'].includes(l.sim_sync_state),
  );
  const unsafe =
    execution &&
    [
      'BLOCKED',
      'PARTIAL',
      'COMPENSATING',
      'COMPENSATION_FAILED',
      'HUMAN_RESOLUTION_REQUIRED',
    ].includes(execution.state);
  const lossLabel = d.class === 'SAFETY' ? 'Model-estimated avoided loss' : 'Model-estimated ΔCAA';
  const isOperational = d.class === 'OPERATIONAL';
  const valuationMissing = d.valuation_status === 'NOT_ESTIMABLE';
  const absenceMessage: Record<Decision['status'], { title: string; detail: string }> = {
    PENDING_APPROVAL: {
      title: 'Waiting for your approval',
      detail:
        'No changes have been sent. Approval is tied to this exact proposal and policy version.',
    },
    APPROVED: {
      title: 'Approved · awaiting execution',
      detail: 'The approval is recorded. Waiting for the backend execution record.',
    },
    REJECTED: {
      title: 'Rejected · no execution started',
      detail: 'This proposal was rejected. No budget changes were sent for this decision.',
    },
    EXPIRED: {
      title: 'Expired · review a fresh proposal',
      detail: 'The approval window or inputs changed. This proposal cannot execute.',
    },
    SUPERSEDED: {
      title: 'Superseded · review its replacement',
      detail: 'A newer decision replaces this proposal. No execution starts from this record.',
    },
    BLOCKED: {
      title: 'Blocked · no execution started',
      detail:
        'A policy or dependency gate prevents execution. Resolve the issue before a new proposal is considered.',
    },
    DRAFT: {
      title: 'Draft · not submitted for approval',
      detail: 'The backend has not submitted this decision. No changes have been sent.',
    },
    EXECUTING: {
      title: 'Execution record is not available yet',
      detail: 'Refresh to verify the external state. Do not resubmit this decision.',
    },
    EXECUTED: {
      title: 'Execution record unavailable',
      detail:
        'The decision is marked executed, but its verification record could not be found. Refresh or ask the backend team to reconcile it.',
    },
    PARTIAL: {
      title: 'Partial execution · review required',
      detail:
        'The execution record is needed to inspect completed legs and resolve the remaining state.',
    },
  };
  return (
    <>
      <Link className="back-link" to="/">
        <ArrowLeft size={15} />
        Back to Command Center
      </Link>
      <div className="page-heading decision-heading">
        <div>
          <h1>{d.title}</h1>
          <div className="heading-badges">
            <Badge>{d.class}</Badge>
            <Status value={d.status} />
          </div>
          <p>
            {d.decision_id} · {dateTime(d.created_at)} · {d.horizon_days}-day horizon
          </p>
        </div>
        <Badge tone="accent">
          {d.objective.replaceAll('_', ' ')} ·{' '}
          {dataMode === 'fixture' ? 'APPROVE MODE' : 'CHANNEL POLICY'}
        </Badge>
      </div>
      {d.follows && (
        <p className="notice">
          Separate revision of{' '}
          <Link className="text-link" to={`/decisions/${d.follows}`}>
            {d.follows}
          </Link>
          . The original proposal remains in history.
        </p>
      )}
      {(decisions.data?.length || 0) > 1 && (
        <nav className="decision-switcher" aria-label="Decision selection">
          {decisions.data!.map((x) => (
            <Link
              key={x.decision_id}
              className={x.decision_id === id ? 'selected' : ''}
              to={`/decisions/${x.decision_id}`}
            >
              {x.title}
            </Link>
          ))}
        </nav>
      )}
      <nav className="section-nav" aria-label="Decision sections">
        <a href="#why">Why this decision</a>
        <a href="#allocation">Budget recommendation</a>
        <a href="#execution">Execution & outcome</a>
        <a href="#decision-review">Review action</a>
      </nav>
      {!isOperational && !valuationMissing && (
        <section className="proposal-context" aria-label="Proposal summary">
          <dl>
            <div>
              <dt>Est. contribution after ads · {d.horizon_days} days</dt>
              <dd>{signedMoney(d.expected.p50)}</dd>
            </div>
            <div>
              <dt>Unallocated budget</dt>
              <dd>{money(d.unallocated)}</dd>
            </div>
          </dl>
          <a href="#decision-review" className="text-link">
            Review proposal <ArrowRight size={15} />
          </a>
        </section>
      )}
      <div className="decision-layout">
        <div className="decision-main">
          <section className="panel" id="why">
            <SectionTitle title="The signal behind the decision">
              <Badge tone="warning">Probable driver</Badge>
            </SectionTitle>
            <p className="section-description">
              Trigger {d.trigger.anomaly_id || d.trigger.opportunity_id || 'manual review'} ·
              deterministic detection
            </p>
            <TrendChart data={e.chart} title={e.chart_metric} />
            {e.decomposition_kind === 'ROAS' ? (
              <div className="decomposition-section">
                <div>
                  <h3>Where did the change come from?</h3>
                  <p>
                    Funnel terms explain the accounting change. They do not establish causality.
                  </p>
                  <Badge>ACCOUNTING IDENTITY</Badge>
                </div>
                <Waterfall data={e.decomposition} total={e.decomposition_total} />
              </div>
            ) : (
              <p className="caption">
                ROAS accounting decomposition does not apply to this metric family. Drivers below
                are ranked by evidence score.
              </p>
            )}
            <div className="evidence-list">
              {e.drivers.map((driver) => (
                <article className="evidence-item" key={driver.id}>
                  <div className="evidence-heading">
                    <h3>{driver.title}</h3>
                    <Badge tone="accent">Evidence score {driver.score.toFixed(2)}</Badge>
                  </div>
                  <Badge>{driver.level}</Badge>
                  <p>{driver.detail}</p>
                  <ul>
                    {driver.observations.map((o) => (
                      <li key={o}>
                        <CheckCircle2 size={14} />
                        {o}
                      </li>
                    ))}
                  </ul>
                  <Disclosure title={`Evidence lineage · ${driver.id}`}>
                    <p>{driver.source}</p>
                    <p>
                      Available at {driver.available_at}. Evidence score is a diagnostic strength
                      index, not a probability of causation.
                    </p>
                    <div className="badge-row">
                      {d.provenance_inputs.map((p) => (
                        <Badge key={p}>{p}</Badge>
                      ))}
                    </div>
                  </Disclosure>
                </article>
              ))}
            </div>
          </section>
          <section className="panel" id="allocation">
            <SectionTitle
              title={
                isOperational ? 'The required operational action' : 'The recommended allocation'
              }
            >
              <Badge tone="accent">Recommended</Badge>
            </SectionTitle>
            <p className="allocation-summary">{d.summary}</p>
            {d.legs.length ? (
              <div className="table-scroll">
                <table className="allocation-table">
                  <thead>
                    <tr>
                      <th>Campaign / budget unit</th>
                      <th>Current / day</th>
                      <th>Proposed / day</th>
                      <th>Change</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.legs.map((leg) => (
                      <tr key={leg.budget_id}>
                        <td>
                          <strong>{leg.entity}</strong>
                          <span className={`channel channel-${leg.platform.toLowerCase()}`}>
                            {leg.platform}
                          </span>
                        </td>
                        <td>{money(leg.before)}</td>
                        <td className="proposed">{money(leg.after)}</td>
                        <td className={leg.after > leg.before ? 'text-success' : 'text-warning'}>
                          {signedMoney(leg.after - leg.before)}
                          <small>
                            {leg.before
                              ? percent((leg.after - leg.before) / leg.before)
                              : 'New budget unit'}
                          </small>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="tracking-hold">
                <LockKeyhole size={20} />
                <p>
                  No platform mutation proposed. Resolve the tracking incident before a new
                  allocation can be generated.
                </p>
              </div>
            )}
            {d.legs.length > 0 && (
              <div className="allocation-mobile">
                {d.legs.map((leg) => (
                  <article key={leg.budget_id}>
                    <div>
                      <strong>{leg.entity}</strong>
                      <span className={`channel channel-${leg.platform.toLowerCase()}`}>
                        {leg.platform}
                      </span>
                    </div>
                    <dl>
                      <div>
                        <dt>Current / day</dt>
                        <dd>{money(leg.before)}</dd>
                      </div>
                      <div>
                        <dt>Proposed / day</dt>
                        <dd className="proposed">{money(leg.after)}</dd>
                      </div>
                      <div>
                        <dt>Change</dt>
                        <dd className={leg.after > leg.before ? 'text-success' : 'text-warning'}>
                          {signedMoney(leg.after - leg.before)}
                          <small>
                            {leg.before
                              ? percent((leg.after - leg.before) / leg.before)
                              : 'New budget unit'}
                          </small>
                        </dd>
                      </div>
                    </dl>
                  </article>
                ))}
              </div>
            )}
            {!isOperational && !valuationMissing && (
              <>
                <div className="reserve-callout">
                  <div>
                    <strong>{money(d.unallocated)} unallocated</strong>
                    <p>
                      Budget is a ceiling. Spend stays undeployed when no feasible move improves
                      contribution.
                    </p>
                  </div>
                  <div>
                    <span>Reserve floor</span>
                    <b>{money(d.reserve_floor)}</b>
                    <small>Ceiling {money(d.budget_ceiling)}</small>
                  </div>
                </div>
                <div className="inventory-summary">
                  <PackageIcon />
                  <div>
                    <strong>Projected stock shortfall after action</strong>
                    <p>
                      {Object.entries(d.inventory_risk_after.by_sku)
                        .map(([sku, units]) => `${sku}: ${units} units`)
                        .join(' · ')}
                    </p>
                    <small>Deterministic projection · not a stockout probability</small>
                  </div>
                </div>
              </>
            )}
          </section>
          {!valuationMissing && (
            <section className="panel">
              <SectionTitle title="Why not the obvious alternative?" />
              <div className="why-not-list">
                {d.why_not.map((item) => (
                  <article key={item.entity}>
                    <div className="why-not-title">
                      <h3>{item.entity}</h3>
                      <Badge tone="warning">Not selected</Badge>
                    </div>
                    <p>{item.reason}</p>
                    <div className="why-not-footer">
                      <span>{item.metric}</span>
                      <code>{item.rule_id}</code>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          )}
          <Suspense fallback={<Loading label="Loading decision tools" />}>
            <DecisionInsights key={d.decision_id} decision={d} />
          </Suspense>
          <section className="panel" id="execution">
            <SectionTitle title="Execution & verification">
              {execution && <Status value={execution.state} />}
            </SectionTitle>
            {execution && (
              <Link className="text-link" to="/executions">
                Open recovery controls & ledger <ArrowRight size={14} />
              </Link>
            )}
            {executions.isError ? (
              <ErrorState error={executions.error!} retry={() => void executions.refetch()} />
            ) : executions.isPending ? (
              <Loading label="Loading execution state" />
            ) : !execution ? (
              <div className="execution-await">
                <ShieldCheck size={23} />
                <div>
                  <strong>{absenceMessage[d.status].title}</strong>
                  <p>{absenceMessage[d.status].detail}</p>
                </div>
              </div>
            ) : (
              <>
                {(outOfSync || unsafe) && (
                  <div className="inline-error" role="alert">
                    <LockKeyhole size={18} />
                    <p>
                      {outOfSync
                        ? 'SIM OUT OF SYNC. World advance is blocked until mirror state is resolved.'
                        : 'Execution needs backend reconciliation. Entities remain frozen until the external state is verified.'}
                    </p>
                  </div>
                )}
                <p className="section-description">{execution.detail}</p>
                <div className="execution-legs">
                  {execution.legs.map((leg) => (
                    <div className="execution-leg" key={leg.budget_id}>
                      <span
                        className={`execution-node ${leg.external_state === 'VERIFIED' ? 'verified' : ''}`}
                      >
                        {leg.external_state === 'VERIFIED' ? (
                          <CheckCircle2 size={19} />
                        ) : (
                          <Loader2
                            size={19}
                            className={leg.external_state === 'SENT' ? 'spin' : ''}
                          />
                        )}
                      </span>
                      <div>
                        <strong>{leg.entity}</strong>
                        <span>
                          {money(leg.before)} <ArrowRight size={12} />
                          {money(leg.after)}
                        </span>
                        {leg.read_back_budget !== undefined && (
                          <small>Latest read-back: {money(leg.read_back_budget)}</small>
                        )}
                      </div>
                      <div className="leg-badges">
                        <Badge tone={leg.mode === 'LIVE' ? 'accent' : 'neutral'}>{leg.mode}</Badge>
                        <Status value={leg.external_state} />
                        {leg.sim_sync_state !== 'NOT_REQUIRED' && (
                          <Badge>{humanStatus(leg.sim_sync_state)}</Badge>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
            {outcomes.isError && (
              <ErrorState error={outcomes.error!} retry={() => void outcomes.refetch()} />
            )}
            {outcome ? (
              <div className="outcome-panel">
                <SectionTitle title="Prediction meets the outcome">
                  <Status value={outcome.verdict} />
                </SectionTitle>
                <div className="outcome-numbers">
                  <div>
                    <span>Decision-time forecast</span>
                    <strong>{money(outcome.predicted)}</strong>
                  </div>
                  <div>
                    <span>
                      {dataMode === 'fixture' ? 'Example measured lift' : 'Measured ΔCAA'}
                    </span>
                    <strong>{money(outcome.measured)}</strong>
                  </div>
                  <div>
                    <span>World</span>
                    <Badge>{outcome.world}</Badge>
                  </div>
                </div>
                <p>{outcome.method}</p>
                <Disclosure title="Measurement & feedback details">
                  <p>
                    Counterfactual CAA: {money(outcome.counterfactual)}. Observed CAA:{' '}
                    {money(outcome.counterfactual + outcome.measured)}. Verdict: {outcome.verdict}.
                    Matured {dateTime(outcome.matured_at)}.
                  </p>
                  <p>
                    {outcome.calibration_applied
                      ? `Factor updated once: ${outcome.factor_before.toFixed(2)} → ${outcome.factor_after.toFixed(2)}. For the same raw forecast ${money(d.expected.raw_pred)}, the next calibrated forecast is ${money(d.expected.raw_pred * outcome.factor_after)}.`
                      : outcome.calibration_note || calibrationSkipReason(outcome)}
                  </p>
                </Disclosure>
              </div>
            ) : (
              d.status === 'EXECUTED' && (
                <div className="feedback-pending">
                  <div>
                    <h3>Complete the feedback loop</h3>
                    <p>
                      Advance the world to measure the decision and update its forecast calibration.
                    </p>
                  </div>
                  <button
                    className="button secondary"
                    disabled={advance.isPending || !!outOfSync || !!unsafe}
                    onClick={() => advance.mutate()}
                  >
                    {advance.isPending ? 'Advancing…' : 'Advance 3 days'}
                    <ArrowRight size={15} />
                  </button>
                  <InlineError error={advance.error} />
                </div>
              )
            )}
          </section>
        </div>
        <aside className="decision-review" id="decision-review" aria-label="Review action">
          <section className="panel review-card">
            <SectionTitle title="Review the proposal">
              <ShieldCheck size={19} />
            </SectionTitle>
            {valuationMissing ? (
              <div className="operational-review">
                <h3>Awaiting backend valuation</h3>
                <p>
                  This draft has no forecast, updated inventory projection or executable approval.
                  Connect the engine to revalue the revised allocation.
                </p>
              </div>
            ) : isOperational ? (
              <div className="operational-review">
                <LockKeyhole size={22} />
                <h3>Resolve tracking first</h3>
                <p>
                  Allocation forecasts are withheld while required reporting is unreliable. No
                  budget action is proposed.
                </p>
              </div>
            ) : (
              <>
                <div className="forecast-value">
                  <span>
                    {lossLabel} · {d.horizon_days} days
                  </span>
                  <strong>{signedMoney(d.expected.p50)}</strong>
                  <small>
                    P10 {money(d.expected.p10)} — P90 {money(d.expected.p90)}
                  </small>
                </div>
                <div className="review-facts">
                  <div>
                    <span>Model P(loss)</span>
                    <strong>{percent(d.expected.prob_loss)}</strong>
                  </div>
                  <div>
                    <span>Net revenue change</span>
                    <strong>{signedMoney(d.expected.delta_net_revenue)}</strong>
                  </div>
                  <div>
                    <span>Unallocated budget</span>
                    <strong>{money(d.unallocated)}</strong>
                  </div>
                </div>
                <p className="caption">
                  <Info size={13} /> Model-derived risk; not a calibrated probability.
                </p>
              </>
            )}
            <hr />
            <h3>Policy checks</h3>
            <div className="policy-list">
              {d.checks.map((check) => (
                <PolicyCheck key={check.id} {...check} />
              ))}
            </div>
            <button
              className="button primary full"
              disabled={!canApprove || approve.isPending}
              onClick={() => open('approve', d)}
            >
              <FileCheck2 size={17} />
              {d.status === 'EXECUTING'
                ? 'Execution in progress'
                : d.status === 'EXECUTED'
                  ? 'Executed & verified'
                  : d.status === 'BLOCKED'
                    ? 'Execution blocked'
                    : d.status === 'REJECTED'
                      ? 'Proposal rejected'
                      : 'Approve & execute'}
            </button>
            <button
              className="button ghost full"
              disabled={d.status !== 'PENDING_APPROVAL'}
              onClick={() => open('reject', d)}
            >
              Reject with a reason
            </button>
            {d.class === 'OPTIMIZATION' && d.status === 'PENDING_APPROVAL' && (
              <Link className="button secondary full" to="/optimizer">
                Modify allocation
              </Link>
            )}
            <div className="approval-footnote">
              <LockKeyhole size={13} />
              <span>
                {dataMode === 'fixture'
                  ? 'Frontend example only. No ad account changes.'
                  : 'Backend revalidates the hash, policy and external state.'}
              </span>
            </div>
            <Disclosure title="Decision identity & provenance">
              <p>
                Hash <code className="break-all">{d.decision_hash}</code>
              </p>
              <p>
                Snapshot {d.snapshot_id}
                <br />
                Policy {d.policy_version}
              </p>
              <div className="badge-row">
                {d.provenance_inputs.map((p) => (
                  <Badge key={p}>{p}</Badge>
                ))}
              </div>
            </Disclosure>
          </section>
          {!isOperational && !valuationMissing && (
            <div className="inaction-note">
              <h3>What if we do nothing?</h3>
              <strong>{money(d.cost_of_inaction_7d)}</strong>
              <p>Model-estimated 7-day cost of inaction. Shown separately; not added to ΔCAA.</p>
            </div>
          )}
          {overview.data && (
            <div className="track-record">
              <h3>Outcome track record</h3>
              <div>
                {Object.entries(overview.data.counts).map(([key, value]) => (
                  <span key={key}>
                    <b>{value}</b>
                    {key}
                  </span>
                ))}
              </div>
              <p>All verdicts included. No production autonomy.</p>
            </div>
          )}
        </aside>
      </div>
      {dialog && (
        <Modal
          title={dialog.kind === 'approve' ? 'Approve this exact proposal' : 'Reject this proposal'}
          close={close}
        >
          <p>{dialog.decision.title}</p>
          <p className="muted small">
            Decision {dialog.decision.decision_id} · policy {dialog.decision.policy_version}
          </p>
          {dialog.kind === 'approve' ? (
            <>
              <div className="approval-preview">
                {dialog.decision.legs
                  .filter((l) => l.before !== l.after)
                  .map((l) => (
                    <div key={l.budget_id}>
                      <span>{l.entity}</span>
                      <strong>
                        {money(l.before)} → {money(l.after)}
                      </strong>
                    </div>
                  ))}
                <div>
                  <span>Unallocated</span>
                  <strong>{money(dialog.decision.unallocated)}</strong>
                </div>
              </div>
              <p className="small">
                {dataMode === 'fixture'
                  ? 'This confirms a frontend fixture interaction. It sends no request to an ad platform.'
                  : 'The backend must revalidate this hash and all policy gates before sending budget changes. Platform execution is not an atomic transaction.'}
              </p>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={confirmed}
                  onChange={(event) => setConfirmed(event.target.checked)}
                />
                I have reviewed the evidence, budget changes and reserve.
              </label>
              <code className="hash-label">{dialog.decision.decision_hash}</code>
              <InlineError error={approve.error} />
              <div className="modal-actions">
                <button className="button secondary" onClick={close} disabled={approve.isPending}>
                  Cancel
                </button>
                <button
                  className="button primary"
                  disabled={!confirmed || approve.isPending}
                  onClick={() => approve.mutate(dialog.decision, { onSuccess: close })}
                >
                  {approve.isPending
                    ? 'Submitting approval…'
                    : dataMode === 'fixture'
                      ? 'Confirm fixture approval'
                      : 'Confirm approval & execute'}
                </button>
              </div>
            </>
          ) : (
            <>
              <label className="field">
                Reason for rejection
                <textarea
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                  placeholder="Explain what needs to change before approval"
                  rows={4}
                  maxLength={1000}
                />
              </label>
              <InlineError error={reject.error} />
              <div className="modal-actions">
                <button className="button secondary" onClick={close} disabled={reject.isPending}>
                  Cancel
                </button>
                <button
                  className="button danger"
                  disabled={reason.trim().length < 5 || reject.isPending}
                  onClick={() =>
                    reject.mutate({ d: dialog.decision, reason }, { onSuccess: close })
                  }
                >
                  {reject.isPending ? 'Rejecting…' : 'Confirm rejection'}
                </button>
              </div>
            </>
          )}
        </Modal>
      )}
    </>
  );
}
/** Why an outcome left the optimism correction factor unchanged, when the API gives no note. */
function calibrationSkipReason(outcome: Outcome): string {
  if (outcome.class !== 'OPTIMIZATION')
    return 'Safety and operational outcomes do not change response-curve calibration.';
  if (outcome.verdict === 'INCONCLUSIVE')
    return 'Inconclusive outcomes are counted separately and do not change the optimism correction factor.';
  return 'This outcome did not change the optimism correction factor.';
}

function PackageIcon() {
  return (
    <svg
      width="21"
      height="21"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      aria-hidden="true"
    >
      <path d="m3 7 9-4 9 4v10l-9 4-9-4V7Zm0 0 9 4 9-4M12 11v10M7 5l9 4" />
    </svg>
  );
}
