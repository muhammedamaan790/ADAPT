import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { dataMode, request, setCsrfToken } from '../api/client';
import { sessionSchema } from '../components/AuthGate';
import { Mark } from './parts';
import './landing.css';

// The backend's seeded accounts (backend/adapt/api/auth.py). Each is a real login; passwords are generated
// on first start and never shipped to the browser.
const roles = [
  { user: 'maria', name: 'Growth manager' },
  { user: 'viewer', name: 'Viewer' },
  { user: 'admin', name: 'Workspace admin' },
];

/** Full-page sign-in, dark. The workspace sends signed-out visitors here and gets them back afterwards. */
export function SignInPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const passwordField = useRef<HTMLInputElement>(null);
  const [userId, setUserId] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const from = (location.state as { from?: string } | null)?.from || '/';

  useEffect(() => {
    const root = document.documentElement;
    const previous = document.title;
    root.dataset.surface = 'signin';
    document.title = 'Sign in · ADAPT';
    return () => {
      delete root.dataset.surface;
      document.title = previous;
    };
  }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const session = await request('/auth/login', sessionSchema, { user_id: userId, password });
      setCsrfToken(session.csrf_token);
      // Drop anything cached under a previous session, then hand the workspace its session.
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== 'auth' });
      queryClient.setQueryData(['auth', 'me'], session);
      navigate(from, { replace: true });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <div className="lp signin-page">
      <main className="signin-main">
        <Link to="/product" className="signin-logo" aria-label="ADAPT product overview">
          <Mark size={46} />
        </Link>
        <h1>Sign in to ADAPT</h1>

        {dataMode === 'fixture' ? (
          <div className="signin-box">
            <p className="signin-note">
              This build runs on illustrative workspace data, so there is no account to sign in to.
            </p>
            <Link to="/" className="signin-submit">
              Open the workspace
            </Link>
          </div>
        ) : (
          <form className="signin-box" onSubmit={(e) => void submit(e)}>
            <label className="signin-field">
              User name
              <input
                name="username"
                autoComplete="username"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                required
                autoFocus
              />
            </label>
            <label className="signin-field">
              Password
              <input
                ref={passwordField}
                name="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-invalid={error ? 'true' : undefined}
                aria-describedby={error ? 'signin-error' : undefined}
                required
              />
            </label>
            {error && (
              <p id="signin-error" className="signin-error" role="alert">
                {error}
              </p>
            )}
            <button className="signin-submit" type="submit" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>

            <p className="signin-or">
              <span>or</span>
            </p>
            <div className="signin-roles" role="group" aria-label="Continue with a workspace role">
              {roles.map((r) => (
                <button
                  key={r.user}
                  type="button"
                  className="signin-alt"
                  aria-pressed={userId === r.user}
                  onClick={() => {
                    setUserId(r.user);
                    setError('');
                    passwordField.current?.focus();
                  }}
                >
                  Continue as {r.name}
                  <code>{r.user}</code>
                </button>
              ))}
            </div>
          </form>
        )}

        <p className="signin-foot">
          New to ADAPT? <Link to="/product">See how it decides</Link>
        </p>
        {import.meta.env.DEV && dataMode === 'api' && (
          <p className="signin-dev">
            Local development: first-start passwords are in{' '}
            <code>auth/initial_credentials.txt</code> in the backend data directory.
          </p>
        )}
      </main>
      <footer className="signin-footer">
        <Link to="/product">Product</Link>
        <span>Approvals, executions and policy changes are recorded under your account.</span>
      </footer>
    </div>
  );
}
