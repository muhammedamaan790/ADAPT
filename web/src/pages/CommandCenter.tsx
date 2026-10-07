import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Check,
  Circle,
  CircleAlert,
  Package,
  RotateCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { useOverview } from '../hooks/workspace';
import { Badge, Empty, ErrorState, Loading, MetricTile, SectionTitle } from '../components/ui';
import { TrendChart } from '../components/charts';
import { dateTime, money } from '../lib/format';

export function CommandCenter() {
  const { data, isPending, error, refetch } = useOverview();
  const cache = useQueryClient();
  if (isPending) return <Loading />;
  if (error || !data)
    return (
      <ErrorState error={error || new Error('No overview returned')} retry={() => void refetch()} />
    );
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Command Center</h1>
          <p>Your advertising decisions, with the evidence behind them.</p>
        </div>
        <button className="button secondary" onClick={() => void cache.invalidateQueries()}>
          <RotateCw size={14} />
          Refresh data
        </button>
      </div>
      <div className="context-line">
        <span>
          <i className="status-dot" />
          Last report {dateTime(data.decision_ts)}
        </span>
        <span>World day {data.world_day} · Last 7 days</span>
        <Badge tone="accent">PROFIT</Badge>
      </div>
      <section className="brief">
        <div className="brief-icon">
          <Sparkles size={21} />
        </div>
        <div>
          <h2>Your morning brief</h2>
          <p>{data.brief}</p>
        </div>
        <Link to="/decisions" className="text-link">
          Review decisions <ArrowRight size={16} />
        </Link>
      </section>
      <section className="metrics" aria-label="Business metrics">
        {data.metrics.map((metric) => (
          <MetricTile key={metric.key} metric={metric} />
        ))}
      </section>
      <div className="overview-grid">
        <section className="panel attention-panel">
          <SectionTitle title="What needs your attention">
            <Badge>{data.attention.length} items</Badge>
          </SectionTitle>
          <p className="section-description">
            Ranked by financial impact. Estimates remain model dependent.
          </p>
          {data.attention.length ? (
            <div className="attention-list">
              {data.attention.map((item) => {
                const Icon =
                  item.kind === 'incident'
                    ? CircleAlert
                    : item.kind === 'inventory'
                      ? Package
                      : item.kind === 'outcome'
                        ? Check
                        : TrendingUp;
                return (
                  <Link
                    className={`attention-row attention-${item.kind}`}
                    key={item.id}
                    to={item.decision_id ? `/decisions/${item.decision_id}` : '/decisions'}
                  >
                    <span className="attention-icon">
                      <Icon size={18} />
                    </span>
                    <div className="attention-copy">
                      <strong>{item.title}</strong>
                      <p>{item.description}</p>
                      <small>
                        {item.kind === 'incident'
                          ? 'Needs investigation'
                          : item.kind === 'outcome'
                            ? 'Loop completed'
                            : 'Review recommendation'}
                      </small>
                    </div>
                    <div className="attention-impact">
                      <strong>{money(item.impact)}</strong>
                      <small>{item.label}</small>
                    </div>
                    <ArrowUpRight size={16} className="muted" />
                  </Link>
                );
              })}
            </div>
          ) : (
            <Empty title="Nothing requires action">
              No open incidents or allocation proposals. Expected budget changes are kept out of the
              incident queue.
            </Empty>
          )}
        </section>
        <section className="panel performance-panel">
          <SectionTitle title="Campaign efficiency">
            <Badge tone="warning">Reconciled</Badge>
          </SectionTitle>
          <p className="section-description">
            Hero campaign · actual return vs the baseline forecast
          </p>
          <TrendChart data={data.series} small />
          <div className="chart-insight">
            <ArrowDownRight size={19} />
            <p>
              <strong>
                {data.scenario === 'S7'
                  ? 'Expected budget change.'
                  : data.scenario === 'S5'
                    ? 'Validate tracking before scaling.'
                    : 'Investigate before scaling.'}
              </strong>{' '}
              {data.scenario === 'S7'
                ? 'No efficiency incident is open.'
                : 'The Decision Center shows accounting drivers and inventory limits.'}
            </p>
          </div>
          <Link to="/decisions" className="text-link">
            Open the investigation <ArrowRight size={15} />
          </Link>
        </section>
      </div>
      <section className="panel source-panel">
        <SectionTitle title="A unified view, from six sources">
          <span className="muted small">Source health · provenance disclosed</span>
        </SectionTitle>
        <div className="source-grid">
          {data.sources.map((source) => (
            <div className="source-item" key={source.id}>
              <div className="source-title">
                <strong>{source.name}</strong>
                <span className={`health-dot health-${source.status.toLowerCase()}`} />
                <span className="sr-only">{source.status}</span>
              </div>
              <span>
                {source.kind} · {source.freshness}
              </span>
              <div>
                <b className={source.status === 'RED' ? 'text-danger' : ''}>{source.score}/100</b>
                <Badge>{source.provenance}</Badge>
              </div>
            </div>
          ))}
        </div>
      </section>
      <section className="panel loop-panel">
        <SectionTitle title="The decision loop">
          <span className="loop-mode">
            <ShieldCheck size={15} />
            Human approval required
          </span>
        </SectionTitle>
        <div className="loop-ribbon">
          {data.loop.map((step, i) => (
            <div key={step.label} className={`loop-step loop-${step.state}`}>
              <span className="loop-node">
                {step.state === 'complete' ? (
                  <Check size={14} />
                ) : step.state === 'current' ? (
                  <Circle size={13} fill="currentColor" />
                ) : (
                  <Circle size={13} />
                )}
              </span>
              <strong>{step.label}</strong>
              {i < data.loop.length - 1 && <ArrowRight size={14} className="loop-arrow" />}
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
