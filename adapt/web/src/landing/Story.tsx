import { useLayoutEffect, useRef, useState } from 'react';
import {
  motion,
  useMotionValueEvent,
  useReducedMotion,
  useScroll,
  useTransform,
  type MotionValue,
} from 'motion/react';
import {
  bridge,
  changePoint,
  channels,
  drivers,
  expectedBand,
  inr,
  lostRevenue,
  marginPct,
  pct,
  prospectingDaily,
  primary,
  proposal,
  roasAfter,
  roasBefore,
  roasChange,
  roasDates,
  roasSeries,
  shift,
  sku,
  totalDaily,
} from './scenario';
import { AppWindow, DataTable, useFixed } from './parts';

/* ── Chapter copy ─────────────────────────────────────────────────────────── */

const steps = ['Observe', 'Diagnose', 'Decide', 'Act', 'Learn'];
const chapters = [
  {
    step: 0,
    title: 'Something changed.',
    body: `Meta Prospecting fell from ${roasBefore.toFixed(2)} to ${roasAfter.toFixed(2)} ROAS in six days, ${pct(roasChange)}. The morning cycle flags it the day it leaves its expected range, not at the end of the week.`,
  },
  {
    step: 1,
    title: 'Not the auction. The store.',
    body: `ADAPT splits ROAS into the four numbers that make it up. CPM and CTR barely moved. Conversion rate fell ${pct(primary.change)} and accounts for ${Math.round(primary.share * 100)}% of the drop.`,
  },
  {
    step: 1,
    title: 'Ad metrics alone can’t tell you what to do.',
    body: `${Math.round(sku.landingShare * 100)}% of that traffic lands on the ${sku.name}. Sizes M and L are sold out, stock covers ${sku.coverDays} days against a ${sku.restockDays}-day restock, and its margin slipped from ${marginPct(sku.marginBefore)} to ${marginPct(sku.marginAfter)}.`,
  },
  {
    step: 2,
    title: 'One recommendation, evidence attached.',
    body: `Move ${inr(shift)} a day to Google PMax, whose traffic lands on in-stock, higher-margin products. Forecast +${inr(proposal.weeklyUplift)} contribution a week. Every policy check passes, and nothing moves until you approve.`,
  },
  {
    step: 3,
    title: 'Approved. The budget moves.',
    body: `Meta goes from 48% to 40% of daily spend and Google from 27% to 35%, inside the daily change limit. The forecast moves with the money.`,
  },
];

/* ── Plot geometry (viewBox units) ────────────────────────────────────────── */

const PW = 640;
const PH = 250;
const PL = 44;
const PT = 14;
const plotW = 580;
const plotH = 200;
const Y0 = 2.5;
const Y1 = 4.5;
const y = (v: number) => PT + ((Y1 - v) / (Y1 - Y0)) * plotH;
const xAt = (i: number, k: number) => PL + i * (plotW / 13) * k;
const COMPRESSED = 0.42;
const B0 = PL + plotW * COMPRESSED + 26;
const slotW = (PW - 14 - B0) / 6;
const slotX = (i: number) => B0 + slotW * (i + 0.5);

// Running ROAS levels through the bridge: before → after each driver.
const levels = bridge.reduce<number[]>(
  (acc, b) => [...acc, acc[acc.length - 1] + b.impact],
  [roasBefore],
);

const fmtDriver = (unit: string, v: number) =>
  unit === 'pct' ? `${(v * 100).toFixed(2)}%` : inr(v);

/* ── Stage: one window, five states, driven by p ∈ [0, 1] ─────────────────── */

