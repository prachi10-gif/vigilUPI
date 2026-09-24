# vigilUPI — UPI Fraud Detection & Real-Time Risk Scoring

A public-facing demo dashboard for UPI transaction monitoring using the project's existing Isolation Forest + XGBoost models and FastAPI scoring service. It highlights transactions for review; a model score is not proof that fraud occurred.

## Features

- Landing page explains the project, model pipeline, and fraud indicators.
- FastAPI scores a submitted transaction using the saved model artifacts.
- Risk-aware alerts, detail modal, deterministic feature explanations, and a session transaction table.
- Risk distribution and statistics are computed from transactions scored by the running API.
- Responsive dashboard with model and API health views.
- Static frontend can be built for GitHub Pages; FastAPI can be hosted separately.
- No OpenAI, Anthropic, Gemini, or other paid AI API is used.

## Architecture

```text
Browser / GitHub Pages (static HTML, CSS, JavaScript built with Vite)
                         │ VITE_API_URL / HTTPS + CORS
                         ▼
                 FastAPI + Uvicorn
                         │
                features.py (shared features)
                 ┌───────┴────────┐
                 ▼                ▼
          Isolation Forest      XGBoost
          anomaly score     supervised score
                 └───────┬────────┘
                         ▼
           Weighted ensemble → risk tier → alert
```

## Technology stack

Python, FastAPI, Uvicorn, pandas, NumPy, scikit-learn, XGBoost, joblib, HTML, CSS, JavaScript, Vite, and Chart.js.

## ML models and risk scoring

- **Isolation Forest** detects anomalous feature combinations. Its raw anomaly score is converted to a percentile using the stored held-out reference in `model_artifacts/risk_calibration.json`.
- **XGBoost** is the existing supervised classifier. Its raw `predict_proba` output is retained.
- **Ensemble:** `0.35 × Isolation Forest percentile + 0.65 × XGBoost probability`.
- The alert cutoff and risk tier cutoffs are loaded from validation calibration metadata and exposed through `/metrics`. The score is a relative risk score, not a calibrated probability.
- Missing location data is unknown and is not treated as a location mismatch. If both locations are supplied, differing cities set the mismatch feature.
- Critical risk indicates a transaction strongly matches the system's configured high-risk patterns and should be reviewed. It does not mean the transaction is definitely fraud.
- No additional accuracy numbers are claimed here; evaluation fields displayed by the dashboard are loaded from project artifacts.

The saved `.joblib` model files are preserved and are small enough for the current repository (about 3 MB under `model_artifacts/`). If they grow beyond GitHub's file limits, use Git LFS or versioned object storage and download the artifacts during backend deployment. Do not commit generated databases or transaction CSVs.

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | API and model artifact status |
| `POST` | `/score` | Validate and score one transaction |
| `GET` | `/metrics` | Model report, weights, thresholds, calibration method |
| `GET` | `/transactions` | Recent in-memory scored transactions |
| `GET` | `/alerts` | Flagged transactions |
| `GET` | `/risk-distribution` | Low/Medium/High/Critical counts and tier boundaries |
| `GET` | `/statistics` | Total, per-tier, and flagged counts from scored session data |
| `GET` | `/stats` | Dataset history and current session summaries |
| `GET` | `/docs` | Interactive OpenAPI documentation |

Example request:

```json
{
  "transaction_id": "TXN-10023",
  "amount": 8500,
  "timestamp": "2025-06-15T11:00:00",
  "sender_avg_amount_30d": 1200,
  "sender_txn_count_24h": 3,
  "is_new_receiver": true,
  "location": "Bhopal",
  "sender_home_city": "Bhopal"
}
```

`/transactions`, `/alerts`, and statistics are in memory and reset when the API restarts. This is suitable for a demo; add persistent storage before relying on long-term history.

## Local setup

From the repository root in PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `FRONTEND_URL` in `.env` to the frontend origin when using a separate frontend host. For local Vite, localhost is already allowed.

