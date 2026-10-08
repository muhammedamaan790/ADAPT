import { lazy, Suspense, useState, useEffect, useRef, type CSSProperties } from 'react';
import { NavLink, Route, Routes, Link, useLocation } from 'react-router-dom';
import { PlatformBanner } from './components/PlatformBanner';
import {
  FlaskConical,
  LayoutDashboard,
  Workflow,
  Radar,
  ListChecks,
  BookOpen,
  Database,
  ChartNoAxesCombined,
  Menu,
} from 'lucide-react';
import { dataMode } from './api/client';
import { Loading, Modal } from './components/ui';
import { useOverview } from './hooks/workspace';
import { ViewBoundary } from './components/ViewBoundary';
import { SessionChip } from './components/AuthGate';
import { useReveal } from './hooks/reveal';

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
const ExecutionLedger = lazy(() =>
  import('./pages/ExecutionLedger').then((m) => ({ default: m.ExecutionLedger })),
);
const Outcomes = lazy(() => import('./pages/Outcomes').then((m) => ({ default: m.Outcomes })));
const Learning = lazy(() => import('./pages/Learning').then((m) => ({ default: m.Learning })));
const DataHub = lazy(() => import('./pages/DataHub').then((m) => ({ default: m.DataHub })));
const routeNames: Record<string, string> = {
  decisions: 'Decision Center',
  scenarios: 'Scenario Lab',
  anomalies: 'Anomalies',
  executions: 'Execution & Ledger',
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
      { to: '/outcomes', label: 'Outcomes', icon: ChartNoAxesCombined },
      { to: '/learning', label: 'Learning', icon: BookOpen },
    ],
  },
  {
    label: 'Workspace',
    items: [
      { to: '/data', label: 'Data Hub', icon: Database },
      { to: '/scenarios', label: 'Scenario Lab', icon: FlaskConical },
    ],
  },
];

