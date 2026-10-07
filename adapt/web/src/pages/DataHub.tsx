import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useSearchParams } from 'react-router-dom';
import { Workspaces } from '../components/Workspaces';
import { SourceChecks } from '../components/SourceChecks';
import { Download, Upload, ArrowRight } from 'lucide-react';
import { insights } from '../api/insights';
import { useAction } from '../hooks/workspace';
import { useOverview } from '../hooks/workspace';
import { dataMode } from '../api/client';
import { getActiveFixtureWorkspace } from '../api/fixture-service';
import {
  parseCsv,
  validateImport,
  importFields,
  downloadText,
  type Csv,
  type ImportType,
} from '../lib/csv';
import {
  Badge,
  Disclosure,
  ErrorState,
  InlineError,
  Loading,
  SectionTitle,
  Status,
} from '../components/ui';
import { money, percent } from '../lib/format';

export function DataHub() {
  const health = useQuery({ queryKey: ['data-health'], queryFn: insights.dataHealth });
  const reconciliation = useQuery({
    queryKey: ['data-reconciliation'],
    queryFn: insights.reconciliation,
  });
  const overview = useOverview();
  const [params, setParams] = useSearchParams();
  const tab =
    params.get('section') === 'import'
      ? 'import'
      : params.get('section') === 'workspaces'
        ? 'workspaces'
        : 'sources';
  const setTab = (section: string) => setParams({ section });
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Data Hub</h1>
          <p>Inspect sources, reconcile attribution, and validate imports before promotion.</p>
        </div>
        <Badge>INR · ASIA/KOLKATA</Badge>
      </div>
      <div className="report-tabs" role="group" aria-label="Data Hub sections">
        <button
          className="button secondary"
          aria-pressed={tab === 'sources'}
          onClick={() => setTab('sources')}
        >
          Sources & lineage
        </button>
        <button
          className="button secondary"
          aria-pressed={tab === 'import'}
          onClick={() => setTab('import')}
        >
          <Upload size={14} /> CSV import
        </button>
        <button
          className="button secondary"
          aria-pressed={tab === 'workspaces'}
          onClick={() => setTab('workspaces')}
        >
          Workspaces
        </button>
      </div>
      {tab === 'workspaces' ? (
        <Workspaces />
      ) : tab === 'import' ? (
        <ImportWizard />
      ) : (
        <>
          <section className="panel">
            <SectionTitle title="Source health">
              <Link className="text-link" to="/connection">
                Check backend <ArrowRight size={14} />
              </Link>
            </SectionTitle>
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
              <ErrorState
                error={reconciliation.error}
                retry={() => void reconciliation.refetch()}
              />
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
        </>
      )}
    </>
  );
}

