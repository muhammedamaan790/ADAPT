import {
  createContext,
  useContext,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { LogOut } from 'lucide-react';
import { z } from 'zod';
import { ApiError, dataMode, request, setAuthRequiredHandler, setCsrfToken } from '../api/client';
import { Loading } from './ui';

const sessionSchema = z.object({
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
  // Only a real 401 shows the sign-in form. Anything else (offline, an older backend without /auth, a mocked
  // API) renders the app, whose pages show their own errors; the backend enforces every check regardless.
  if (me.error instanceof ApiError && me.error.status === 401)
    return (
      <LoginForm
        onSignedIn={(s) => {
          setCsrfToken(s.csrf_token);
          // drop any data cached under a previous session, but keep the session query itself attached
          queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== 'auth' });
          queryClient.setQueryData(['auth', 'me'], s);
        }}
      />
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

function LoginForm({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      onSignedIn(await request('/auth/login', sessionSchema, { user_id: userId, password }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <LoginScreen>
      <form className="panel login-panel" onSubmit={(e) => void submit(e)}>
        <h1>Sign in to ADAPT</h1>
        <p>Approvals, executions and policy changes are recorded under your name.</p>
        <label className="field">
          User name
          <input
            name="username"
            autoComplete="username"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
            required
          />
        </label>
        <label className="field">
          Password
          <input
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={error ? 'true' : undefined}
            required
          />
        </label>
        {error && (
          <p className="login-error" role="alert">
            {error}
          </p>
        )}
        <button className="button primary full" type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </LoginScreen>
  );
}

/** Same theme tokens as the app shell (`.app` / `.app.dark`), following the saved theme choice. */
function LoginScreen({ children }: { children: ReactNode }) {
  let dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  try {
    const saved = localStorage.getItem('adapt.theme');
    if (saved) dark = saved === 'dark';
  } catch {
    /* storage unavailable: follow the system theme */
  }
  return <main className={`app login-screen ${dark ? 'dark' : ''}`}>{children}</main>;
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
