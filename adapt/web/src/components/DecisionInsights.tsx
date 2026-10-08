import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { insights } from '../api/insights';
import { api, dataMode } from '../api/client';
import { stage2 } from '../api/stage2';
import type { Decision } from '../api/contracts';
import { useAction } from '../hooks/workspace';
import { Badge, Disclosure, ErrorState, InlineError, Loading, Modal, SectionTitle } from './ui';
import { dateTime, money, percent, signedMoney } from '../lib/format';
import { downloadText } from '../lib/csv';
import { ReplayArchive } from './ReplayArchive';

export function DecisionInsights({ decision: d }: { decision: Decision }) {
  const navigate = useNavigate();
  const [view, setView] = useState<'comparison' | 'replay' | null>(null);
  const [choice, setChoice] = useState<string | null>(null);
  const comparison = useQuery({
    queryKey: ['comparison', d.decision_id, d.decision_hash],
    queryFn: () => insights.comparison(d),
    enabled: view === 'comparison',
    retry: false,
  });
  const timeline = useQuery({
    queryKey: ['timeline', d.decision_id],
    queryFn: () => insights.timeline(d.decision_id),
    enabled: view === 'replay',
    refetchInterval: view === 'replay' ? 5000 : false,
  });
  const replay = useAction(() => insights.replay(d));
  const snapshot = useAction(async () => {
    const s = await insights.snapshot(d.decision_id);
    downloadText(`adapt-${d.decision_id}-snapshot.json`, JSON.stringify(s, null, 2));
  });
  const revision = useAction(async (id: string) => {
    const a = comparison.data?.alternatives.find((a) => a.id === id);
    if (!a) throw new Error('Alternative no longer available.');
    // the backend re-solves under that risk preference: a new decision with its own snapshot and hash
    if (dataMode === 'api') return stage2.chooseAlternative(d, a.id);
    return api.modify({
      decision_id: d.decision_id,
      decision_hash: d.decision_hash,
      policy_version: d.policy_version,
      objective: d.objective,
      legs: a.legs.map((l) => ({ budget_id: l.budget_id, after: l.after })),
    });
  });
  return (
    <section className="panel">
      <SectionTitle title="Compare & inspect history" />
      <p className="section-description">
        Compare model estimates separately from realized outcomes. Replay verification requires
        archived engine artifacts.
      </p>
      <div className="workbench-actions">
        <button
          className="button secondary"
          aria-pressed={view === 'comparison'}
          onClick={() => setView(view === 'comparison' ? null : 'comparison')}
        >
          Comparison & sensitivity
        </button>
        <button
          className="button secondary"
          aria-pressed={view === 'replay'}
          onClick={() => setView(view === 'replay' ? null : 'replay')}
        >
          Decision replay timeline
        </button>
        <button
          className="button secondary"
          disabled={snapshot.isPending}
          onClick={() => snapshot.mutate()}
        >
          Download snapshot
        </button>
      </div>
      <InlineError error={snapshot.error} />
      {view === 'comparison' &&
        (comparison.isPending ? (
          <Loading label="Loading comparison" />
        ) : comparison.error ? (
          <ErrorState error={comparison.error} retry={() => void comparison.refetch()} />
        ) : (
          <>
            <p className="notice">{comparison.data!.note}</p>
            {comparison.data!.status === 'AVAILABLE' && (
              <>
                <h3 className="workbench-title">Naive vs ADAPT · model estimates</h3>
                <div className="comparison-rows">
                  {comparison.data!.strategies.map((s) => (
                    <article key={s.name}>
                      <div className="ledger-heading">
                        <h3>{s.name}</h3>
                        <Badge tone="accent">
                          {dataMode === 'fixture' ? 'RECORDED EXAMPLE' : 'MODEL ESTIMATE'}
                        </Badge>
                      </div>
                      <dl className="fact-grid">
                        <div>
                          <dt>Allocated / day</dt>
                          <dd>{money(s.allocated)}</dd>
                        </div>
                        <div>
                          <dt>Median ΔCAA</dt>
                          <dd>{signedMoney(s.estimate.p50)}</dd>
                        </div>
                        <div>
                          <dt>Model P(loss)</dt>
                          <dd>{percent(s.estimate.prob_loss)}</dd>
                        </div>
                      </dl>
                      <p className="workbench-copy">{s.reason}</p>
                    </article>
                  ))}
                </div>
                <h3 className="workbench-title">Sensitivity alternatives</h3>
                {comparison.data!.alternatives.map((a) => (
                  <article className="alternative-row" key={a.id}>
                    <div className="ledger-heading">
                      <h3>{a.name}</h3>
                      <Badge>Same input envelope</Badge>
                    </div>
                    <p className="workbench-copy">{a.reason}</p>
                    <dl className="fact-grid">
                      <div>
                        <dt>Median ΔCAA</dt>
                        <dd>{signedMoney(a.estimate.p50)}</dd>
                      </div>
                      <div>
                        <dt>P10–P90</dt>
                        <dd>
                          {money(a.estimate.p10)}–{money(a.estimate.p90)}
                        </dd>
                      </div>
                      <div>
                        <dt>Model P(loss)</dt>
                        <dd>{percent(a.estimate.prob_loss)}</dd>
                      </div>
                    </dl>
                    <button
                      className="button secondary"
                      disabled={d.status !== 'PENDING_APPROVAL' || a.checks.some((c) => !c.passed)}
                      onClick={() => {
                        revision.reset();
                        setChoice(a.id);
                      }}
                    >
                      Revise to {a.name.toLowerCase()} allocation
                    </button>
                  </article>
                ))}
                <Disclosure title="Confidence interpretation">
                  {comparison.data!.confidence.map((c) => (
                    <p key={c.label}>
                      {c.label}: {percent(c.value)}. {c.meaning}
                    </p>
                  ))}
                  <p>Model-derived loss risk is not a calibrated probability.</p>
                </Disclosure>
              </>
            )}
          </>
        ))}
      {view === 'replay' && (
        <>
          {timeline.isPending ? (
            <Loading label="Loading decision history" />
          ) : timeline.error ? (
            <ErrorState error={timeline.error} retry={() => void timeline.refetch()} />
          ) : (
            <ol className="ledger-list">
              {timeline.data?.map((t) => (
                <li key={t.id}>
                  <div className="ledger-heading">
                    <strong>{t.label}</strong>
                    <time>{dateTime(t.at)}</time>
                  </div>
                  <p className="break-all">{t.detail}</p>
                  <Link className="text-link" to={t.href}>
                    Inspect related artifact
                  </Link>
                </li>
              ))}
            </ol>
          )}
          <ReplayArchive decision={d} />
          <button
            className="button secondary"
            disabled={replay.isPending}
            onClick={() => replay.mutate()}
          >
            {replay.isPending ? 'Checking…' : 'Check replay availability'}
          </button>
          <InlineError error={replay.error} />
          {replay.data && (
            <p className="notice" role="status">
              <strong>{replay.data.status.replaceAll('_', ' ')}</strong> · {replay.data.message}
              <br />
              <code className="break-all">
                Expected hash: {replay.data.expected_hash} · Actual:{' '}
                {replay.data.actual_hash || 'unavailable'}
              </code>
            </p>
          )}
        </>
      )}
      {choice && (
        <Modal
          title="Create sensitivity revision"
          close={() => {
            if (!revision.isPending) setChoice(null);
          }}
        >
          <p className="modal-description">
            This creates a separate proposal and supersedes the pending decision.{' '}
            {dataMode === 'fixture'
              ? 'The fixture revision becomes a non-executable draft awaiting backend revaluation.'
              : 'The backend creates a new decision under this risk preference (its own snapshot and hash); it needs its own approval.'}{' '}
            No budget execution starts here.
          </p>
          <InlineError error={revision.error} />
          <div className="modal-actions">
            <button
              className="button secondary"
              disabled={revision.isPending}
              onClick={() => setChoice(null)}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={revision.isPending}
              onClick={() =>
                revision.mutate(choice, {
                  onSuccess: (r) => {
                    setChoice(null);
                    navigate(`/decisions/${r.decision_id}`);
                  },
                })
              }
            >
              Confirm sensitivity revision
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
