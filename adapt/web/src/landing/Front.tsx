import { Link } from 'react-router-dom';
import { motion } from 'motion/react';
import {
  Image,
  IndianRupee,
  Megaphone,
  Package,
  Percent,
  Shirt,
  ShoppingBag,
  TrendingDown,
  type LucideIcon,
} from 'lucide-react';
import { inr, proposal, shift } from './scenario';
import { Mark } from './parts';

// What ADAPT reads each morning rides the belt; the light key is the decision it produces.
type Key = { label: string; icon?: LucideIcon; decision?: boolean };
const keys: Key[] = [
  { label: 'Ad spend', icon: Megaphone },
  { label: 'ROAS', icon: TrendingDown },
  { label: 'Orders', icon: ShoppingBag },
  { label: 'Decision', decision: true },
  { label: 'SKU', icon: Shirt },
  { label: 'Stock', icon: Package },
  { label: 'Margin', icon: Percent },
  { label: 'Creative', icon: Image },
  { label: 'COGS', icon: IndianRupee },
  { label: 'Decision', decision: true },
];

const ease = [0.2, 0.7, 0.2, 1] as const;

function Keycap({ k }: { k: Key }) {
  const Icon = k.icon;
  return (
    <div className={`keycap ${k.decision ? 'decision' : ''}`}>
      <div className="keycap-well">
        {k.decision ? <Mark size={58} /> : Icon && <Icon size={50} strokeWidth={1.6} />}
      </div>
      <span>{k.label}</span>
    </div>
  );
}

export function FrontHero() {
  const lines = ['Know what changed.', 'Know why.', 'Know what to fund next.'];
  return (
    <section className="front" data-nav-tone="dark" aria-labelledby="front-title">
      <div className="belt-scene" aria-hidden="true">
        <div className="belt">
          <div className="belt-run">
            {[...keys, ...keys].map((k, i) => (
              <Keycap key={i} k={k} />
            ))}
          </div>
        </div>
        <div className="belt-light" />
      </div>

      <div className="lp-shell front-copy">
        <h1 id="front-title" className="front-title">
          {lines.map((l, i) => (
            <span className="mask" key={l}>
              <motion.span
                initial={{ y: '105%' }}
                animate={{ y: '0%' }}
                transition={{ duration: 0.9, ease, delay: 0.2 + i * 0.09 }}
              >
                {l}
                {i === 2 && (
                  <span className="front-arrow" aria-hidden="true">
                    {' '}
                    ↘
                  </span>
                )}
              </motion.span>
            </span>
          ))}
        </h1>
        <motion.div
          className="front-actions"
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.8, ease, delay: 0.6 }}
        >
          <Link to="/signin" className="pill-cta">
            Get started
            <span aria-hidden="true">→</span>
          </Link>
          <a className="front-card" href="#story">
            <span className="front-card-k mono">
              <i aria-hidden="true" />
              {proposal.id} · ready
            </span>
            <b>Shift {inr(shift)}/day to Google PMax</b>
            <span className="front-card-go">
              Follow the decision <span aria-hidden="true">↓</span>
            </span>
          </a>
        </motion.div>
      </div>
    </section>
  );
}
