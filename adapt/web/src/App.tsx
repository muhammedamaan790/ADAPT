import { lazy, Suspense, useState } from 'react';
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
  Radar,
  SlidersHorizontal,
  ListChecks,
  PlugZap,
  MessageSquare,
  TrendingUp,
  BookOpen,
  Database,
  ChartNoAxesCombined,
} from 'lucide-react';
import { CommandCenter } from './pages/CommandCenter';
import { DecisionCenter } from './pages/DecisionCenter';
import { ScenarioLab } from './pages/ScenarioLab';
import { dataMode } from './api/client';
import { Badge, Loading } from './components/ui';
import { useOverview } from './hooks/workspace';

const Anomalies = lazy(() => import('./pages/Anomalies').then((m) => ({ default: m.Anomalies })));
const Optimizer = lazy(() => import('./pages/Optimizer').then((m) => ({ default: m.Optimizer })));
const ExecutionLedger = lazy(() =>
  import('./pages/ExecutionLedger').then((m) => ({ default: m.ExecutionLedger })),
);
const Connection = lazy(() =>
  import('./pages/Connection').then((m) => ({ default: m.Connection })),
);
const Opportunities = lazy(() =>
  import('./pages/Opportunities').then((m) => ({ default: m.Opportunities })),
);
const Outcomes = lazy(() => import('./pages/Outcomes').then((m) => ({ default: m.Outcomes })));
const Learning = lazy(() => import('./pages/Learning').then((m) => ({ default: m.Learning })));
const DataHub = lazy(() => import('./pages/DataHub').then((m) => ({ default: m.DataHub })));
const Copilot = lazy(() => import('./components/Copilot').then((m) => ({ default: m.Copilot })));
const routeNames: Record<string, string> = {
  decisions: 'Decision Center',
  scenarios: 'Scenario Lab',
  anomalies: 'Anomalies',
  optimizer: 'Optimizer',
  executions: 'Execution & Ledger',
  connection: 'Backend Connection',
  opportunities: 'Opportunity Map',
  outcomes: 'Outcomes',
  learning: 'Learning',
  data: 'Data Hub',
};

export function App() {
  const [dark, setDark] = useState(() => localStorage.getItem('adapt.theme') === 'dark');
  const [collapsed, setCollapsed] = useState(false);
  const [morePages, setMorePages] = useState(false);
  const [copilot, setCopilot] = useState(false);
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
        <Link className="workspace" to="/data?section=workspaces" aria-label="Manage workspaces">
          <span className="workspace-avatar">D</span>
          <div>
            <strong>
              {overview.data?.workspace ||
                (dataMode === 'fixture' ? 'D2C workspace' : 'Workspace unavailable')}
            </strong>
            <small>Decision workspace · INR</small>
          </div>
        </Link>
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
          <NavLink to="/anomalies">
            <Radar size={19} />
            <span>Anomalies</span>
          </NavLink>
          <NavLink to="/optimizer">
            <SlidersHorizontal size={19} />
            <span>Optimizer</span>
          </NavLink>
          <NavLink to="/executions">
            <ListChecks size={19} />
            <span>Execution & Ledger</span>
          </NavLink>
          <NavLink to="/connection">
            <PlugZap size={19} />
            <span>Backend Connection</span>
          </NavLink>
          <button
            className="more-pages"
            aria-expanded={morePages}
            aria-controls="insight-navigation"
            onClick={() => setMorePages(!morePages)}
          >
            More pages
          </button>
          <div
            id="insight-navigation"
            className={`insight-navigation ${morePages ? 'expanded' : ''}`}
          >
            <NavLink to="/opportunities">
              <TrendingUp size={19} />
              <span>Opportunity Map</span>
            </NavLink>
            <NavLink to="/outcomes">
              <ChartNoAxesCombined size={19} />
              <span>Outcomes</span>
            </NavLink>
            <NavLink to="/learning">
              <BookOpen size={19} />
              <span>Learning</span>
            </NavLink>
            <NavLink to="/data">
              <Database size={19} />
              <span>Data Hub</span>
            </NavLink>
          </div>
        </nav>
        <div className="sidebar-bottom">
          <div className="mode-box">
            <ShieldCheck size={20} />
            <div>
              <strong>{dataMode === 'fixture' ? 'Human approval' : 'Decision controls'}</strong>
              <p>
                {dataMode === 'fixture' ? 'You remain in control.' : 'Review channel policy.'}
                <br />
                {dataMode === 'fixture' ? 'PROFIT objective only.' : 'Backend capabilities apply.'}
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
            <strong>{routeNames[route.split('/')[1]] || 'Command Center'}</strong>
          </div>
          <div className="topbar-actions">
            <button
              className="icon-button"
              aria-label="Open Copilot"
              onClick={() => setCopilot(true)}
            >
              <MessageSquare size={18} />
            </button>
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
          <Suspense fallback={<Loading label="Loading workspace" />}>
            <Routes>
              <Route path="/" element={<CommandCenter />} />
              <Route path="/decisions" element={<DecisionCenter />} />
              <Route path="/decisions/:id" element={<DecisionCenter />} />
              <Route path="/scenarios" element={<ScenarioLab />} />
              <Route path="/anomalies" element={<Anomalies />} />
              <Route path="/optimizer" element={<Optimizer />} />
              <Route path="/executions" element={<ExecutionLedger />} />
              <Route path="/connection" element={<Connection />} />
              <Route path="/opportunities" element={<Opportunities />} />
              <Route path="/outcomes" element={<Outcomes />} />
              <Route path="/learning" element={<Learning />} />
              <Route path="/data" element={<DataHub />} />
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
          </Suspense>
        </main>
        <footer className="app-footer">
          <span>
            <Activity size={13} /> ADAPT · Evidence before action
          </span>
          <span>
            {dataMode === 'fixture'
              ? 'Approve mode · Illustrative values · No production autonomy'
              : 'Channel policy · Backend-owned decisions · Evidence-gated execution'}
          </span>
        </footer>
      </div>
      {copilot && (
        <Suspense fallback={<Loading label="Opening Copilot" />}>
          <Copilot close={() => setCopilot(false)} />
        </Suspense>
      )}
    </div>
  );
}
