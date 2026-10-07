import { lazy, Suspense, useState, useEffect } from 'react';
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
  Menu,
} from 'lucide-react';
import { dataMode } from './api/client';
import { Badge, Loading, Modal } from './components/ui';
import { useOverview } from './hooks/workspace';
import { ViewBoundary } from './components/ViewBoundary';
import { SessionChip } from './components/AuthGate';

const CommandCenter = lazy(() =>
  import('./pages/CommandCenter').then((m) => ({ default: m.CommandCenter })),
);
const DecisionCenter = lazy(() =>
  import('./pages/DecisionCenter').then((m) => ({ default: m.DecisionCenter })),
);
const ScenarioLab = lazy(() =>
  import('./pages/ScenarioLab').then((m) => ({ default: m.ScenarioLab })),
);
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

const navigation = [
  {
    label: 'Operate',
    items: [
      { to: '/', label: 'Command Center', icon: LayoutDashboard },
      { to: '/decisions', label: 'Decision Center', icon: Workflow },
      { to: '/executions', label: 'Execution & Ledger', icon: ListChecks },
    ],
  },
  {
    label: 'Analyze',
    items: [
      { to: '/anomalies', label: 'Anomalies', icon: Radar },
      { to: '/opportunities', label: 'Opportunity Map', icon: TrendingUp },
      { to: '/optimizer', label: 'Optimizer', icon: SlidersHorizontal },
      { to: '/outcomes', label: 'Outcomes', icon: ChartNoAxesCombined },
      { to: '/learning', label: 'Learning', icon: BookOpen },
    ],
  },
  {
    label: 'Workspace',
    items: [
      { to: '/data', label: 'Data Hub', icon: Database },
      { to: '/scenarios', label: 'Scenario Lab', icon: FlaskConical },
      { to: '/connection', label: 'Backend Connection', icon: PlugZap },
    ],
  },
];

function Navigation({ close }: { close?: () => void }) {
  return (
    <nav aria-label="Primary navigation" className="navigation-groups">
      {navigation.map((group) => (
        <div className="navigation-group" key={group.label}>
          <p className="navigation-label">{group.label}</p>
          {group.items.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === '/'} title={label} onClick={close}>
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

export function App() {
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem('adapt.theme');
    return saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
  });
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [copilot, setCopilot] = useState(false);
  const overview = useOverview();
  const route = useLocation().pathname;
  useEffect(() => {
    document.title = `${routeNames[route.split('/')[1]] || 'Command Center'} · ADAPT`;
  }, [route]);
  const toggleTheme = () => {
    setDark(!dark);
    localStorage.setItem('adapt.theme', dark ? 'light' : 'dark');
  };
  return (
    <div
      className={`app ${dark ? 'dark' : ''} ${collapsed ? 'compact-nav' : ''}`}
      data-page={route === '/' ? 'command' : 'detail'}
    >
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <Link to="/" className="brand" aria-label="ADAPT Command Center">
          <span className="brand-mark">
            <svg viewBox="0 0 256 256" aria-hidden="true">
              <g fill="currentColor" transform="translate(3 -6)">
                <path d="M32 80H80V178H176V224H80L32 176Z" />
                <path d="M112 32H176L224 80V144H176V78H112Z" />
              </g>
            </svg>
          </span>
          <span>
            ADAPT<span className="brand-sub">Advertising intelligence</span>
          </span>
        </Link>
        <Link className="workspace" to="/data?section=workspaces" aria-label="Manage workspaces">
          <span className="workspace-avatar">D2C</span>
          <div>
            <strong>
              {overview.data?.workspace ||
                (dataMode === 'fixture' ? 'D2C workspace' : 'Workspace unavailable')}
            </strong>
            <small>Decision workspace · INR</small>
          </div>
        </Link>
        <Navigation />
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
          <button
            className="icon-button mobile-menu-button"
            aria-label="Open navigation"
            onClick={() => setMobileNav(true)}
          >
            <Menu size={20} />
          </button>
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
              <span className="copilot-label">Copilot</span>
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
            <SessionChip />
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
          <ViewBoundary key={route}>
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
          </ViewBoundary>
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
      {mobileNav && (
        <Modal title="Navigation" close={() => setMobileNav(false)}>
          <Navigation close={() => setMobileNav(false)} />
        </Modal>
      )}
      {copilot && (
        <ViewBoundary
          fallback={
            <Modal title="Copilot unavailable" close={() => setCopilot(false)}>
              <p className="modal-description">
                The assistant could not be displayed. Close this dialog to continue reviewing the
                workspace, or reload to retrieve the latest application.
              </p>
              <div className="modal-actions">
                <button className="button secondary" onClick={() => setCopilot(false)}>
                  Close assistant
                </button>
                <button className="button primary" onClick={() => window.location.reload()}>
                  Reload workspace
                </button>
              </div>
            </Modal>
          }
        >
          <Suspense fallback={<Loading label="Opening Copilot" />}>
            <Copilot close={() => setCopilot(false)} />
          </Suspense>
        </ViewBoundary>
      )}
    </div>
  );
}
