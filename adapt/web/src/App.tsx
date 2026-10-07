import { useState } from 'react';
import { NavLink, Route, Routes, Link, useLocation } from 'react-router-dom';
import {
  Activity,
  ArrowUpRight,
  FlaskConical,
  LayoutDashboard,
  Moon,
  PanelLeftClose,
  ShieldCheck,
  Sun,
  Workflow,
} from 'lucide-react';
import { CommandCenter } from './pages/CommandCenter';
import { DecisionCenter } from './pages/DecisionCenter';
import { ScenarioLab } from './pages/ScenarioLab';
import { dataMode } from './api/client';
import { Badge } from './components/ui';
import { useOverview } from './hooks/workspace';

export function App() {
  const [dark, setDark] = useState(() => localStorage.getItem('adapt.theme') === 'dark');
  const [collapsed, setCollapsed] = useState(false);
  const overview = useOverview();
  const route = useLocation().pathname;
  const toggleTheme = () => {
    setDark(!dark);
    localStorage.setItem('adapt.theme', dark ? 'light' : 'dark');
  };
  return (
    <div className={`app ${dark ? 'dark' : ''} ${collapsed ? 'compact-nav' : ''}`}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link to="/" className="brand" aria-label="ADAPT Command Center">
          <span className="brand-mark">
            <svg viewBox="0 0 32 32" aria-hidden="true">
              <path d="M5 25 16 6 27 25M10 19h12" />
            </svg>
          </span>
          <span>
            ADAPT<span className="brand-sub">Advertising intelligence</span>
          </span>
        </Link>
        <div className="workspace">
          <span className="workspace-avatar">D</span>
          <div>
            <strong>D2C workspace</strong>
            <small>Stage 1 · INR</small>
          </div>
        </div>
        <nav aria-label="Primary navigation">
          <NavLink to="/" end>
            <LayoutDashboard size={19} />
            <span>Command Center</span>
          </NavLink>
          <NavLink to="/decisions">
            <Workflow size={19} />
            <span>Decision Center</span>
          </NavLink>
          <NavLink to="/scenarios">
            <FlaskConical size={19} />
            <span>Scenario Lab</span>
          </NavLink>
        </nav>
        <div className="sidebar-bottom">
          <div className="mode-box">
            <ShieldCheck size={20} />
            <div>
              <strong>Human approval</strong>
              <p>
                You remain in control.
                <br />
                PROFIT objective only.
              </p>
            </div>
          </div>
          <div className="sidebar-footer">
            <span>
              <i className="status-dot" />
              {dataMode === 'fixture'
                ? 'Fixture workspace'
                : overview.isError
                  ? 'API unavailable'
                  : 'API workspace'}
            </span>
            <button
              className="icon-button"
              aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              onClick={() => setCollapsed(!collapsed)}
            >
              <PanelLeftClose size={17} />
            </button>
          </div>
        </div>
      </aside>
      <div className="workspace-main">
        <header className="topbar">
          <div className="breadcrumb">
            Workspace <span>/</span>{' '}
            <strong>
              {route.startsWith('/decisions')
                ? 'Decision Center'
                : route.startsWith('/scenarios')
                  ? 'Scenario Lab'
                  : 'Command Center'}
            </strong>
          </div>
          <div className="topbar-actions">
            <Badge tone={dataMode === 'fixture' ? 'warning' : 'accent'}>
              {dataMode === 'fixture' ? 'FRONTEND FIXTURES' : 'API MODE'}
            </Badge>
            <button
              className="icon-button"
              onClick={toggleTheme}
              aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
            >
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <span className="avatar" title="Growth manager">
              GM
            </span>
          </div>
        </header>
        {dataMode === 'fixture' && (
          <div className="fixture-banner">
            <FlaskConical size={15} />
            <span>
              Illustrative frontend data. No engine, simulator or ad account is connected.
            </span>
            <Link to="/scenarios">
              Explore examples <ArrowUpRight size={13} />
            </Link>
          </div>
        )}
        <main id="main" tabIndex={-1}>
          <Routes>
            <Route path="/" element={<CommandCenter />} />
            <Route path="/decisions" element={<DecisionCenter />} />
            <Route path="/decisions/:id" element={<DecisionCenter />} />
            <Route path="/scenarios" element={<ScenarioLab />} />
            <Route
              path="*"
              element={
                <div className="empty">
                  <h1>Page not found</h1>
                  <Link to="/">Return to Command Center</Link>
                </div>
              }
            />
          </Routes>
        </main>
        <footer className="app-footer">
          <span>
            <Activity size={13} /> ADAPT · Evidence before action
          </span>
          <span>
            Approve mode ·{' '}
            {dataMode === 'fixture' ? 'Illustrative values' : 'Backend-owned decisions'} · No
            production autonomy
          </span>
        </footer>
      </div>
    </div>
  );
}
