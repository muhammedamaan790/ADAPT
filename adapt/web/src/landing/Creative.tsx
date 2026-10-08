import { useRef, useState, type ReactNode } from 'react';
import {
  LayoutGroup,
  motion,
  useMotionValueEvent,
  useReducedMotion,
  useScroll,
} from 'motion/react';
import { classify, creatives, fatigue, inr, lanes, TARGET_ROAS, type Lane } from './scenario';
import { DataTable, useMedia } from './parts';

type Creative = (typeof creatives)[number];

/* ── Original thumbnails: flat, geometric stand-ins for real ad creative ──── */

const TEE = 'M26 32 L36 27 Q40 31 44 27 L54 32 L61 42 L53 46 L53 74 L27 74 L27 46 L19 42 Z';

export function CreativeArt({ kind }: { kind: Creative['art'] }) {
  return (
    <svg className="cr-art" viewBox="0 0 80 100" aria-hidden="true">
      {kind === 'reel' && (
        <>
          <rect width="80" height="100" fill="#1B1C20" />
          <rect x="14" y="12" width="52" height="76" rx="4" fill="#2A2B30" />
          <path d={TEE} fill="#E9E2D6" transform="translate(0 2)" />
          <path d="M22 80 L28 83.5 L22 87 Z" fill="#FFFFFF" />
          <rect x="32" y="83" width="30" height="1.6" rx=".8" fill="#FFFFFF" opacity=".25" />
          <rect x="32" y="83" width="12" height="1.6" rx=".8" fill="#FFFFFF" />
          <text
            x="18"
            y="22"
            fontSize="6"
            fill="#FFFFFF"
            opacity=".75"
            fontFamily="JetBrains Mono Variable, monospace"
          >
            0:15
          </text>
        </>
      )}
      {kind === 'flatlay' && (
        <>
          <rect width="80" height="100" fill="#ECE5D8" />
          <path
            d={TEE}
            fill="#FBF8F2"
            transform="translate(40 50) rotate(-8) translate(-40 -50) scale(1 1.05)"
          />
          <path
            d="M36 27 L40 37 L44 27"
            fill="none"
            stroke="#D8CFBF"
            strokeWidth="1.2"
            transform="translate(40 50) rotate(-8) translate(-40 -50)"
          />
          <rect x="54" y="72" width="16" height="16" rx="8" fill="#C9B79C" />
          <rect x="10" y="80" width="22" height="3" rx="1.5" fill="#1B1C20" opacity=".7" />
        </>
      )}
      {kind === 'price' && (
        <>
          <rect width="80" height="100" fill="#141518" />
          <text
            x="10"
            y="30"
            fontSize="9"
            fill="#FFFFFF"
            opacity=".8"
            fontFamily="Instrument Sans Variable, sans-serif"
            fontWeight="600"
          >
            2 for
          </text>
          <text
            x="10"
            y="48"
            fontSize="16"
            fill="#FFFFFF"
            fontFamily="Instrument Sans Variable, sans-serif"
            fontWeight="700"
            letterSpacing="-.6"
          >
            ₹1,999
          </text>
          <path d={TEE} fill="#FFFFFF" opacity=".95" transform="translate(30 38) scale(.55)" />
          <path d={TEE} fill="#8A8C92" transform="translate(44 44) scale(.55)" />
        </>
      )}
      {kind === 'quote' && (
        <>
          <rect width="80" height="100" fill="#F6F3EC" />
          <text x="10" y="32" fontSize="22" fill="#1B1C20" fontFamily="Georgia, serif">
            “
          </text>
          <rect x="10" y="40" width="58" height="4" rx="2" fill="#1B1C20" />
          <rect x="10" y="49" width="50" height="4" rx="2" fill="#1B1C20" />
          <rect x="10" y="58" width="36" height="4" rx="2" fill="#1B1C20" />
          {[0, 1, 2, 3, 4].map((i) => (
            <rect key={i} x={10 + i * 8} y="72" width="5.5" height="5.5" rx="1" fill="#E5A50A" />
          ))}
          <rect x="10" y="84" width="24" height="2.5" rx="1.25" fill="#8A8C92" />
        </>
      )}
      {kind === 'unbox' && (
        <>
          <rect width="80" height="100" fill="#E7E5DF" />
          <path d="M18 50 L62 50 L62 84 L18 84 Z" fill="#C8A27A" />
          <path d="M18 50 L10 40 L30 40 L40 50 Z M62 50 L70 40 L50 40 L40 50 Z" fill="#B58D63" />
          <path d="M26 50 Q24 30 40 24 Q56 30 54 50 Z" fill="#1B1C20" />
          <path d="M34 34 Q40 40 46 34" fill="none" stroke="#3A3B40" strokeWidth="2" />
          <rect x="22" y="62" width="36" height="3" rx="1.5" fill="#FFFFFF" opacity=".7" />
        </>
      )}
      {kind === 'colours' && (
        <>
          <rect width="80" height="100" fill="#F1F1EE" />
          {['#1B1C20', '#E9E2D6', '#8A8C92', '#C9C9C3', '#B5583F', '#6E7B4F'].map((c, i) => (
            <path
              key={c}
              d={TEE}
              fill={c}
              transform={`translate(${6 + (i % 3) * 23} ${14 + Math.floor(i / 3) * 38}) scale(.42)`}
            />
          ))}
          <rect x="10" y="88" width="30" height="3" rx="1.5" fill="#1B1C20" opacity=".75" />
        </>
      )}
    </svg>
  );
}

