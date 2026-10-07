import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { management } from '../api/management';
import {
  evaluationReportSchema,
  strategies,
  type EvaluationReport,
} from '../api/management-contracts';
import { Badge, Disclosure, ErrorState, InlineError, Loading, SectionTitle } from './ui';
import { money, dateTime } from '../lib/format';
import { downloadText } from '../lib/csv';
export function Evaluation() {
  const query = useQuery({
    queryKey: ['evaluation-report'],
    queryFn: management.evaluation,
    retry: false,
  });
  const [uploaded, setUploaded] = useState<EvaluationReport | null>(null);
  const [fileName, setFileName] = useState('');
  const [error, setError] = useState<Error | null>(null);
  const [reading, setReading] = useState(false);
  const [seed, setSeed] = useState('ALL');
  const report = uploaded || query.data?.report;
  const chosen = report?.rows.filter((r) => seed === 'ALL' || r.seed === Number(seed)) || [];
  async function load(file: File | undefined) {
    setUploaded(null);
    setFileName('');
    setError(null);
    setSeed('ALL');
    if (!file) return;
    setReading(true);
    try {
      if (file.size > 1_000_000) throw new Error('Report must be smaller than 1 MB.');
      const v = evaluationReportSchema.safeParse(JSON.parse(await file.text()));
      if (!v.success)
        throw new Error(
          'Report contract mismatch. Check required strategy rows, paired seeds, horizon and report metadata.',
        );
      setUploaded(v.data);
      setFileName(file.name);
    } catch (e) {
      setError(e instanceof Error ? e : new Error('Could not read this report.'));
    } finally {
      setReading(false);
    }
  }
  return (
    <>
      <section className="panel">
        <SectionTitle title="Head-to-Head evaluation">
          <Badge tone="accent">PRECOMPUTED REPORTS</Badge>
        </SectionTitle>
        <p className="workbench-copy">
          Compare safe-static, safe-contribution and ADAPT on paired worlds. The oracle is a
          clairvoyant benchmark, never a deployable strategy. This view reads reports; it does not
          start a simulator or generate benchmark results.
        </p>
        <div className="workbench-actions">
          <button
            className="button secondary"
            disabled={query.isFetching || reading}
            onClick={() => {
              setUploaded(null);
              setFileName('');
              setSeed('ALL');
              setError(null);
              void query.refetch();
            }}
          >
            Refresh backend report
          </button>
          {uploaded && (
            <button
              className="button secondary"
              onClick={() => {
                setUploaded(null);
                setSeed('ALL');
                setError(null);
              }}
            >
              Clear uploaded report
            </button>
          )}
          <button
            className="button secondary"
            disabled={!report}
            onClick={() =>
              downloadText('adapt-evaluation-report.json', JSON.stringify(report, null, 2))
            }
          >
            Export displayed report
          </button>
        </div>
        <label className="field compact-field">
          Precomputed evaluation JSON
          <input
            key={uploaded ? 'loaded' : 'empty'}
            type="file"
            accept=".json,application/json"
            disabled={reading}
            onChange={(e) => void load(e.target.files?.[0])}
          />
        </label>
        <InlineError error={error} />
        {reading && <Loading label="Validating evaluation report" />}
        {uploaded && (
          <p className="notice" role="status">
            Uploaded file: {fileName}. Schema checked locally; provenance and results have not been
            independently verified. No engine state changed.
          </p>
        )}
        {!uploaded &&
          (query.isPending ? (
            <Loading label="Loading evaluation report" />
          ) : query.error ? (
            <ErrorState error={query.error} retry={() => void query.refetch()} />
          ) : (
            <p className="notice">{query.data!.note}</p>
          ))}
        {report && (
          <>
            <div className="badge-row">
              <Badge>{report.report_id}</Badge>
              <Badge>{uploaded ? 'UPLOADED ARTIFACT' : 'BACKEND REPORT'}</Badge>
              <Badge tone={report.common_random_numbers ? 'success' : 'warning'}>
                {report.common_random_numbers
                  ? 'Common random numbers reported'
                  : 'Pairing protocol not established'}
              </Badge>
            </div>
            <dl className="fact-grid">
              <div>
                <dt>World seeds</dt>
                <dd>{report.seeds.length}</dd>
              </div>
              <div>
                <dt>Horizon</dt>
                <dd>{report.horizon_days} days</dd>
              </div>
              <div>
                <dt>Daily ceiling / reserve</dt>
                <dd>
                  {money(report.budget_ceiling)} / {money(report.reserve_floor)}
                </dd>
              </div>
            </dl>
            <label className="field compact-field">
              Evaluation seed
              <select value={seed} onChange={(e) => setSeed(e.target.value)}>
                <option value="ALL">All paired seeds</option>
                {report.seeds.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <div className="table-scroll">
              <table>
                <caption>
                  Totals across selected paired seeds; realized values supplied by the report
                </caption>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Realized CAA</th>
                    <th>Actual spend</th>
                    <th>CAA / ₹</th>
                    <th>Stock-risk days</th>
                    <th>Breaches</th>
                    <th>Forced interventions</th>
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((s) => {
                    const rows = chosen.filter((r) => r.strategy === s);
                    const caa = rows.reduce((n, r) => n + r.realized_caa, 0),
                      spend = rows.reduce((n, r) => n + r.spend, 0);
                    return (
                      <tr key={s}>
                        <th scope="row">
                          {s}
                          {s === 'oracle' && <small className="table-note">Benchmark only</small>}
                        </th>
                        <td>{money(caa)}</td>
                        <td>{money(spend)}</td>
                        <td>{spend === 0 ? 'Unavailable' : (caa / spend).toFixed(3)}</td>
                        <td>{rows.reduce((n, r) => n + r.stock_risk_days, 0)}</td>
                        <td>{rows.reduce((n, r) => n + r.constraint_breaches, 0)}</td>
                        <td>{rows.reduce((n, r) => n + r.forced_interventions, 0)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="notice">{report.fairness_statement}</p>
            <Disclosure title="Report provenance & evaluation envelope">
              <p>
                Generated {dateTime(report.generated_at)} · horizon {report.horizon_days} days.
              </p>
              <p className="break-all">Code version: {report.code_sha}</p>
              <p>{report.feasibility_envelope}</p>
              <p>
                Reported common random numbers: {report.common_random_numbers ? 'yes' : 'no'}.
                Paired seed rows alone do not verify equal starting state or establish statistically
                significant uplift. No confidence interval is inferred here.
              </p>
            </Disclosure>
          </>
        )}
      </section>
    </>
  );
}
