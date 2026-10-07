import { useState } from 'react';
import { ArrowDown, ArrowUp, ChevronDown } from 'lucide-react';
import type { OptimizerContext } from '../api/workbench-contracts';
import { money, percent } from '../lib/format';
import { Badge } from './ui';

type Sort = 'entity' | 'roas' | 'margin' | 'marginal_caa';
export function CampaignEconomics({ campaigns }: { campaigns: OptimizerContext['campaigns'] }) {
  const [search, setSearch] = useState('');
  const [channel, setChannel] = useState('ALL');
  const [sort, setSort] = useState<Sort>('marginal_caa');
  const [ascending, setAscending] = useState(false);
  const rows = campaigns
    .filter(
      (c) =>
        (channel === 'ALL' || c.platform === channel) &&
        `${c.entity} ${c.budget_id}`.toLowerCase().includes(search.trim().toLowerCase()),
    )
    .sort((a, b) => {
      const left = a[sort],
        right = b[sort];
      if (left === null) return right === null ? 0 : 1;
      if (right === null) return -1;
      const result =
        typeof left === 'string' && typeof right === 'string'
          ? left.localeCompare(right)
          : Number(left) - Number(right);
      return ascending ? result : -result;
    });
  const sortBy = (key: Sort) => {
    if (key === sort) setAscending(!ascending);
    else {
      setSort(key);
      setAscending(key === 'entity');
    }
  };
  const heading = (key: Sort, label: string) => (
    <th aria-sort={sort === key ? (ascending ? 'ascending' : 'descending') : 'none'}>
      <button className="table-sort" onClick={() => sortBy(key)}>
        {label}
        {sort === key &&
          (ascending ? (
            <ArrowUp size={13} aria-hidden="true" />
          ) : (
            <ArrowDown size={13} aria-hidden="true" />
          ))}
      </button>
    </th>
  );
  return (
    <details className="campaign-economics">
      <summary>
        <ChevronDown size={16} aria-hidden="true" />
        Compare campaign economics <span>{campaigns.length} budget units · read-only context</span>
      </summary>
      <div className="economics-body">
        <p className="section-description">
          ROAS measures return on ad spend. Margin and marginal contribution help explain whether
          extra spend is worthwhile. Marginal CAA is model output, not measured lift.
        </p>
        <div className="filter-bar">
          <label className="field">
            Find a campaign
            <input
              type="search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Campaign or budget ID"
            />
          </label>
          <label className="field">
            Campaign channel
            <select
              aria-label="Campaign channel"
              value={channel}
              onChange={(e) => setChannel(e.target.value)}
            >
              <option value="ALL">All channels</option>
              <option>Meta</option>
              <option>Google</option>
            </select>
          </label>
        </div>
        <p className="caption" role="status">
          {rows.length} matching budget units
        </p>
        {rows.length ? (
          <div className="table-scroll">
            <table className="economics-table">
              <thead>
                <tr>
                  {heading('entity', 'Campaign')}
                  <th>Current / day</th>
                  {heading('roas', 'ROAS')}
                  {heading('margin', 'Margin')}
                  {heading('marginal_caa', 'Marginal CAA / ₹1')}
                  <th>Inventory gate</th>
                  <th>Allowed budget / day</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.budget_id}>
                    <td>
                      <strong>{c.entity}</strong>
                      <small>
                        {c.platform} · {c.budget_id}
                      </small>
                    </td>
                    <td>{money(c.before)}</td>
                    <td>{c.roas.toFixed(2)}×</td>
                    <td>{percent(c.margin)}</td>
                    <td>{c.marginal_caa === null ? 'Not estimable' : c.marginal_caa.toFixed(2)}</td>
                    <td>
                      <Badge
                        tone={
                          c.inventory_gate === 'BLOCK'
                            ? 'danger'
                            : c.inventory_gate === 'LIMIT'
                              ? 'warning'
                              : 'neutral'
                        }
                      >
                        {c.inventory_gate.toLowerCase()}
                      </Badge>
                    </td>
                    <td>
                      {money(c.min_budget)} – {money(c.max_budget)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="workbench-copy">No campaigns match. Clear your search or change channel.</p>
        )}
      </div>
    </details>
  );
}