/* ── Board ────────────────────────────────────────────────────────────────── */

const laneRule: Record<Lane, string> = {
  Winner: `ROAS ≥ ${(TARGET_ROAS * 1.25).toFixed(2)} (1.25 × target)`,
  Stable: 'Within the target band',
  Fatiguing: 'CTR −20% from peak at frequency ≥ 3',
  Underperforming: 'ROAS below product break-even',
};

function reason(c: Creative, d: number, lane: Lane) {
  const peak = Math.max(...Array.from({ length: d }, (_, i) => c.ctr(i + 1)));
  if (lane === 'Fatiguing')
    return `CTR −${Math.round((1 - c.ctr(d) / peak) * 100)}% from peak at ${c.freq(d).toFixed(1)} frequency`;
  if (lane === 'Underperforming')
    return `ROAS ${c.roas(d).toFixed(2)} below ${c.breakEven.toFixed(2)} break-even`;
  if (lane === 'Winner') return `ROAS ${c.roas(d).toFixed(2)}, holding CTR`;
  return `ROAS ${c.roas(d).toFixed(2)}, inside target band`;
}

function Spark({ c, day }: { c: Creative; day: number }) {
  const W = 72;
  const H = 20;
  const vals = Array.from({ length: 21 }, (_, i) => c.ctr(i + 1));
  const lo = Math.min(...vals) * 0.9;
  const hi = Math.max(...vals) * 1.05;
  const px = (i: number) => (i / 20) * W;
  const py = (v: number) => H - ((v - lo) / (hi - lo)) * H;
  const pts = vals
    .slice(0, day)
    .map((v, i) => `${px(i).toFixed(1)},${py(v).toFixed(1)}`)
    .join(' ');
  return (
    <svg
      className="spark"
      width={W}
      height={H + 4}
      viewBox={`-2 -2 ${W + 4} ${H + 4}`}
      aria-hidden="true"
    >
      <line x1={0} x2={W} y1={H} y2={H} className="spark-base" />
      <polyline points={pts} />
      <circle cx={px(day - 1)} cy={py(vals[day - 1])} r={2.6} />
    </svg>
  );
}

