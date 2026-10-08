import { useRef, useState, type CSSProperties } from 'react';
import { motion, useInView, useReducedMotion } from 'motion/react';
import { inr, outcomeActual, outcomeForecast, proposal, responseFactor, shift } from './scenario';
import { AppWindow, DataTable } from './parts';

const W = 560;
const H = 250;
const L = 52;
const R = 132;
const T = 16;
const B = 30;
const plotW = W - L - R;
const plotH = H - T - B;
const YMAX = 60000;
const x = (d: number) => L + (d / 7) * plotW;
const y = (v: number) => T + (1 - v / YMAX) * plotH;
const actual = [0, ...outcomeActual];
const forecastAt = (d: number) => (outcomeForecast * d) / 7;
const bandAt = (d: number) => [(proposal.interval[0] * d) / 7, (proposal.interval[1] * d) / 7];
const ease = [0.2, 0.7, 0.2, 1] as const;

const band =
  Array.from({ length: 8 }, (_, d) => `${x(d)},${y(bandAt(d)[1])}`).join(' ') +
  ' ' +
  Array.from({ length: 8 }, (_, d) => `${x(7 - d)},${y(bandAt(7 - d)[0])}`).join(' ');
const actualPath = actual.map((v, d) => `${d ? 'L' : 'M'}${x(d)},${y(v)}`).join('');

const loop = [
  { k: 'Decision', v: `${proposal.id} approved`, t: 'Day 0 · 09:12' },
  {
    k: 'Action',
    v: `PMax +${inr(shift)}/day, Prospecting −${inr(shift)}/day`,
    t: 'Day 0 · 09:14 · verified',
  },
  { k: 'Result', v: `+${inr(outcomeActual[6])} measured over 7 days`, t: 'Day 7' },
  {
    k: 'Learning',
    v: `Response factor ${responseFactor.toFixed(2)} written to calibration`,
    t: 'Next cycle',
  },
];

