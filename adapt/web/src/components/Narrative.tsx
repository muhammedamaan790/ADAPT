import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ApiError } from '../api/client';
import { stage2 } from '../api/stage2';
import { Badge, InlineError, Loading, SectionTitle } from './ui';

const NEXT: Record<string, { label: string; href: (id: string) => string }> = {
  VIEW_DECISION: { label: 'Open the decision', href: (id) => `/decisions/${id}` },
  APPROVE_REVIEW: { label: 'Review for approval', href: (id) => `/decisions/${id}` },
  INVESTIGATE: { label: 'Investigate the incident', href: () => '/anomalies' },
  RECONCILE: { label: 'Reconcile the execution', href: () => '/executions' },
};

/** The guarded explanation of an incident or decision (spec §11). Every number in it was checked against the
 * deterministic claim atoms; financial figures elsewhere on the page never come from this text. */
export function Narrative({ kind, id }: { kind: 'decision' | 'incident'; id: string }) {
  const q = useQuery({
    queryKey: ['narrative', kind, id],
    queryFn: () => stage2.narrative(kind, id),
    retry: false,
  });
  if (q.isPending) return <Loading label="Loading explanation" />;
  // 404: no explanation exists for this item (uploaded-data workspaces have none): show nothing, not an error
  if (q.error)
    return q.error instanceof ApiError && q.error.status === 404 ? null : (
      <InlineError error={q.error} />
    );
  const n = q.data;
  if (!n) return null;
  const llm = n.source.startsWith('llm:');
  const next = n.next_step.ref_id ? NEXT[n.next_step.kind] : undefined;
  return (
    <section className="panel" aria-label="Explanation">
      <SectionTitle title={n.headline}>
        <Badge tone={llm ? 'accent' : 'neutral'}>
          {llm ? `LLM · ${n.source.slice(4)}` : 'TEMPLATE'}
        </Badge>
        <Badge tone="success">{n.badge}</Badge>
      </SectionTitle>
      <ul className="narrative-list">
        {n.sentences.map((s, i) => (
          <li key={i}>
            <p>{s.text}</p>
            <span className="narrative-chips">
              {s.claim_levels.map((c) => (
                <Badge key={c}>{c.replaceAll('_', ' ')}</Badge>
              ))}
              {s.evidence_ids.map((e) => (
                <code key={e}>{e}</code>
              ))}
            </span>
          </li>
        ))}
      </ul>
      {n.not_estimable_reason && <p className="notice">{n.not_estimable_reason}</p>}
      {!llm && n.fallback_reason && (
        <p className="workbench-copy">Deterministic template shown: {n.fallback_reason}.</p>
      )}
      {next && n.next_step.ref_id && (
        <Link className="text-link" to={next.href(n.next_step.ref_id)}>
          {next.label}
        </Link>
      )}
    </section>
  );
}