function Stage({ p, chapter }: { p: MotionValue<number>; chapter: number }) {
  // Signal
  const draw = useTransform(p, [0.015, 0.11], [0, 1]);
  const k = useTransform(p, [0.2, 0.26], [1, COMPRESSED]);
  const path = useTransform(k, (kk) =>
    roasSeries
      .map((v, i) => `${i ? 'L' : 'M'}${xAt(i, kk).toFixed(1)},${y(v).toFixed(1)}`)
      .join(''),
  );
  const figure = useTransform(draw, (d) => {
    const at = d * 13;
    const i = Math.min(12, Math.floor(at));
    return (roasSeries[i] + (roasSeries[i + 1] - roasSeries[i]) * (at - i)).toFixed(2);
  });
  const figureDate = useTransform(draw, (d) => roasDates[Math.round(d * 13)]);
  const bandOpacity = useTransform(p, [0, 0.04], [0, 1]);
  const bandW = useTransform(k, (kk) => plotW * kk);
  const windowX = useTransform(k, (kk) => xAt(changePoint - 0.5, kk));
  const windowW = useTransform(k, (kk) => xAt(13, kk) - xAt(changePoint - 0.5, kk));
  const windowOpacity = useTransform(p, [0.09, 0.12], [0, 1]);
  const deltaOpacity = useTransform(p, [0.1, 0.12], [0, 1]);
  const cpX = useTransform(k, (kk) => xAt(changePoint, kk));
  const endX = useTransform(k, (kk) => xAt(13, kk));
  const cpOpacity = useTransform(p, [0.06, 0.075], [0, 1]);
  const endOpacity = useTransform(p, [0.105, 0.115], [0, 1]);
  const noteOpacity = useTransform(p, [0.12, 0.14, 0.2, 0.22], [0, 1, 1, 0]);
  const dateMidX = useTransform(k, (kk) => xAt(changePoint, kk));
  const dateEndX = useTransform(k, (kk) => xAt(13, kk));

  const detectOpacity = useTransform(p, [0.12, 0.15, 0.2, 0.23], [0, 1, 1, 0]);
  const detectY = useTransform(p, [0.12, 0.15], [12, 0]);

  // Diagnosis
  const bridgeOpacity = useTransform(p, [0.23, 0.25], [0, 1]);
  const startTick = useTransform(p, [0.24, 0.26], [0, 1]);
  const barGrow = bridge.map((_, i) =>
    useTransform(p, [0.26 + i * 0.022, 0.28 + i * 0.022], [0, 1]),
  );
  const endTick = useTransform(p, [0.35, 0.37], [0, 1]);
  const recede = useTransform(p, [0.36, 0.39], [1, 0.28]);
  const focusBand = useTransform(p, [0.36, 0.39], [0, 1]);

  // Driver table → SKU context
  const rowIn = drivers.map((_, i) =>
    useTransform(
      p,
      [0.24 + i * 0.02, 0.27 + i * 0.02, 0.36, 0.39, 0.4, 0.43],
      [0, 1, 1, i === 2 ? 1 : 0.32, i === 2 ? 1 : 0.32, i === 2 ? 1 : 0],
    ),
  );
  const tableHead = useTransform(p, [0.24, 0.26, 0.4, 0.43], [0, 1, 1, 0]);
  const cvrLift = useTransform(p, [0.41, 0.45], [0, -1]);
  const cvrY = useTransform(cvrLift, (v) => `${v * 200}%`);
  const cvrHighlight = useTransform(p, [0.36, 0.39], ['rgba(20,21,24,0)', 'rgba(20,21,24,0.05)']);
  const tagOpacity = useTransform(p, [0.37, 0.39], [0, 1]);
  const skuIn = [0, 1, 2, 3].map((i) =>
    useTransform(p, [0.44 + i * 0.02, 0.47 + i * 0.02], [0, 1]),
  );
  const skuRise = skuIn.map((o) => useTransform(o, [0, 1], [10, 0]));
  const coverFill = useTransform(p, [0.5, 0.55], [0, sku.coverDays / sku.restockDays]);
  const margin = useTransform(p, [0.51, 0.56], [sku.marginBefore * 100, sku.marginAfter * 100]);
  const marginText = useTransform(margin, (m) => `${Math.round(m)}%`);

  // Analysis out, decision in
  const analysisOpacity = useTransform(p, [0.6, 0.64], [1, 0]);
  const analysisY = useTransform(p, [0.6, 0.64], [0, -24]);
  const evidence = [0, 1, 2].map((i) =>
    useTransform(p, [0.63 + i * 0.018, 0.66 + i * 0.018, 0.8, 0.83], [0, 1, 1, 0]),
  );
  const evidenceY = evidence.map((o) => useTransform(o, [0, 1], [-10, 0]));
  const cardOpacity = useTransform(p, [0.67, 0.71, 0.8, 0.83], [0, 1, 1, 0]);
  const cardY = useTransform(p, [0.67, 0.72, 0.8, 0.83], [36, 0, 0, -16]);
  const checks = proposal.checks.map((_, i) =>
    useTransform(p, [0.71 + i * 0.014, 0.725 + i * 0.014], [0, 1]),
  );
  const decisionVisible = useTransform(p, (v) => (v > 0.6 ? 'visible' : 'hidden'));

  // Act: the budget flow
  const approved = useTransform(p, [0.81, 0.84], [0, 1]);
  const flowOpacity = useTransform(p, [0.82, 0.86], [0, 1]);
  const flowY = useTransform(p, [0.82, 0.86], [24, 0]);
  const t = useTransform(p, [0.86, 0.96], [0, 1]);
  const flowVisible = useTransform(p, (v) => (v > 0.8 ? 'visible' : 'hidden'));

  const decisionStage = chapter >= 3;
  return (
    <AppWindow
      className="stage-win"
      crumb={
        <span className="crumb-swap" key={decisionStage ? 'd' : 'a'}>
          {decisionStage ? (
            <>
              Decision Center <i>/</i> <b>{proposal.id}</b>
            </>
          ) : (
            <>
              Anomalies <i>/</i> <b>Meta Prospecting</b>
            </>
          )}
        </span>
      }
    >
      {/* Analysis: signal, bridge, drivers, SKU */}
      <motion.div className="layer analysis" style={{ opacity: analysisOpacity, y: analysisY }}>
        <div className="st-head">
          <div>
            <p className="st-label">
              ROAS · <motion.span>{figureDate}</motion.span>
            </p>
            <p className="st-figure">
              <motion.span>{figure}</motion.span>
              <motion.span className="st-delta harm" style={{ opacity: deltaOpacity }}>
                {pct(roasChange)} in 6 days
              </motion.span>
            </p>
          </div>
          <ul className="st-legend" aria-hidden="true">
            <li>
              <i className="key-line" /> Daily ROAS
            </li>
            <li>
              <i className="key-band" /> Expected range
            </li>
            <motion.li style={{ opacity: bridgeOpacity }}>
              <i className="key-bar" />
              <i className="key-bar gain" /> Driver impact
            </motion.li>
          </ul>
        </div>

        <svg className="st-plot" viewBox={`0 0 ${PW} ${PH}`} aria-hidden="true">
          {[2.5, 3, 3.5, 4, 4.5].map((v) => (
            <g key={v}>
              <line className="grid" x1={PL} x2={PW - 14} y1={y(v)} y2={y(v)} />
              <text className="tick" x={PL - 8} y={y(v) + 3.5} textAnchor="end">
                {v.toFixed(1)}
              </text>
            </g>
          ))}
          <motion.rect
            className="band"
            x={PL}
            y={y(expectedBand[1])}
            height={y(expectedBand[0]) - y(expectedBand[1])}
            width={bandW}
            style={{ opacity: bandOpacity }}
          />
          <motion.rect
            className="anomaly-window"
            y={PT}
            height={plotH}
            width={windowW}
            style={{ opacity: windowOpacity, x: windowX }}
          />
          <motion.path className="roas-line" d={path} style={{ pathLength: draw }} />
          <motion.circle
            className="dot harm"
            r={4.5}
            cy={y(roasSeries[changePoint])}
            style={{ x: cpX, opacity: cpOpacity }}
          />
          <motion.circle
            className="dot ink"
            r={4.5}
            cy={y(roasAfter)}
            style={{ x: endX, opacity: endOpacity }}
          />
          <motion.g style={{ x: cpX, opacity: noteOpacity }}>
            <line
              className="leader"
              x1={0}
              x2={0}
              y1={y(roasSeries[changePoint]) - 9}
              y2={y(4.36)}
            />
            <text className="note" x={6} y={y(4.36) + 4}>
              Change point · {roasDates[changePoint]}
            </text>
            <text className="note muted" x={6} y={y(4.36) + 18}>
              outside expected range
            </text>
          </motion.g>

          <text className="tick" x={PL} y={PH - 8}>
            {roasDates[0]}
          </text>
          <motion.text
            className="tick"
            y={PH - 8}
            textAnchor="middle"
            style={{ x: dateMidX, opacity: recede }}
          >
            {roasDates[changePoint]}
          </motion.text>
          <motion.text className="tick" y={PH - 8} textAnchor="end" style={{ x: dateEndX }}>
            {roasDates[13]}
          </motion.text>

          {/* ROAS bridge: shares the y-axis with the line, so the bars span exactly the gap */}
          <motion.g style={{ opacity: bridgeOpacity }}>
            <motion.rect
              className="focus-band"
              x={slotX(3) - slotW / 2 + 3}
              width={slotW - 6}
              y={PT - 4}
              height={plotH + 8}
              rx={6}
              style={{ opacity: focusBand }}
            />
            <motion.g style={{ opacity: startTick }}>
              <line
                className="level"
                x1={slotX(0) - 13}
                x2={slotX(0) + 13}
                y1={y(roasBefore)}
                y2={y(roasBefore)}
              />
              <text className="val" x={slotX(0)} y={y(roasBefore) - 8} textAnchor="middle">
                {roasBefore.toFixed(2)}
              </text>
            </motion.g>
            {bridge.map((b, i) => {
              const a = levels[i];
              const z = levels[i + 1];
              const down = z < a;
              const top = y(Math.max(a, z));
              const h = Math.max(1.5, Math.abs(y(z) - y(a)));
              const harm = b.impact < 0;
              const isPrimary = b.key === primary.key;
              return (
                <motion.g key={b.key} style={{ opacity: isPrimary ? 1 : recede }}>
                  <line
                    className="connector"
                    x1={slotX(i) + 11}
                    x2={slotX(i + 1) - 11}
                    y1={y(a)}
                    y2={y(a)}
                  />
                  <motion.rect
                    className={`bar ${harm ? 'harm' : 'gain'}`}
                    x={slotX(i + 1) - 11}
                    y={top}
                    width={22}
                    height={h}
                    rx={2}
                    style={{ scaleY: barGrow[i], originY: down ? 0 : 1, transformBox: 'fill-box' }}
                  />
                  <motion.text
                    className={`val ${isPrimary ? 'strong' : ''}`}
                    x={slotX(i + 1)}
                    y={top - 8}
                    textAnchor="middle"
                    style={{ opacity: barGrow[i] }}
                  >
                    {b.impact > 0 ? '+' : '−'}
                    {Math.abs(b.impact).toFixed(2)}
                  </motion.text>
                  {isPrimary && (
                    <motion.text
                      className="note"
                      x={slotX(i + 1)}
                      y={y(z) + 18}
                      textAnchor="middle"
                      style={{ opacity: focusBand }}
                    >
                      {Math.round(b.share * 100)}% of drop
                    </motion.text>
                  )}
                </motion.g>
              );
            })}
            <motion.g style={{ opacity: endTick }}>
              <line
                className="connector"
                x1={slotX(4) + 11}
                x2={slotX(5) - 13}
                y1={y(levels[4])}
                y2={y(levels[4])}
              />
              <line
                className="level"
                x1={slotX(5) - 13}
                x2={slotX(5) + 13}
                y1={y(roasAfter)}
                y2={y(roasAfter)}
              />
              <text className="val" x={slotX(5)} y={y(roasAfter) + 18} textAnchor="middle">
                {roasAfter.toFixed(2)}
              </text>
            </motion.g>
            {['Before', ...bridge.map((b) => b.key), 'After'].map((label, i) => (
              <text
                key={label}
                className={`tick ${label === primary.key ? 'strong' : ''}`}
                x={slotX(i)}
                y={PH - 8}
                textAnchor="middle"
              >
                {label}
              </text>
            ))}
          </motion.g>
        </svg>

        <div className="st-lower">
          <motion.div className="detect" style={{ opacity: detectOpacity, y: detectY }}>
            <div>
              <label>Spend per day</label>
              <p>{inr(prospectingDaily)}</p>
              <small>Unchanged through the drop</small>
            </div>
            <div>
              <label>Revenue per day</label>
              <p>
                {inr(prospectingDaily * roasBefore)} <span>→</span>{' '}
                {inr(prospectingDaily * roasAfter)}
              </p>
              <small>Same spend, less return</small>
            </div>
            <div>
              <label>Revenue lost, 6 days</label>
              <p className="harm-text">{inr(Math.round(lostRevenue / 100) * 100)}</p>
              <small>Against the pre-change ROAS</small>
            </div>
          </motion.div>
          <motion.div className="drv-head" style={{ opacity: tableHead }}>
            <span>Driver</span>
            <span>Before → after</span>
            <span>Change</span>
            <span>ROAS impact</span>
          </motion.div>
          <div className="drv-rows">
            {drivers.map((d, i) => {
              const b = bridge[i];
              const isPrimary = d.key === primary.key;
              return (
                <motion.div
                  key={d.key}
                  className={`drv-row ${isPrimary ? 'primary' : ''}`}
                  style={{
                    opacity: rowIn[i],
                    ...(isPrimary ? { y: cvrY, backgroundColor: cvrHighlight } : {}),
                  }}
                >
                  <span className="drv-key">
                    <b>{d.key}</b>
                    <small>{d.label}</small>
                  </span>
                  <span className="mono">
                    {fmtDriver(d.unit, d.before)} → {fmtDriver(d.unit, d.after)}
                  </span>
                  <span className={`mono ${b.impact < 0 ? 'harm' : 'gain'}`}>{pct(b.change)}</span>
                  <span className="mono">
                    {b.impact > 0 ? '+' : '−'}
                    {Math.abs(b.impact).toFixed(2)}
                    {isPrimary && (
                      <motion.em className="tag primary-tag" style={{ opacity: tagOpacity }}>
                        Primary driver
                      </motion.em>
                    )}
                  </span>
                </motion.div>
              );
            })}
          </div>

          <div className="sku">
            <motion.p className="sku-link" style={{ opacity: skuIn[0], y: skuRise[0] }}>
              <span aria-hidden="true">↳</span> {Math.round(sku.landingShare * 100)}% of these
              sessions land on
            </motion.p>
            <motion.div className="sku-head" style={{ opacity: skuIn[0], y: skuRise[0] }}>
              <b>{sku.name}</b>
              <code>{sku.code}</code>
              <span className="mono">{inr(sku.price)}</span>
            </motion.div>
            <div className="sku-grid">
              <motion.div style={{ opacity: skuIn[1], y: skuRise[1] }}>
                <label>Sizes in stock</label>
                <div className="sizes">
                  {sku.sizes.map((s) => (
                    <span key={s.size} className={s.inStock ? '' : 'out'}>
                      {s.size}
                    </span>
                  ))}
                </div>
                <small className="harm-text">M and L sold out</small>
              </motion.div>
              <motion.div style={{ opacity: skuIn[2], y: skuRise[2] }}>
                <label>Stock cover vs restock</label>
                <div className="meter">
                  <motion.i style={{ scaleX: coverFill }} />
                  <b style={{ left: '100%' }} />
                </div>
                <small>
                  <strong className="harm-text">{sku.coverDays} days</strong> · restock takes{' '}
                  {sku.restockDays}
                </small>
              </motion.div>
              <motion.div style={{ opacity: skuIn[3], y: skuRise[3] }}>
                <label>Contribution margin</label>
                <p className="sku-figure">
                  <span className="was">{marginPct(sku.marginBefore)}</span>
                  <motion.span>{marginText}</motion.span>
                </p>
                <small>COGS +{inr(sku.price * (sku.marginBefore - sku.marginAfter))} a unit</small>
              </motion.div>
            </div>
          </div>
        </div>
      </motion.div>

      {/* Decision: evidence stack + proposal */}
      <motion.div className="layer decision" style={{ visibility: decisionVisible }}>
        <ol className="evidence">
          {[
            [
              'ROAS',
              `${roasBefore.toFixed(2)} → ${roasAfter.toFixed(2)}`,
              `${pct(roasChange)} in 6 days`,
            ],
            [
              'Primary driver',
              'Conversion rate',
              `${pct(primary.change)} · ${Math.round(primary.share * 100)}% of the drop`,
            ],
            [
              'Landing SKU',
              sku.name,
              `${sku.coverDays} days cover · M/L sold out · ${marginPct(sku.marginAfter)} margin`,
            ],
          ].map(([k, v, d], i) => (
            <motion.li key={k} style={{ opacity: evidence[i], y: evidenceY[i] }}>
              <span className="ev-k">{k}</span>
              <b>{v}</b>
              <span className="ev-d">{d}</span>
            </motion.li>
          ))}
        </ol>
        <motion.div className="proposal" style={{ opacity: cardOpacity, y: cardY }}>
          <div className="pr-top">
            <span className="tag action">Shift budget</span>
            <span className="pr-id">
              {proposal.id} · awaiting approval · <code>{proposal.hash}</code>
            </span>
          </div>
          <div className="pr-moves">
            <div>
              <span>{proposal.from.name}</span>
              <span className="mono muted">{inr(proposal.from.daily)}/day</span>
              <b className="mono harm-text">−{inr(shift)}</b>
            </div>
            <div>
              <span>{proposal.to.name}</span>
              <span className="mono muted">{inr(proposal.to.daily)}/day</span>
              <b className="mono gain-text">+{inr(shift)}</b>
            </div>
          </div>
          <div className="pr-figures">
            <div>
              <label>Forecast contribution</label>
              <p className="pr-figure">
                +{inr(proposal.weeklyUplift)}
                <small>/week</small>
              </p>
              <small>
                80% interval {inr(proposal.interval[0])}–{inr(proposal.interval[1])}
              </small>
            </div>
            <div>
              <label>Model confidence</label>
              <p className="pr-figure">{Math.round(proposal.confidence * 100)}%</p>
              <div className="conf" aria-hidden="true">
                <i style={{ transform: `scaleX(${proposal.confidence})` }} />
              </div>
            </div>
          </div>
          <ul className="checks">
            {proposal.checks.map((c, i) => (
              <motion.li key={c.name} style={{ opacity: checks[i] }}>
                <span className="tick-mark" aria-hidden="true">
                  ✓
                </span>
                <b>{c.name}</b>
                <span>{c.detail}</span>
              </motion.li>
            ))}
          </ul>
          <div className="pr-actions">
            <span className="fake-btn primary">Approve shift</span>
            <span className="fake-btn">Open evidence</span>
          </div>
        </motion.div>
      </motion.div>

      {/* Act: budget flow */}
      <motion.div className="layer flow-layer" style={{ visibility: flowVisible }}>
        <motion.p className="approved" style={{ opacity: approved }}>
          <span className="tick-mark" aria-hidden="true">
            ✓
          </span>
          Approved 09:12 IST · <b>{proposal.id}</b> · within daily change limit
        </motion.p>
        <motion.div style={{ opacity: flowOpacity, y: flowY }}>
          <BudgetFlow t={t} />
        </motion.div>
      </motion.div>
    </AppWindow>
  );
}

