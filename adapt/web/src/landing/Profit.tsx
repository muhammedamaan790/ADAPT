import { useEffect, useRef, useState } from 'react';
import { motion, useInView, useReducedMotion } from 'motion/react';
import { campaigns, inr } from './scenario';
import { AppWindow, DataTable } from './parts';

const MAX = 10;
const pos = (roas: number) => `${(roas / MAX) * 100}%`;

const stepsText = [
  'Ranked by ROAS, Campaign A looks like the one to scale.',
  'But each campaign has its own break-even ROAS: one divided by its margin.',
  'Per ₹100 of spend, A loses ₹49 and B earns ₹55. The ranking flips.',
  'A also runs out of stock in 4 days. ADAPT scales B.',
];
const stepNames = ['ROAS', 'Break-even', 'Contribution', 'Verdict'];
const ease = [0.2, 0.7, 0.2, 1] as const;

export function Profit() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { amount: 0.5 });
  const reduce = useReducedMotion();
  const [step, setStep] = useState(reduce ? 3 : -1);
  const [auto, setAuto] = useState(!reduce);

  useEffect(() => {
    if (!auto || !inView) return;
    if (step >= 3) {
      setAuto(false);
      return;
    }
    const id = window.setTimeout(() => setStep((s) => s + 1), step < 0 ? 250 : 2300);
    return () => window.clearTimeout(id);
  }, [auto, inView, step]);

  const shown = Math.max(0, step);
  const order = shown >= 2 ? [...campaigns].sort((a, b) => b.per100 - a.per100) : campaigns;

  return (
    <section className="profit" id="profit">
      <div className="lp-shell">
        <div className="section-head">
          <p className="eyebrow">Profitability intelligence</p>
          <h2 className="section-title">
            The highest ROAS is not the best place for the next rupee.
          </h2>
          <p className="lead">
            ROAS ignores what the product earns and whether it can ship. ADAPT ranks campaigns by
            contribution per rupee, against stock cover, before it ever proposes a scale-up.
          </p>
        </div>

        <div ref={ref}>
          <AppWindow
            crumb={
              <>
                Decision Center <i>/</i> <b>Scale candidates</b>
              </>
            }
          >
            <div className="pf">
              <div className="pf-controls" role="group" aria-label="Walk through the comparison">
                {stepNames.map((n, i) => (
                  <button
                    key={n}
                    type="button"
                    aria-pressed={shown === i}
                    data-done={shown > i}
                    onClick={() => {
                      setAuto(false);
                      setStep(i);
                    }}
                  >
                    <span className="mono">{i + 1}</span>
                    {n}
                  </button>
                ))}
                <button
                  type="button"
                  className="pf-replay"
                  onClick={() => {
                    setStep(-1);
                    setAuto(true);
                  }}
                >
                  Replay
                </button>
              </div>

              <div className="pf-rows">
                {order.map((c, rank) => {
                  const harm = c.per100 < 0;
                  const lo = Math.min(c.roas, c.breakEven);
                  const hi = Math.max(c.roas, c.breakEven);
                  return (
                    <motion.div
                      layout={!reduce}
                      transition={{ duration: 0.7, ease }}
                      key={c.key}
                      className="pf-row"
                      data-verdict={shown >= 3 ? (harm ? 'harm' : 'gain') : undefined}
                    >
                      <div className="pf-id">
                        <span className="pf-rank mono">
                          #{rank + 1}
                          <small>{shown >= 2 ? 'by contribution' : 'by ROAS'}</small>
                        </span>
                        <b>{c.name}</b>
                        <small>{c.product}</small>
                      </div>

                      <div className="pf-track">
                        <motion.i
                          className={`pf-gap ${harm ? 'harm' : 'gain'}`}
                          style={{
                            left: pos(lo),
                            width: `calc(${pos(hi)} - ${pos(lo)})`,
                            originX: harm ? 0 : 1,
                          }}
                          initial={false}
                          animate={{ opacity: shown >= 2 ? 1 : 0, scaleX: shown >= 2 ? 1 : 0 }}
                          transition={{ duration: 0.7, ease }}
                        />
                        <motion.i
                          className="pf-bar"
                          style={{ width: pos(c.roas), originX: 0 }}
                          initial={false}
                          animate={{ scaleX: step >= 0 ? 1 : 0 }}
                          transition={{ duration: 0.9, ease, delay: rank * 0.08 }}
                        />
                        <motion.span
                          className="pf-val mono"
                          style={{ left: pos(c.roas) }}
                          initial={false}
                          animate={{ opacity: step >= 0 ? 1 : 0 }}
                        >
                          {c.roas.toFixed(1)}
                        </motion.span>
                        <motion.span
                          className="pf-be"
                          style={{ left: pos(c.breakEven) }}
                          initial={false}
                          animate={{ opacity: shown >= 1 ? 1 : 0, y: shown >= 1 ? 0 : -8 }}
                          transition={{ duration: 0.5, ease }}
                        >
                          <span className="mono">break-even {c.breakEven.toFixed(2)}</span>
                        </motion.span>
                        <motion.span
                          className={`pf-gap-label mono ${harm ? 'harm-text' : 'gain-text'}`}
                          style={{ left: `calc((${pos(lo)} + ${pos(hi)}) / 2)` }}
                          initial={false}
                          animate={{ opacity: shown >= 2 ? 1 : 0 }}
                          transition={{ duration: 0.4, delay: shown >= 2 ? 0.4 : 0 }}
                        >
                          {harm ? '−' : '+'}
                          {inr(Math.abs(Math.round(c.per100)))} per ₹100
                        </motion.span>
                      </div>

                      <div className="pf-side">
                        <dl>
                          <div>
                            <dt>Margin</dt>
                            <dd className="mono">{Math.round(c.margin * 100)}%</dd>
                          </div>
                          <div data-hot={shown >= 3 && c.cover < 7}>
                            <dt>Stock cover</dt>
                            <dd className="mono">{c.cover} days</dd>
                          </div>
                        </dl>
                        <motion.p
                          className={`verdict ${harm ? 'harm' : 'gain'}`}
                          initial={false}
                          animate={{ opacity: shown >= 3 ? 1 : 0, y: shown >= 3 ? 0 : 6 }}
                          transition={{ duration: 0.5, ease }}
                        >
                          {harm ? 'Not safe to scale' : 'Better scale opportunity'}
                        </motion.p>
                      </div>
                    </motion.div>
                  );
                })}
              </div>

              <div className="pf-axis" aria-hidden="true">
                {[0, 2, 4, 6, 8, 10].map((v) => (
                  <span key={v} style={{ left: pos(v) }}>
                    {v}
                  </span>
                ))}
                <em>ROAS</em>
              </div>

              <p className="pf-caption" aria-live="polite">
                {stepsText[shown]}
              </p>
            </div>
          </AppWindow>
        </div>
        <DataTable
          caption="Campaign profitability"
          head={[
            'Campaign',
            'ROAS',
            'Margin',
            'Break-even ROAS',
            'Contribution per ₹100',
            'Stock cover',
          ]}
          rows={campaigns.map((c) => [
            c.name,
            c.roas.toFixed(1),
            `${Math.round(c.margin * 100)}%`,
            c.breakEven.toFixed(2),
            inr(Math.round(c.per100)),
            `${c.cover} days`,
          ])}
        />
      </div>
    </section>
  );
}
