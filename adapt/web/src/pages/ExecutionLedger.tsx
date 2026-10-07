import { useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AutonomyPanel } from '../components/AutonomyPanel';
import { WorkspaceSettings } from '../components/WorkspaceSettings';
import { RefreshCw, ShieldCheck, ArrowRight } from 'lucide-react';
import { api, dataMode } from '../api/client';
import type { Execution, Decision } from '../api/contracts';
import type { RecoveryAction, RecoveryInput } from '../api/workbench-contracts';
import { useAction, useDecisions, useExecutions, useLedger, useOutcomes } from '../hooks/workspace';
import {
  Badge,
  Disclosure,
  Empty,
  ErrorState,
  InlineError,
  Loading,
  Modal,
  SectionTitle,
  Status,
} from '../components/ui';
import { dateTime, humanStatus, money } from '../lib/format';

const actionLabels: Record<RecoveryAction, string> = {
  verify: 'Verify platform state',
  retry: 'Retry failed legs',
  rollback: 'Restore prior settings',
  reconcile: 'Record reconciliation',
};

export function ExecutionLedger() {
  const [params, setParams] = useSearchParams();
  const view =
    params.get('section') === 'policy'
      ? 'policy'
      : params.get('section') === 'settings'
        ? 'settings'
        : 'records';
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Execution & Ledger</h1>
          <p>
            Verify budget changes, inspect the action trail and review channel execution policy.
          </p>
        </div>
      </div>
      <div className="report-tabs" role="group" aria-label="Execution views">
        <button
          className="button secondary"
          aria-pressed={view === 'records'}
          onClick={() => setParams({})}
        >
          Execution records
        </button>
        <button
          className="button secondary"
          aria-pressed={view === 'policy'}
          onClick={() => setParams({ section: 'policy' })}
        >
          Policy & readiness
        </button>
        <button
          className="button secondary"
          aria-pressed={view === 'settings'}
          onClick={() => setParams({ section: 'settings' })}
        >
          Workspace settings
        </button>
      </div>
      {view === 'policy' ? (
        <AutonomyPanel />
      ) : view === 'settings' ? (
        <WorkspaceSettings />
      ) : (
        <ExecutionRecords />
      )}
    </>
  );
}
function ExecutionRecords() {
  const executions = useExecutions();
  const decisions = useDecisions();
  const ledger = useLedger();
  const outcomes = useOutcomes();
  const [selected, setSelected] = useState<string | null>(null);
  const [filter, setFilter] = useState('ALL');
  if (executions.isPending || decisions.isPending)
    return <Loading label="Loading execution records" />;
  if (executions.error || decisions.error)
    return (
      <ErrorState
        error={executions.error || decisions.error!}
        retry={() => {
          void executions.refetch();
          void decisions.refetch();
        }}
      />
    );
  const records = executions.data || [];
  const current = records.find((e) => e.execution_id === selected) || records.at(-1);
  const decision = decisions.data?.find((d) => d.decision_id === current?.decision_id);
  const entries =
    ledger.data?.filter(
      (l) => l.execution_id === current?.execution_id && (filter === 'ALL' || l.action === filter),
    ) || [];
  return (
    <>
      <div className="workbench-actions">
        <button
          className="button secondary"
          aria-label="Refresh execution and ledger"
          onClick={() => {
            void executions.refetch();
            void ledger.refetch();
          }}
        >
          <RefreshCw size={15} /> Refresh
        </button>
      </div>
      {!current ? (
        <section className="panel">
          <Empty title="No execution has started">
            Approve an eligible decision to create an execution record. Reviewing or revising a
            proposal sends no budget changes.
          </Empty>
          <Link className="button secondary" to="/decisions">
            Review a decision <ArrowRight size={15} />
          </Link>
        </section>
      ) : (
        <>
          <label className="field execution-picker">
            Execution record
            <select value={current.execution_id} onChange={(e) => setSelected(e.target.value)}>
              {records.map((e) => (
                <option key={e.execution_id} value={e.execution_id}>
                  {e.decision_id} · {humanStatus(e.state)}
                </option>
              ))}
            </select>
          </label>
          {decision ? (
            <ExecutionDetail
              key={current.execution_id}
              execution={current}
              decision={decision}
              matured={!!outcomes.data?.some((o) => o.decision_id === decision.decision_id)}
              feedbackKnown={!outcomes.isPending && !outcomes.isError}
            />
          ) : (
            <section className="panel">
              <Empty title="Decision identity unavailable">
                Recovery controls require the decision hash. Refresh the decision records before
                taking action.
              </Empty>
            </section>
          )}
          <section className="panel">
            <SectionTitle title="Action ledger">
              <Badge>{entries.length} entries</Badge>
            </SectionTitle>
            <p className="section-description">
              Budget settings and read-backs, recorded separately. This ledger is not a transaction
              or spend report.
            </p>
            <label className="field ledger-filter">
              Action type
              <select value={filter} onChange={(e) => setFilter(e.target.value)}>
                <option value="ALL">All actions</option>
                {['SET_BUDGET', 'VERIFY', 'RESTORE_SETTINGS', 'RECONCILE'].map((a) => (
                  <option value={a} key={a}>
                    {humanStatus(a)}
                  </option>
                ))}
              </select>
            </label>
            {ledger.isPending ? (
              <Loading label="Loading ledger" />
            ) : ledger.error ? (
              <ErrorState error={ledger.error} retry={() => void ledger.refetch()} />
            ) : !entries.length ? (
              <Empty title="No matching ledger entries">
                Choose another action type or wait for an execution action.
              </Empty>
            ) : (
              <ol className="ledger-list">
                {[...entries].reverse().map((l) => (
                  <li key={l.ledger_id}>
                    <div className="ledger-heading">
                      <div className="badge-row">
                        <Badge tone={l.mode === 'LIVE' ? 'danger' : 'neutral'}>{l.mode}</Badge>
                        <strong>{humanStatus(l.action)}</strong>
                      </div>
                      <time>{dateTime(l.at)}</time>
                    </div>
                    <h3>
                      {l.entity} <span className="muted">· {l.platform}</span>
                    </h3>
                    <p className="ledger-values">
                      {money(l.before)} <ArrowRight size={14} /> {money(l.after)} / day
                    </p>
                    <p>{l.note}</p>
                    <Disclosure title="Audit identifiers">
                      <code className="break-all">
                        Request: {l.request_id}
                        <br />
                        Entry: {l.ledger_id}
                        <br />
                        Execution: {l.execution_id}
                        <br />
                        Budget unit: {l.budget_id}
                      </code>
                    </Disclosure>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </>
      )}
    </>
  );
}

function ExecutionDetail({
  execution: e,
  decision: d,
  matured,
  feedbackKnown,
}: {
  execution: Execution;
  decision: Decision;
  matured: boolean;
  feedbackKnown: boolean;
}) {
  const [dialog, setDialog] = useState<RecoveryAction | null>(null);
  const [reason, setReason] = useState('');
  const recover = useAction(api.recover);
  const fault = useAction(async (type: 'UNKNOWN' | 'FAILED') => {
    await api.fault(type);
  });
  const uncertainty = e.legs.some((l) =>
    ['UNKNOWN', 'CONFLICT', 'SENT'].includes(l.external_state),
  );
  const mirrorPending = e.legs.some((l) =>
    ['MIRROR_PENDING', 'MIRROR_FAILED'].includes(l.sim_sync_state),
  );
  const retryEligible =
    !uncertainty &&
    !mirrorPending &&
    ['PARTIAL', 'HUMAN_RESOLUTION_REQUIRED'].includes(e.state) &&
    e.legs.some((l) => l.external_state === 'FAILED');
  const restoreEligible =
    !uncertainty &&
    !mirrorPending &&
    ['SUCCEEDED', 'PARTIAL', 'ACCEPTED_PARTIAL'].includes(e.state);
  const reconcileEligible =
    !uncertainty &&
    !mirrorPending &&
    ['PARTIAL', 'HUMAN_RESOLUTION_REQUIRED', 'COMPENSATED'].includes(e.state);
  const pending = recover.isPending || fault.isPending;
  const open = (action: RecoveryAction) => {
    recover.reset();
    setReason('');
    setDialog(action);
  };
  const payload = (action: RecoveryAction): RecoveryInput => ({
    execution_id: e.execution_id,
    decision_hash: d.decision_hash,
    action,
    reason,
    ...(action === 'reconcile' ? { final_resolution: 'COMPENSATED' as const } : {}),
  });
  return (
    <>
      <section className="panel">
        <SectionTitle title="Execution state">
          <Status value={e.state} />
        </SectionTitle>
        <p className="section-description">{e.detail}</p>
        <Link className="text-link" to={`/decisions/${d.decision_id}`}>
          Review decision {d.decision_id} <ArrowRight size={14} />
        </Link>
        <div className="execution-budget-list">
          {e.legs.map((l) => (
            <article key={l.budget_id}>
              <div className="ledger-heading">
                <h3>{l.entity}</h3>
                <Badge tone={l.mode === 'LIVE' ? 'danger' : 'neutral'}>
                  {l.mode} · {l.platform}
                </Badge>
              </div>
              <dl className="fact-grid">
                <div>
                  <dt>Prior setting</dt>
                  <dd>{money(l.before)}</dd>
                </div>
                <div>
                  <dt>Requested setting</dt>
                  <dd>{money(l.after)}</dd>
                </div>
                <div>
                  <dt>Latest read-back</dt>
                  <dd>
                    {l.read_back_budget === undefined || l.read_back_budget === null
                      ? 'Unverified'
                      : money(l.read_back_budget)}
                  </dd>
                </div>
              </dl>
              <div className="badge-row">
                <span className="caption">Request state</span>
                <Status value={l.external_state} />
                <span className="caption">Simulation sync</span>
                <Status value={l.sim_sync_state} />
              </div>
              <code className="caption break-all">{l.budget_id}</code>
            </article>
          ))}
        </div>
        {(uncertainty || mirrorPending) && (
          <p className="notice warning" role="status">
            {uncertainty
              ? 'External state is uncertain. Verify before retrying or restoring settings.'
              : 'Simulation mirror is not confirmed. Resolve synchronization before advancing the world.'}
          </p>
        )}
        <div className="workbench-actions">
          <button
            className="button primary"
            disabled={pending}
            onClick={() => recover.mutate(payload('verify'))}
          >
            <ShieldCheck size={15} /> Verify platform state
          </button>
          <button
            className="button secondary"
            disabled={pending || !retryEligible}
            onClick={() => open('retry')}
          >
            Retry failed legs
          </button>
          <button
            className="button secondary"
            disabled={pending || !restoreEligible}
            onClick={() => open('rollback')}
          >
            Restore prior settings
          </button>
          <button
            className="button secondary"
            disabled={pending || !reconcileEligible}
            onClick={() => open('reconcile')}
          >
            Record reconciliation
          </button>
        </div>
        <InlineError error={dialog ? null : recover.error} />
        {recover.isSuccess && !dialog && (
          <p className="caption" role="status">
            Latest execution state verified or updated. Inspect the read-back and ledger above.
          </p>
        )}
        <p className="caption">
          Settings restoration changes future budgets. It cannot undo spend, exposure or outcomes
          already incurred. Reconciliation records a verified resolution; it does not restore
          settings.
        </p>
      </section>
      {dataMode === 'fixture' && (
        <section className="panel fixture-tools">
          <SectionTitle title="Recovery examples">
            <Badge tone="warning">FRONTEND FIXTURES</Badge>
          </SectionTitle>
          <p className="section-description">
            Inject a labelled UI fault into the latest execution, before its outcome matures. No ad
            account is connected.
          </p>
          <div className="workbench-actions">
            <button
              className="button secondary"
              disabled={
                pending ||
                matured ||
                !feedbackKnown ||
                !['EXECUTING', 'SUCCEEDED'].includes(e.state)
              }
              onClick={() => fault.mutate('UNKNOWN')}
            >
              Inject unknown result
            </button>
            <button
              className="button secondary"
              disabled={
                pending ||
                matured ||
                !feedbackKnown ||
                !['EXECUTING', 'SUCCEEDED'].includes(e.state)
              }
              onClick={() => fault.mutate('FAILED')}
            >
              Inject failed leg
            </button>
          </div>
          <InlineError error={fault.error} />
        </section>
      )}
      {dialog && (
        <Modal
          title={actionLabels[dialog]}
          close={() => {
            if (!pending) setDialog(null);
          }}
        >
          <p className="modal-description">
            {dialog === 'retry'
              ? 'Only verified failed legs are eligible. The request sets absolute budgets; uncertain requests must be verified first.'
              : dialog === 'rollback'
                ? 'Restore every known budget setting to its prior value. Previous spend and exposure remain incurred.'
                : 'Record COMPENSATED only after fresh read-backs confirm every prior setting. Restore settings first if needed. The backend owns resolution validation.'}
          </p>
          <label className="field">
            Recovery reason
            <textarea
              rows={3}
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Record why this action is appropriate"
            />
          </label>
          <p className="hash-label">Bound to decision hash: {d.decision_hash}</p>
          <InlineError error={recover.error} />
          <div className="modal-actions">
            <button className="button secondary" disabled={pending} onClick={() => setDialog(null)}>
              Cancel
            </button>
            <button
              className="button primary"
              disabled={pending || reason.trim().length < 5}
              onClick={() => recover.mutate(payload(dialog), { onSuccess: () => setDialog(null) })}
            >
              {pending ? 'Submitting…' : 'Confirm recovery'}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
