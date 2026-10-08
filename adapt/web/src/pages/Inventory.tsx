import { Fragment, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Check,
  ChevronDown,
  Eye,
  PackagePlus,
  PauseCircle,
  Rocket,
  Search,
  Sparkles,
  Tags,
  Truck,
  type LucideIcon,
} from 'lucide-react';
import {
  inventory,
  type Inventory as InventoryData,
  type InventoryAction,
  type InventorySku,
} from '../api/inventory';
import { dataMode } from '../api/client';
import { Badge, Empty, ErrorState, Loading } from '../components/ui';
import { money } from '../lib/format';
import './inventory.css';

const actions: Record<
  InventoryAction,
  { label: string; icon: LucideIcon; tone: 'stop' | 'warn' | 'go' | 'calm'; rank: number }
> = {
  HOLD_SPEND: { label: 'Hold ad spend', icon: PauseCircle, tone: 'stop', rank: 0 },
  RESTOCK: { label: 'Restock', icon: PackagePlus, tone: 'stop', rank: 1 },
  EXPEDITE: { label: 'Expedite inbound', icon: Truck, tone: 'warn', rank: 2 },
  WATCH: { label: 'Watch velocity', icon: Eye, tone: 'warn', rank: 3 },
  SCALE_DEMAND: { label: 'Scale spend', icon: Rocket, tone: 'go', rank: 4 },
  CLEAR_EXCESS: { label: 'Clear excess', icon: Tags, tone: 'go', rank: 5 },
  CONTINUE: { label: 'Continue as is', icon: Check, tone: 'calm', rank: 6 },
};

type Filter = 'ALL' | 'NEEDS_ACTION' | InventoryAction;
const units = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 });
const rate = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 });
const shortDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });

