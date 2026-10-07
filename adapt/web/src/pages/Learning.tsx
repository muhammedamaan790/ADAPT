import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { insights } from '../api/insights';
import { Badge, Empty, ErrorState, Loading, SectionTitle } from '../components/ui';
import { dateTime, money } from '../lib/format';

export function Learning() {
  const query = useQuery({
    queryKey: ['learning'],
    queryFn: insights.learning,
    refetchInterval: 5000,
  });
  if (query.isPending) return <Loading label="Loading calibration and learning records" />;
  if (query.error) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  const d = query.data!;
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Learning</h1>
          <p>
            Track calibration changes, inspect prediction errors and separate feedback from
            evaluation.
          </p>
        </div>
        <Badge tone="accent">EVIDENCE OF FEEDBACK</Badge>
      </div>
      <div className="workbench-stats">
        <div>
          <span>Current calibration factor</span>
          <strong>{d.calibration.factor.toFixed(2)}×</strong>
        </div>
        <div>
          <span>Optimization samples</span>
          <strong>{d.accuracy.sample_count}</strong>
        </div>
        <div>
          <span>Mean absolute error</span>
          <strong>{d.accuracy.mae === null ? 'Unavailable' : money(d.accuracy.mae)}</strong>
        </div>
      </div>
      <p className="notice">
        {d.accuracy.note} A small sample does not establish accuracy or causal uplift.
      </p>
      <div className="insight-grid">
        <section className="panel">
          <SectionTitle title="Calibration history" />
          <p className="section-description">{d.calibration.note}</p>
          {!d.calibration.updates.length ? (
            <Empty title="No calibration changes yet">
              An eligible matured outcome will add its before/after factor here.
            </Empty>
          ) : (
            <ol className="ledger-list">
              {d.calibration.updates.map((u) => (
                <li key={u.outcome_id}>
                  <div className="ledger-heading">
                    <strong>
                      {u.before.toFixed(2)}× → {u.after.toFixed(2)}×
                    </strong>
                    <time>{dateTime(u.at)}</time>
                  </div>
                  <Link className="text-link" to={`/decisions/${u.decision_id}`}>
                    {u.decision_id}
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </section>
        <section className="panel">
          <SectionTitle title="Feedback eligibility" />
          {!d.feedback.length ? (
            <Empty title="No feedback records">
              Outcome records will show why each decision was included or excluded.
            </Empty>
          ) : (
            <ul className="connection-list">
              {d.feedback.map((f) => (
                <li key={f.outcome_id}>
                  <div className="ledger-heading">
                    <Link className="text-link" to={`/decisions/${f.decision_id}`}>
                      {f.decision_id}
                    </Link>
                    <Badge tone={f.eligible ? 'success' : 'neutral'}>
                      {f.eligible ? 'Applied' : 'Excluded'}
                    </Badge>
                  </div>
                  <p>{f.reason}</p>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <section className="panel">
        <SectionTitle title="Held-out strategy evaluation">
          <Badge tone={d.uplift.status === 'AVAILABLE' ? 'accent' : 'warning'}>
            {d.uplift.status === 'AVAILABLE' ? 'Evaluation report' : 'Not available'}
          </Badge>
        </SectionTitle>
        <p className="workbench-copy">{d.uplift.note}</p>
        {d.uplift.rows.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Strategy</th>
                  <th>Realized CAA</th>
                  <th>Actual spend</th>
                  <th>Constraint breaches</th>
                </tr>
              </thead>
              <tbody>
                {d.uplift.rows.map((r) => (
                  <tr key={r.strategy}>
                    <td>{r.strategy}</td>
                    <td>{money(r.realized_caa)}</td>
                    <td>{money(r.spend)}</td>
                    <td>{r.constraint_breaches}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="panel">
        <SectionTitle title="Model registry" />
        <ul className="connection-list">
          {d.models.map((m) => (
            <li key={`${m.name}:${m.version}`}>
              <div className="ledger-heading">
                <strong>{m.name}</strong>
                <Badge tone={m.status === 'CHAMPION' ? 'success' : 'neutral'}>
                  {m.status.replaceAll('_', ' ')}
                </Badge>
              </div>
              <p>
                Version {m.version} · trained{' '}
                {m.trained_at ? dateTime(m.trained_at) : 'not reported'}
              </p>
              <p>{m.note}</p>
            </li>
          ))}
        </ul>
        <p className="caption">
          Model promotion and rollback are backend-owned. Missing model metadata stays unavailable.
        </p>
      </section>
    </>
  );
}