// Footer destinations deep-link into views; their names stay distinct from the primary routes.
const footerLinks = [
  {
    label: 'Review',
    links: [
      { to: '/decisions', label: 'Pending approvals' },
      { to: '/executions', label: 'Verified changes' },
      { to: '/scenarios', label: 'Controlled scenarios' },
    ],
  },
  {
    label: 'Evidence',
    links: [
      { to: '/anomalies', label: 'Signals & incidents' },
      { to: '/outcomes', label: 'Measured results' },
      { to: '/learning', label: 'Calibration history' },
    ],
  },
  {
    label: 'Controls',
    links: [
      { to: '/executions?section=policy', label: 'Channel policy' },
      { to: '/data', label: 'Source health' },
      { to: '/scenarios', label: 'World controls' },
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
            <NavLink key={to} to={to} end={to === '/'} onClick={close}>
              <Icon size={17} aria-hidden="true" />
              <span>{label}</span>
              <span className="navigation-arrow" aria-hidden="true">
                →
              </span>
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

// The approved Forward Shift symbol; its two forms ease apart on hover.
function BrandMark() {
  return (
    <svg className="wordmark-mark" viewBox="0 0 256 256" aria-hidden="true">
      <g fill="currentColor" transform="translate(3 -6)">
        <path className="mark-lower" d="M32 80H80V178H176V224H80L32 176Z" />
        <path className="mark-upper" d="M112 32H176L224 80V144H176V78H112Z" />
      </g>
    </svg>
  );
}

function RouteStrip() {
  return (
    <nav aria-label="Primary navigation" className="route-strip">
      {navigation.map((group) => (
        <div className="route-group" role="group" aria-label={group.label} key={group.label}>
          {group.items.map(({ to, label }) => (
            <NavLink key={to} to={to} end={to === '/'}>
              {label}
            </NavLink>
          ))}
        </div>
      ))}
    </nav>
  );
}

function readTheme() {
  try {
    const saved = localStorage.getItem('adapt.theme');
    if (saved) return saved === 'dark';
  } catch {
    // Storage can be unavailable in private windows; fall back to the system preference.
  }
  return window.matchMedia('(prefers-color-scheme: dark)').matches;
}

export function App() {
  const [dark, setDark] = useState(readTheme);
  const [mobileNav, setMobileNav] = useState(false);
  const overview = useOverview();
  const route = useLocation().pathname;
  const section = route.split('/')[1];
  const main = useRef<HTMLElement>(null);
  useReveal(main, route);
  useEffect(() => {
    document.title = `${routeNames[section] || 'Command Center'} · ADAPT`;
  }, [section]);
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
  }, [dark]);
  const toggleTheme = () => {
    setDark(!dark);
    try {
      localStorage.setItem('adapt.theme', dark ? 'light' : 'dark');
    } catch {
      // The toggle still applies for this visit.
    }
  };
  const group = navigation.find((g) =>
    g.items.some((item) => (item.to === '/' ? route === '/' : route.startsWith(item.to))),
  );
  const workspace =
    overview.data?.workspace ||
    (dataMode === 'fixture' ? 'D2C workspace' : 'Workspace unavailable');
  return (
    <div className={`app ${dark ? 'dark' : ''}`} data-page={route === '/' ? 'command' : 'detail'}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="site-header">
        <div className="shell-wide site-header-row">
          <div className="site-header-start">
            <button
              className="nav-round mobile-menu-button"
              aria-label="Open navigation"
              onClick={() => setMobileNav(true)}
            >
              <Menu size={18} aria-hidden="true" />
            </button>
            <Link to="/" className="wordmark" aria-label="ADAPT Command Center">
              <BrandMark />
              <span className="wordmark-text">ADAPT</span>
            </Link>
            <span className="workspace workspace-pill" title="Active workspace">
              <strong className="workspace-pill-name">{workspace}</strong>
              <span className="workspace-pill-meta">INR</span>
            </span>
          </div>
          <div className="site-header-actions">
            <span className={`mode-pill ${dataMode === 'fixture' ? 'mode-fixture' : 'mode-api'}`}>
              {dataMode === 'fixture' ? 'FRONTEND FIXTURES' : 'API MODE'}
            </span>
            <button
              className="nav-round theme-toggle"
              onClick={toggleTheme}
              aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}
            >
              <span aria-hidden="true">{dark ? '☀' : '☾'}</span>
            </button>
            <SessionChip />
          </div>
        </div>
        <div className="shell-wide route-row">
          <RouteStrip />
        </div>
      </header>
      {dataMode === 'fixture' && (
        <div className="shell-wide fixture-banner">
          <span className="fixture-dot" aria-hidden="true" />
          <span>Illustrative frontend data. No engine, simulator or ad account is connected.</span>
          <Link to="/scenarios">
            Explore examples <span aria-hidden="true">→</span>
          </Link>
        </div>
      )}
      <main
        id="main"
        tabIndex={-1}
        ref={main}
        className="shell-wide"
        style={{ '--eyebrow': `"${group?.label || 'Workspace'}"` } as CSSProperties}
      >
        <PlatformBanner />
        <ViewBoundary key={route}>
          <Suspense fallback={<Loading label="Loading workspace" />}>
            <Routes>
              <Route path="/" element={<CommandCenter />} />
              <Route path="/decisions" element={<DecisionCenter />} />
              <Route path="/decisions/:id" element={<DecisionCenter />} />
              <Route path="/scenarios" element={<ScenarioLab />} />
              <Route path="/anomalies" element={<Anomalies />} />
              <Route path="/executions" element={<ExecutionLedger />} />
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
      <footer className="site-footer">
        <div className="shell-wide site-footer-grid">
          <div className="site-footer-brand">
            <div className="wordmark">
              <BrandMark />
              <span className="wordmark-text">ADAPT</span>
            </div>
            <p>
              Evidence before action. Every proposal carries its forecast, its policy checks and the
              data behind it, and nothing moves budget without{' '}
              {dataMode === 'fixture' ? 'your approval' : 'the channel policy'}.
            </p>
          </div>
          {footerLinks.map((column) => (
            <div key={column.label}>
              <h2 className="site-footer-label">{column.label}</h2>
              <ul>
                {column.links.map(({ to, label }) => (
                  <li key={label}>
                    <Link to={to}>{label}</Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="shell-wide">
          <div className="site-footer-base">
            <span>
              <i className="status-dot" />
              {dataMode === 'fixture'
                ? 'Fixture workspace'
                : overview.isError
                  ? 'API unavailable'
                  : 'API workspace'}
            </span>
            <span>
              {dataMode === 'fixture'
                ? 'Approve mode · Illustrative values · No production autonomy'
                : 'Channel policy · Backend-owned decisions · Evidence-gated execution'}
            </span>
          </div>
        </div>
      </footer>
      {mobileNav && (
        <Modal title="Navigation" close={() => setMobileNav(false)}>
          <Navigation close={() => setMobileNav(false)} />
        </Modal>
      )}
    </div>
  );
}