export function Inventory() {
  const query = useQuery({ queryKey: ['inventory'], queryFn: inventory.get });
  const [filter, setFilter] = useState<Filter>('NEEDS_ACTION');
  const [search, setSearch] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const data = query.data;
  const rows = useMemo(() => {
    if (!data) return [];
    const term = search.trim().toLowerCase();
    return data.skus
      .filter((s) =>
        filter === 'ALL'
          ? true
          : filter === 'NEEDS_ACTION'
            ? s.action !== 'CONTINUE'
            : s.action === filter,
      )
      .filter(
        (s) =>
          !term ||
          s.sku.toLowerCase().includes(term) ||
          s.title.toLowerCase().includes(term) ||
          (s.category || '').toLowerCase().includes(term),
      )
      .sort(
        (a, b) =>
          actions[a.action].rank - actions[b.action].rank || b.ad_spend_28d - a.ad_spend_28d,
      );
  }, [data, filter, search]);

  if (query.isPending) return <Loading label="Loading inventory" />;
  if (query.error || !data)
    return (
      <ErrorState
        error={query.error || new Error('Inventory unavailable')}
        retry={() => void query.refetch()}
      />
    );
  const counts = data.skus.reduce(
    (acc, s) => ({ ...acc, [s.action]: (acc[s.action] || 0) + 1 }),
    {} as Partial<Record<InventoryAction, number>>,
  );
  const needsAction = data.skus.filter((s) => s.action !== 'CONTINUE').length;
  const outOfStock = data.skus.filter((s) => s.available <= 0).length;
  const atRiskSpend = data.skus
    .filter((s) => s.action === 'HOLD_SPEND' || s.action === 'RESTOCK')
    .reduce((sum, s) => sum + s.ad_spend_28d, 0);
  const excessUnits = data.skus
    .filter((s) => s.action === 'CLEAR_EXCESS')
    .reduce((sum, s) => sum + s.available, 0);
  const chips: { key: Filter; label: string; count: number }[] = [
    { key: 'NEEDS_ACTION', label: 'Needs action', count: needsAction },
    { key: 'ALL', label: 'All SKUs', count: data.skus.length },
    ...(Object.keys(actions) as InventoryAction[])
      .filter((a) => a !== 'CONTINUE' && counts[a])
      .map((a) => ({ key: a as Filter, label: actions[a].label, count: counts[a] || 0 })),
  ];
  const show = (f: Filter) => {
    setFilter(f);
    setSearch('');
    document.getElementById('inventory-table')?.scrollIntoView({ block: 'start' });
  };

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Inventory</h1>
          <p>Stock, demand and the ad spend behind every SKU, with the agent's next step.</p>
        </div>
        <Badge>
          As of {shortDate(data.as_of)} · last {data.window_days} days
        </Badge>
      </div>

      <dl className="inv-summary">
        <div>
          <dt>Need action</dt>
          <dd>
            {needsAction}
            <small> of {data.skus.length} SKUs</small>
          </dd>
        </div>
        <div>
          <dt>Out of stock</dt>
          <dd className={outOfStock ? 'is-stop' : ''}>{outOfStock}</dd>
        </div>
        <div>
          <dt>Ad spend on at-risk SKUs</dt>
          <dd className={atRiskSpend ? 'is-stop' : ''}>{money(atRiskSpend, true)}</dd>
        </div>
        <div>
          <dt>Excess units</dt>
          <dd>{units.format(excessUnits)}</dd>
        </div>
      </dl>

      <div className="inv-layout">
        <section className="inv-board" id="inventory-table" aria-label="SKU inventory">
          <header className="inv-board-head">
            <span className="inv-window" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
            <h2>Stock &amp; demand</h2>
            <span className="inv-crumb">
              Last {data.window_days} days / lead time {data.lead_time_days} days
            </span>
          </header>
          <div className="inv-tools">
            <div className="inv-chips" role="group" aria-label="Filter by recommendation">
              {chips.map((c) => (
                <button
                  key={c.key}
                  type="button"
                  aria-pressed={filter === c.key}
                  onClick={() => setFilter(c.key)}
                >
                  {c.label} <span>{c.count}</span>
                </button>
              ))}
            </div>
            <label className="inv-search">
              <Search size={15} aria-hidden="true" />
              <span className="sr-only">Search SKUs</span>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search SKU or category"
              />
            </label>
          </div>
          {rows.length === 0 ? (
            <Empty title="No SKUs match">Change the filter or search to see more SKUs.</Empty>
          ) : (
            <div className="table-scroll inv-scroll" tabIndex={0}>
              <table className="inv-table">
                <thead>
                  <tr>
                    <th scope="col">SKU</th>
                    <th scope="col" className="num">
                      Available
                    </th>
                    <th scope="col" className="num">
                      Sells / day
                    </th>
                    <th scope="col" className="agent-col">
                      <Sparkles size={14} aria-hidden="true" /> ADAPT agent
                    </th>
                    <th scope="col" className="num">
                      Days of cover
                    </th>
                    <th scope="col" className="num">
                      Inbound
                    </th>
                    <th scope="col" className="num">
                      Ad spend
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s, i) => (
                    <Fragment key={s.sku}>
                      <SkuRow
                        s={s}
                        data={data}
                        last={i === rows.length - 1 && open !== s.sku}
                        expanded={open === s.sku}
                        toggle={() => setOpen(open === s.sku ? null : s.sku)}
                      />
                      {open === s.sku && <SkuDetail s={s} data={data} />}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="caption inv-note">{data.note}</p>
        </section>

        <AgentFeed data={data} counts={counts} show={show} />
      </div>
    </>
  );
}

function SkuRow({
  s,
  data,
  last,
  expanded,
  toggle,
}: {
  s: InventorySku;
  data: InventoryData;
  last: boolean;
  expanded: boolean;
  toggle: () => void;
}) {
  const a = actions[s.action];
  const Icon = a.icon;
  const change = s.velocity_change;
  const fast = change !== null && change >= data.velocity_alert;
  return (
    <tr className={`${expanded ? 'is-open' : ''} ${last ? 'is-last' : ''}`}>
      <th scope="row">
        <button
          type="button"
          className="inv-sku"
          aria-expanded={expanded}
          onClick={toggle}
          title="Show the agent's reasoning"
        >
          <ChevronDown size={14} aria-hidden="true" />
          <span>
            <strong>{s.title}</strong>
            <small>
              {s.sku}
              {s.category ? ` · ${s.category}` : ''}
            </small>
            <em className={`inv-action inv-action-inline tone-${a.tone}`}>
              <Icon size={12} aria-hidden="true" />
              {a.label}
            </em>
          </span>
        </button>
      </th>
      <td className={`num ${s.available <= 0 ? 'is-stop' : ''}`}>
        {s.available <= 0 ? 'Out' : units.format(s.available)}
      </td>
      <td className="num">
        {rate.format(s.units_7d_avg)}
        {change !== null && (
          <small className={fast ? 'is-fast' : change < 0 ? 'is-slow' : ''}>
            {change >= 0 ? <ArrowUpRight size={11} /> : <ArrowDownRight size={11} />}
            {Math.abs(Math.round(change * 100))}%
          </small>
        )}
      </td>
      <td className="agent-col">
        <span className={`inv-action tone-${a.tone}`}>
          <Icon size={14} aria-hidden="true" />
          {a.label}
        </span>
      </td>
      <td className="num">
        {s.cover_days === null ? (
          '—'
        ) : (
          <CoverBar
            days={s.cover_days}
            lead={data.lead_time_days}
            excess={data.excess_cover_days}
          />
        )}
      </td>
      <td className="num">
        {s.inbound_qty ? (
          <>
            {units.format(s.inbound_qty)}
            {s.expected_arrival && <small>{shortDate(s.expected_arrival)}</small>}
          </>
        ) : (
          <span className="muted">—</span>
        )}
      </td>
      <td className="num">{s.ad_spend_28d ? money(s.ad_spend_28d, true) : '—'}</td>
    </tr>
  );
}

function CoverBar({ days, lead, excess }: { days: number; lead: number; excess: number }) {
  const max = excess * 1.5;
  const tone = days < lead ? 'stop' : days > excess ? 'go' : 'calm';
  return (
    <span className="inv-cover">
      <span className="inv-cover-value">{Math.round(days)}d</span>
      <span className="inv-cover-track" aria-hidden="true">
        <span
          className={`inv-cover-fill tone-${tone}`}
          style={{ width: `${Math.min(100, (days / max) * 100)}%` }}
        />
        <span className="inv-cover-mark" style={{ left: `${(lead / max) * 100}%` }} />
        <span className="inv-cover-mark" style={{ left: `${(excess / max) * 100}%` }} />
      </span>
    </span>
  );
}

function SkuDetail({ s, data }: { s: InventorySku; data: InventoryData }) {
  return (
    <tr className="inv-detail">
      <td colSpan={7}>
        <p>
          <Sparkles size={14} aria-hidden="true" /> {s.reason}
        </p>
        <dl>
          <div>
            <dt>On hand / reserved</dt>
            <dd>
              {units.format(s.on_hand)} / {units.format(s.reserved)}
            </dd>
          </div>
          <div>
            <dt>Reorder point</dt>
            <dd>{units.format(s.reorder_point)}</dd>
          </div>
          <div>
            <dt>Safety stock</dt>
            <dd>{units.format(s.safety_stock)}</dd>
          </div>
          <div>
            <dt>Sells / day (7d vs {data.window_days}d)</dt>
            <dd>
              {rate.format(s.units_7d_avg)} vs {rate.format(s.units_28d_avg)}
            </dd>
          </div>
          <div>
            <dt>Days out of stock</dt>
            <dd>{s.stockout_days_28d}</dd>
          </div>
          <div>
            <dt>Net revenue</dt>
            <dd>{money(s.net_revenue_28d, true)}</dd>
          </div>
          <div>
            <dt>Contribution before ads</dt>
            <dd>{money(s.cba_28d, true)}</dd>
          </div>
        </dl>
      </td>
    </tr>
  );
}

type Alert = {
  key: InventoryAction | 'VELOCITY';
  filter: Filter;
  title: string;
  body: string;
};

function AgentFeed({
  data,
  counts,
  show,
}: {
  data: InventoryData;
  counts: Partial<Record<InventoryAction, number>>;
  show: (f: Filter) => void;
}) {
  const sum = (pick: (s: InventorySku) => boolean, value: (s: InventorySku) => number) =>
    data.skus.filter(pick).reduce((t, s) => t + value(s), 0);
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const fast = data.skus.filter(
    (s) => s.velocity_change !== null && s.velocity_change >= data.velocity_alert,
  );
  const alerts: Alert[] = [];
  if (counts.HOLD_SPEND)
    alerts.push({
      key: 'HOLD_SPEND',
      filter: 'HOLD_SPEND',
      title: 'Stockout & ad spend',
      body: `${plural(counts.HOLD_SPEND, 'SKU')} will sell out before inbound stock lands while their ads keep spending (${money(
        sum(
          (s) => s.action === 'HOLD_SPEND',
          (s) => s.ad_spend_28d,
        ),
        true,
      )} in ${data.window_days} days). Reduce spend until stock arrives.`,
    });
  if (counts.RESTOCK)
    alerts.push({
      key: 'RESTOCK',
      filter: 'RESTOCK',
      title: 'Inventory replenishment',
      body: `${plural(counts.RESTOCK, 'SKU')} sit at or below the reorder point with no inbound order to cover the ${data.lead_time_days}-day lead time. See what to re-stock first.`,
    });
  if (fast.length)
    alerts.push({
      key: 'VELOCITY',
      filter: 'ALL',
      title: 'Sell-through velocity',
      body: `${plural(fast.length, 'SKU')} are selling ${Math.round(data.velocity_alert * 100)}%+ faster than their ${data.window_days}-day average. See what's ready to scale and what needs a reorder.`,
    });
  if (counts.CLEAR_EXCESS)
    alerts.push({
      key: 'CLEAR_EXCESS',
      filter: 'CLEAR_EXCESS',
      title: 'Excess stock',
      body: `${plural(counts.CLEAR_EXCESS, 'SKU')} hold more than ${data.excess_cover_days} days of cover (${units.format(
        sum(
          (s) => s.action === 'CLEAR_EXCESS',
          (s) => s.available,
        ),
      )} units). The Inventory Clearance objective can move spend toward them.`,
    });
  if (counts.EXPEDITE)
    alerts.push({
      key: 'EXPEDITE',
      filter: 'EXPEDITE',
      title: 'Inbound timing',
      body: `${plural(counts.EXPEDITE, 'SKU')} run out before their inbound arrives. Bring the purchase orders forward where possible.`,
    });
  const [featured, ...rest] = alerts;
  return (
    <aside className="inv-feed" aria-label="Agent alerts">
      <div className="inv-feed-head">
        <h2>Agent alerts</h2>
        <span>{alerts.length ? `${alerts.length} active` : 'All clear'}</span>
      </div>
      {featured ? (
        <button
          type="button"
          className="inv-alert inv-alert-featured"
          onClick={() => show(featured.filter)}
        >
          <span className="inv-alert-mark" aria-hidden="true">
            <Sparkles size={16} />
          </span>
          <span>
            <strong>{featured.title} agent</strong>
            <span>{featured.body}</span>
          </span>
        </button>
      ) : (
        <div className="inv-alert">
          <strong>Every SKU is within range</strong>
          <span>Cover, reorder points and velocity need no action today.</span>
        </div>
      )}
      {rest.map((a) => (
        <button type="button" className="inv-alert" key={a.key} onClick={() => show(a.filter)}>
          <strong>
            <i aria-hidden="true" /> {a.title} agent
          </strong>
          <span>{a.body}</span>
          <small>
            Show SKUs <ArrowRight size={12} aria-hidden="true" />
          </small>
        </button>
      ))}
      <Link className="inv-feed-link" to="/decisions">
        Budget changes go through the Decision Center <ArrowRight size={14} aria-hidden="true" />
      </Link>
      {dataMode === 'fixture' && (
        <p className="caption">
          Illustrative SKUs. Connect the API for ERP stock and store demand.
        </p>
      )}
    </aside>
  );
}
