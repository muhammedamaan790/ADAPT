import { useState } from 'react';
import { useQuery, useIsMutating } from '@tanstack/react-query';
import { management } from '../api/management';
import { dataMode } from '../api/client';
import { useAction } from '../hooks/workspace';
import { Badge, ErrorState, InlineError, Loading, Modal, SectionTitle } from './ui';
export function Workspaces() {
  const list = useQuery({ queryKey: ['workspaces'], queryFn: management.workspaces });
  const gate = useQuery({
    queryKey: ['workspace-switch-gate'],
    queryFn: management.switchAllowed,
    refetchInterval: 1500,
  });
  const mutating = useIsMutating();
  const [name, setName] = useState('');
  const [target, setTarget] = useState<string | null>(null);
  const create = useAction(management.createWorkspace);
  const activate = useAction(management.activateWorkspace);
  const busy = mutating > 0;
  const current = list.data?.items.find((w) => w.id === list.data.active_id);
  return (
    <section className="panel">
      <SectionTitle title="Workspaces">
        <Badge tone={dataMode === 'fixture' ? 'warning' : 'accent'}>
          {dataMode === 'fixture' ? 'ISOLATED LOCAL DEMOS' : 'BACKEND CONTEXT'}
        </Badge>
      </SectionTitle>
      <p className="workbench-copy">
        {dataMode === 'fixture'
          ? 'Each browser workspace has its own decisions, clock, outcomes and calibration. This creates local demo state, not an ad account or backend workspace.'
          : 'Workspace creation and activation are backend-owned. Switching reloads the app after server confirmation so cached decisions are not reused across contexts.'}
      </p>
      {list.isPending ? (
        <Loading label="Loading workspaces" />
      ) : list.error ? (
        <ErrorState error={list.error} retry={() => void list.refetch()} />
      ) : (
        <>
          <p className="notice">
            Active workspace: <strong>{current?.name}</strong> · INR · Asia/Kolkata
          </p>
          <ul className="connection-list">
            {list.data!.items.map((w) => (
              <li key={w.id}>
                <div className="ledger-heading">
                  <strong>{w.name}</strong>
                  {w.id === list.data!.active_id ? (
                    <Badge tone="accent">Active</Badge>
                  ) : (
                    <button
                      className="button secondary"
                      disabled={busy || gate.isPending || gate.isError || !gate.data}
                      onClick={() => {
                        activate.reset();
                        setTarget(w.id);
                      }}
                    >
                      Switch to {w.name}
                    </button>
                  )}
                </div>
                <p>
                  {w.currency} · {w.timezone} · <code>{w.id}</code>
                </p>
              </li>
            ))}
          </ul>
          <form
            className="workspace-create"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate(name, { onSuccess: () => setName('') });
            }}
          >
            <label className="field">
              New workspace name
              <input
                value={name}
                maxLength={60}
                disabled={busy}
                onChange={(e) => {
                  setName(e.target.value);
                  create.reset();
                }}
                placeholder="Example: Refill brand demo"
              />
            </label>
            <button
              className="button secondary"
              disabled={busy || name.trim().length < 2 || list.data!.items.length >= 20}
            >
              Create {dataMode === 'fixture' ? 'local demo' : 'workspace'}
            </button>
          </form>
          <InlineError error={create.error} />
          {create.data && (
            <p role="status" className="notice">
              Created {create.data.name}. Switch explicitly to open it; the active workspace is
              unchanged.
            </p>
          )}
        </>
      )}
      {gate.isError ? (
        <ErrorState error={gate.error} retry={() => void gate.refetch()} />
      ) : (
        gate.data === false && (
          <p className="notice">
            Resolve or verify the current execution before switching workspaces.
          </p>
        )
      )}
      {target && (
        <Modal
          title="Switch workspace"
          close={() => {
            if (!activate.isPending) setTarget(null);
          }}
        >
          <p className="modal-description">
            Open {list.data?.items.find((w) => w.id === target)?.name}? The app will reload and
            return to Command Center. Current decisions stay in their original workspace.{' '}
            {dataMode === 'fixture'
              ? 'The selected demo starts with separate illustrative state.'
              : 'The backend must validate access and unresolved execution state before activation.'}
          </p>
          <InlineError error={activate.error} />
          <div className="modal-actions">
            <button
              className="button secondary"
              disabled={activate.isPending}
              onClick={() => setTarget(null)}
            >
              Cancel
            </button>
            <button
              className="button primary"
              disabled={activate.isPending || gate.isPending || gate.isError || !gate.data}
              onClick={() =>
                activate.mutate(target, {
                  onSuccess: () => window.location.assign(import.meta.env.BASE_URL),
                })
              }
            >
              {activate.isPending ? 'Switching…' : 'Confirm workspace switch'}
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
