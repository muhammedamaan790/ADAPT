import { useState } from 'react';
import { Link } from 'react-router-dom';
import { Download, ArrowRight } from 'lucide-react';
import { useDecisions, useOutcomes } from '../hooks/workspace';
import { dataMode } from '../api/client';
import {
  Badge,
  Disclosure,
  Empty,
  ErrorState,
  Loading,
  SectionTitle,
  Status,
} from '../components/ui';
import { dateTime, money, signedMoney } from '../lib/format';
import { downloadText } from '../lib/csv';
import { OutcomeComparison } from '../components/FinancialComparison';

export function Outcomes() {
  const outcomes = useOutcomes();
  const decisions = useDecisions();
  const [kind, setKind] = useState('ALL');
  const [verdict, setVerdict] = useState('ALL');
  if (outcomes.isPending) return <Loading label="Loading outcomes" />;
  if (outcomes.error)
    return <ErrorState error={outcomes.error} retry={() => void outcomes.refetch()} />;
  const all = outcomes.data || [];
  const rows = all.filter(
    (o) => (kind === 'ALL' || o.class === kind) && (verdict === 'ALL' || o.verdict === verdict),
  );
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Outcomes</h1>
          <p>
            Compare the decision-time prediction with its measured result and feedback eligibility.
          </p>
        </div>
        <button
          className="button secondary"
          disabled={!all.length}
          aria-label="Download outcome records"
          onClick={() =>
            downloadText(
              'adapt-outcomes.json',
              JSON.stringify({ mode: dataMode, outcomes: all }, null, 2),
            )
          }
        >
          <Download size={15} /> Export records
        </button>
      </div>
      <div className="workbench-stats">
        <div>
          <span>Matured records</span>
          <strong>{all.length}</strong>
        </div>
        <div>
          <span>Calibration updates</span>
          <strong>{all.filter((o) => o.calibration_applied).length}</strong>
        </div>
        <div>
          <span>Inconclusive</span>
          <strong>{all.filter((o) => o.verdict === 'INCONCLUSIVE').length}</strong>
        </div>
      </div>
      <div className="filter-bar report-filters">
        <label className="field">
          Decision class
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="ALL">All classes</option>
            {['OPTIMIZATION', 'SAFETY', 'OPERATIONAL', 'EXPLORATION'].map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
        </label>
        <label className="field">
          Verdict
          <select value={verdict} onChange={(e) => setVerdict(e.target.value)}>
            <option value="ALL">All verdicts</option>
            {['SUCCESS', 'NEUTRAL', 'FAILED', 'INCONCLUSIVE'].map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
      </div>
      {!rows.length ? (
        <section className="panel">
          <Empty title={all.length ? 'No matching outcomes' : 'No matured outcomes yet'}>
            {all.length
              ? 'Change your class or verdict filter.'
              : 'Verify an approved decision, then advance its evaluation horizon in Scenario Lab. Drafts and restored fixture allocations do not produce optimization feedback.'}
          </Empty>
          <Link className="button secondary" to="/scenarios">
            Open Scenario Lab <ArrowRight size={15} />
          </Link>
        </section>
      ) : (
        rows.map((o) => (
          <section className="panel" key={o.outcome_id}>
            <SectionTitle
              title={
                decisions.data?.find((d) => d.decision_id === o.decision_id)?.title || o.decision_id
              }
            >
              <Status value={o.verdict} />
            </SectionTitle>
            <div className="badge-row">
              <Badge>{o.class}</Badge>
              <Badge tone={o.world === 'REAL' ? 'accent' : 'neutral'}>{o.world}</Badge>
              <Badge>
                {o.calibration_applied ? 'Calibration applied' : 'Excluded from calibration'}
              </Badge>
            </div>
            <dl className="fact-grid">
              <div>
                <dt>Decision-time forecast</dt>
                <dd>{signedMoney(o.predicted)}</dd>
              </div>
              <div>
                <dt>{dataMode === 'fixture' ? 'Example measured ΔCAA' : 'Measured ΔCAA'}</dt>
                <dd>{signedMoney(o.measured)}</dd>
              </div>
              <div>
                <dt>Prediction error</dt>
                <dd>{signedMoney(o.measured - o.predicted)}</dd>
              </div>
            </dl>
            <OutcomeComparison predicted={o.predicted} measured={o.measured} />
            <p className="workbench-copy">{o.method}</p>
            <Disclosure title="Measurement context">
              <p>
                Counterfactual contribution: {money(o.counterfactual)}. Matured{' '}
                {dateTime(o.matured_at)}.
              </p>
              <p>
                {o.calibration_applied
                  ? `Calibration factor ${o.factor_before.toFixed(2)} → ${o.factor_after.toFixed(2)}. Applied once.`
                  : 'This outcome does not update response-curve calibration.'}
              </p>
              <code>{o.outcome_id}</code>
            </Disclosure>
            <Link className="text-link" to={`/decisions/${o.decision_id}`}>
              Inspect original decision <ArrowRight size={14} />
            </Link>
          </section>
        ))
      )}
    </>
  );
}
