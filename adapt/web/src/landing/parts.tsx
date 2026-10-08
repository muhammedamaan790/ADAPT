import { useEffect, useState, type ReactNode } from 'react';
import { motionValue, type MotionValue } from 'motion/react';

// The approved Forward Shift symbol, monochrome. Never recoloured.
export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 256" aria-hidden="true" className="lp-mark">
      <g fill="currentColor" transform="translate(3 -6)">
        <path className="lp-mark-lower" d="M32 80H80V178H176V224H80L32 176Z" />
        <path className="lp-mark-upper" d="M112 32H176L224 80V144H176V78H112Z" />
      </g>
    </svg>
  );
}

/** Product-window chrome shared by every mockup, so they read as one application. */
export function AppWindow({
  crumb,
  meta,
  children,
  className = '',
}: {
  crumb: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`win ${className}`}>
      <div className="win-bar">
        <span className="win-brand">
          <Mark size={15} />
          ADAPT
        </span>
        <span className="win-crumb">{crumb}</span>
        <span className="win-meta">{meta ?? 'D2C workspace · INR'}</span>
      </div>
      <div className="win-body">{children}</div>
    </div>
  );
}

/** A fixed progress value, used to render a scroll-driven composition as a static frame. */
export function useFixed(value: number): MotionValue<number> {
  const [mv] = useState(() => motionValue(value));
  useEffect(() => mv.set(value), [mv, value]);
  return mv;
}

export function useMedia(query: string) {
  const [match, setMatch] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia(query).matches,
  );
  useEffect(() => {
    const list = window.matchMedia(query);
    const update = () => setMatch(list.matches);
    update();
    list.addEventListener('change', update);
    return () => list.removeEventListener('change', update);
  }, [query]);
  return match;
}

export const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** Visually hidden data table: the accessible twin of a chart. */
export function DataTable({
  caption,
  head,
  rows,
}: {
  caption: string;
  head: string[];
  rows: (string | number)[][];
}) {
  // The wrapper does the hiding: table internals escape a 1px table box and widen the page.
  return (
    <div className="lp-sr">
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j}>{c}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
