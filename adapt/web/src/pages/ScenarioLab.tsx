import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { policy } from '../api/policy';
import {
  ArrowRight,
  Beaker,
  CalendarClock,
  CheckCircle2,
  Clock3,
  FlaskConical,
  Play,
  RotateCcw,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { api, dataMode } from '../api/client';
import type { ScenarioKey } from '../api/contracts';
import { useAction, useEvents, useExecutions, useOverview } from '../hooks/workspace';
import {
  Badge,
  Disclosure,
  Empty,
  ErrorState,
  InlineError,
  Loading,
  Modal,
  SectionTitle,
} from '../components/ui';
import { dateTime, money } from '../lib/format';

export function ScenarioLab() {
  const overview = useOverview();
  const catalog = useQuery({ queryKey: ['scenario-catalog'], queryFn: policy.scenarios });
  const events = useEvents();
  const executions = useExecutions();
  const [selected, setSelected] = useState<ScenarioKey>('DEMO_01');
  const [days, setDays] = useState(3);
  const [seed, setSeed] = useState('42');
  const [resetDialog, setResetDialog] = useState(false);
  const [scenarioDialog, setScenarioDialog] = useState(false);
  const scenario = useAction(async (key: ScenarioKey) => {
    await policy.loadScenario(key);
  });
  const advance = useAction(async (n: number) => {
    await api.advance(n);
  });
  const reset = useAction(async (n: number) => {
    await api.reset(n);
  });
  if (overview.isPending)
    return (
      <>
        <div className="page-heading">
          <h1>Scenario Lab</h1>
        </div>
        <Loading label="Loading Scenario Lab" />
      </>
    );
  if (overview.error || !overview.data)
    return (
      <>
        <div className="page-heading">
          <h1>Scenario Lab</h1>
        </div>
        <ErrorState
          error={overview.error || new Error('World state unavailable')}
          retry={() => void overview.refetch()}
        />
      </>
    );
  const world = overview.data;
  const busy = scenario.isPending || advance.isPending || reset.isPending;
  const pending = executions.data?.some(
    (e) =>
      [
        'PENDING',
        'EXECUTING',
        'PARTIAL',
        'COMPENSATING',
        'COMPENSATION_FAILED',
        'HUMAN_RESOLUTION_REQUIRED',
      ].includes(e.state) ||
      e.legs.some(
        (l) =>
          ['UNKNOWN', 'CONFLICT'].includes(l.external_state) ||
          ['MIRROR_PENDING', 'MIRROR_FAILED'].includes(l.sim_sync_state),
      ),
  );
  const cannotAdvance = busy || pending || executions.isPending || executions.isError;
  const seedValid =
    /^\d+$/.test(seed) && Number.isSafeInteger(Number(seed)) && Number(seed) <= 2147483647;
  const available = catalog.data?.items.filter((s) => s.status === 'AVAILABLE') || [];
  const later = catalog.data?.items.filter((s) => s.status === 'NOT_BUILT') || [];
  const chosen = available.find((s) => s.key === selected) || available[0];
  const cannotLoad =
    busy ||
    pending ||
    executions.isPending ||
    executions.isError ||
    catalog.isPending ||
    catalog.isError ||
    !chosen;
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Scenario Lab</h1>
          <p>Follow a controlled scenario from signal to measured outcome.</p>
        </div>
        <Badge tone="accent">{catalog.data?.stage || 'Scenario catalog unavailable'}</Badge>
      </div>
      <div className="lab-intro">
        <FlaskConical size={23} />
        <div>
          <strong>
            {dataMode === 'fixture'
              ? 'A rehearsal space for the frontend'
              : 'A controlled simulation workspace'}
          </strong>
          <p>
            {dataMode === 'fixture'
              ? 'These examples exercise screens and interactions. They do not validate detection, optimization or simulator truth.'
              : 'Controls are proxied through FastAPI to the separate world service. Hidden ground truth is not exposed here.'}
          </p>
        </div>
      </div>
      <div className="lab-layout">
        <section className="panel scenario-selector">
          <SectionTitle title="Choose a scenario">
            <Badge>{available.length} available</Badge>
          </SectionTitle>
          <p className="section-description">{catalog.data?.note}</p>
          {catalog.isPending && <Loading label="Loading scenario capabilities" />}
          {catalog.error && (
            <ErrorState error={catalog.error} retry={() => void catalog.refetch()} />
          )}
          {!catalog.isPending && !catalog.error && !available.length && (
            <Empty title="No supported scenarios">
              Required backend modules must be built before a scenario can be loaded.
            </Empty>
          )}
          <div className="scenario-list">
            {available.map((s) => (
              <label
                className={`scenario-option ${chosen?.key === s.key ? 'selected' : ''}`}
                key={s.key}
              >
                <input
                  type="radio"
                  name="scenario"
                  value={s.key}
                  checked={chosen?.key === s.key}
                  disabled={busy}
                  onChange={() => setSelected(s.key)}
                />
                <span className="scenario-key">{s.key}</span>
                <div>
                  <strong>{s.title}</strong>
                  <p>{s.description}</p>
                </div>
                {chosen?.key === s.key && <CheckCircle2 size={17} />}
              </label>
            ))}
          </div>
          <div className="scenario-actions">
            <span>
              Currently loaded <b>{world.scenario}</b>
            </span>
            <button
              className="button primary"
              disabled={!!cannotLoad}
              onClick={() => {
                scenario.reset();
                setScenarioDialog(true);
              }}
            >
              <Play size={15} />
              {scenario.isPending ? 'Loading…' : 'Load scenario'}
            </button>
          </div>
          <InlineError error={scenario.error} />
          {later.length > 0 && (
            <Disclosure title="Later-stage scenarios">
              <ul className="connection-list">
                {later.map((s) => (
                  <li key={s.key}>
                    <div className="ledger-heading">
                      <strong>
                        {s.key} · {s.title}
                      </strong>
                      <Badge tone="warning">Not built</Badge>
                    </div>
                    <p>{s.description}</p>
                    <p className="caption">
                      Required modules:{' '}
                      {s.missing_modules.length
                        ? s.missing_modules.join(', ')
                        : 'backend qualification pending'}
                      . Loading is unavailable.
                    </p>
                  </li>
                ))}
              </ul>
            </Disclosure>
          )}
        </section>
        <aside className="lab-controls">
          <section className="panel">
            <SectionTitle title="World clock">
              <CalendarClock size={20} />
            </SectionTitle>
            <div className="world-day">
              <span>Current day</span>
              <strong>{world.world_day.toString().padStart(2, '0')}</strong>
              <Badge>{world.scenario}</Badge>
            </div>
            <label className="field">
              Advance by
              <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
                <option value={1}>1 day</option>
                <option value={3}>3 days · demo outcome</option>
                <option value={7}>7 days</option>
              </select>
            </label>
            <button
              className="button primary full"
              disabled={!!cannotAdvance}
              onClick={() => advance.mutate(days)}
            >
              {advance.isPending ? 'Advancing…' : `Advance ${days} day${days === 1 ? '' : 's'}`}
              <ArrowRight size={16} />
            </button>
            {pending && (
              <p className="inline-error">
                Execution or mirror state is unresolved. Advance is blocked.
              </p>
            )}
            {executions.isError && <InlineError error={executions.error} />}
            <InlineError error={advance.error} />
            <p className="caption">
              Executed decisions mature through the outcome path. Repeated advance must not reapply
              feedback.
            </p>
          </section>
          <section className="panel">
            <SectionTitle title="Feedback state" />
            <div className="feedback-factor">
              <span>Impact calibration factor</span>
              <strong>{world.calibration.toFixed(2)}×</strong>
            </div>
            <p className="caption">
              Next forecast = raw forecast × factor. Only optimization outcomes update curves.
            </p>
            <div className="verdict-grid">
              {Object.entries(world.counts).map(([k, v]) => (
                <div key={k}>
                  <b>{v}</b>
                  <span>{k}</span>
                </div>
              ))}
            </div>
            {world.counts.success + world.counts.failed + world.counts.neutral > 0 && (
              <p className="factor-illustration">
                For an example raw forecast of ₹8,000:
                <strong>{money(8000 * world.calibration)}</strong>
              </p>
            )}
          </section>
          <section className="reset-section">
            <h3>Start a fresh run</h3>
            <p>
              {dataMode === 'fixture'
                ? 'Resets local fixture decisions and events. Seed is reserved for the future world adapter.'
                : 'Resets the world workspace, decisions and event feed.'}
            </p>
            <label className="field">
              World seed
              <input
                inputMode="numeric"
                value={seed}
                onChange={(e) => setSeed(e.target.value)}
                aria-invalid={!seedValid}
              />
            </label>
            <button
              className="button secondary full"
              disabled={busy || !seedValid || !!pending}
              onClick={() => {
                reset.reset();
                setResetDialog(true);
              }}
            >
              <RotateCcw size={15} />
              Reset workspace
            </button>
            <InlineError error={reset.error} />
          </section>
        </aside>
      </div>
      <section className="panel event-panel">
        <SectionTitle title="Event feed">
          <span className="small muted">
            <Clock3 size={13} />
            Newest first · refreshed every 3 seconds
          </span>
        </SectionTitle>
        {events.isPending ? (
          <Loading label="Loading events" />
        ) : events.error ? (
          <ErrorState error={events.error} retry={() => void events.refetch()} />
        ) : !events.data?.length ? (
          <Empty title="No events yet">Load a scenario or advance the world to begin.</Empty>
        ) : (
          <ol className="event-list">
            {events.data.map((event) => (
              <li key={event.id}>
                <span className="event-node">
                  <Beaker size={15} />
                </span>
                <div>
                  <div className="event-heading">
                    <Badge>{event.kind}</Badge>
                    <time dateTime={event.at}>{dateTime(event.at)}</time>
                  </div>
                  <p>{event.message}</p>
                  {event.decision_id && (
                    <Link className="text-link" to={`/decisions/${event.decision_id}`}>
                      View decision <ArrowRight size={13} />
                    </Link>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </section>
      {scenarioDialog && chosen && (
        <Modal
          title={`Load ${chosen.key}?`}
          close={() => {
            if (!scenario.isPending) setScenarioDialog(false);
          }}
        >
          <p>
            {chosen.title}. Loading a scenario replaces the current workspace’s example decisions,
            outcomes and events.
          </p>
          <InlineError error={scenario.error} />
          <div className="modal-actions">
            <button
              className="button secondary"
              onClick={() => setScenarioDialog(false)}
              disabled={scenario.isPending}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={!!cannotLoad}
              onClick={() =>
                scenario.mutate(chosen.key, { onSuccess: () => setScenarioDialog(false) })
              }
            >
              Confirm load
            </button>
          </div>
        </Modal>
      )}
      {resetDialog && (
        <Modal
          title="Reset the workspace?"
          close={() => {
            if (!reset.isPending) setResetDialog(false);
          }}
        >
          <p>
            This removes the current {dataMode === 'fixture' ? 'local fixture' : 'simulation'}{' '}
            decisions, execution states, outcomes and event history. Seed: {seed}.
          </p>
          <InlineError error={reset.error} />
          <div className="modal-actions">
            <button
              className="button secondary"
              onClick={() => setResetDialog(false)}
              disabled={reset.isPending}
            >
              Cancel
            </button>
            <button
              className="button danger"
              disabled={reset.isPending}
              onClick={() => reset.mutate(Number(seed), { onSuccess: () => setResetDialog(false) })}
            >
              Confirm reset
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
