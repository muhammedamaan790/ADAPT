import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { MotionConfig, motion, useMotionValueEvent, useScroll, useTransform } from 'motion/react';
import { channels, inr, pct, proposal, roasChange, shift, totalDaily } from './scenario';

// Seven-day ROAS by channel; spend-weighted they give the blended 3.42 shown above.
const channelRoas: Record<string, number> = { meta: 3.32, google: 4.15, tiktok: 2.9, other: 2.6 };
import { AppWindow, Mark } from './parts';
import { Story } from './Story';
import { FrontHero } from './Front';
import { Creative } from './Creative';
import { Profit } from './Profit';
import { Outcome } from './Outcome';
import './landing.css';

const ease = [0.2, 0.7, 0.2, 1] as const;

function Nav() {
  const { scrollY } = useScroll();
  const [scrolled, setScrolled] = useState(false);
  const [dark, setDark] = useState(false);
  useMotionValueEvent(scrollY, 'change', (v) => setScrolled(v > 24));
  // The bar turns dark while a dark band sits under it.
  useEffect(() => {
    const bands = document.querySelectorAll('[data-nav-tone="dark"]');
    const under = new Set<Element>();
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => (e.isIntersecting ? under.add(e.target) : under.delete(e.target)));
        setDark(under.size > 0);
      },
      { rootMargin: '0px 0px -95% 0px' },
    );
    bands.forEach((b) => io.observe(b));
    return () => io.disconnect();
  }, []);
  return (
    <header className="lp-nav" data-scrolled={scrolled} data-tone={dark ? 'dark' : 'light'}>
      <div className="lp-shell lp-nav-row">
        <Link to="/product" className="lp-brand" aria-label="ADAPT product overview">
          <Mark size={22} />
          <span>ADAPT</span>
        </Link>
        <nav aria-label="Product sections" className="lp-links">
          <a href="#story">Diagnosis</a>
          <a href="#creative">Creative</a>
          <a href="#profit">Profit</a>
          <a href="#outcomes">Outcomes</a>
        </nav>
        <Link to="/signin" className="lp-btn outline small">
          Sign in
        </Link>
      </div>
    </header>
  );
}

/* ── Hero ─────────────────────────────────────────────────────────────────── */

const kpis = [
  {
    label: 'Contribution, 7 days',
    value: '₹4.71 L',
    delta: -0.048,
    harmWhenDown: true,
    series: [62, 64, 63, 66, 67, 66, 68, 69, 67, 65, 63, 62, 61, 60],
  },
  {
    label: 'Ad spend, 7 days',
    value: '₹15.75 L',
    delta: 0.004,
    harmWhenDown: false,
    neutral: true,
    series: [60, 61, 60, 61, 62, 61, 61, 62, 61, 62, 61, 62, 62, 62],
  },
  {
    label: 'Blended ROAS',
    value: '3.42',
    delta: -0.063,
    harmWhenDown: true,
    series: [70, 71, 70, 72, 71, 71, 72, 72, 69, 67, 65, 64, 63, 62],
  },
  {
    label: 'New-customer CAC',
    value: '₹612',
    delta: 0.079,
    harmWhenDown: false,
    series: [50, 49, 50, 49, 50, 50, 49, 50, 52, 54, 56, 57, 58, 59],
  },
];

const feed = [
  { tone: 'ok', time: '06:00', text: 'Synced Meta Ads and Google Ads · 14 campaigns' },
  { tone: 'ok', time: '06:02', text: 'Reconciled 3,412 store orders' },
  { tone: 'ok', time: '06:03', text: 'Refreshed margins and stock · 212 SKUs' },
  {
    tone: 'harm',
    time: '06:04',
    text: `Meta Prospecting ROAS ${pct(roasChange)} · outside expected range`,
  },
  { tone: 'action', time: '06:05', text: `${proposal.id} ready · shift ${inr(shift)}/day` },
];