function Card({ c, day, lane }: { c: Creative; day: number; lane: Lane }) {
  const f = fatigue(c, day);
  return (
    <motion.article
      layout
      layoutId={c.id}
      className="cr-card"
      data-lane={lane}
      transition={{ type: 'spring', stiffness: 220, damping: 30, mass: 0.9 }}
    >
      <CreativeArt kind={c.art} />
      <div className="cr-body">
        <p className="cr-name">
          <b>{c.name}</b>
          <small>{c.format}</small>
        </p>
        <p className="cr-reason">{reason(c, day, lane)}</p>
        <dl className="cr-stats">
          <div>
            <dt>ROAS</dt>
            <dd>{c.roas(day).toFixed(2)}</dd>
          </div>
          <div>
            <dt>CTR</dt>
            <dd>{c.ctr(day).toFixed(2)}%</dd>
          </div>
          <div>
            <dt>CVR</dt>
            <dd>{c.cvr(day).toFixed(1)}%</dd>
          </div>
          <div>
            <dt>CAC</dt>
            <dd>{inr(Math.round(c.cac(day) / 10) * 10)}</dd>
          </div>
        </dl>
        <div className="cr-fatigue">
          <span className="mono">Freq {c.freq(day).toFixed(1)}</span>
          <span className="fatigue-meter" title={`Fatigue ${Math.round(f * 100)}%`}>
            {[0, 1, 2, 3, 4].map((i) => (
              <i key={i} data-on={f > i / 5 + 0.04} />
            ))}
          </span>
          <Spark c={c} day={day} />
        </div>
      </div>
    </motion.article>
  );
}

function Board({ day }: { day: number }) {
  const placed = creatives.map((c) => ({ c, lane: classify(c, day) }));
  return (
    <LayoutGroup>
      <div className="board">
        {lanes.map((lane) => {
          const items = placed.filter((x) => x.lane === lane);
          return (
            <section
              key={lane}
              className="lane"
              data-lane={lane}
              aria-label={`${lane}: ${items.length}`}
            >
              <header>
                <span className="lane-dot" aria-hidden="true" />
                <b>{lane}</b>
                <span className="mono lane-count">{items.length}</span>
                <small>{laneRule[lane]}</small>
              </header>
              <div className="lane-cards">
                {items.map(({ c }) => (
                  <Card key={c.id} c={c} day={day} lane={lane} />
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </LayoutGroup>
  );
}

function Intro({ day, children }: { day: number; children?: ReactNode }) {
  return (
    <div className="cr-intro">
      <div>
        <p className="eyebrow">Creative intelligence</p>
        <h2 className="section-title">Know which ad is wearing out before your CAC does.</h2>
        <p className="lead">
          Every creative is scored daily on return, click-through, conversion and frequency against
          its own peak. When one starts to tire, it changes lanes, and the reason is written on the
          card.
        </p>
      </div>
      <div className="day">
        <p className="day-label">
          <span className="mono">Day</span> <b>{day}</b> <span className="mono">of 21</span>
        </p>
        <div className="day-ticks" aria-hidden="true">
          {Array.from({ length: 21 }, (_, i) => (
            <i key={i} data-on={i < day} />
          ))}
        </div>
        {children}
      </div>
    </div>
  );
}

function CreativeTable() {
  return (
    <DataTable
      caption="Creative lanes on day 1, 11, 14 and 21"
      head={['Creative', 'Day 1', 'Day 11', 'Day 14', 'Day 21']}
      rows={creatives.map((c) => [c.name, ...[1, 11, 14, 21].map((d) => classify(c, d))])}
    />
  );
}

export function Creative() {
  const reduce = useReducedMotion();
  const narrow = useMedia('(max-width: 900px)');
  return reduce || narrow ? <InteractiveCreative /> : <PinnedCreative />;
}

function PinnedCreative() {
  const ref = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({ target: ref, offset: ['start start', 'end end'] });
  const [day, setDay] = useState(1);
  useMotionValueEvent(scrollYProgress, 'change', (v) => {
    const next = Math.min(21, Math.max(1, 1 + Math.round(((v - 0.08) / 0.84) * 20)));
    setDay((d) => (d === next ? d : next));
  });
  return (
    <section ref={ref} className="creative pinned" id="creative">
      <div className="creative-sticky">
        <Intro day={day} />
        <Board day={day} />
      </div>
      <CreativeTable />
    </section>
  );
}

function InteractiveCreative() {
  const [day, setDay] = useState(14);
  return (
    <section className="creative" id="creative">
      <Intro day={day}>
        <label className="day-range">
          <span className="lp-sr">Day</span>
          <input
            type="range"
            min={1}
            max={21}
            value={day}
            onChange={(e) => setDay(Number(e.target.value))}
          />
        </label>
      </Intro>
      <Board day={day} />
      <CreativeTable />
    </section>
  );
}
