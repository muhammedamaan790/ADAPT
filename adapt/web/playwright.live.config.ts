// Live Stage 1 journey (D5 against the REAL backend). Start the world service (:8100), adapt-api (:8000, workspace
// bootstrapped and reset to day 0) and `VITE_DATA_MODE=api npm run dev`, then: npx playwright test -c playwright.live.config.ts
// Uses Firefox (installed with Playwright here); the journey takes ~9 minutes because each simulated day runs the pipeline.
import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/live-api',
  workers: 1,
  timeout: 600000,
  use: {
    baseURL: 'http://127.0.0.1:5173',
    browserName: 'firefox',
    viewport: { width: 1440, height: 1000 },
    screenshot: 'on',
    trace: 'retain-on-failure',
  },
  outputDir: process.env.PW_OUT || 'test-results/live',
});
