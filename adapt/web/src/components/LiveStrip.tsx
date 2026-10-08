import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, dataMode } from '../api/client';
import { policy } from '../api/policy';
import type { ScenarioKey } from '../api/contracts';
import { dateTime } from '../lib/format';
import { useOverview } from '../hooks/workspace';
import { useSession } from './AuthGate';

/** Says whether the numbers on screen are live: world day, scenario, last cycle and sync age, with the demo driver. */
export function LiveStrip() {
  const overview = useOverview();
  const session = useSession();
  const queryClient = useQueryClient();
  const [now, setNow] = useState(() => Date.now());
  const [pulse, setPulse] = useState(false);
  const [note, setNote] = useState('');
  const [pick, setPick] = useState<ScenarioKey | ''>('');
  const first = useRef(true);

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  // react-query keeps the same reference when a refetch returns identical data, so this fires only on real change
  useEffect(() => {
    if (!overview.data) return;
    if (first.current) {
      first.current = false;
      return;
    }
    setPulse(true);
    const t = window.setTimeout(() => setPulse(false), 2400);
    return () => window.clearTimeout(t);
  }, [overview.data]);

  const canDrive = dataMode === 'fixture' || (session !== null && session.user.role !== 'viewer');
  const catalog = useQuery({
    queryKey: ['sim', 'scenarios'],
    queryFn: policy.scenarios,
    enabled: canDrive,
    staleTime: 60_000,
  });
  const drive = useMutation({
    mutationFn: async (
      action: { kind: 'advance'; days: number } | { kind: 'scenario'; key: ScenarioKey },
    ) => (action.kind === 'advance' ? api.advance(action.days) : policy.loadScenario(action.key)),
    onSuccess: (_, action) => {
      setNote(
        action.kind === 'advance'
          ? dataMode === 'api'
            ? `Advancing ${action.days} day${action.days === 1 ? '' : 's'}; the pipeline runs each day and values update as it finishes.`
            : `Example clock advanced ${action.days} day${action.days === 1 ? '' : 's'}.`
          : `${action.key} injected. Advance the clock to let it play out.`,
      );
      void queryClient.invalidateQueries();
    },
    onError: (e) => setNote(e instanceof Error ? e.message : 'The request failed.'),
  });

  const d = overview.data;
  const age = overview.dataUpdatedAt
    ? Math.max(0, Math.round((now - overview.dataUpdatedAt) / 1000))
    : null;
  const live = dataMode === 'api';
  const stale = live && (overview.isError || (age !== null && age > 20));
  const available = catalog.data?.items.filter((s) => s.status === 'AVAILABLE') ?? [];

  return (
    <div className="shell-wide live-strip" data-mode={dataMode} aria-live="polite">
      <span
        className={`live-dot${pulse ? ' live-dot-pulse' : ''}${stale ? ' live-dot-stale' : ''}${live ? '' : ' live-dot-example'}`}
        aria-hidden="true"
      />
      <span className="live-state">
        <strong>{live ? (stale ? 'Connection lost' : 'Live') : 'Example data'}</strong>
        {d && (
          <>
            <span>Day {d.world_day}</span>
            <span>Scenario {d.scenario}</span>
            <span>Last cycle {dateTime(d.decision_ts)}</span>
          </>
        )}
        {live ? (
          age !== null && <span className="live-age">synced {age}s ago</span>
        ) : (
          <span className="live-age">
            Illustrative frontend data. No engine, simulator or ad account is connected.
          </span>
        )}
        {pulse && <span className="live-updated">values updated</span>}
      </span>
      {canDrive && (
        <span className="live-controls">
          <select
            aria-label="Scenario to inject"
            value={pick}
            onChange={(e) => setPick(e.target.value as ScenarioKey | '')}
            disabled={drive.isPending}
          >
            <option value="">Inject scenario…</option>
            {available.map((s) => (
              <option key={s.key} value={s.key}>
                {s.key} · {s.title}
              </option>
            ))}
          </select>
          <button
            className="live-button"
            disabled={!pick || drive.isPending}
            onClick={() => pick && drive.mutate({ kind: 'scenario', key: pick })}
          >
            Inject
          </button>
          <button
            className="live-button"
            disabled={drive.isPending}
            onClick={() => drive.mutate({ kind: 'advance', days: 1 })}
          >
            +1 day
          </button>
          <button
            className="live-button"
            disabled={drive.isPending}
            onClick={() => drive.mutate({ kind: 'advance', days: 3 })}
          >
            +3 days
          </button>
        </span>
      )}
      {note && (
        <span className="live-note" role="status">
          {note}
        </span>
      )}
    </div>
  );
}
