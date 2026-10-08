import React, { lazy, Suspense } from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@fontsource-variable/instrument-sans/index.css';
import '@fontsource-variable/fraunces/opsz.css';
import '@fontsource-variable/jetbrains-mono/index.css';
import { App } from './App';
import { AuthGate } from './components/AuthGate';
import './styles.css';
import './components/evidence-workbench.css';
import './components/command-workspace.css';
import './design.css';

// The public product page sits outside the workspace shell and its session gate.
const Landing = lazy(() => import('./landing/Landing').then((m) => ({ default: m.Landing })));
const SignInPage = lazy(() => import('./landing/SignIn').then((m) => ({ default: m.SignInPage })));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 1000 }, mutations: { retry: false } },
});
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <Routes>
          <Route
            path="/product"
            element={
              <Suspense fallback={null}>
                <Landing />
              </Suspense>
            }
          />
          <Route
            path="/signin"
            element={
              <Suspense fallback={null}>
                <SignInPage />
              </Suspense>
            }
          />
          <Route
            path="*"
            element={
              <AuthGate>
                <App />
              </AuthGate>
            }
          />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