function ImportWizard() {
  const localWorkspace = getActiveFixtureWorkspace();
  const historyKey =
    dataMode === 'fixture'
      ? localWorkspace === 'default'
        ? 'adapt.import-previews'
        : `adapt.import-previews.${localWorkspace}`
      : null;
  const [type, setType] = useState<ImportType>('ads');
  const [csv, setCsv] = useState<Csv | null>(null);
  const [name, setName] = useState('');
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [error, setError] = useState<Error | null>(null);
  const [reading, setReading] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const stage = useAction(
    (input: {
      type: ImportType;
      records: Record<string, string | number>[];
      mapping: Record<string, string>;
    }) => insights.importData(input.type, input.records, input.mapping),
  );
  const [history, setHistory] = useState<
    { id: string; name: string; rows: number; mode: string }[]
  >(() => {
    try {
      const v = JSON.parse(historyKey ? localStorage.getItem(historyKey) || '[]' : '[]');
      return Array.isArray(v)
        ? v
            .filter(
              (r) =>
                typeof r.id === 'string' && typeof r.name === 'string' && Number.isInteger(r.rows),
            )
            .slice(0, 10)
        : [];
    } catch {
      return [];
    }
  });
  const validation = csv ? validateImport(csv, type, mapping) : null;
  const reset = () => {
    setCsv(null);
    setName('');
    setMapping({});
    setError(null);
    setConfirmed(false);
    stage.reset();
  };
  async function load(file: File | undefined) {
    reset();
    if (!file) return;
    setReading(true);
    try {
      if (file.size > 2_000_000) throw new Error('CSV must be smaller than 2 MB.');
      const parsed = parseCsv(await file.text());
      setCsv(parsed);
      setName(file.name);
      setMapping(
        Object.fromEntries(
          importFields[type].map((k) => [
            k,
            parsed.headers.find((h) => h.toLowerCase() === k) || '',
          ]),
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e : new Error('Could not read this file.'));
    } finally {
      setReading(false);
    }
  }
  const template = () => {
    const rows = {
      ads: '2026-10-07,g-bundle,Google,24000,100000,2000',
      inventory: 'bundle,500,20,30',
      margins: 'bundle,1499,600',
    };
    downloadText(
      `adapt-${type}-template.csv`,
      `${importFields[type].join(',')}\n${rows[type]}\n`,
      'text/csv;charset=utf-8',
    );
  };
  return (
    <>
      <section className="panel">
        <SectionTitle title="Validate a CSV import">
          <Badge tone={dataMode === 'fixture' ? 'warning' : 'accent'}>
            {dataMode === 'fixture' ? 'LOCAL PREVIEW' : 'BACKEND INGEST'}
          </Badge>
        </SectionTitle>
        <p className="workbench-copy">
          {dataMode === 'fixture'
            ? 'Files are parsed and checked in this browser. Staging records preview metadata locally; it does not ingest data into the engine.'
            : 'After your review, validated rows are submitted to backend staging and mapping confirmation. The backend must run canonical data gates before promotion.'}{' '}
          Up to 2 MB and 5,000 rows.
        </p>
        <div className="import-inputs">
          <label className="field">
            Import type
            <select
              value={type}
              disabled={reading || stage.isPending}
              onChange={(e) => {
                setType(e.target.value as ImportType);
                reset();
              }}
            >
              <option value="ads">Ads performance</option>
              <option value="inventory">Inventory</option>
              <option value="margins">Product margins</option>
            </select>
          </label>
          <label className="field">
            CSV file
            <input
              key={type}
              type="file"
              accept=".csv,text/csv"
              disabled={reading || stage.isPending}
              onChange={(e) => void load(e.target.files?.[0])}
            />
          </label>
        </div>
        <button className="button secondary" onClick={template}>
          <Download size={14} /> Download template
        </button>
        <InlineError error={error} />
        {reading && <Loading label="Reading CSV" />}
        {csv && (
          <>
            <h3 className="workbench-title">Map columns · {name}</h3>
            <p className="section-description">
              {csv.rows.length} rows · {csv.headers.length} columns. Each required field needs a
              distinct column.
            </p>
            <div className="mapping-grid">
              {importFields[type].map((k) => (
                <label className="field" key={k}>
                  {k}
                  <select
                    value={mapping[k] || ''}
                    disabled={stage.isPending}
                    onChange={(e) => {
                      setMapping((m) => ({ ...m, [k]: e.target.value }));
                      setConfirmed(false);
                      stage.reset();
                    }}
                  >
                    <option value="">Choose column</option>
                    {csv.headers.map((h) => (
                      <option key={h}>{h}</option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
            <Disclosure title="Raw file preview (first five rows)">
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      {csv.headers.map((h) => (
                        <th key={h}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {csv.rows.slice(0, 5).map((r, i) => (
                      <tr key={i}>
                        {r.map((v, j) => (
                          <td key={j}>{v}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Disclosure>
            {validation!.errors.length ? (
              <div className="input-errors" role="alert">
                <strong>{validation!.errors.length} validation error(s)</strong>
                <ul>
                  {validation!.errors.slice(0, 12).map((e) => (
                    <li key={e}>{e}</li>
                  ))}
                </ul>
                {validation!.errors.length > 12 && (
                  <p>Fix the file before reviewing the remaining errors.</p>
                )}
              </div>
            ) : (
              <p className="notice" role="status">
                All {csv.rows.length} rows pass browser schema, range and duplicate-key checks.
                Backend freshness, identity and canonical validation are still required.
              </p>
            )}
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={!!validation!.errors.length || stage.isPending}
                onChange={(e) => setConfirmed(e.target.checked)}
              />{' '}
              I reviewed the mapping and understand{' '}
              {dataMode === 'fixture'
                ? 'this stages a local preview only.'
                : 'these records will be sent to backend ingest.'}
            </label>
            <button
              className="button primary"
              disabled={
                !confirmed || !!validation!.errors.length || stage.isPending || stage.isSuccess
              }
              onClick={() =>
                stage.mutate(
                  { type, records: validation!.records, mapping },
                  {
                    onSuccess: (r) => {
                      const next = [
                        { id: r.import_id, name, rows: r.row_count, mode: dataMode },
                        ...history,
                      ].slice(0, 10);
                      setHistory(next);
                      try {
                        if (historyKey) localStorage.setItem(historyKey, JSON.stringify(next));
                      } catch {
                        /* The current preview still works without persistence. */
                      }
                    },
                  },
                )
              }
            >
              {stage.isPending
                ? 'Submitting…'
                : dataMode === 'fixture'
                  ? 'Stage local preview'
                  : 'Submit validated import'}
            </button>
            <InlineError error={stage.error} />
            {stage.data && (
              <p className="notice" role="status">
                {stage.data.message} · {stage.data.import_id}
              </p>
            )}
          </>
        )}
      </section>
      <section className="panel">
        <SectionTitle title="Recent import submissions" />
        {history.length ? (
          <ul className="connection-list">
            {history.map((h) => (
              <li key={h.id}>
                <div className="ledger-heading">
                  <strong>{h.name}</strong>
                  <Badge>{h.mode.toUpperCase()}</Badge>
                </div>
                <p>
                  {h.rows} rows · {h.id}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="workbench-copy">
            No files have been staged from this browser. Stored entries contain metadata, not the
            uploaded records.
          </p>
        )}
      </section>
    </>
  );
}
