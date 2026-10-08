# GitHub Pages

The original `main` frontend is deployed at https://muhammedamaan790.github.io/ADAPT/.

A repository administrator must first select **Settings → Pages → Build and deployment → Source → GitHub Actions**. The `Deploy frontend to GitHub Pages` workflow then publishes pushes to `main` that change the frontend. It can also be run manually from Actions.

The workflow builds with `VITE_BASE_PATH=/ADAPT/` and `VITE_DATA_MODE=fixture`. All visible example-data labels remain intact. Fixture changes are browser-local; this deployment does not run Python services, connect advertising accounts, or execute live budgets. A production API requires separate HTTPS backend hosting and authentication/CORS configuration before building in API mode.

Vite prefixes asset URLs, React Router uses the same base path, and the workflow copies `index.html` to `404.html` so bookmarked client routes render when refreshed. GitHub Pages still returns HTTP 404 for these fallback requests; the frontend loads normally. This preserves existing route hashes used by the review workflow.

Local verification (PowerShell):

```powershell
$env:VITE_BASE_PATH='/ADAPT/'
$env:VITE_DATA_MODE='fixture'
npm ci
npm test
npm run build
Copy-Item dist/index.html dist/404.html
npm run preview -- --port 4175
```

Visit http://127.0.0.1:4175/ADAPT/. Local development keeps its default `/` base unless `VITE_BASE_PATH` is set.
