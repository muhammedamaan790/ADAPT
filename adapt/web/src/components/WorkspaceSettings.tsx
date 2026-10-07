import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { completion } from '../api/completion';
import type { Objective } from '../api/contracts';
import type { workspaceObjectiveSchema } from '../api/completion-contracts';
import type { z } from 'zod';
import { useAction } from '../hooks/workspace';
import { Badge, Empty, ErrorState, InlineError, Loading, Modal, SectionTitle } from './ui';
import { dateTime, humanStatus } from '../lib/format';

export function WorkspaceSettings() {
  const objective = useQuery({ queryKey: ['workspace-objective'], queryFn: completion.objective });
  const history = useQuery({ queryKey: ['policy-history'], queryFn: completion.history });
  const [selected, setSelected] = useState<Objective | null>(null);
  const [review, setReview] = useState<{
    current: z.infer<typeof workspaceObjectiveSchema>;
    choice: Objective;
  } | null>(null);
  const [reason, setReason] = useState('');
  const change = useAction(
    (value: {
      current: z.infer<typeof workspaceObjectiveSchema>;
      objective: Objective;
      reason: string;
    }) => completion.changeObjective(value.current, value.objective, value.reason),
  );
  const current = objective.data;
  const choice = selected ?? current?.objective;
  return (
    <>
      <section className="panel">
        <SectionTitle title="Workspace objective">
          <Badge>VERSIONED SETTINGS</Badge>
        </SectionTitle>
        <p className="section-description">
          Set the default for future optimizer runs. Existing proposals retain their recorded
          objective; the backend owns invalidation and policy enforcement.
        </p>
        {objective.isPending ? (
          <Loading label="Loading workspace objective" />
        ) : objective.error ? (
          <ErrorState error={objective.error} retry={() => void objective.refetch()} />
        ) : (
          current && (
            <>
              <p className="workbench-copy">{current.note}</p>
              <dl className="fact-grid">
                <div>
                  <dt>Workspace</dt>
                  <dd>{current.workspace_id}</dd>
                </div>
                <div>
                  <dt>Current objective</dt>
                  <dd>{humanStatus(current.objective)}</dd>
                </div>
                <div>
                  <dt>Revision</dt>
                  <dd className="break-all">{current.revision}</dd>
                </div>
              </dl>
              <label className="field">
                Default objective
                <select
                  value={choice}
                  disabled={!current.can_change || change.isPending}
                  onChange={(e) => {
                    setSelected(e.target.value as Objective);
                    change.reset();
                  }}
                >
                  {current.supported_objectives.map((o) => (
                    <option key={o} value={o}>
                      {humanStatus(o)}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="button secondary"
                disabled={!current.can_change || choice === current.objective || change.isPending}
                onClick={() => {
                  setReason('');
                  change.reset();
                  if (choice) setReview({ current, choice });
                }}
              >
                Review objective change
              </button>
              {!current.can_change && (
                <p className="caption">
                  Changing this setting is unavailable for the current backend permissions or
                  fixture workspace.
                </p>
              )}
              {change.isSuccess && (
                <p className="notice" role="status">
                  Workspace objective updated. Review refreshed proposals before any execution.
                </p>
              )}
            </>
          )
        )}
      </section>
      <section className="panel">
        <SectionTitle title="Policy version history" />
        {history.isPending ? (
          <Loading label="Loading policy history" />
        ) : history.error ? (
          <ErrorState error={history.error} retry={() => void history.refetch()} />
        ) : (
          <>
            <p className="workbench-copy">{history.data!.note}</p>
            {!history.data!.versions.length ? (
              <Empty title="No policy history supplied">
                The backend audit service must report version changes, actors and review reasons.
              </Empty>
            ) : (
              <ol className="ledger-list">
                {history.data!.versions.map((v) => (
                  <li key={v.version}>
                    <div className="ledger-heading">
                      <strong>{v.version}</strong>
                      <time>{dateTime(v.at)}</time>
                    </div>
                    <p>
                      {v.actor} · {v.reason}
                    </p>
                    <ul>
                      {v.changes.map((c, i) => (
                        <li key={i}>
                          {c.field}: {c.before} → {c.after}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </section>
      {review && current && choice && (
        <Modal
          title="Change workspace objective"
          close={() => {
            if (!change.isPending) setReview(null);
          }}
        >
          <p className="modal-description">
            {humanStatus(review.current.objective)} → {humanStatus(review.choice)}. This changes
            future recommendations and sends no ad-account budget instruction.
          </p>
          <p className="hash-label">
            Workspace: {review.current.workspace_id} · revision: {review.current.revision}
          </p>
          <label className="field">
            Change reason
            <textarea
              rows={3}
              maxLength={2000}
              disabled={change.isPending}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <InlineError error={change.error} />
          <div className="modal-actions">
            <button
              className="button secondary"
              disabled={change.isPending}
              onClick={() => setReview(null)}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={
                change.isPending ||
                reason.trim().length < 10 ||
                !current.can_change ||
                current.revision !== review.current.revision ||
                current.workspace_id !== review.current.workspace_id
              }
              onClick={() =>
                change.mutate(
                  { current: review.current, objective: review.choice, reason },
                  {
                    onSuccess: () => {
                      setReview(null);
                      setSelected(null);
                    },
                  },
                )
              }
            >
              {change.isPending ? 'Saving…' : 'Confirm objective change'}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
