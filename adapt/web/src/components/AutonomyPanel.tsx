import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { policy } from '../api/policy';
import { dataMode } from '../api/client';
import { type ChannelPolicy, type Mode, type Policy } from '../api/policy-contracts';
import { useAction } from '../hooks/workspace';
import {
  Badge,
  Disclosure,
  Empty,
  ErrorState,
  InlineError,
  Loading,
  Modal,
  SectionTitle,
} from './ui';
import { dateTime, money, percent } from '../lib/format';
export const modeLabels: Record<Mode, string> = {
  OBSERVE: 'Observe',
  APPROVE: 'Approve',
  SIMULATION_AUTONOMOUS: 'Simulation autonomous',
  PRODUCTION_AUTONOMOUS: 'Production autonomous',
};
export function AutonomyPanel() {
  const query = useQuery({ queryKey: ['execution-policy'], queryFn: policy.current });
  const [channel, setChannel] = useState('Meta');
  const c = query.data?.channels.find((c) => c.channel === channel) || query.data?.channels[0];
  return (
    <>
      <section className="panel">
        <SectionTitle title="Execution policy & autonomy readiness">
          <Badge>CHANNEL-SPECIFIC</Badge>
        </SectionTitle>
        <p className="workbench-copy">
          Observe records recommendations without execution. Approve requires a reviewed decision.
          Autonomous execution belongs to the backend and still requires every candidate policy gate
          to pass.
        </p>
        {query.isPending ? (
          <Loading label="Loading channel policy" />
        ) : query.error ? (
          <ErrorState error={query.error} retry={() => void query.refetch()} />
        ) : (
          <>
            <p className="notice">{query.data!.note}</p>
            <label className="field compact-field">
              Policy channel
              <select value={c!.channel} onChange={(e) => setChannel(e.target.value)}>
                {query.data!.channels.map((c) => (
                  <option key={c.channel}>{c.channel}</option>
                ))}
              </select>
            </label>
            <ChannelPolicyView
              key={`${query.data!.revision}:${c!.channel}`}
              current={query.data!}
              channel={c!}
            />
          </>
        )}
      </section>
      <ShadowLog />
    </>
  );
}
function ChannelPolicyView({
  current: p,
  channel: c,
}: {
  current: Policy;
  channel: ChannelPolicy;
}) {
  const [mode, setMode] = useState<Mode>(c.mode),
    [confirm, setConfirm] = useState(false),
    [reason, setReason] = useState(''),
    [reviewed, setReviewed] = useState(false);
  const change = useAction(() => policy.change(p, c, mode, reason));
  return (
    <>
      <div className="ledger-heading">
        <h3>{c.channel} policy</h3>
        <div className="badge-row">
          <Badge tone="accent">{modeLabels[c.mode]}</Badge>
          <Badge>
            {c.execution_mode}
            {c.test_account ? ' · test account' : ''}
          </Badge>
        </div>
      </div>
      <p className="workbench-copy">{c.note}</p>
      {c.test_account && !c.serves_ads && (
        <p className="notice">
          This test account does not serve ads. Test-account budget read-backs cannot provide
          measured real advertising outcomes or qualify production autonomy.
        </p>
      )}
      <div className="readiness-grid">
        <Readiness title="Simulation readiness" readiness={c.simulation} production={false} />
        <Readiness title="Production readiness" readiness={c.production} production />
      </div>
      <Disclosure title="Policy version & request boundaries">
        <p className="break-all">
          Policy {p.policy_version} · revision {p.revision}
        </p>
        <p>
          Readiness is supplied by the backend. Eligibility is not an instruction to execute.
          Missing, failed or inconclusive evidence cannot authorize a mode request. The server must
          revalidate permissions, evidence, tracking, execution health and the current policy
          revision.
        </p>
      </Disclosure>
      <label className="field compact-field">
        Requested execution mode
        <select
          value={mode}
          disabled={change.isPending}
          onChange={(e) => {
            setMode(e.target.value as Mode);
            change.reset();
          }}
        >
          {Object.entries(modeLabels).map(([m, label]) => (
            <option
              value={m}
              key={m}
              disabled={m !== c.mode && !c.allowed_modes.includes(m as Mode)}
            >
              {label}
              {m !== c.mode && !c.allowed_modes.includes(m as Mode) ? ' · unavailable' : ''}
            </option>
          ))}
        </select>
      </label>
      <button
        className="button secondary"
        disabled={
          dataMode !== 'api' ||
          change.isPending ||
          mode === c.mode ||
          !c.allowed_modes.includes(mode)
        }
        onClick={() => {
          setConfirm(true);
          setReason('');
          setReviewed(false);
          change.reset();
        }}
      >
        Review mode change
      </button>
      {dataMode === 'fixture' && (
        <p className="caption">
          Mode changes and autonomous execution require a connected policy engine. Fixture outcomes
          do not qualify readiness.
        </p>
      )}
      {confirm && (
        <Modal
          title={`Change ${c.channel} execution mode`}
          close={() => {
            if (!change.isPending) setConfirm(false);
          }}
        >
          <p className="modal-description">
            Request {modeLabels[mode]} instead of {modeLabels[c.mode]} for {c.channel}.{' '}
            {mode === 'SIMULATION_AUTONOMOUS'
              ? 'The backend may execute future policy-passing changes on this mock channel without per-decision approval.'
              : mode === 'PRODUCTION_AUTONOMOUS'
                ? 'The backend may execute future policy-passing changes on a serving live ad account without per-decision approval.'
                : mode === 'OBSERVE'
                  ? 'The backend records recommendations and shadow forecasts without execution.'
                  : 'Future budget changes require human approval.'}{' '}
            No budget execution starts from this confirmation.
          </p>
          <p className="hash-label">
            Bound to {p.policy_version} · revision {p.revision}
          </p>
          <label className="field">
            Mode change reason
            <textarea
              rows={3}
              value={reason}
              maxLength={1000}
              disabled={change.isPending}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={reviewed}
              disabled={change.isPending}
              onChange={(e) => setReviewed(e.target.checked)}
            />{' '}
            I reviewed the readiness evidence and future execution behavior.
          </label>
          <InlineError error={change.error} />
          <div className="modal-actions">
            <button
              className="button secondary"
              disabled={change.isPending}
              onClick={() => setConfirm(false)}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={change.isPending || !reviewed || reason.trim().length < 10}
              onClick={() => change.mutate(undefined, { onSuccess: () => setConfirm(false) })}
            >
              {change.isPending ? 'Requesting…' : 'Confirm mode request'}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
function Readiness({
  title,
  readiness: r,
  production,
}: {
  title: string;
  readiness: ChannelPolicy['simulation'];
  production: boolean;
}) {
  return (
    <section className="readiness-section" aria-label={title}>
      <div className="ledger-heading">
        <h3>{title}</h3>
        <Badge tone={r.eligible ? 'success' : 'warning'}>
          {r.eligible ? 'Eligible' : 'Not eligible'}
        </Badge>
      </div>
      <dl className="constraint-list">
        <div>
          <dt>Executed {production ? 'real' : 'simulated'} decisions</dt>
          <dd>{r.executed_decisions} / 10 minimum</dd>
        </div>
        <div>
          <dt>Measured {production ? 'real' : 'simulated'} outcomes</dt>
          <dd>
            {r.measured_outcomes} / {production ? '30' : '10'} minimum
          </dd>
        </div>
        {!production && (
          <div>
            <dt>Independent warm-up worlds</dt>
            <dd>{r.independent_worlds} / 3 minimum</dd>
          </div>
        )}
        <div>
          <dt>Wilson 95% lower bound</dt>
          <dd>{r.wilson_lower === null ? 'Unavailable' : percent(r.wilson_lower)} · 60% minimum</dd>
        </div>
        <div>
          <dt>Held-out reliability</dt>
          <dd>{r.reliability.replaceAll('_', ' ')}</dd>
        </div>
        <div>
          <dt>Guardrail violations</dt>
          <dd>{r.guardrail_violations}</dd>
        </div>
      </dl>
      <p className="caption">{r.note}</p>
      {r.checks.length ? (
        <ul className="readiness-checks">
          {r.checks.map((check) => (
            <li key={check.id}>
              <div className="ledger-heading">
                <strong>{check.label}</strong>
                <Badge
                  tone={
                    check.passed === true
                      ? 'success'
                      : check.passed === false
                        ? 'danger'
                        : 'warning'
                  }
                >
                  {check.passed === null ? 'Unknown' : check.passed ? 'Pass' : 'Fail'}
                </Badge>
              </div>
              <p>{check.detail}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="notice">No qualification checks supplied.</p>
      )}
    </section>
  );
}
function ShadowLog() {
  const query = useQuery({ queryKey: ['shadow-log'], queryFn: policy.shadows });
  const [channel, setChannel] = useState('ALL'),
    [world, setWorld] = useState('ALL');
  const rows =
    query.data?.records.filter(
      (r) => (channel === 'ALL' || r.channel === channel) && (world === 'ALL' || r.world === world),
    ) || [];
  return (
    <section className="panel">
      <SectionTitle title="Observe-mode shadow decisions">
        <Badge>FORECASTS ONLY</Badge>
      </SectionTitle>
      <p className="workbench-copy">
        Unexecuted recommendations have no measured causal outcome. Shadow forecasts never count as
        real outcomes or production autonomy evidence.
      </p>
      {query.isPending ? (
        <Loading label="Loading shadow decisions" />
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : (
        <>
          <p className="notice">{query.data!.note}</p>
          {query.data!.records.length > 0 && (
            <div className="filter-bar report-filters">
              <label className="field">
                Shadow channel
                <select value={channel} onChange={(e) => setChannel(e.target.value)}>
                  <option value="ALL">All channels</option>
                  {[...new Set(query.data!.records.map((r) => r.channel))].map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                Shadow world
                <select value={world} onChange={(e) => setWorld(e.target.value)}>
                  <option value="ALL">All worlds</option>
                  <option value="SIMULATED">Simulated</option>
                  <option value="REAL">Real account context</option>
                </select>
              </label>
            </div>
          )}
          {!rows.length ? (
            <Empty title="No recorded shadow decisions">
              {query.data!.status === 'NOT_AVAILABLE'
                ? 'The backend Observe-mode log has not been supplied.'
                : 'No records match these filters.'}
            </Empty>
          ) : (
            <ol className="ledger-list">
              {rows.map((r) => (
                <li key={r.id}>
                  <div className="ledger-heading">
                    <Link
                      className="text-link"
                      to={`/decisions/${encodeURIComponent(r.decision_id)}`}
                    >
                      {r.decision_id}
                    </Link>
                    <time>{dateTime(r.at)}</time>
                  </div>
                  <div className="badge-row">
                    <Badge>{r.channel}</Badge>
                    <Badge>{r.world === 'REAL' ? 'Real account context' : 'Simulated'}</Badge>
                    <Badge>Not executed</Badge>
                  </div>
                  <p>
                    {r.expected
                      ? `Forecast median ΔCAA ${money(r.expected.p50)}; P10–P90 ${money(r.expected.p10)} to ${money(r.expected.p90)}.`
                      : 'Forecast unavailable.'}
                  </p>
                  <p>{r.note}</p>
                  <p className="caption">
                    Reported guardrail breaches:{' '}
                    {r.guardrail_breaches.length ? r.guardrail_breaches.join(', ') : 'none'}. No
                    observed outcome is asserted.
                  </p>
                  <Disclosure title={`Shadow identity ${r.id}`}>
                    <p className="break-all">Decision hash: {r.decision_hash}</p>
                    <p>
                      Method: forecast only. World label describes account context, not an observed
                      result.
                    </p>
                  </Disclosure>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}