export function Outcome() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { amount: 0.4, once: true });
  const reduce = useReducedMotion();
  const on = inView || !!reduce;
  const [hover, setHover] = useState<number | null>(null);
  const diff = outcomeActual[6] - outcomeForecast;

  return (
    <section className="outcome" id="outcomes">
      <div className="lp-shell">
        <div className="section-head">
          <p className="eyebrow">05 · Learn</p>
          <h2 className="section-title">Then it checks its own work.</h2>
          <p className="lead">
            Every approved decision is measured against its forecast. The gap becomes a correction,
            so the next proposal starts from what actually happened, not from what the model hoped.
          </p>
        </div>

        <div ref={ref} className="oc-grid">
          <AppWindow
            crumb={
              <>
                Outcomes <i>/</i> <b>{proposal.id}</b>
              </>
            }
            className="oc-win"
          >
            <div className="oc-chart">
              <div className="oc-chart-head">
                <p className="st-label">Cumulative contribution change since approval</p>
                <ul className="st-legend" aria-hidden="true">
                  <li>
                    <i className="key-line" /> Measured
                  </li>
                  <li>
                    <i className="key-dash" /> Forecast
                  </li>
                  <li>
                    <i className="key-band" /> 80% interval
                  </li>
                </ul>
              </div>
              <div className="oc-plot">
                <svg
                  viewBox={`0 0 ${W} ${H}`}
                  role="img"
                  aria-label={`Measured +${inr(outcomeActual[6])} against a forecast of +${inr(outcomeForecast)} after 7 days`}
                >
                  {[0, 20000, 40000, 60000].map((v) => (
                    <g key={v}>
                      <line className="grid" x1={L} x2={L + plotW} y1={y(v)} y2={y(v)} />
                      <text className="tick" x={L - 8} y={y(v) + 3.5} textAnchor="end">
                        {v ? `₹${v / 1000}K` : '₹0'}
                      </text>
                    </g>
                  ))}
                  {Array.from({ length: 8 }, (_, d) => (
                    <text key={d} className="tick" x={x(d)} y={H - 8} textAnchor="middle">
                      {d === 0 ? 'Approve' : `D${d}`}
                    </text>
                  ))}
                  <motion.polygon
                    className="band"
                    points={band}
                    initial={false}
                    animate={{ opacity: on ? 1 : 0 }}
                    transition={{ duration: 0.8 }}
                  />
                  <motion.line
                    className="forecast"
                    x1={x(0)}
                    y1={y(0)}
                    x2={x(7)}
                    y2={y(outcomeForecast)}
                    initial={false}
                    animate={{ opacity: on ? 1 : 0 }}
                    transition={{ duration: 0.6, delay: 0.2 }}
                  />
                  <motion.path
                    className="roas-line"
                    d={actualPath}
                    initial={{ pathLength: reduce ? 1 : 0 }}
                    animate={{ pathLength: on ? 1 : 0 }}
                    transition={{ duration: 1.6, ease, delay: 0.5 }}
                  />
                  {actual.map((v, d) =>
                    d === 0 ? null : (
                      <motion.circle
                        key={d}
                        className="dot ink"
                        cx={x(d)}
                        cy={y(v)}
                        r={d === 7 ? 4.5 : 3}
                        initial={false}
                        animate={{ opacity: on ? 1 : 0 }}
                        transition={{ duration: 0.2, delay: reduce ? 0 : 0.5 + (d / 7) * 1.5 }}
                      />
                    ),
                  )}
                  <motion.g
                    initial={false}
                    animate={{ opacity: on ? 1 : 0 }}
                    transition={{ delay: reduce ? 0 : 2.1 }}
                  >
                    <text className="end-label muted" x={x(7) + 12} y={y(outcomeForecast) - 6}>
                      Forecast +{inr(outcomeForecast)}
                    </text>
                    <text className="end-label" x={x(7) + 12} y={y(outcomeActual[6]) + 16}>
                      Measured +{inr(outcomeActual[6])}
                    </text>
                  </motion.g>
                  {hover !== null && (
                    <line className="crosshair" x1={x(hover)} x2={x(hover)} y1={T} y2={T + plotH} />
                  )}
                  {Array.from({ length: 7 }, (_, i) => i + 1).map((d) => (
                    <rect
                      key={d}
                      className="hit"
                      x={x(d) - plotW / 14}
                      y={T}
                      width={plotW / 7}
                      height={plotH}
                      onMouseEnter={() => setHover(d)}
                      onMouseLeave={() => setHover(null)}
                    />
                  ))}
                </svg>
                {hover !== null && (
                  <div
                    className="tip"
                    style={{
                      left: `${(x(hover) / W) * 100}%`,
                      top: `${(y(actual[hover]) / H) * 100}%`,
                    }}
                  >
                    <b>Day {hover}</b>
                    <span>
                      Measured <em className="mono">+{inr(actual[hover])}</em>
                    </span>
                    <span>
                      Forecast{' '}
                      <em className="mono">+{inr(Math.round(forecastAt(hover) / 100) * 100)}</em>
                    </span>
                    <span>
                      Interval{' '}
                      <em className="mono">
                        {inr(Math.round(bandAt(hover)[0] / 100) * 100)}–
                        {inr(Math.round(bandAt(hover)[1] / 100) * 100)}
                      </em>
                    </span>
                  </div>
                )}
              </div>
            </div>
          </AppWindow>

          <div className="oc-side">
            <dl className="oc-compare">
              <div>
                <dt>Recommendation</dt>
                <dd>
                  Increase Google PMax <b className="mono">+{inr(shift)}/day</b>
                </dd>
              </div>
              <div>
                <dt>Expected</dt>
                <dd>
                  <span className="oc-fig">+{inr(outcomeForecast)}</span>
                  <small>
                    /week · 80% interval {inr(proposal.interval[0])}–{inr(proposal.interval[1])}
                  </small>
                </dd>
              </div>
              <div className="oc-actual">
                <dt>Measured</dt>
                <dd>
                  <span className="oc-fig">+{inr(outcomeActual[6])}</span>
                  <small>
                    /week · {inr(diff)} (
                    {((diff / outcomeForecast) * 100).toFixed(1).replace('-', '−')}%), inside the
                    interval
                  </small>
                </dd>
              </div>
            </dl>
            <div className="learn">
              <p className="learn-k">ADAPT learned from this outcome</p>
              <p>
                Response factor for PMax scale-ups{' '}
                <b className="mono">1.00 → {responseFactor.toFixed(2)}</b>
              </p>
              <div className="learn-meter" aria-hidden="true">
                <motion.i
                  initial={false}
                  animate={{ scaleX: on ? responseFactor : 1 }}
                  transition={{ duration: 0.9, ease, delay: reduce ? 0 : 2.3 }}
                />
                <b />
              </div>
              <small>The next forecast for a move like this starts 9% lower.</small>
            </div>
          </div>
        </div>

        <ol className="loop" data-on={on}>
          {loop.map((s, i) => (
            <li
              key={s.k}
              style={{ '--d': reduce ? '0ms' : `${2400 + i * 220}ms` } as CSSProperties}
            >
              <span className="loop-k mono">
                0{i + 1} · {s.k}
              </span>
              <b>{s.v}</b>
              <small className="mono">{s.t}</small>
            </li>
          ))}
        </ol>
        <DataTable
          caption="Cumulative contribution change after approval"
          head={['Day', 'Measured', 'Forecast', 'Interval low', 'Interval high']}
          rows={outcomeActual.map((v, i) => [
            `Day ${i + 1}`,
            inr(v),
            inr(Math.round(forecastAt(i + 1))),
            inr(Math.round(bandAt(i + 1)[0])),
            inr(Math.round(bandAt(i + 1)[1])),
          ])}
        />
      </div>
    </section>
  );
}
