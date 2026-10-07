import { ArrowRight, LockKeyhole } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { Decision } from '../api/contracts';
import { money, percent, signedMoney } from '../lib/format';
import { Status } from './ui';

export function ChannelMark({ platform }: { platform: string }) {
  return (
    <span
      className={`channel-mark ${platform === 'Meta' ? 'mark-meta' : 'mark-google'}`}
      aria-hidden="true"
    >
      <img
        src={platform === 'Meta' ? '/providers/meta.svg' : '/providers/google.png'}
        alt=""
        width="28"
        height="28"
      />
    </span>
  );
}

export function HomeProposal({ decision: d }: { decision: Decision }) {
  const scale = Math.max(1, ...d.legs.flatMap((l) => [l.before, l.after]));
  const financial = d.class !== 'OPERATIONAL' && d.valuation_status !== 'NOT_ESTIMABLE';
  const blocked = d.checks.some((c) => !c.passed);
  return (
    <section className="panel home-proposal">
      <div className="home-section-heading">
        <h2>{d.legs.length ? 'Budget proposal' : 'Operational response'}</h2>
        <Status value={d.status} />
      </div>
      <p className="home-section-description" title={d.summary}>
        {d.legs.length
          ? `Daily recommendation · ${d.horizon_days}-day evaluation horizon`
          : d.summary}
      </p>
      <div className="home-allocation-mobile">
        {d.legs.map((l) => (
          <div className="mobile-budget-leg" key={l.budget_id}>
            <div className="mobile-budget-identity">
              <ChannelMark platform={l.platform} />
              <strong>{l.entity}</strong>
              <span>{signedMoney(l.after - l.before)} / day</span>
            </div>
            <dl>
              <div>
                <dt>Current / day</dt>
                <dd>{money(l.before)}</dd>
                <span className="home-budget-track" aria-hidden="true">
                  <i className="budget-current" style={{ width: `${(l.before / scale) * 100}%` }} />
                </span>
              </div>
              <div>
                <dt>Proposed / day</dt>
                <dd className="home-proposed-value">{money(l.after)}</dd>
                <span className="home-budget-track" aria-hidden="true">
                  <i className="budget-proposed" style={{ width: `${(l.after / scale) * 100}%` }} />
                </span>
              </div>
            </dl>
          </div>
        ))}
        {d.legs.length > 0 && (
          <div className="mobile-budget-reserve">
            <span>Unallocated / day</span>
            <strong>{money(d.unallocated)}</strong>
          </div>
        )}
      </div>
      {d.legs.length ? (
        <div
          className="home-allocation-scroll"
          tabIndex={0}
          role="region"
          aria-label="Recommended daily campaign budgets"
        >
          <table className="home-allocation">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Current / day</th>
                <th>Proposed / day</th>
                <th>Change</th>
              </tr>
            </thead>
            <tbody>
              {d.legs.map((l) => (
                <tr key={l.budget_id}>
                  <td>
                    <div className="home-campaign">
                      <ChannelMark platform={l.platform} />
                      <div>
                        <strong>{l.entity}</strong>
                        <small>{l.platform}</small>
                      </div>
                    </div>
                  </td>
                  <td>
                    <strong>{money(l.before)}</strong>
                    <span className="home-budget-track" aria-hidden="true">
                      <i
                        className="budget-current"
                        style={{ width: `${(l.before / scale) * 100}%` }}
                      />
                    </span>
                  </td>
                  <td className="home-proposed-value">
                    <strong>{money(l.after)}</strong>
                    <span className="home-budget-track" aria-hidden="true">
                      <i
                        className="budget-proposed"
                        style={{ width: `${(l.after / scale) * 100}%` }}
                      />
                    </span>
                  </td>
                  <td>
                    <strong>{signedMoney(l.after - l.before)}</strong>
                    <small>
                      {l.before ? percent((l.after - l.before) / l.before) : 'New budget'}
                    </small>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th colSpan={2}>
                  Unallocated budget <span>Daily ceiling {money(d.budget_ceiling)}</span>
                </th>
                <td colSpan={2}>
                  <strong>{money(d.unallocated)}</strong>
                  <small>Held outside this allocation</small>
                </td>
              </tr>
            </tfoot>
          </table>
        </div>
      ) : (
        <div className="home-operational">
          <LockKeyhole size={25} />
          <strong>Budget changes stay on hold</strong>
          <p>Inspect the incident and resolve the supplied policy checks before taking action.</p>
        </div>
      )}
      <div className="home-proposal-footer">
        <div>
          <span>
            {financial ? `Estimated ΔCAA · ${d.horizon_days} days` : 'Financial forecast'}
          </span>
          <strong>{financial ? signedMoney(d.expected.p50) : 'Not estimable'}</strong>
        </div>
        <Link
          className={`button ${blocked ? 'secondary' : 'primary'}`}
          to={`/decisions/${encodeURIComponent(d.decision_id)}#decision-review`}
        >
          {blocked
            ? 'Inspect policy blockers'
            : d.status === 'PENDING_APPROVAL'
              ? 'Review & approve'
              : 'Inspect decision'}
          <ArrowRight size={15} />
        </Link>
      </div>
      <p className="home-proposal-note">
        {blocked ? 'A policy check blocks execution.' : 'Human approval required.'} This view does
        not execute budget changes.
      </p>
    </section>
  );
}
