import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight, ChevronDown } from 'lucide-react';
import type { Decision } from '../api/contracts';
import { signedMoney } from '../lib/format';
import { Badge, Status } from './ui';

export function DecisionInbox({
  decisions,
  selected,
}: {
  decisions: Decision[];
  selected: string;
}) {
  const [search, setSearch] = useState('');
  const [scope, setScope] = useState('ALL');
  const pending = decisions.filter((d) => d.status === 'PENDING_APPROVAL').length;
  const rows = decisions.filter(
    (d) =>
      (scope === 'ALL' || d.status === scope) &&
      `${d.title} ${d.decision_id} ${d.class}`.toLowerCase().includes(search.trim().toLowerCase()),
  );
  return (
    <details className="decision-inbox">
      <summary>
        <ChevronDown size={16} aria-hidden="true" />
        Decision inbox{' '}
        <span>
          {pending} awaiting approval · {decisions.length} total
        </span>
      </summary>
      <div className="inbox-body">
        <div className="filter-bar">
          <label className="field">
            Find a decision
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Title, ID or class"
            />
          </label>
          <label className="field">
            Decision status
            <select
              aria-label="Decision status"
              value={scope}
              onChange={(e) => setScope(e.target.value)}
            >
              <option value="ALL">All statuses</option>
              {[...new Set(decisions.map((d) => d.status))].map((s) => (
                <option key={s} value={s}>
                  {s.toLowerCase().replaceAll('_', ' ')}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="caption" role="status">
          {rows.length} matching {rows.length === 1 ? 'decision' : 'decisions'}
        </p>
        {rows.length ? (
          <ul className="inbox-list">
            {rows.map((d) => (
              <li key={d.decision_id}>
                <Link
                  to={`/decisions/${encodeURIComponent(d.decision_id)}`}
                  aria-current={d.decision_id === selected ? 'page' : undefined}
                >
                  <div className="inbox-identity">
                    <strong>{d.title}</strong>
                    <span>
                      {d.decision_id} · {d.class.toLowerCase()}
                    </span>
                  </div>
                  <Status value={d.status} />
                  <div className="inbox-estimate">
                    <strong>
                      {d.class === 'OPERATIONAL'
                        ? 'Operational action'
                        : d.valuation_status === 'NOT_ESTIMABLE'
                          ? 'Not estimable'
                          : signedMoney(d.expected.p50)}
                    </strong>
                    <span>
                      {d.class === 'OPERATIONAL'
                        ? 'No financial forecast'
                        : `Est. ΔCAA · ${d.horizon_days} days`}
                    </span>
                  </div>
                  {d.checks.some((c) => !c.passed) && <Badge tone="warning">Policy blocked</Badge>}
                  <ArrowRight size={16} aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="workbench-copy">
            No decisions match. Clear your search or choose another status.
          </p>
        )}
      </div>
    </details>
  );
}
