import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { dataMode } from '../api/client';
import { management } from '../api/management';
import { InlineError, Modal } from './ui';

export const workspacesKey = ['workspaces'] as const;
export const useWorkspaces = () =>
  useQuery({ queryKey: workspacesKey, queryFn: management.workspaces, staleTime: 30_000 });
/** API mode: a brand workspace holds uploaded data only (it starts empty); the other one is the simulated world. */
export const isBrandWorkspace = (id: string | undefined) =>
  dataMode === 'api' && !!id && id.startsWith('brand-');

/** The header's workspace pill: switch between workspaces or create a new, empty one. */
export function WorkspaceSwitcher({ fallbackName }: { fallbackName: string }) {
  const queryClient = useQueryClient();
  const list = useWorkspaces();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const active = list.data?.items.find((w) => w.id === list.data?.active_id);

  // Drop every cached view of the previous workspace so nothing from it shows (or counts as a change) in the next.
  const switched = (data: Awaited<ReturnType<typeof management.activateWorkspace>>) => {
    queryClient.removeQueries({
      predicate: (q) => q.queryKey[0] !== 'auth' && q.queryKey[0] !== workspacesKey[0],
    });
    queryClient.setQueryData(workspacesKey, data);
    setOpen(false);
  };
  const activate = useMutation({ mutationFn: management.activateWorkspace, onSuccess: switched });
  const create = useMutation({
    mutationFn: async (n: string) => {
      const ws = await management.createWorkspace(n);
      return management.activateWorkspace(ws.id);
    },
    onSuccess: (data) => {
      setName('');
      switched(data);
    },
  });

  return (
    <>
      <button
        className="workspace workspace-pill workspace-switch"
        title="Switch or create a workspace"
        onClick={() => setOpen(true)}
      >
        <strong className="workspace-pill-name">{active?.name ?? fallbackName}</strong>
        <span className="workspace-pill-meta">INR ▾</span>
      </button>
      {open && (
        <Modal title="Workspaces" close={() => setOpen(false)}>
          <p className="section-description">
            {dataMode === 'api'
              ? 'A new workspace starts empty: every number is 0 until you upload CSVs into it. The simulated world runs the decision engine.'
              : 'Each example workspace keeps its own decisions, clock and calibration in this browser.'}
          </p>
          <ul className="workspace-list">
            {list.data?.items.map((w) => (
              <li key={w.id}>
                <button
                  className={`workspace-option${w.id === list.data?.active_id ? ' is-active' : ''}`}
                  aria-current={w.id === list.data?.active_id}
                  disabled={activate.isPending || w.id === list.data?.active_id}
                  onClick={() => activate.mutate(w.id)}
                >
                  <span>{w.name}</span>
                  <small>
                    {w.id === list.data?.active_id
                      ? 'Active'
                      : isBrandWorkspace(w.id)
                        ? 'Uploaded data'
                        : dataMode === 'api'
                          ? 'Simulated world'
                          : 'Example'}
                  </small>
                </button>
              </li>
            ))}
          </ul>
          <form
            className="workspace-create"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim().length >= 2) create.mutate(name.trim());
            }}
          >
            <label htmlFor="new-workspace">New workspace</label>
            <div>
              <input
                id="new-workspace"
                value={name}
                maxLength={60}
                placeholder="e.g. Acme Apparel"
                onChange={(e) => setName(e.target.value)}
              />
              <button
                className="live-button"
                type="submit"
                disabled={create.isPending || name.trim().length < 2}
              >
                Create &amp; open
              </button>
            </div>
          </form>
          <InlineError error={(activate.error || create.error || list.error) as Error | null} />
        </Modal>
      )}
    </>
  );
}
