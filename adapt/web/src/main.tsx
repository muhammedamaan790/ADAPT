import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
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

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, staleTime: 1000 }, mutations: { retry: false } },
});
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthGate>
          <App />
        </AuthGate>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
