import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { management } from '../api/management';
import type { Decision } from '../api/contracts';
import { Badge, Disclosure, ErrorState, Loading, SectionTitle } from './ui';
import { dateTime } from '../lib/format';
export function ReplayArchive({ decision: d }: { decision: Decision }) {
  const query = useQuery({
    queryKey: ['replay-archive', d.decision_id, d.decision_hash],
    queryFn: () => management.archive(d),
  });
  if (query.isPending) return <Loading label="Loading archived replay manifest" />;
  if (query.error) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  const r = query.data!;
  return (
    <div className="replay-archive">
      <SectionTitle title="Archived evidence & environment">
        <Badge tone={r.status === 'AVAILABLE' ? 'accent' : 'warning'}>
          {r.status.toLowerCase()}
        </Badge>
      </SectionTitle>
      <p className="workbench-copy">{r.note}</p>
      <Disclosure title="Snapshot and reproducibility identities">
        <dl className="identity-list">
          {[
            ['Snapshot', r.snapshot_id],
            ['Decision hash', r.decision_hash],
            ['Environment fingerprint', r.environment_fingerprint],
            ['Code version', r.code_sha],
            ['Dependency lock hash', r.lock_hash],
            ['Seed', r.seed?.toString()],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd className="break-all">{value ?? 'Not supplied'}</dd>
            </div>
          ))}
        </dl>
      </Disclosure>
      <ul className="connection-list">
        {r.artifacts.map((a) => (
          <li key={a.id}>
            <div className="ledger-heading">
              <strong>{a.label}</strong>
              <Badge tone={a.status === 'MISSING' ? 'warning' : 'neutral'}>
                {a.status.toLowerCase()}
              </Badge>
            </div>
            <p>
              {a.kind} · <span className="break-all">SHA-256: {a.hash ?? 'Not supplied'}</span>
            </p>
            {a.status === 'PRESENT' && a.href && (
              <Link className="text-link" to={a.href}>
                Inspect {a.label}
              </Link>
            )}
          </li>
        ))}
      </ul>
      {r.steps.length > 0 ? (
        <>
          <h3 className="workbench-title">Full archived processing trace</h3>
          <ol className="ledger-list">
            {r.steps.map((s) => {
              const a = r.artifacts.find((a) => a.id === s.artifact_id);
              return (
                <li key={s.id}>
                  <div className="ledger-heading">
                    <strong>{s.label}</strong>
                    <time>{dateTime(s.at)}</time>
                  </div>
                  <p>{s.detail}</p>
                  {a?.status === 'PRESENT' && a.href ? (
                    <Link className="text-link" to={a.href}>
                      Inspect {a.label}
                    </Link>
                  ) : (
                    <p className="caption">
                      {a ? 'Referenced artifact is unavailable.' : 'No artifact link supplied.'}
                    </p>
                  )}
                </li>
              );
            })}
          </ol>
        </>
      ) : (
        <p className="notice">
          No full archived processing trace has been supplied. The event history above shows only
          captured lifecycle events, not a reconstructed engine trace.
        </p>
      )}
      <p className="caption">
        Artifact availability is not replay verification. Use the hash check below to inspect a
        backend replay result.
      </p>
    </div>
  );
}
