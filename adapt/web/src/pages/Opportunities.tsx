import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { insights } from '../api/insights';
import { useAction } from '../hooks/workspace';
import type { Opportunity } from '../api/insight-contracts';
import { Badge, Empty, ErrorState, InlineError, Loading, SectionTitle } from '../components/ui';
import { money } from '../lib/format';
import { CreativeSignals } from '../components/CreativeSignals';
import { dataMode } from '../api/client';
import { stage3, creativeFields } from '../api/stage3';

export function Opportunities() {
  const query = useQuery({ queryKey: ['opportunities'], queryFn: insights.opportunities });
  const fatigue = useQuery({ queryKey: ['creative-fatigue'], queryFn: insights.fatigue });
  const [selected, setSelected] = useState<string | null>(null);
  const [channel, setChannel] = useState('ALL');
  const domains = useQuery({queryKey: ['creative-attributes'], queryFn: stage3.attributes});
  const [attributes, setAttributes] = useState<Record<string, string>>({});
  const score = useAction(insights.scoreCreative);
  if (query.isPending) return <Loading label="Loading ranked opportunities" />;
  if (query.error) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  const rows = (query.data || []).filter((o) => channel === 'ALL' || o.platform === channel);
  const current = rows.find((o) => o.id === selected) || rows[0];
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Opportunity Map</h1>
          <p>
            Rank feasible growth candidates, then inspect their constraints and supporting evidence.
          </p>
        </div>
        <Badge tone="accent">MARGINAL CONTRIBUTION</Badge>
      </div>
      <p className="notice">
        {dataMode === 'fixture'
          ? 'Scores and curves are recorded illustrations, not engine output.'
          : 'Scores and curves are backend model outputs.'}{' '}
        A ranking is not an executable allocation; review the linked decision first.
      </p>
      <label className="field compact-field">
        Opportunity channel
        <select value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="ALL">All channels</option>
          <option>Meta</option>
          <option>Google</option>
        </select>
      </label>
      {!rows.length ? (
        <section className="panel">
          <Empty title="No ranked opportunities">
            Choose an optimization scenario or wait for eligible backend candidates.
          </Empty>
        </section>
      ) : (
        <div className="investigation-layout">
          <section className="panel">
            <SectionTitle title="Ranked candidates" />
            {rows.map((o, i) => (
              <button
                key={o.id}
                className={`investigation-row ${o.id === current?.id ? 'selected' : ''}`}
                aria-pressed={o.id === current?.id}
                onClick={() => setSelected(o.id)}
              >
                <div className="badge-row">
                  <Badge>{o.platform}</Badge>
                  <Badge tone={o.status === 'BLOCKED' ? 'danger' : 'accent'}>
                    {o.status.toLowerCase().replaceAll('_', ' ')}
                  </Badge>
                </div>
                <strong>
                  {i + 1}. {o.entity}
                </strong>
                <span>{o.reason}</span>
                <div className="signal-change">
                  <b>{o.score === null ? 'Unavailable' : o.score.toFixed(2)}</b>
                  <small>Opportunity score · not a probability</small>
                </div>
              </button>
            ))}
          </section>
          {current && <OpportunityDetail key={current.id} opportunity={current} />}
        </div>
      )}
      <section className="panel">
        <SectionTitle title="Creative fatigue" />
        {fatigue.isPending ? (
          <Loading label="Loading creatives" />
        ) : fatigue.error ? (
          <ErrorState error={fatigue.error} retry={() => void fatigue.refetch()} />
        ) : !fatigue.data?.length ? (
          <p className="workbench-copy">No creative fatigue evidence supplied for this scenario.</p>
        ) : (
          <CreativeSignals creatives={fatigue.data!} />
        )}
      </section>
      <section className="panel">
        <SectionTitle title="Creative assessment" />
        <p className="section-description">
          Estimate early click-through from six observed creative attributes. Copy and images are not analysed.
        </p>
        {domains.isPending ? <Loading label="Loading trained attribute domains" /> : domains.error ?
          <ErrorState error={domains.error} retry={() => void domains.refetch()} /> :
          domains.data?.status !== 'AVAILABLE' ? <p className="notice">{domains.data?.note}</p> :
          <fieldset disabled={score.isPending} className="creative-attributes">
            <legend className="sr-only">Structured creative attributes</legend>
            {creativeFields.map(field => <label className="field" key={field}>
              {field === 'cta' ? 'Call to action' : field.replaceAll('_', ' ')}
              <select value={attributes[field] || ''} onChange={e => {
                setAttributes(old => ({...old, [field]: e.target.value})); score.reset();
              }}>
                <option value="">Choose {field.replaceAll('_', ' ')}</option>
                {(domains.data?.domains[field] || []).map(value => <option key={value}>{value}</option>)}
              </select>
            </label>)}
          </fieldset>}
        <button
          className="button secondary"
          disabled={score.isPending || domains.data?.status !== 'AVAILABLE' || creativeFields.some(f => !attributes[f])}
          onClick={() => score.mutate({attributes})}
        >
          {score.isPending ? 'Assessing…' : 'Assess creative'}
        </button>
        <InlineError error={score.error} />
        {score.data && (
          <p className="notice" role="status">
            {score.data.score === null
              ? 'Not estimable'
              : `Expected early CTR: ${(score.data.score*100).toFixed(2)}%`}{' '}
            · {score.data.explanation}
          </p>
        )}
      </section>
    </>
  );
}
function OpportunityDetail({ opportunity: o }: { opportunity: Opportunity }) {
  const curve = useQuery({
    queryKey: ['curve', o.budget_id],
    queryFn: () => insights.curve(o.budget_id),
  });
  return (
    <div>
      <section className="panel">
        <SectionTitle title={o.entity}>
          <Badge>{o.platform}</Badge>
        </SectionTitle>
        <dl className="fact-grid">
          <div>
            <dt>Score</dt>
            <dd>{o.score === null ? 'Unavailable' : o.score.toFixed(2)}</dd>
          </div>
          <div>
            <dt>Marginal CAA / ₹1</dt>
            <dd>{o.marginal_caa?.toFixed(2) ?? 'Unavailable'}</dd>
          </div>
          <div>
            <dt>Probe increment</dt>
            <dd>{money(o.delta_budget)}</dd>
          </div>
        </dl>
        <p className="workbench-copy">{o.reason}</p>
        <SectionTitle title="Evidence graph">
          <div className="badge-row">
            {o.provenance.map((p) => (
              <Badge key={p}>{p}</Badge>
            ))}
          </div>
        </SectionTitle>
        <div className="evidence-graph">
          <div>
            <span>Signal</span>
            <strong>{o.entity}</strong>
          </div>
          <ArrowRight size={16} aria-hidden="true" />
          <div>
            <span>Feasibility</span>
            <strong>{o.status.toLowerCase().replaceAll('_', ' ')}</strong>
          </div>
          <ArrowRight size={16} aria-hidden="true" />
          <div>
            <span>Decision</span>
            {o.decision_id ? (
              <Link className="text-link" to={`/decisions/${o.decision_id}`}>
                Review proposal
              </Link>
            ) : (
              <strong>No proposal</strong>
            )}
          </div>
        </div>
        <ul className="evidence-links">
          {o.evidence.map((e) => (
            <li key={e.href}>
              <Link className="text-link" to={e.href}>
                {e.label}
              </Link>
            </li>
          ))}
        </ul>
      </section>
      <section className="panel">
        <SectionTitle title="Response curve" />
        {curve.isPending ? (
          <Loading label="Loading response curve" />
        ) : curve.error ? (
          <ErrorState error={curve.error} retry={() => void curve.refetch()} />
        ) : (
          <>
            <Badge tone="accent">{curve.data!.label}</Badge>
            <p className="workbench-copy">{curve.data!.reason}</p>
            <p className="caption">{curve.data!.unit}</p>
            <ResponseCurve points={curve.data!.points} />
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Daily budget</th>
                    <th>Contribution / day</th>
                  </tr>
                </thead>
                <tbody>
                  {curve.data!.points.map((p) => (
                    <tr key={p.budget}>
                      <td>{money(p.budget)}</td>
                      <td>{money(p.contribution)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
function ResponseCurve({ points }: { points: { budget: number; contribution: number }[] }) {
  if (points.length < 2)
    return <p className="caption">At least two points are required to plot a curve.</p>;
  const ordered = [...points].sort((a, b) => a.budget - b.budget);
  const minX = ordered[0].budget,
    rangeX = ordered.at(-1)!.budget - minX || 1;
  const minY = Math.min(...ordered.map((p) => p.contribution)),
    rangeY = Math.max(...ordered.map((p) => p.contribution)) - minY || 1;
  const coords = ordered.map((p) => ({
    x: 20 + ((p.budget - minX) / rangeX) * 420,
    y: 145 - ((p.contribution - minY) / rangeY) * 120,
  }));
  return (
    <figure className="response-curve">
      <svg
        viewBox="0 0 460 170"
        role="img"
        aria-label="Contribution response by daily budget; exact point values appear in the table below"
      >
        <path d="M20 15 V145 H440" fill="none" stroke="var(--rule)" />
        <polyline
          points={coords.map((p) => `${p.x},${p.y}`).join(' ')}
          fill="none"
          stroke="var(--accent)"
          strokeWidth="3"
        />
        {coords.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r="4" fill="var(--accent)" />
        ))}
      </svg>
      <figcaption>
        Budget increases left to right; contribution increases upward. Lines connect supplied points
        and do not imply a fitted model.
      </figcaption>
    </figure>
  );
}
