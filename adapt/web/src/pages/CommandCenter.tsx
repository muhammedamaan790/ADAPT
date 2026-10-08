import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CircleAlert,
  Package,
  RotateCw,
  ShieldCheck,
  TrendingUp,
  Database,
  ShoppingBag,
  ChartNoAxesCombined,
  Boxes,
  Tag,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { useQueryClient, useIsFetching } from '@tanstack/react-query';
import { useOverview, useDecisions } from '../hooks/workspace';
import { Badge, Empty, ErrorState, Loading, MetricTile } from '../components/ui';
import { TrendChart } from '../components/charts';
import { HomeProposal, ChannelMark } from '../components/HomeProposal';
import { AskAdapt } from '../components/AskAdapt';
import { dateTime, money } from '../lib/format';

export function CommandCenter() {
  const { data, isPending, error, refetch } = useOverview();
  const decisions = useDecisions();
  const cache = useQueryClient();
  const refreshing = useIsFetching({ queryKey: ['overview'] }) > 0;
  const proposal =
    decisions.data?.find((d) => d.status === 'PENDING_APPROVAL') || decisions.data?.[0];
  if (isPending) return <Loading />;
  if (error || !data)
    return (
      <ErrorState error={error || new Error('No overview returned')} retry={() => void refetch()} />
    );
  return (
    <div className="command-center command-workspace">
      <div className="page-heading command-heading">
        <div>
          <h1>Command Center</h1>
          <p>Advertising decisions, with the evidence behind them.</p>
        </div>
        <div className="command-refresh">
          <button
            className="button primary"
            aria-label="Refresh data"
            aria-busy={refreshing}
            disabled={refreshing}
            onClick={() => void cache.invalidateQueries()}
          >
            <RotateCw size={15} className={refreshing ? 'spin' : ''} />
            {refreshing ? 'Refreshing…' : 'Refresh data'}
          </button>
          <span>Last report {dateTime(data.decision_ts)}</span>
        </div>
      </div>
      <section className="command-brief" aria-label="Morning brief">
        <strong>Morning brief</strong>
        <p>{data.brief}</p>
      </section>
      <AskAdapt />
      <section className="metrics" aria-label="Business metrics">
        {data.metrics.map((metric) => (
          <MetricTile key={metric.key} metric={metric} />
        ))}
      </section>
      <div className="command-analysis">
        {decisions.isPending ? (
          <section className="panel home-proposal">
            <Loading label="Loading the current proposal" />
          </section>
        ) : decisions.error ? (
          <section className="panel home-proposal">
            <h2>Budget proposal unavailable</h2>
            <ErrorState error={decisions.error} retry={() => void decisions.refetch()} />
          </section>
        ) : proposal ? (
          <HomeProposal decision={proposal} />
        ) : (
          <section className="panel home-proposal">
            <h2>Budget proposal</h2>
            <Empty title="No allocation needs review">
              No open proposal has been supplied for this workspace.
            </Empty>
            <Link className="button secondary" to="/scenarios">
              Explore scenarios <ArrowRight size={15} />
            </Link>
          </section>
        )}
        <section className="panel home-performance">
          <div className="home-section-heading">
            <h2>Campaign efficiency</h2>
            <Badge>ROAS</Badge>
          </div>
          <p className="home-section-description">
            Hero campaign · {data.series.length} daily observations · actual vs baseline
          </p>
          <TrendChart data={data.series} presentation="overview" />
          <div className="home-chart-footer">
            <span>
              {data.scenario === 'S7'
                ? 'Expected budget change; no efficiency incident.'
                : data.scenario === 'S5'
                  ? 'Validate tracking before scaling.'
                  : 'Review the funnel drivers before scaling.'}
            </span>
            <Link className="text-link" to="/decisions">
              Investigate <ArrowRight size={14} />
            </Link>
          </div>
        </section>
      </div>
      <section className="panel command-attention">
        <div className="home-section-heading">
          <div>
            <h2>Attention register</h2>
            <p>Signals requiring a decision, with the supplied impact and evidence.</p>
          </div>
          <Link className="text-link" to="/decisions">
            Review decisions <ArrowRight size={15} />
          </Link>
        </div>
        {data.attention.length ? (
          <div className="attention-register" role="list">
            <div className="register-columns" aria-hidden="true">
              <span>Signal</span>
              <span>Supporting evidence</span>
              <span>Estimated impact</span>
              <span>Action</span>
            </div>
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
                <div role="listitem" key={item.id}>
                  <Link
                    className={`register-row register-${item.kind}`}
                    to={
                      item.decision_id
                        ? `/decisions/${encodeURIComponent(item.decision_id)}`
                        : '/decisions'
                    }
                  >
                    <div className="register-signal">
                      <Icon size={21} aria-hidden="true" />
                      <strong>{item.title}</strong>
                    </div>
                    <p>{item.description}</p>
                    <div className="register-impact">
                      <strong>{money(item.impact)}</strong>
                      <small>{item.label}</small>
                    </div>
                    <span className="register-action">
                      {item.kind === 'outcome' ? 'Inspect outcome' : 'Review'}
                      <ArrowUpRight size={14} aria-hidden="true" />
                    </span>
                  </Link>
                </div>
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
      <section className="panel command-sources">
        <div className="home-section-heading">
          <h2>Data sources</h2>
          <span>
            Health score and provenance ·{' '}
            <Link className="text-link" to="/data">
              View source checks <ArrowRight size={13} />
            </Link>
          </span>
        </div>
        <div className="command-source-grid">
          {data.sources.map((s) => {
            const Icon =
              s.id === 'store'
                ? ShoppingBag
                : s.id === 'ga4'
                  ? ChartNoAxesCombined
                  : s.id === 'erp'
                    ? Boxes
                    : s.id === 'econ'
                      ? Tag
                      : Database;
            return (
              <div className={`command-source source-${s.id}`} key={s.id}>
                {s.id === 'meta' || s.id === 'google' ? (
                  <ChannelMark platform={s.id === 'meta' ? 'Meta' : 'Google'} />
                ) : (
                  <span className="source-symbol">
                    <Icon size={26} aria-hidden="true" />
                  </span>
                )}
                <div>
                  <strong>{s.name}</strong>
                  <b className={s.status === 'RED' ? 'text-danger' : ''}>
                    {s.score}
                    <small>/100 health</small>
                  </b>
                  <span>
                    {s.provenance}
                    <i className={`health-dot health-${s.status.toLowerCase()}`} />
                    <span className="sr-only">{s.status}</span>
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </section>
      <section className="command-loop" aria-label="The decision loop">
        <div className="command-loop-label">
          <ShieldCheck size={16} />
          <span>
            Human approval
            <br />
            <strong>before execution</strong>
          </span>
        </div>
        <ol>
          {data.loop.map((step, i) => (
            <li className={`step-${step.state}`} key={step.label}>
              <span className="command-step-number">
                {step.state === 'complete' ? <Check size={14} /> : i + 1}
              </span>
              <strong>{step.label}</strong>
              {i < data.loop.length - 1 && <ArrowRight size={13} aria-hidden="true" />}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
