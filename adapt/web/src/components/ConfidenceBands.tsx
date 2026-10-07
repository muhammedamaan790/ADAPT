import { useQuery } from '@tanstack/react-query';
import { completion } from '../api/completion';
import { Badge, Empty, ErrorState, Loading, SectionTitle } from './ui';
import { dateTime, percent } from '../lib/format';

export function ConfidenceBands() {
  const query = useQuery({
    queryKey: ['confidence-qualification'],
    queryFn: completion.confidence,
  });
  return (
    <section className="panel">
      <SectionTitle title="Confidence-region evidence">
        <Badge>INFORMATIONAL</Badge>
      </SectionTitle>
      <p className="section-description">
        Observed success rates and supplied 95% Wilson intervals. Confidence scores are not success
        probabilities, and these bands do not authorize execution.
      </p>
      {query.isPending ? (
        <Loading label="Loading confidence regions" />
      ) : query.error ? (
        <ErrorState error={query.error} retry={() => void query.refetch()} />
      ) : (
        <>
          <p className="workbench-copy">{query.data!.note}</p>
          {!query.data!.pools.length ? (
            <Empty title="No qualification evidence supplied">
              Simulation and real outcomes require separate calibrated evaluation pools.
            </Empty>
          ) : (
            query.data!.pools.map((p, i) => (
              <div key={i} className="confidence-pool">
                <div className="badge-row">
                  <Badge>{p.world}</Badge>
                  <Badge>{p.scope.replace('_', ' ')}</Badge>
                  <span>
                    {p.model_version} · {dateTime(p.evaluated_at)}
                  </span>
                </div>
                <p>{p.outcome_definition}</p>
                <p className="caption">Worlds: {p.world_ids.join(', ')}</p>
                <div className="table-scroll confidence-results">
                  <table>
                    <caption className="sr-only">
                      {p.world} {p.scope} confidence region results
                    </caption>
                    <thead>
                      <tr>
                        <th>Confidence region</th>
                        <th>Successes / outcomes</th>
                        <th>Observed rate</th>
                        <th>95% interval</th>
                      </tr>
                    </thead>
                    <tbody>
                      {p.regions.map((r) => (
                        <tr key={r.name}>
                          <td>
                            {r.name} ·{' '}
                            {r.name === 'LOW'
                              ? '[0, 0.6)'
                              : r.name === 'MID'
                                ? '[0.6, 0.8)'
                                : '[0.8, 1]'}
                          </td>
                          <td>
                            {r.successes} / {r.total}
                          </td>
                          <td>{r.total ? percent(r.successes / r.total) : 'Not estimable'}</td>
                          <td>
                            {r.wilson_lower === null || r.wilson_upper === null
                              ? 'Not estimable'
                              : `${percent(r.wilson_lower)}–${percent(r.wilson_upper)}`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ))
          )}
        </>
      )}
    </section>
  );
}
