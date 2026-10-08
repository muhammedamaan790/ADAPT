import { createContext, useContext, useEffect, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Navigate, useLocation } from 'react-router-dom';
import { LogOut } from 'lucide-react';
import { z } from 'zod';
import { ApiError, dataMode, request, setAuthRequiredHandler, setCsrfToken } from '../api/client';
import { Loading } from './ui';

export const sessionSchema = z.object({
  auth_enabled: z.boolean(),
  user: z.object({ user_id: z.string(), display_name: z.string(), role: z.string() }),
  csrf_token: z.string(),
});
type Session = z.infer<typeof sessionSchema>;

const SessionContext = createContext<{ session: Session | null; signOut: () => void }>({
  session: null,
  signOut: () => undefined,
});

/** API mode: sign in before the app renders (session cookie + in-memory CSRF token). Fixture mode: no gate. */
export function AuthGate({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const location = useLocation();
  const me = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: () => request('/auth/me', sessionSchema),
    enabled: dataMode === 'api',
    retry: false,
    staleTime: Infinity,
  });
  useEffect(() => {
    setAuthRequiredHandler(() => void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] }));
    return () => setAuthRequiredHandler(null);
  }, [queryClient]);
  useEffect(() => {
    if (me.data) setCsrfToken(me.data.csrf_token);
  }, [me.data]);

  if (dataMode === 'fixture') return <>{children}</>;
  // only the first check shows a loader: later refetches (any query invalidation) must never unmount the app
  if (me.isPending && !me.isFetched) return <Loading label="Checking your session" />;
  // Only a real 401 sends the visitor to the sign-in page, which returns them to the route they asked for.
  // Anything else (offline, an older backend without /auth, a mocked API) renders the app, whose pages show
  // their own errors; the backend enforces every check regardless.
  if (me.error instanceof ApiError && me.error.status === 401)
    return (
      <Navigate to="/signin" replace state={{ from: `${location.pathname}${location.search}` }} />
    );
  const signOut = async () => {
    await request('/auth/logout', z.object({ ok: z.literal(true) }), {}).catch(() => undefined);
    setCsrfToken('');
    await queryClient.resetQueries(); // the session query refetches, gets 401 and shows sign-in
  };
  return (
    <SessionContext.Provider value={{ session: me.data ?? null, signOut: () => void signOut() }}>
      {children}
    </SessionContext.Provider>
  );
}

/** The signed-in user's initials, role and a sign-out button (the topbar avatar). */
export function SessionChip() {
  const { session, signOut } = useContext(SessionContext);
  if (!session)
    return (
      <span className="avatar" title="Fixture workspace">
        GM
      </span>
    );
  const { user } = session;
  const initials = user.display_name
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
  return (
    <span className="session-chip">
      <span className="avatar" title={`${user.display_name} · ${user.role}`}>
        {initials}
      </span>
      {session.auth_enabled && (
        <button className="icon-button" onClick={signOut} aria-label="Sign out" title="Sign out">
          <LogOut size={17} />
        </button>
      )}
    </span>
  );
}
