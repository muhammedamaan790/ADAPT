import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, Search, Activity, Filter } from 'lucide-react';
import { api } from '../api/client';
import { useAction, useAnomalies } from '../hooks/workspace';
import type { Anomaly } from '../api/workbench-contracts';
import {
  Badge,
  Empty,
  ErrorState,
  InlineError,
  Loading,
  PolicyCheck,
  SectionTitle,
  Status,
  Disclosure,
} from '../components/ui';
import { TrendChart } from '../components/charts';
import { Narrative } from '../components/Narrative';
import { dateTime, money, percent, humanStatus } from '../lib/format';

export function Anomalies() {
  const query = useAnomalies();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('ALL');
  const [channel, setChannel] = useState('ALL');
  const [selected, setSelected] = useState<string | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  if (query.isPending) return <Loading label="Loading investigations" />;
  if (query.error) return <ErrorState error={query.error} retry={() => void query.refetch()} />;
  const all = query.data || [];
  const filtered = all.filter(
    (a) =>
      (status === 'ALL' || a.status === status) &&
      (channel === 'ALL' || a.platform === channel) &&
      `${a.title} ${a.entity} ${a.metric}`.toLowerCase().includes(search.toLowerCase()),
  );
  const current = filtered.find((a) => a.anomaly_id === selected) || filtered[0];
  const incidents = all.filter((a) => a.kind !== 'BUDGET_CHANGE');
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Anomalies</h1>
          <p>Investigate the signal, inspect its evidence, then review the action.</p>
        </div>
        <Badge tone="accent">EVIDENCE FIRST</Badge>
      </div>
      <div className="workbench-stats" aria-label="Investigation summary">
        <div>
          <span>Open incidents</span>
          <strong>{incidents.filter((a) => a.status === 'OPEN').length}</strong>
        </div>
        <div>
          <span>Acknowledged</span>
          <strong>{incidents.filter((a) => a.status === 'ACKNOWLEDGED').length}</strong>
        </div>
        <div>
          <span>Expected changes</span>
          <strong>{all.filter((a) => a.kind === 'BUDGET_CHANGE').length}</strong>
        </div>
      </div>
      <button
        className="button secondary mobile-filter-toggle"
        aria-expanded={filtersOpen}
        aria-controls="investigation-filters"
        onClick={() => setFiltersOpen(!filtersOpen)}
      >
        <Filter size={14} /> {filtersOpen ? 'Hide filters' : 'Filter investigations'}
        <Badge>
          {Number(!!search) + Number(status !== 'ALL') + Number(channel !== 'ALL')} active
        </Badge>
      </button>
      <div className={`filter-bar ${filtersOpen ? 'filters-open' : ''}`} id="investigation-filters">
        <label className="field search-field">
          <span>
            <Search size={14} /> Search investigations
          </span>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Campaign, signal or metric"
          />
        </label>
        <label className="field">
          Status
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="ALL">All statuses</option>
            {['OPEN', 'ACKNOWLEDGED', 'RESOLVED'].map((s) => (
              <option key={s} value={s}>
                {humanStatus(s)}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Channel
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="ALL">All channels</option>
            <option>Meta</option>
            <option>Google</option>
          </select>
        </label>
      </div>
      {!filtered.length ? (
        <section className="panel">
          <Empty title="No matching investigations">
            Adjust your filters or load another example in Scenario Lab.
          </Empty>
          <button
            className="button secondary"
            onClick={() => {
              setSearch('');
              setStatus('ALL');
              setChannel('ALL');
            }}
          >
            Clear filters
          </button>
        </section>
      ) : (
        <div className="investigation-layout">
          <section className="panel investigation-list">
            <SectionTitle title="Investigation queue">
              <span className="muted">
                {filtered.length} signal{filtered.length === 1 ? '' : 's'}
              </span>
            </SectionTitle>
            {filtered.map((a) => (
              <button
                key={a.anomaly_id}
                className={`investigation-row ${current?.anomaly_id === a.anomaly_id ? 'selected' : ''}`}
                onClick={() => setSelected(a.anomaly_id)}
                aria-pressed={current?.anomaly_id === a.anomaly_id}
              >
                <div className="badge-row">
                  <Badge>{a.platform}</Badge>
                  <Status value={a.status} />
                </div>
                <strong>{a.title}</strong>
                <span>{a.entity}</span>
                <div className="signal-change">
                  <b>{percent(a.change)}</b>
                  <small>{a.metric} vs baseline</small>
                </div>
                <small>{dateTime(a.detected_at)}</small>
              </button>
            ))}
          </section>
          {current && <Investigation key={current.anomaly_id} anomaly={current} />}
        </div>
      )}
    </>
  );
}

function Investigation({ anomaly: a }: { anomaly: Anomaly }) {
  const evidence = useQuery({
    queryKey: ['evidence', a.decision_id],
    queryFn: () => api.evidence(a.decision_id!),
    enabled: !!a.decision_id,
  });
  const [reason, setReason] = useState('');
  const update = useAction((next: Anomaly['status']) =>
    api.anomalyStatus(a.anomaly_id, next, reason),
  );
  return (
    <div>
      <section className="panel">
        <SectionTitle title={a.kind === 'BUDGET_CHANGE' ? 'Expected movement' : 'Signal detail'}>
          <Badge tone={a.kind === 'BUDGET_CHANGE' ? 'neutral' : 'warning'}>
            {humanStatus(a.kind)}
          </Badge>
        </SectionTitle>
        <h3 className="workbench-title">{a.title}</h3>
        <p className="section-description">
          {a.entity} · {a.metric} · {a.anomaly_id}
        </p>
        {a.kind === 'BUDGET_CHANGE' && (
          <p className="notice">
            Movement explained by a recorded budget change. Excluded from efficiency incident
            counts.
          </p>
        )}
        <dl className="fact-grid">
          <div>
            <dt>Observed</dt>
            <dd>{a.actual.toFixed(2)}</dd>
          </div>
          <div>
            <dt>Baseline</dt>
            <dd>{a.baseline.toFixed(2)}</dd>
          </div>
          <div>
            <dt>{a.impact_label}</dt>
            <dd>{money(a.impact)}</dd>
          </div>
        </dl>
        {evidence.isPending && a.decision_id ? (
          <Loading label="Loading signal evidence" />
        ) : evidence.error ? (
          <ErrorState error={evidence.error} retry={() => void evidence.refetch()} />
        ) : evidence.data ? (
          <TrendChart data={evidence.data.chart} title={evidence.data.chart_metric} responsive />
        ) : (
          <p className="caption">
            No linked decision or diagnostic chart for this expected movement.
          </p>
        )}
        <div className="check-list">
          {a.gates.map((c) => (
            <PolicyCheck key={c.id} {...c} />
          ))}
        </div>
        <Disclosure title="Evidence lineage">
          <div className="badge-row">
            {a.provenance_inputs.map((p) => (
              <Badge key={p}>{p}</Badge>
            ))}
          </div>
          <p>
            Detected {dateTime(a.detected_at)}. These inputs identify provenance; scores do not
            prove causation.
          </p>
        </Disclosure>
      </section>
      <Narrative kind="incident" id={a.anomaly_id} />
      <section className="panel">
        <SectionTitle title="Root-cause assessment">
          <Activity size={17} />
        </SectionTitle>
        <p className="section-description">
          Probable driver: <strong>{humanStatus(a.driver)}</strong>
        </p>
        <Badge tone={a.causal.status === 'ESTIMABLE' ? 'accent' : 'warning'}>
          {humanStatus(a.causal.status)}
        </Badge>
        <p className="workbench-copy">{a.causal.reason}</p>
        {a.causal.status === 'ESTIMABLE' && a.causal.effect_pct !== null && (
          <p>
            Estimated effect: {percent(a.causal.effect_pct)} · interval{' '}
            {a.causal.lower_pct === null ? 'unavailable' : percent(a.causal.lower_pct)} to{' '}
            {a.causal.upper_pct === null ? 'unavailable' : percent(a.causal.upper_pct)}
          </p>
        )}
        <Disclosure title="Causal assumptions to check">
          <ul>
            {a.causal.assumptions.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </Disclosure>
        {a.decision_id && (
          <Link className="button secondary" to={`/decisions/${a.decision_id}`}>
            Review linked decision <ArrowRight size={15} />
          </Link>
        )}
      </section>
      <section className="panel">
        <SectionTitle title="Investigation status">
          <Status value={a.status} />
        </SectionTitle>
        <p className="section-description">
          Acknowledgement records review. Resolution records a reason; it does not execute a budget
          change.
        </p>
        {a.resolution_reason && <p className="notice">Recorded reason: {a.resolution_reason}</p>}
        <label className="field">
          Resolution reason
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
            placeholder="What was checked or resolved?"
          />
        </label>
        <div className="workbench-actions">
          <button
            className="button secondary"
            disabled={update.isPending || a.status !== 'OPEN'}
            onClick={() => update.mutate('ACKNOWLEDGED')}
          >
            Acknowledge
          </button>
          <button
            className="button primary"
            disabled={update.isPending || a.status === 'RESOLVED' || reason.trim().length < 5}
            onClick={() => update.mutate('RESOLVED')}
          >
            Resolve investigation
          </button>
          {a.status === 'RESOLVED' && (
            <button
              className="button secondary"
              disabled={update.isPending}
              onClick={() => update.mutate('OPEN')}
            >
              Reopen
            </button>
          )}
        </div>
        <InlineError error={update.error} />
      </section>
    </div>
  );
}