Start the API and dashboard together:

```powershell
uvicorn realtime_scorer:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. The API binds to loopback for local use. Do not use this bind address for a hosted service; Render's start command below binds to `0.0.0.0`.

For the standalone Vite frontend during development, in a second terminal:

```powershell
cd dashboard
npm install
Copy-Item .env.example .env.local
npm run dev
```

Open `http://127.0.0.1:5173`. Set `VITE_API_URL=http://127.0.0.1:8000` in `dashboard/.env.local`.

## Environment variables

| Variable | Used by | Example | Purpose |
|---|---|---|---|
| `VITE_API_URL` | Frontend build | `https://your-api.onrender.com` | FastAPI base URL embedded during the static build |
| `VITE_BASE_PATH` | Frontend build | `/your-repository/` | GitHub Pages project-site path |
| `FRONTEND_URL` | FastAPI | `https://your-user.github.io` | Allowed browser origin(s), comma-separated if needed |
| `PORT` | Hosting platform | Provided by Render | HTTP port for the hosted API |

Only public URLs belong in frontend build variables. Never put passwords, tokens, or private API keys in frontend variables or committed `.env` files.

## Build and deploy frontend to GitHub Pages

GitHub Pages serves static files and cannot run Python/FastAPI. The GitHub Actions workflow in `.github/workflows/pages.yml` builds the existing dashboard and publishes `docs/`.

1. Push this project to a GitHub repository.
2. In repository **Settings → Pages**, select **GitHub Actions** as the source.
3. In **Settings → Secrets and variables → Actions → Variables**, add `VITE_API_URL` with the deployed API base URL, for example `https://vigilupi-api.onrender.com` (no trailing slash).
4. Push to `main` or start the “Deploy dashboard to GitHub Pages” workflow manually.
5. The frontend URL will be `https://YOUR-GITHUB-USERNAME.github.io/YOUR-REPOSITORY/` for a project site.

Manual build:

```powershell
cd dashboard
npm install
$env:VITE_API_URL = "https://YOUR-BACKEND-URL"
$env:VITE_BASE_PATH = "/YOUR-REPOSITORY/"
npm run build
```

The static output is written to the repository's `docs/` directory.

## Deploy FastAPI backend to Render

The root `render.yaml` defines a Python web service and health check. Push the repository, create a Render Blueprint from it, and update `FRONTEND_URL` to the GitHub Pages **origin** (for example `https://YOUR-GITHUB-USERNAME.github.io`, with no repository path). Render installs `requirements.txt` and runs Uvicorn on the platform-provided port. The resulting URL is the value for frontend `VITE_API_URL`; if it changes, update the GitHub repository variable and rebuild Pages.

Other Python hosts can use the same root install and start commands:

```text
Build: pip install -r requirements.txt
Start: uvicorn realtime_scorer:app --host 0.0.0.0 --port $PORT
```

Hosted in-memory transaction history clears on restart or instance replacement. Add a persistent database for production use. Restrict `FRONTEND_URL` to your published frontend origin(s).

## Push to GitHub

If this directory has not been connected to a Git repository yet:

```powershell
git init
git add .
git commit -m "Prepare UPI fraud dashboard for deployment"
git branch -M main
git remote add origin https://github.com/YOUR-GITHUB-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

Replace the username and repository values with your own. Generated datasets, local database files, environment files, and virtual environments are excluded by `.gitignore`; the saved model artifacts remain included.

## Screenshots

Add screenshots here after running the dashboard, for example:

```text
![Landing page](screenshots/landing.png)
![Dashboard](screenshots/dashboard.png)
![Transaction details](screenshots/transaction-details.png)
```

## Future improvements

- Persist transaction history in a production database.
- Add user authentication and role-based review queues.
- Monitor input drift and evaluate against newer labeled transactions.
- Add accessibility review and automated browser tests to CI.
- Move the chart library from CDN to a locally bundled package if fully offline operation is required.
