import { useState } from 'react';
import type { z } from 'zod';
import type { fatigueSchema } from '../api/insight-contracts';
import { Badge } from './ui';
import { percent } from '../lib/format';

export function CreativeSignals({ creatives }: { creatives: z.infer<typeof fatigueSchema>[] }) {
  const [scope, setScope] = useState('ALL');
  const rows = creatives.filter((c) => scope === 'ALL' || c.status === scope);
  const review = creatives.filter((c) => c.status === 'REVIEW').length;
  return (
    <>
      <div className="creative-filter">
        <p>
          {review} of {creatives.length} creatives need review. CTR change and exposure frequency
          are diagnostic signals, not proof of fatigue.
        </p>
        <label className="field">
          Creative status
          <select
            aria-label="Creative status"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="ALL">All creatives</option>
            <option value="REVIEW">Needs review</option>
            <option value="STABLE">Stable</option>
          </select>
        </label>
      </div>
      <p className="sr-only" role="status">
        {rows.length} matching creatives
      </p>
      {rows.length ? (
        <ul className="creative-signals">
          {rows.map((c) => (
            <li key={c.creative_id}>
              <div className="ledger-heading">
                <h3>{c.name}</h3>
                <Badge tone={c.status === 'REVIEW' ? 'warning' : 'neutral'}>
                  {c.status === 'REVIEW' ? 'Needs review' : 'Stable'}
                </Badge>
              </div>
              <p className="creative-identity">
                {c.entity} · {c.creative_id}
              </p>
              <dl className="creative-measures">
                <div>
                  <dt>CTR change</dt>
                  <dd>
                    {c.ctr_change > 0 ? '+' : ''}
                    {percent(c.ctr_change)}
                  </dd>
                </div>
                <div>
                  <dt>Frequency</dt>
                  <dd>
                    {c.frequency.toFixed(1)}
                    <small> exposures</small>
                  </dd>
                </div>
              </dl>
              <p className="creative-reason">{c.reason}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="workbench-copy">No creatives in this status. Choose another filter.</p>
      )}
    </>
  );
}
