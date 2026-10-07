import type { Decision } from '../api/contracts';
import { Badge, SectionTitle } from './ui';
import { percent } from '../lib/format';

const REGION: Record<string, string> = {
  R1: 'LOW [0, 0.6)',
  R2: 'MID [0.6, 0.8)',
  R3: 'HIGH [0.8, 1]',
};

/** The decision's confidence index (spec §8.4) and, on Simulation Autonomous channels, the autonomy verdict with
 * every gate. The band is informational: only a qualified region (Wilson bound + held-out PASS) authorizes autonomy. */
export function ConfidencePanel({ decision: d }: { decision: Decision }) {
  const c = d.confidence;
  const a = d.autonomy;
  if (!c && !a) return null;
  return (
    <section className="panel" aria-label="Confidence and autonomy">
      <SectionTitle title="Confidence & autonomy">
        {c && (
          <Badge tone={c.band === 'HIGH' ? 'success' : c.band === 'MEDIUM' ? 'accent' : 'warning'}>
            {c.band} · {c.overall.toFixed(2)}
          </Badge>
        )}
        {a && (
          <Badge tone={a.result === 'EXECUTED' ? 'success' : 'warning'}>
            {a.result === 'EXECUTED' ? 'EXECUTED AUTONOMOUSLY (SIMULATION)' : 'REQUIRES REVIEW'}
          </Badge>
        )}
      </SectionTitle>
      {c && (
        <>
          <dl className="fact-grid">
            <div>
              <dt>Data quality</dt>
              <dd>{percent(c.data_quality)}</dd>
            </div>
            <div>
              <dt>Prediction quality</dt>
              <dd>{percent(c.prediction_quality)}</dd>
            </div>
            <div>
              <dt>Track record</dt>
              <dd>{percent(c.track_record)}</dd>
            </div>
            <div>
              <dt>Region</dt>
              <dd>{REGION[c.region]}</dd>
            </div>
          </dl>
          <p className="workbench-copy">
            Geometric mean of the three components. The band is informational: autonomy needs a
            qualified region (warm-up Wilson lower bound ≥ 0.60 and a held-out PASS), never a high
            raw index.{c.constraint_coverage ? '' : ' Not every policy rule could be evaluated.'}
          </p>
        </>
      )}
      {a && (
        <ul className="ledger-list">
          {a.gates.map((g) => (
            <li key={g.id}>
              <div className="ledger-heading">
                <strong>{g.id.replaceAll('_', ' ')}</strong>
                <Badge tone={g.passed ? 'success' : 'danger'}>{g.passed ? 'PASS' : 'FAIL'}</Badge>
              </div>
              <p>{g.detail}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
