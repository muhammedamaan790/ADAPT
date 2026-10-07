import { useQuery } from '@tanstack/react-query';
import { completion } from '../api/completion';
import { dataMode } from '../api/client';
import { Badge, Disclosure, Empty, ErrorState, Loading, PolicyCheck, SectionTitle } from './ui';
import { dateTime, percent } from '../lib/format';
export function SourceChecks() {
  const query = useQuery({ queryKey: ['source-checks'], queryFn: completion.sourceChecks });
  return (
    <section className="panel">
      <SectionTitle title="Source quality checks" />
      {query.isPending ? (
        <Loading label="Loading detailed source health" />
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : !query.data?.length ? (
        <Empty title="No detailed source checks supplied">
          {dataMode === 'fixture'
            ? 'The fixture overview does not provide canonical source-quality checks.'
            : 'Sync connectors and build canonical source health, then refresh this view.'}
        </Empty>
      ) : (
        <ul className="connection-list">
          {query.data.map((s) => (
            <li key={s.id}>
              <div className="ledger-heading">
                <strong>{s.name}</strong>
                <Badge>{s.provenance}</Badge>
              </div>
              <p className="caption">
                As of {dateTime(s.as_of)} · newest day {s.newest_date ?? 'Unavailable'} · age{' '}
                {s.age_hours === null ? 'Unavailable' : `${s.age_hours.toFixed(1)} h`}
              </p>
              <dl className="fact-grid">
                <div>
                  <dt>Freshness</dt>
                  <dd>{percent(s.freshness_score)}</dd>
                </div>
                <div>
                  <dt>Completeness</dt>
                  <dd>{percent(s.completeness)}</dd>
                </div>
                <div>
                  <dt>Consistency</dt>
                  <dd>{percent(s.consistency)}</dd>
                </div>
              </dl>
              {s.hard_failures.length > 0 && (
                <p className="notice warning">Hard failures: {s.hard_failures.join(', ')}</p>
              )}
              <Disclosure title={`Inspect ${s.name} checks`}>
                {s.checks.length ? (
                  s.checks.map((c) => (
                    <PolicyCheck
                      key={c.id}
                      id={c.id}
                      label={c.id.replaceAll('_', ' ')}
                      passed={c.passed}
                      detail={c.detail}
                    />
                  ))
                ) : (
                  <p>No individual checks reported.</p>
                )}
              </Disclosure>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
