import { useEffect, useRef, useState, useId } from 'react';
import type { Evidence } from '../api/contracts';

type Point = Evidence['chart'][number];
export function TrendChart({
  data,
  title = 'Reconciled ROAS',
  small = false,
  responsive = true,
}: {
  data: Point[];
  title?: string;
  small?: boolean;
  responsive?: boolean;
}) {
  const [active, setActive] = useState<number | null>(null);
  const observationId = useId();
  const chartRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(680);
  useEffect(() => {
    if (!responsive || !chartRef.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.max(280, Math.min(680, entry.contentRect.width))),
    );
    observer.observe(chartRef.current);
    return () => observer.disconnect();
  }, [responsive, data.length]);
  if (data.length === 0) return <p className="muted">No time-series observations yet.</p>;
  const w = responsive ? width : 680,
    h = small ? 150 : 230,
    left = 38,
    right = 12,
    top = 22,
    bottom = 30;
  const values = data.flatMap((d) => [d.actual, d.baseline]);
  const min = Math.max(0, Math.floor(Math.min(...values) - 0.3)),
    max = Math.ceil(Math.max(...values) + 0.3);
  const x = (i: number) => left + (i / Math.max(data.length - 1, 1)) * (w - left - right);
  const y = (v: number) => top + ((max - v) / Math.max(max - min, 1)) * (h - top - bottom);
  const points = (key: 'actual' | 'baseline') =>
    data.map((d, i) => `${x(i)},${y(d[key])}`).join(' ');
  const selectedIndex = Math.min(active ?? data.length - 1, data.length - 1);
  const selected = data[selectedIndex];
  return (
    <div className="trend-chart" ref={chartRef}>
      <div className="chart-meta">
        <span>
          <i className="legend-line actual" />
          {title}
        </span>
        <span>
          <i className="legend-line baseline" />
          Baseline forecast
        </span>
      </div>
      <svg
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-label={`${title}: last actual ${data.at(-1)!.actual.toFixed(2)} versus baseline ${data.at(-1)!.baseline.toFixed(2)}. Use the observation slider or data table for daily values.`}
      >
        {[0, 1, 2, 3].map((i) => {
          const value = min + ((max - min) * i) / 3;
          return (
            <g key={i}>
              <line x1={left} x2={w - right} y1={y(value)} y2={y(value)} className="grid-line" />
              <text x={left - 9} y={y(value) + 4} textAnchor="end">
                {value.toFixed(1)}×
              </text>
            </g>
          );
        })}
        <polygon
          points={`${left},${h - bottom} ${points('actual')} ${w - right},${h - bottom}`}
          className="chart-area"
        />
        <polyline points={points('baseline')} className="baseline-path" />
        <polyline points={points('actual')} className="actual-path" />
        {data.map((d, i) => (
          <g key={d.date}>
            <rect
              x={x(i) - 18}
              y={top}
              width={36}
              height={h - top - bottom}
              fill="transparent"
              onPointerEnter={() => setActive(i)}
              onPointerDown={() => setActive(i)}
            />
            <circle
              cx={x(i)}
              cy={y(d.actual)}
              r={selectedIndex === i || i === data.length - 1 ? 4 : 0}
              className="chart-dot"
            />
            {(i === 0 || i === data.length - 1 || i === 6) && (
              <text
                x={x(i)}
                y={h - 6}
                textAnchor={i === 0 ? 'start' : i === data.length - 1 ? 'end' : 'middle'}
              >
                {d.date.slice(5).replace('-', '/')}
              </text>
            )}
          </g>
        ))}
      </svg>
      <div className="chart-observation">
        <label htmlFor={observationId}>{selected.date}</label>
        <span>
          <strong>{selected.actual.toFixed(2)}×</strong> actual
        </span>
        <span>
          <strong>{selected.baseline.toFixed(2)}×</strong> baseline
        </span>
        <input
          id={observationId}
          type="range"
          min="0"
          max={data.length - 1}
          value={selectedIndex}
          aria-label={`${title} observation day`}
          aria-valuetext={`${selected.date}: actual ${selected.actual.toFixed(2)}, baseline ${selected.baseline.toFixed(2)}`}
          onChange={(event) => setActive(Number(event.target.value))}
        />
      </div>
      <details className="chart-access">
        <summary>View chart data</summary>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Actual</th>
                <th>Baseline</th>
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.date}>
                  <td>{d.date}</td>
                  <td>{d.actual.toFixed(2)}×</td>
                  <td>{d.baseline.toFixed(2)}×</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
export function Waterfall({ data, total }: { data: Evidence['decomposition']; total: number }) {
  let cumulative = 0;
  const values = data.map((d) => {
    const before = cumulative;
    cumulative += d.value;
    return { ...d, before, after: cumulative };
  });
  const min = Math.min(0, total, ...values.flatMap((d) => [d.before, d.after])) - 0.12;
  const max = Math.max(0, total, ...values.flatMap((d) => [d.before, d.after])) + 0.2;
  const y = (v: number) => 20 + ((max - v) / (max - min)) * 135;
  return (
    <div className="waterfall">
      <svg
        viewBox="0 0 500 200"
        role="img"
        aria-label={`Accounting decomposition of ROAS change: ${data.map((d) => `${d.label} ${d.value}`).join(', ')}; total ${total} points.`}
      >
        <line x1="8" x2="490" y1={y(0)} y2={y(0)} className="grid-line" />
        {[...values, { label: 'Total', value: total, before: 0, after: total }].map((d, i) => {
          const x = 18 + i * 96;
          return (
            <g key={d.label}>
              <rect
                x={x}
                y={y(Math.max(d.before, d.after))}
                width="60"
                height={Math.max(2, Math.abs(y(d.before) - y(d.after)))}
                rx="3"
                className={
                  i === values.length ? 'bar-total' : d.value > 0 ? 'bar-positive' : 'bar-negative'
                }
              />
              <text x={x + 30} y={y(Math.min(d.before, d.after)) + 18} textAnchor="middle">
                {d.value > 0 ? '+' : ''}
                {d.value.toFixed(2)}
              </text>
              <text x={x + 30} y="193" textAnchor="middle">
                {d.label}
              </text>
              {i < values.length - 1 && (
                <line
                  x1={x + 60}
                  x2={x + 96}
                  y1={y(d.after)}
                  y2={y(d.after)}
                  className="connector-line"
                />
              )}
            </g>
          );
        })}
      </svg>
      <p className="caption">
        Exact accounting decomposition · ROAS points · contributions sum to {total.toFixed(2)}
      </p>
    </div>
  );
}
