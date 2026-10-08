import { useQuery } from '@tanstack/react-query';
import { SourceChecks } from '../components/SourceChecks';
import { SavedImports } from '../components/SavedImports';
import { insights } from '../api/insights';
import { useOverview } from '../hooks/workspace';
import { Badge, Disclosure, ErrorState, Loading, SectionTitle, Status } from '../components/ui';
import { money, percent } from '../lib/format';

export function DataHub() {
  const health = useQuery({ queryKey: ['data-health'], queryFn: insights.dataHealth });
  const reconciliation = useQuery({
    queryKey: ['data-reconciliation'],
    queryFn: insights.reconciliation,
  });
  const overview = useOverview();
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Data Hub</h1>
          <p>
            Inspect every source's health, reconcile attribution and trace each metric to its
            inputs.
          </p>
        </div>
        <Badge>INR · ASIA/KOLKATA</Badge>
      </div>
      <section className="panel">
        <SectionTitle title="Source health" />
        {health.isPending ? (
          <Loading label="Loading source contracts" />
        ) : health.error ? (
          <ErrorState error={health.error} retry={() => void health.refetch()} />
        ) : (
          <>
            <p className="workbench-copy">{health.data!.note}</p>
            <ul className="connection-list">
              {health.data!.sources.map((s) => (
                <li key={s.id}>
                  <div className="ledger-heading">
                    <strong>{s.name}</strong>
                    <div className="badge-row">
                      <Badge>{s.provenance}</Badge>
                      <Status value={s.status} />
                    </div>
                  </div>
                  <p>
                    {s.kind} · {s.freshness} · quality {s.score.toFixed(0)}/100
                  </p>
                </li>
              ))}
            </ul>
            <p className="notice">
              SKU mapping coverage: {percent(health.data!.coverage)}.{' '}
              {health.data!.unmapped.length
                ? `Unmapped: ${health.data!.unmapped.join(', ')}`
                : 'No unmapped IDs reported.'}
            </p>
          </>
        )}
      </section>
      <SourceChecks />
      <section className="panel">
        <SectionTitle title="Attribution reconciliation" />
        {reconciliation.isPending ? (
          <Loading label="Loading reconciliation" />
        ) : reconciliation.error ? (
          <ErrorState error={reconciliation.error} retry={() => void reconciliation.refetch()} />
        ) : (
          <>
            <dl className="fact-grid">
              <div>
                <dt>Platform-attributed revenue</dt>
                <dd>{money(reconciliation.data!.platform_revenue)}</dd>
              </div>
              <div>
                <dt>Store net revenue</dt>
                <dd>{money(reconciliation.data!.store_revenue)}</dd>
              </div>
              <div>
                <dt>Attribution excess</dt>
                <dd>{money(reconciliation.data!.attribution_excess)}</dd>
              </div>
            </dl>
            <p className="workbench-copy">{reconciliation.data!.note}</p>
            {reconciliation.data!.window_start && (
              <p className="caption">
                Window: {reconciliation.data!.window_start} to {reconciliation.data!.window_end}
              </p>
            )}
            {reconciliation.data!.platforms?.length ? (
              <div
                className="table-scroll"
                tabIndex={0}
                role="region"
                aria-label="Platform reconciliation; scroll for additional columns"
              >
                <table>
                  <caption className="sr-only">Per-platform attribution reconciliation</caption>
                  <thead>
                    <tr>
                      <th>Platform</th>
                      <th>Reported conversions</th>
                      <th>Attributed store orders</th>
                      <th>Conversion / order ratio</th>
                      <th>Session / click ratio</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reconciliation.data!.platforms.map((p) => (
                      <tr key={p.platform}>
                        <td>{p.platform}</td>
                        <td>{p.platform_conversions.toLocaleString('en-IN')}</td>
                        <td>{p.store_attributed_orders}</td>
                        <td>
                          {p.over_attribution === null
                            ? p.over_attribution_reason || 'Not estimable'
                            : `${p.over_attribution.toFixed(2)}×`}
                        </td>
                        <td>
                          {p.session_click_ratio === null
                            ? 'Not estimable'
                            : `${p.session_click_ratio.toFixed(2)}×`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="caption">No per-platform conversion reconciliation supplied.</p>
            )}
          </>
        )}
      </section>
      <section className="panel">
        <SectionTitle title="Metric lineage" />
        {overview.isPending ? (
          <Loading label="Loading metric lineage" />
        ) : overview.error ? (
          <ErrorState error={overview.error} retry={() => void overview.refetch()} />
        ) : (
          overview.data?.metrics.map((m) => (
            <Disclosure key={m.key} title={`${m.label} · ${m.source}`}>
              <p>{m.formula}</p>
              <p>
                Available {m.available_at}. {m.reason || ''}
              </p>
              <div className="badge-row">
                {m.provenance_inputs.map((p) => (
                  <Badge key={p}>{p}</Badge>
                ))}
              </div>
            </Disclosure>
          ))
        )}
      </section>
      <SavedImports />
    </>
  );
}