/* ── Signature: budget flow ───────────────────────────────────────────────── */

const SCALE = 0.6; // a full track is 60% of daily budget; leaves room for the token's arc
const ROW_H = 44;

function BudgetFlow({ t }: { t: MotionValue<number> }) {
  const track = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(400);
  useLayoutEffect(() => {
    const el = track.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const meta = channels[0];
  const google = channels[1];
  const moved = meta.before - meta.after; // 0.08
  const start = { x: (meta.before / SCALE) * w, y: ROW_H / 2 };
  const end = { x: (google.after / SCALE) * w, y: ROW_H * 1.5 };
  const ctrl = { x: Math.min(w - 4, Math.max(start.x, end.x) + w * 0.22), y: ROW_H };
  const at = (v: number) => {
    const u = 1 - v;
    return {
      x: u * u * start.x + 2 * u * v * ctrl.x + v * v * end.x,
      y: u * u * start.y + 2 * u * v * ctrl.y + v * v * end.y,
    };
  };
  const tokenX = useTransform(t, (v) => at(v).x);
  const tokenY = useTransform(t, (v) => at(v).y);
  const tokenOpacity = useTransform(t, [0, 0.04, 0.96, 1], [0, 1, 1, 0]);
  const trail = useTransform(t, [0, 1], [0, 1]);
  const leaving = useTransform(t, [0, 1], [1, 0]);
  const arriving = useTransform(t, [0, 1], [0, 1]);
  const metaPct = useTransform(t, (v) => `${Math.round((meta.before - moved * v) * 100)}%`);
  const googlePct = useTransform(t, (v) => `${Math.round((google.before + moved * v) * 100)}%`);
  const metaInr = useTransform(t, (v) =>
    inr(Math.round((totalDaily * (meta.before - moved * v)) / 250) * 250),
  );
  const googleInr = useTransform(t, (v) =>
    inr(Math.round((totalDaily * (google.before + moved * v)) / 250) * 250),
  );
  const fromOpacity = useTransform(t, [0, 0.06], [0, 1]);
  const execIn = useTransform(t, [0.8, 1], [0, 1]);
  const counter = useTransform(
    t,
    (v) => `+${inr(Math.round((v * proposal.weeklyUplift) / 100) * 100)}`,
  );
  const stripX = (shares: number[], i: number) => shares.slice(0, i).reduce((a, b) => a + b, 0);
  const stripAfter = channels.map((_, i) =>
    useTransform(t, (v) => {
      const s = channels.map((c) => c.before + (c.after - c.before) * v);
      return { x: stripX(s, i), w: s[i] };
    }),
  );
  const stripAfterX = stripAfter.map((m) => useTransform(m, (v) => `${v.x * 100}%`));
  const stripAfterW = stripAfter.map((m) => useTransform(m, (v) => `calc(${v.w * 100}% - 2px)`));

  return (
    <div className="flow">
      <div className="flow-top">
        <div>
          <label>Daily allocation</label>
          <p className="flow-total">
            {inr(totalDaily)} <small>total, unchanged</small>
          </p>
        </div>
        <div className="flow-counter">
          <label>Forecast contribution change</label>
          <p>
            <motion.span>{counter}</motion.span>
            <small>/week</small>
          </p>
        </div>
      </div>

      <div className="flow-rows">
        {channels.map((c, i) => {
          const isMeta = c.key === 'meta';
          const isGoogle = c.key === 'google';
          const base = Math.min(c.before, c.after);
          return (
            <div key={c.key} className={`flow-row ${isMeta || isGoogle ? 'moving' : ''}`}>
              <span className="flow-name">
                <i className={`sw sw-${c.key}`} />
                {c.name}
              </span>
              <div className="flow-track" ref={i === 0 ? track : undefined}>
                <i className={`fbar fbar-${c.key}`} style={{ width: `${(base / SCALE) * 100}%` }} />
                {isMeta && (
                  <motion.i
                    className="fbar fbar-piece"
                    style={{
                      left: `calc(${(base / SCALE) * 100}% + 2px)`,
                      width: `calc(${(moved / SCALE) * 100}% - 2px)`,
                      scaleX: leaving,
                      originX: 0,
                    }}
                  />
                )}
                {isGoogle && (
                  <motion.i
                    className="fbar fbar-piece arriving"
                    style={{
                      left: `calc(${(base / SCALE) * 100}% + 2px)`,
                      width: `calc(${(moved / SCALE) * 100}% - 2px)`,
                      scaleX: arriving,
                      originX: 0,
                    }}
                  />
                )}
              </div>
              <span className="flow-val mono">
                {isMeta ? (
                  <>
                    <motion.b>{metaPct}</motion.b>
                    <motion.small>{metaInr}</motion.small>
                    <motion.s style={{ opacity: fromOpacity }}>from 48%</motion.s>
                  </>
                ) : isGoogle ? (
                  <>
                    <motion.b>{googlePct}</motion.b>
                    <motion.small>{googleInr}</motion.small>
                    <motion.s style={{ opacity: fromOpacity }}>from 27%</motion.s>
                  </>
                ) : (
                  <>
                    <b>{Math.round(c.before * 100)}%</b>
                    <small>{inr(c.before * totalDaily)}</small>
                  </>
                )}
              </span>
            </div>
          );
        })}
        <div className="flow-overlay" style={{ width: w }} aria-hidden="true">
          <svg width={w} height={ROW_H * 2} className="flow-trail">
            <motion.path
              d={`M${start.x},${start.y} Q${ctrl.x},${ctrl.y} ${end.x},${end.y}`}
              style={{ pathLength: trail }}
            />
          </svg>
          <motion.span className="token" style={{ x: tokenX, y: tokenY, opacity: tokenOpacity }}>
            ₹18K
          </motion.span>
        </div>
      </div>

      <motion.ul className="exec" style={{ opacity: execIn }}>
        <li>
          <span className="mono">09:14</span>
          <span>{proposal.from.name} daily budget</span>
          <b className="mono">
            {inr(proposal.from.daily)} → {inr(proposal.from.daily - shift)}
          </b>
          <em>Verified</em>
        </li>
        <li>
          <span className="mono">09:14</span>
          <span>{proposal.to.name} daily budget</span>
          <b className="mono">
            {inr(proposal.to.daily)} → {inr(proposal.to.daily + shift)}
          </b>
          <em>Verified</em>
        </li>
      </motion.ul>

      <div className="strips" aria-hidden="true">
        <div className="strip">
          <span>Before</span>
          <div>
            {channels.map((c, i) => (
              <i
                key={c.key}
                className={`seg sw-${c.key}`}
                style={{
                  left: `${
                    stripX(
                      channels.map((x) => x.before),
                      i,
                    ) * 100
                  }%`,
                  width: `calc(${c.before * 100}% - 2px)`,
                }}
              />
            ))}
          </div>
        </div>
        <div className="strip">
          <span>After</span>
          <div>
            {channels.map((c, i) => (
              <motion.i
                key={c.key}
                className={`seg sw-${c.key}`}
                style={{ left: stripAfterX[i], width: stripAfterW[i] }}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Section ──────────────────────────────────────────────────────────────── */

function Rail({ chapter, progress }: { chapter: number; progress?: MotionValue<number> }) {
  const active = chapters[chapter].step;
  return (
    <ol className="rail" aria-label="Decision loop">
      {steps.map((s, i) => (
        <li key={s} data-state={i < active ? 'done' : i === active ? 'now' : 'next'}>
          <span className="mono">0{i + 1}</span>
          {s}
        </li>
      ))}
      {progress && <motion.i className="rail-progress" style={{ scaleX: progress }} />}
    </ol>
  );
}

function StoryTable() {
  return (
    <>
      <DataTable
        caption="Meta Prospecting daily ROAS, 24 Sep to 7 Oct"
        head={['Date', 'ROAS']}
        rows={roasSeries.map((v, i) => [roasDates[i], v.toFixed(2)])}
      />
      <DataTable
        caption="ROAS change by driver"
        head={['Driver', 'Change', 'ROAS impact', 'Share of drop']}
        rows={bridge.map((b) => [
          b.key,
          pct(b.change),
          b.impact.toFixed(2),
          `${Math.round(b.share * 100)}%`,
        ])}
      />
      <DataTable
        caption="Daily allocation before and after approval"
        head={['Channel', 'Before', 'After']}
        rows={channels.map((c) => [
          c.name,
          `${Math.round(c.before * 100)}%`,
          `${Math.round(c.after * 100)}%`,
        ])}
      />
    </>
  );
}

export function Story() {
  const reduce = useReducedMotion();
  return reduce ? <StaticStory /> : <PinnedStory />;
}

function PinnedStory() {
  const ref = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end end'] });
  const [chapter, setChapter] = useState(0);
  useMotionValueEvent(scrollYProgress, 'change', (v) => {
    const next = Math.min(4, Math.max(0, Math.floor(v * 5)));
    setChapter((c) => (c === next ? c : next));
  });
  return (
    <section ref={ref} className="story" id="story" aria-labelledby="story-title">
      <h2 id="story-title" className="lp-sr">
        From a falling number to an approved budget move
      </h2>
      <div className="story-sticky">
        <div className="story-copy">
          <Rail chapter={chapter} progress={scrollYProgress} />
          <div className="chapters">
            {chapters.map((c, i) => (
              <article
                key={c.title}
                className="chapter"
                data-state={i === chapter ? 'now' : i < chapter ? 'past' : 'next'}
              >
                <p className="eyebrow">
                  0{c.step + 1} · {steps[c.step]}
                </p>
                <h3>{c.title}</h3>
                <p>{c.body}</p>
              </article>
            ))}
          </div>
        </div>
        <div className="story-stage" aria-hidden="true">
          <Stage p={scrollYProgress} chapter={chapter} />
        </div>
      </div>
      <StoryTable />
    </section>
  );
}

// Reduced motion: each chapter is a still frame at its settled state.
const settled = [0.19, 0.39, 0.59, 0.79, 1];
function StaticFrame({ i }: { i: number }) {
  const p = useFixed(settled[i]);
  const c = chapters[i];
  return (
    <div className="static-frame">
      <div className="story-copy">
        <p className="eyebrow">
          0{c.step + 1} · {steps[c.step]}
        </p>
        <h3>{c.title}</h3>
        <p>{c.body}</p>
      </div>
      <div className="story-stage" aria-hidden="true">
        <Stage p={p} chapter={i} />
      </div>
    </div>
  );
}
function StaticStory() {
  return (
    <section className="story static" id="story" aria-labelledby="story-title">
      <h2 id="story-title" className="lp-sr">
        From a falling number to an approved budget move
      </h2>
      {chapters.map((_, i) => (
        <StaticFrame key={i} i={i} />
      ))}
      <StoryTable />
    </section>
  );
}
