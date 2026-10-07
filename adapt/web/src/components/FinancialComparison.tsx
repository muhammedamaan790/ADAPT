import type { Decision } from '../api/contracts';
import { money, signedMoney } from '../lib/format';

export function BudgetMovement({ legs }: { legs: Decision['legs'] }) {
  const max = Math.max(1, ...legs.flatMap((l) => [l.before, l.after]));
  const delta = legs.reduce((sum, l) => sum + l.after - l.before, 0);
  return (
    <figure className="budget-movement">
      <figcaption>
        <strong>Where the budget moves</strong>
        <span>Daily allocation change {signedMoney(delta)} · all rows share one scale</span>
      </figcaption>
      <div className="comparison-legend">
        <span>
          <i className="current-key" />
          Current
        </span>
        <span>
          <i className="proposed-key" />
          Proposed
        </span>
      </div>
      {legs.map((l) => (
        <div className="budget-movement-row" key={l.budget_id}>
          <div className="budget-movement-label">
            <strong>{l.entity}</strong>
            <span>
              {l.platform} · {signedMoney(l.after - l.before)} / day
            </span>
          </div>
          <div className="paired-bars">
            <div>
              <span className="allocation-track" aria-hidden="true">
                <i className="current-bar" style={{ width: `${(l.before / max) * 100}%` }} />
              </span>
              <b>
                <span className="sr-only">Current: </span>
                {money(l.before)}
              </b>
            </div>
            <div>
              <span className="allocation-track" aria-hidden="true">
                <i className="proposed-bar" style={{ width: `${(l.after / max) * 100}%` }} />
              </span>
              <b>
                <span className="sr-only">Proposed: </span>
                {money(l.after)}
              </b>
            </div>
          </div>
        </div>
      ))}
    </figure>
  );
}

export function OutcomeComparison({
  predicted,
  measured,
}: {
  predicted: number;
  measured: number;
}) {
  const min = Math.min(0, predicted, measured);
  const max = Math.max(0, predicted, measured);
  const range = max - min || 1;
  const zero = (-min / range) * 100;
  return (
    <figure className="outcome-comparison">
      <figcaption>
        Forecast versus measured ΔCAA <span>Shared scale · zero is marked</span>
      </figcaption>
      {[
        { name: 'Forecast', value: predicted, cls: 'forecast-bar' },
        { name: 'Measured', value: measured, cls: 'measured-bar' },
      ].map((r) => (
        <div className="outcome-comparison-row" key={r.name}>
          <span>{r.name}</span>
          <div className="signed-track" aria-hidden="true">
            <i className="zero-line" style={{ left: `${zero}%` }} />
            <i
              className={r.cls}
              style={{
                borderWidth: r.value === 0 ? 0 : undefined,
                left: `${((Math.min(0, r.value) - min) / range) * 100}%`,
                width: `${(Math.abs(r.value) / range) * 100}%`,
              }}
            />
          </div>
          <strong>{signedMoney(r.value)}</strong>
        </div>
      ))}
    </figure>
  );
}
