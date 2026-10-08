import { useQuery } from '@tanstack/react-query';
import { stage2 } from '../api/stage2';
import { Badge } from './ui';

/** Execution mode per platform (fixed at backend startup, never an automatic fallback) and the SIM OUT OF SYNC state:
 * a verified live change not yet mirrored into the simulated world blocks world advance (spec §9.4). */
export function PlatformBanner() {
  const q = useQuery({
    queryKey: ['platforms'],
    queryFn: stage2.platforms,
    refetchInterval: 15000,
    retry: false,
  });
  const p = q.data;
  if (!p) return null;
  const failing = p.platforms.filter((x) => !x.ok);
  const live = p.platforms.filter((x) => x.mode === 'LIVE');
  if (!failing.length && !live.length && !p.sim_out_of_sync.length) return null;
  return (
    <div className="platform-banner" role="status">
      {p.sim_out_of_sync.length > 0 && (
        <p>
          <Badge tone="danger">SIM OUT OF SYNC</Badge> {p.sim_out_of_sync.length} verified live
          change(s) are not mirrored into the simulated world (
          {p.sim_out_of_sync
            .map((s) => `${s.budget_id} ${s.state.replaceAll('_', ' ')}`)
            .join(', ')}
          ). World advance is blocked until they are mirrored or resolved on the Execution page.
        </p>
      )}
      {live.map((x) => (
        <p key={x.platform}>
          <Badge tone={x.ok ? 'accent' : 'danger'}>{x.ok ? 'LIVE' : 'LIVE · BLOCKED'}</Badge>{' '}
          {x.label}
          {x.reason ? ` · ${x.reason}` : ''}
        </p>
      ))}
      {failing
        .filter((x) => x.mode !== 'LIVE')
        .map((x) => (
          <p key={x.platform}>
            <Badge tone="danger">{x.platform.toUpperCase()} UNAVAILABLE</Badge> {x.reason}
          </p>
        ))}
    </div>
  );
}
