import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { RefreshCw, PlugZap } from 'lucide-react';
import { apiBase, checkConnection, dataMode, readinessEndpoints } from '../api/client';
import { Badge, ErrorState, Loading, SectionTitle } from '../components/ui';
import { humanStatus } from '../lib/format';

export function Connection() {
  const connection = useQuery({
    queryKey: ['backend-connection'],
    queryFn: checkConnection,
    retry: false,
    staleTime: 30000,
  });
  const health = connection.data?.find((p) => p.path === '/health')?.health;
  const ready = connection.data?.filter((p) => p.status === 'READY').length || 0;
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Backend Connection</h1>
          <p>Read-only checks against the configured backend, including in fixture mode.</p>
        </div>
        <button
          className="button secondary"
          disabled={connection.isFetching}
          aria-label="Check backend endpoints"
          onClick={() => void connection.refetch()}
        >
          <RefreshCw size={15} /> {connection.isFetching ? 'Checking…' : 'Check endpoints'}
        </button>
      </div>
      <section className="panel">
        <SectionTitle title="Workspace connection">
          <PlugZap size={19} />
        </SectionTitle>
        <dl className="fact-grid">
          <div>
            <dt>Frontend data mode</dt>
            <dd>{dataMode === 'fixture' ? 'Fixtures' : 'API'}</dd>
          </div>
          <div>
            <dt>Configured API base</dt>
            <dd className="break-all">{apiBase}</dd>
          </div>
          <div>
            <dt>Validated read endpoints</dt>
            <dd>
              {ready} / {readinessEndpoints.length + 1}
            </dd>
          </div>
        </dl>
        <p className="workbench-copy">
          Health confirms service availability. It does not prove the decision engine or execution
          routes are implemented. These checks send no mutations and never substitute fixture
          responses.
        </p>
        {health && (
          <>
            <Badge tone={health.status === 'ok' ? 'success' : 'warning'}>
              Backend {health.status}
            </Badge>
            <dl className="constraint-list">
              <div>
                <dt>Version / workspace</dt>
                <dd>
                  {health.version} / {health.workspace}
                </dd>
              </div>
              <div>
                <dt>Database</dt>
                <dd>{health.database}</dd>
              </div>
              <div>
                <dt>LLM mode</dt>
                <dd>{health.llm_mode}</dd>
              </div>
              <div>
                <dt>Execution configuration</dt>
                <dd>
                  {Object.entries(health.execution_modes)
                    .map(([platform, mode]) => `${platform}: ${mode}`)
                    .join(' · ') || 'Not reported'}
                </dd>
              </div>
            </dl>
          </>
        )}
      </section>
      <section className="panel">
        <SectionTitle title="Endpoint readiness">
          <Badge tone="accent">READ ONLY</Badge>
        </SectionTitle>
        {connection.isPending ? (
          <Loading label="Probing backend endpoints" />
        ) : connection.error ? (
          <ErrorState error={connection.error} retry={() => void connection.refetch()} />
        ) : (
          <ul className="connection-list">
            {connection.data?.map((p) => (
              <li key={p.path}>
                <div className="ledger-heading">
                  <strong>{p.name}</strong>
                  <Badge
                    tone={
                      p.status === 'READY'
                        ? 'success'
                        : p.status === 'MISSING' || p.status === 'AUTH_REQUIRED'
                          ? 'warning'
                          : 'danger'
                    }
                  >
                    {humanStatus(p.status)}
                  </Badge>
                </div>
                <code>{p.path}</code>
                <p>{p.detail}</p>
              </li>
            ))}
          </ul>
        )}
        <p className="caption">
          Several endpoints are proposed frontend contracts. Entity-specific detail routes and
          streaming endpoints require their own checks. Authentication, mutations and model output
          need their own backend validation before integration is complete.
        </p>
      </section>
      <p className="workbench-copy">
        Switch modes through <code>VITE_DATA_MODE</code> and <code>VITE_API_BASE_URL</code>, then
        restart Vite. In API mode, unavailable endpoints show an error.{' '}
        <Link className="text-link" to="/scenarios">
          Return to Scenario Lab
        </Link>
      </p>
    </>
  );
}
