import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { management } from '../api/management';
import { dataMode } from '../api/client';
import type { ModelDetail } from '../api/management-contracts';
import { useAction } from '../hooks/workspace';
import {
  Badge,
  Disclosure,
  ErrorState,
  InlineError,
  Loading,
  Modal,
  PolicyCheck,
  SectionTitle,
} from './ui';
import { dateTime } from '../lib/format';
export function ModelManagement() {
  const models = useQuery({ queryKey: ['model-management-list'], queryFn: management.models });
  const [selected, setSelected] = useState<string | null>(null);
  const rows = models.data || [];
  const current =
    rows.find((m) => `${m.name}:${m.version}` === selected) ||
    rows.find((m) => m.status !== 'NOT_AVAILABLE');
  return (
    <section className="panel">
      <SectionTitle title="Model registry & controls" />
      <p className="workbench-copy">
        Inspect family baselines, champion comparisons and coverage gates before a registry change.
        Model changes do not revalue or alter an already snapshotted decision.
      </p>
      {models.isPending ? (
        <Loading label="Loading model registry" />
      ) : models.error ? (
        <ErrorState error={models.error} retry={() => void models.refetch()} />
      ) : (
        <>
          <ul className="connection-list">
            {rows.map((m) => (
              <li key={`${m.name}:${m.version}`}>
                <div className="ledger-heading">
                  <strong>
                    {m.name} · {m.version}
                  </strong>
                  <Badge>{m.status.replaceAll('_', ' ')}</Badge>
                </div>
                <p>{m.note}</p>
                <button
                  className="button secondary"
                  disabled={m.status === 'NOT_AVAILABLE'}
                  onClick={() => setSelected(`${m.name}:${m.version}`)}
                >
                  Inspect {m.name} {m.version}
                </button>
              </li>
            ))}
          </ul>
          {current ? (
            <ModelVersion
              key={`${current.name}:${current.version}`}
              name={current.name}
              version={current.version}
            />
          ) : (
            <p className="notice">
              Promotion and rollback are unavailable until the backend supplies trained artifacts,
              version identities and validated gates. No registry changes are simulated.
            </p>
          )}
        </>
      )}
    </section>
  );
}
function ModelVersion({ name, version }: { name: string; version: string }) {
  const query = useQuery({
    queryKey: ['model-version', name, version],
    queryFn: () => management.model(name, version),
  });
  if (query.isPending) return <Loading label="Loading model artifact and gates" />;
  if (query.error) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  return <ModelDetailView key={query.data!.registry_revision} detail={query.data!} />;
}
function ModelDetailView({ detail: d }: { detail: ModelDetail }) {
  const [choice, setChoice] = useState<'PROMOTE' | 'ROLLBACK' | null>(null);
  const [reason, setReason] = useState('');
  const [reviewed, setReviewed] = useState(false);
  const action = useAction((a: 'PROMOTE' | 'ROLLBACK') => management.modelAction(d, a, reason));
  return (
    <div className="model-detail">
      <div className="ledger-heading">
        <h3>
          {d.name} · {d.version}
        </h3>
        <Badge tone="accent">{d.role}</Badge>
      </div>
      <p className="workbench-copy">{d.note}</p>
      <dl className="fact-grid">
        <div>
          <dt>Trained</dt>
          <dd>{d.trained_at ? dateTime(d.trained_at) : 'Not reported'}</dd>
        </div>
        <div>
          <dt>Previous champion</dt>
          <dd>{d.rollback_version || 'Unavailable'}</dd>
        </div>
        <div>
          <dt>Registry revision</dt>
          <dd className="break-all">{d.registry_revision}</dd>
        </div>
      </dl>
      <Disclosure title="Artifact identity">
        <p className="break-all">Artifact SHA-256: {d.artifact_hash || 'Unavailable'}</p>
        <p className="break-all">Training snapshot: {d.training_snapshot_hash || 'Unavailable'}</p>
        <p>{d.promotion_reason || 'No promotion reason recorded.'}</p>
      </Disclosure>
      {d.metrics.length > 0 && (
        <div className="table-scroll">
          <table>
            <caption>Backend validation metrics; candidate vs champion vs family baseline</caption>
            <thead>
              <tr>
                <th>Metric</th>
                <th>Candidate</th>
                <th>Champion</th>
                <th>Baseline</th>
              </tr>
            </thead>
            <tbody>
              {d.metrics.map((m) => (
                <tr key={m.label}>
                  <td>
                    {m.label} ({m.unit})
                  </td>
                  {[m.candidate, m.champion, m.baseline].map((v, i) => (
                    <td key={i}>
                      {v === null
                        ? 'Unavailable'
                        : v.toLocaleString('en-IN', { maximumFractionDigits: 4 })}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <h3 className="workbench-title">Promotion checks</h3>
      {d.checks.length ? (
        d.checks.map((c) => <PolicyCheck key={c.id} {...c} />)
      ) : (
        <p className="notice">No validation gates supplied; promotion is unavailable.</p>
      )}
      <div className="workbench-actions">
        {(['PROMOTE', 'ROLLBACK'] as const).map((a) => (
          <button
            key={a}
            className="button secondary"
            disabled={
              dataMode !== 'api' ||
              !d.allowed_actions.includes(a) ||
              action.isPending ||
              action.isSuccess
            }
            onClick={() => {
              setChoice(a);
              setReason('');
              setReviewed(false);
              action.reset();
            }}
          >
            {a === 'PROMOTE' ? 'Request candidate promotion' : 'Roll back champion'}
          </button>
        ))}
      </div>
      {action.data && (
        <p className="notice" role="status">
          {action.data.message}. Version {action.data.version} · registry{' '}
          {action.data.registry_revision}
        </p>
      )}
      {choice && (
        <Modal
          title={choice === 'PROMOTE' ? 'Request model promotion' : 'Roll back model champion'}
          close={() => {
            if (!action.isPending) setChoice(null);
          }}
        >
          <p className="modal-description">
            {choice === 'PROMOTE'
              ? 'The backend must rerun every family baseline, champion non-inferiority and coverage gate before promotion.'
              : 'Restore the recorded prior champion through the backend registry.'}{' '}
            This request is bound to {d.name}/{d.version} and registry revision{' '}
            {d.registry_revision}. Existing approved decisions retain their archived models.
          </p>
          <label className="field">
            Registry change reason
            <textarea
              rows={3}
              maxLength={1000}
              value={reason}
              disabled={action.isPending}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={reviewed}
              disabled={action.isPending}
              onChange={(e) => setReviewed(e.target.checked)}
            />{' '}
            I reviewed the artifacts and backend gates.
          </label>
          <InlineError error={action.error} />
          <div className="modal-actions">
            <button
              className="button secondary"
              disabled={action.isPending}
              onClick={() => setChoice(null)}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={!reviewed || reason.trim().length < 10 || action.isPending}
              onClick={() => action.mutate(choice, { onSuccess: () => setChoice(null) })}
            >
              {action.isPending ? 'Submitting…' : 'Confirm registry request'}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