function Sparkline({ series, tone, delay }: { series: number[]; tone: string; delay: number }) {
  const lo = Math.min(...series) - 2;
  const hi = Math.max(...series) + 2;
  const d = series
    .map(
      (v, i) =>
        `${i ? 'L' : 'M'}${((i / 13) * 200).toFixed(1)},${(28 - ((v - lo) / (hi - lo)) * 26).toFixed(1)}`,
    )
    .join('');
  return (
    <svg
      className={`kpi-spark ${tone}`}
      viewBox="0 0 200 30"
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      <motion.path
        d={d}
        initial={{ pathLength: 0 }}
        animate={{ pathLength: 1 }}
        transition={{ duration: 1.2, ease, delay }}
      />
    </svg>
  );
}

function CommandCenter() {
  return (
    <AppWindow
      crumb={<b>Command Center</b>}
      meta={<span className="win-live">Daily cycle · 06:05 IST</span>}
    >
      <div className="cc">
        <aside className="cc-nav" aria-hidden="true">
          {[
            ['Operate', ['Command Center', 'Decision Center', 'Execution & Ledger']],
            ['Analyze', ['Anomalies', 'Outcomes', 'Learning']],
            ['Workspace', ['Data Hub', 'Scenario Lab']],
          ].map(([g, items]) => (
            <div key={g as string}>
              <p>{g}</p>
              {(items as string[]).map((it) => (
                <span key={it} className={it === 'Command Center' ? 'on' : ''}>
                  {it}
                  {it === 'Anomalies' && <i className="cc-badge">1</i>}
                  {it === 'Decision Center' && <i className="cc-badge action">1</i>}
                </span>
              ))}
            </div>
          ))}
        </aside>
        <div className="cc-main">
          <div className="cc-head">
            <div>
              <p className="st-label">Thursday, 06:05 IST</p>
              <p className="cc-title">One decision is waiting for you.</p>
            </div>
          </div>
          <div className="cc-kpis">
            {kpis.map((k, i) => {
              const bad = k.neutral ? false : k.harmWhenDown ? k.delta < 0 : k.delta > 0;
              return (
                <div key={k.label} className="cc-kpi">
                  <p className="st-label">{k.label}</p>
                  <p className="cc-kpi-v">
                    {k.value}
                    <span
                      className={`mono ${k.neutral ? 'muted' : bad ? 'harm-text' : 'gain-text'}`}
                    >
                      {pct(k.delta)}
                    </span>
                  </p>
                  <Sparkline
                    series={k.series}
                    tone={k.neutral ? 'neutral' : bad ? 'harm' : 'gain'}
                    delay={0.6 + i * 0.1}
                  />
                </div>
              );
            })}
          </div>
          <div className="cc-row">
            <div className="cc-alloc">
              <p className="st-label">Daily allocation · ₹2,25,000</p>
              <div className="cc-stack">
                {channels.map((c, i) => (
                  <motion.i
                    key={c.key}
                    className={`seg sw-${c.key}`}
                    style={{ width: `calc(${c.before * 100}% - 2px)`, originX: 0 }}
                    initial={{ scaleX: 0 }}
                    animate={{ scaleX: 1 }}
                    transition={{ duration: 0.8, ease, delay: 0.7 + i * 0.08 }}
                  />
                ))}
              </div>
              <table className="cc-channels">
                <thead>
                  <tr>
                    <th scope="col">Channel</th>
                    <th scope="col">Share</th>
                    <th scope="col">Per day</th>
                    <th scope="col">ROAS 7d</th>
                  </tr>
                </thead>
                <tbody>
                  {channels.map((c) => (
                    <tr key={c.key}>
                      <th scope="row">
                        <i className={`sw sw-${c.key}`} />
                        {c.name}
                      </th>
                      <td className="mono">{Math.round(c.before * 100)}%</td>
                      <td className="mono">{inr(c.before * totalDaily)}</td>
                      <td className={`mono ${c.key === 'meta' ? 'harm-text' : ''}`}>
                        {channelRoas[c.key].toFixed(2)}
                        {c.key === 'meta' && ' ↓'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="cc-feed">
              <p className="st-label">This morning’s cycle</p>
              <ol>
                {feed.map((f, i) => (
                  <motion.li
                    key={f.text}
                    data-tone={f.tone}
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.5, ease, delay: 1.1 + i * 0.5 }}
                  >
                    <span className="mono">{f.time}</span>
                    <i aria-hidden="true" />
                    <span>{f.text}</span>
                  </motion.li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      </div>
    </AppWindow>
  );
}

function CommandSection() {
  const ref = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start end', 'center center'] });
  const rotateX = useTransform(scrollYProgress, [0, 1], [12, 0]);
  const scale = useTransform(scrollYProgress, [0, 1], [0.94, 1]);
  return (
    <section ref={ref} className="hero cc-section">
      <div className="lp-shell section-head">
        <p className="eyebrow">Command Center</p>
        <h2 className="section-title">Every morning, one screen and the decision that matters.</h2>
        <p className="lead">
          ADAPT reads your ad platforms, orders, SKU margins and stock together. It explains what
          moved, proposes where the budget should go and shows the evidence. You approve, and it
          learns from the result.
        </p>
      </div>
      <div className="hero-visual lp-shell">
        <div className="hero-tilt">
          <motion.div style={{ rotateX, scale, transformPerspective: 1800, originY: 0 }}>
            <CommandCenter />
          </motion.div>
        </div>
      </div>
    </section>
  );
}

const sources = [
  ['Meta Ads', 'spend, delivery'],
  ['Google Ads', 'spend, delivery'],
  ['Store orders', 'revenue, AOV'],
  ['SKU costs', 'COGS, margin'],
  ['Inventory', 'stock, restock time'],
  ['Any channel', 'via CSV'],
];

function Sources() {
  return (
    <section className="sources" aria-label="Data ADAPT reads">
      <div className="lp-shell sources-row">
        <p className="sources-k">
          Reconciled every morning <span aria-hidden="true">→</span>
        </p>
        <ul>
          {sources.map(([n, d]) => (
            <li key={n}>
              <i aria-hidden="true" />
              <b>{n}</b>
              <span>{d}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function Closing() {
  return (
    <section className="closing" data-nav-tone="dark">
      <div className="lp-shell">
        <ol className="closing-loop" aria-label="The ADAPT loop">
          {['Observe', 'Diagnose', 'Decide', 'Act', 'Learn'].map((s, i) => (
            <li key={s}>
              <span className="mono">0{i + 1}</span>
              {s}
            </li>
          ))}
        </ol>
        <h2 className="closing-title">Every rupee explained before it moves.</h2>
        <p className="lead">
          Proposals carry their forecast, their policy checks and the data behind them. Nothing
          changes without approval, and every outcome is measured.
        </p>
        <div className="hero-cta">
          <Link to="/signin" className="lp-btn light">
            Sign in <span aria-hidden="true">→</span>
          </Link>
          <Link to="/decisions" className="lp-btn ghost-light">
            See the Decision Center
          </Link>
        </div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="lp-footer" data-nav-tone="dark">
      <div className="lp-shell lp-footer-row">
        <span className="lp-brand">
          <Mark size={18} />
          <span>ADAPT</span>
        </span>
        <p>
          Autonomous Decision &amp; Allocation Platform for D2C. The scenario on this page is
          illustrative: its figures are an example, not customer results.
        </p>
        <nav aria-label="Workspace">
          <Link to="/">Command Center</Link>
          <Link to="/anomalies">Anomalies</Link>
          <Link to="/outcomes">Outcomes</Link>
          <Link to="/data">Data Hub</Link>
        </nav>
      </div>
    </footer>
  );
}

export function Landing() {
  useEffect(() => {
    const root = document.documentElement;
    const previous = document.title;
    root.dataset.surface = 'landing';
    document.title = 'ADAPT · Advertising decisions on evidence';
    return () => {
      delete root.dataset.surface;
      document.title = previous;
    };
  }, []);
  return (
    <MotionConfig reducedMotion="user">
      <div className="lp">
        <a className="lp-skip" href="#lp-main">
          Skip to content
        </a>
        <Nav />
        <main id="lp-main">
          <FrontHero />
          <Sources />
          <CommandSection />
          <Story />
          <Creative />
          <Profit />
          <Outcome />
          <Closing />
        </main>
        <Footer />
      </div>
    </MotionConfig>
  );
}
