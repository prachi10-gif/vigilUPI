# vigilUPI dashboard frontend

The existing static dashboard can be served by the FastAPI app or built with Vite for a static host. It uses the existing backend's `/score` results; it does not calculate or fake model predictions in the browser.

See the repository [deployment and project guide](../README.md) for local setup, `VITE_API_URL`, GitHub Pages, Render, API routes, and model artifact notes.

Development with Vite:

```powershell
npm install
Copy-Item .env.example .env.local
npm run dev
```

The local FastAPI service defaults to `http://127.0.0.1:8000`. The backend also serves this frontend at its root path.

Build a GitHub Pages static site:

```powershell
$env:VITE_API_URL = "https://YOUR-BACKEND-URL"
$env:VITE_BASE_PATH = "/YOUR-REPOSITORY/"
npm run build
```

Vite writes the production files to the repository root `docs/` directory.
