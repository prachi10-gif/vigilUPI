"""
Real-time UPI fraud scoring service.

Loads the trained Isolation Forest + XGBoost ensemble and exposes a
single `score_transaction()` call that a FastAPI endpoint (or a
Kafka/stream consumer) can invoke per-transaction with <50ms latency.

Run standalone as a demo:  python realtime_scorer.py
Run as an API:             uvicorn realtime_scorer:app --reload
"""
import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from threading import Lock
from urllib.parse import urlsplit

import joblib
import numpy as np
import pandas as pd

from features import get_feature_matrix, FEATURE_COLUMNS

for _env_line in (Path(__file__).resolve().parent / ".env").read_text(encoding="utf-8").splitlines() if (Path(__file__).resolve().parent / ".env").is_file() else ():
    _env_line = _env_line.strip()
    if _env_line and not _env_line.startswith("#") and "=" in _env_line:
        _env_key, _env_value = _env_line.split("=", 1)
        os.environ.setdefault(_env_key.strip(), _env_value.strip().strip("\"'"))

ARTIFACT_DIR = Path("model_artifacts")


class FraudScorer:
    def __init__(self, artifact_dir: Path = ARTIFACT_DIR):
        self.iso_forest = joblib.load(artifact_dir / "isolation_forest.joblib")
        self.xgb_model = joblib.load(artifact_dir / "xgboost_model.joblib")
        self.scaler = joblib.load(artifact_dir / "scaler.joblib")
        with open(artifact_dir / "metrics.json") as f:
            self.metrics = json.load(f)
        calibration_path = artifact_dir / "risk_calibration.json"
        self.calibration = None
        self._iso_reference = None
        if calibration_path.is_file():
            with calibration_path.open(encoding="utf-8") as f:
                self.calibration = json.load(f)
            self._iso_reference = np.asarray(
                self.calibration["isolation_forest_reference_scores"], dtype=float
            )
        self.threshold = (self.calibration or {}).get("alert_threshold", self.metrics["alert_threshold"])
        weights = (self.calibration or {}).get("ensemble_weights", self.metrics.get("ensemble_weights", {}))
        self.w_iso = float(weights.get("isolation_forest", 0.35))
        self.w_xgb = float(weights.get("xgboost", 0.65))
        self.tier_thresholds = (self.calibration or {}).get("risk_tier_thresholds")
        if not self.tier_thresholds:
            self.tier_thresholds = {
                "low_medium": self.threshold * 0.5,
                "medium_high": self.threshold,
                "high_critical": 0.75,
            }

    def _risk_tier(self, score: float) -> str:
        if self.tier_thresholds:
            if score >= self.tier_thresholds["high_critical"]:
                return "Critical"
            if score >= self.tier_thresholds["medium_high"]:
                return "High"
            if score >= self.tier_thresholds["low_medium"]:
                return "Medium"
            return "Low"
        if score >= 0.75:
            return "Critical"
        if score >= self.threshold:
            return "High"
        if score >= self.threshold * 0.5:
            return "Medium"
        return "Low"

    def score_transaction(self, txn: dict) -> dict:
        """
        txn: dict with keys matching a row of the synthetic dataset:
          amount, timestamp, sender_avg_amount_30d, sender_txn_count_24h,
          is_new_receiver, location (optional), sender_home_city (optional)
        Returns a structured alert payload.
        """
        df = pd.DataFrame([txn])
        X = get_feature_matrix(df)
        X_scaled = self.scaler.transform(X)

        # Isolation forest: higher raw anomaly = more anomalous.
        iso_raw = -self.iso_forest.score_samples(X_scaled)[0]
        if self._iso_reference is not None and len(self._iso_reference):
            below = np.searchsorted(self._iso_reference, iso_raw, side="left")
            above = np.searchsorted(self._iso_reference, iso_raw, side="right")
            iso_score = (below + above + 1) / (2 * len(self._iso_reference) + 2)
        else:
            # Compatibility for deployments without the validation calibration file.
            iso_score = 1 / (1 + np.exp(-(iso_raw - 0.5) * 4))

        xgb_score = float(self.xgb_model.predict_proba(X)[:, 1][0])

        ensemble_score = self.w_iso * iso_score + self.w_xgb * xgb_score
        flagged = ensemble_score >= self.threshold
        hour = int(X.iloc[0]["hour"])

        return {
            "transaction_id": txn.get("transaction_id", "unknown"),
            "timestamp_scored": datetime.now(timezone.utc).isoformat(),
            "isolation_forest_score": round(float(iso_score), 4),
            "xgboost_score": round(float(xgb_score), 4),
            "ensemble_score": round(float(ensemble_score), 4),
            "flagged": bool(flagged),
            "risk_tier": self._risk_tier(ensemble_score),
            "threshold_used": round(self.threshold, 4),
            "risk_tier_thresholds": self.tier_thresholds,
            "feature_summary": {
                "amount_vs_avg_ratio": round(float(X.iloc[0]["amount_vs_avg_ratio"]), 4),
                "sender_txn_count_24h": int(X.iloc[0]["sender_txn_count_24h"]),
                "is_new_receiver": bool(X.iloc[0]["is_new_receiver"]),
                "hour": hour,
                "is_odd_hour": bool(X.iloc[0]["is_odd_hour"]),
                "is_location_mismatch": bool(X.iloc[0]["is_location_mismatch"]),
            },
        }


# ---------------- Demo when run standalone ----------------
if __name__ == "__main__":
    scorer = FraudScorer()

    demo_txns = [
        {
            "transaction_id": "demo-legit-1",
            "amount": 450,
            "timestamp": "2025-06-15T14:30:00",
            "sender_avg_amount_30d": 500,
            "sender_txn_count_24h": 2,
            "is_new_receiver": False,
        },
        {
            "transaction_id": "demo-fraud-odd-hour",
            "amount": 18000,
            "timestamp": "2025-06-15T02:47:00",
            "sender_avg_amount_30d": 500,
            "sender_txn_count_24h": 1,
            "is_new_receiver": True,
        },
        {
            "transaction_id": "demo-fraud-velocity",
            "amount": 1200,
            "timestamp": "2025-06-15T11:00:00",
            "sender_avg_amount_30d": 500,
            "sender_txn_count_24h": 27,
            "is_new_receiver": True,
        },
    ]

    for txn in demo_txns:
        result = scorer.score_transaction(txn)
        print(f"\n{txn['transaction_id']}:")
        print(json.dumps(result, indent=2))


# ---------------- Local FastAPI API + dashboard ----------------
try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel, Field

    ROOT = Path(__file__).resolve().parent
    STATIC_DIR = ROOT / "dashboard" / "static"
    METRICS_PATH = ROOT / "model_artifacts" / "metrics.json"
    DATABASE_PATH = ROOT / "upi_fraud.db"
    MODEL_FILES = [
        ROOT / "model_artifacts" / "isolation_forest.joblib",
        ROOT / "model_artifacts" / "xgboost_model.joblib",
        ROOT / "model_artifacts" / "scaler.joblib",
        METRICS_PATH,
        ROOT / "model_artifacts" / "risk_calibration.json",
    ]

    app = FastAPI(title="vigilUPI | Fraud Detection & Real-Time Risk Monitoring")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000", "http://localhost:5173",
            "http://127.0.0.1:3000", "http://127.0.0.1:5173",
            *[
                f"{urlsplit(origin.strip()).scheme}://{urlsplit(origin.strip()).netloc}"
                for origin in os.getenv("FRONTEND_URL", "").split(",")
                if origin.strip() and urlsplit(origin.strip()).scheme and urlsplit(origin.strip()).netloc
            ],
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")

    _scorer = None
    _scorer_error = None
    _transactions: list[dict] = []
    _transactions_lock = Lock()

    class TransactionIn(BaseModel):
        transaction_id: str = Field(min_length=1, max_length=100)
        amount: float = Field(gt=0, le=10_000_000)
        timestamp: str
        sender_avg_amount_30d: float = Field(ge=0)
        sender_txn_count_24h: int = Field(ge=0)
        is_new_receiver: bool
        location: str | None = None
        sender_home_city: str | None = None
        sender_vpa: str | None = None
        receiver_vpa: str | None = None

    @app.on_event("startup")
    def load_model():
        global _scorer, _scorer_error
        try:
            _scorer = FraudScorer(METRICS_PATH.parent)
            _scorer_error = None
        except Exception as exc:
            _scorer = None
            _scorer_error = str(exc)

    @app.get("/", include_in_schema=False)
    def dashboard():
        from fastapi.responses import HTMLResponse
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        page = page.replace("./dashboard.css", "/static/dashboard.css").replace("./app.js", "/static/app.js")
        return HTMLResponse(page)

    @app.get("/dashboard.css", include_in_schema=False)
    def dashboard_css():
        return FileResponse(STATIC_DIR / "dashboard.css")

    @app.get("/app.js", include_in_schema=False)
    def dashboard_js():
        return FileResponse(STATIC_DIR / "app.js", media_type="text/javascript")

    @app.post("/score")
    def score(txn: TransactionIn):
        if _scorer is None:
            raise HTTPException(status_code=503, detail="Model artifacts are unavailable")
        values = txn.model_dump()
        try:
            parsed_timestamp = datetime.fromisoformat(values["timestamp"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="timestamp must be a valid ISO 8601 datetime")
        values["timestamp"] = parsed_timestamp.isoformat()
        try:
            result = _scorer.score_transaction(values)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Invalid transaction features: {exc}")

        record = {
            **values,
            **result,
            "fraud_status": "Flagged" if result["flagged"] else "Clear",
        }
        with _transactions_lock:
            _transactions.append(record)
            if len(_transactions) > 1000:
                del _transactions[:-1000]
        return record

    @app.get("/health")
    def health():
        artifacts = {path.name: path.is_file() for path in MODEL_FILES}
        loaded = _scorer is not None
        return {
            "status": "ok" if loaded else "degraded",
            "api_status": "online",
            "model_status": "loaded" if loaded else "unavailable",
            "model_artifacts_available": all(artifacts.values()),
            "model_artifacts": artifacts,
            "model_error": _scorer_error,
        }

    @app.get("/metrics")
    def metrics():
        if not METRICS_PATH.is_file():
            raise HTTPException(status_code=503, detail="model_artifacts/metrics.json is missing")
        try:
            with METRICS_PATH.open(encoding="utf-8") as metrics_file:
                result = json.load(metrics_file)
            if _scorer is not None and _scorer.calibration:
                calibration = _scorer.calibration
                validation = calibration.get("validation_metrics", {})
                result["alert_threshold"] = calibration["alert_threshold"]
                result["risk_tier_thresholds"] = calibration["risk_tier_thresholds"]
                result["ensemble_weights"] = calibration["ensemble_weights"]
                result["ensemble_auc"] = validation.get("ensemble_auc", result.get("ensemble_auc"))
                result["xgboost_avg_precision"] = validation.get("xgboost_avg_precision", result.get("xgboost_avg_precision"))
                result["classification_report"] = validation.get("classification_report", result.get("classification_report"))
                result["confusion_matrix"] = validation.get("confusion_matrix", result.get("confusion_matrix"))
                result["score_calibration"] = {
                    "source": calibration["source"],
                    "isolation_forest": calibration["isolation_forest_normalization"],
                    "xgboost_probability": calibration["xgboost_probability_calibration"],
                    "risk_tier_method": calibration["risk_tier_method"],
                    "alert_threshold_method": calibration["alert_threshold_method"],
                }
            if _scorer is not None:
                result["alert_threshold"] = _scorer.threshold
                result["risk_tier_thresholds"] = _scorer.tier_thresholds
                result["ensemble_weights"] = {
                    "isolation_forest": _scorer.w_iso,
                    "xgboost": _scorer.w_xgb,
                }
            return result
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail=f"Could not read model metrics: {exc}")

    def transaction_snapshot():
        with _transactions_lock:
            return list(reversed(_transactions))

    @app.get("/transactions")
    def transactions(
        limit: int = Query(default=200, ge=1, le=1000),
        search: str = "",
        risk_tier: str = "all",
        status: str = "all",
    ):
        items = transaction_snapshot()
        if risk_tier.lower() != "all":
            items = [item for item in items if item["risk_tier"].lower() == risk_tier.lower()]
        if status.lower() == "flagged":
            items = [item for item in items if item["flagged"]]
        elif status.lower() == "clear":
            items = [item for item in items if not item["flagged"]]
        if search:
            term = search.casefold()
            items = [item for item in items if term in item["transaction_id"].casefold() or term in (item.get("location") or "").casefold()]
        return {"items": items[:limit], "count": len(items), "storage": "in-memory session history"}

    @app.get("/alerts")
    def alerts(
        limit: int = Query(default=100, ge=1, le=1000),
        risk_tier: str = "all",
    ):
        items = [item for item in transaction_snapshot() if item["flagged"]]
        if risk_tier.lower() != "all":
            items = [item for item in items if item["risk_tier"].lower() == risk_tier.lower()]
        return {"items": items[:limit], "count": len(items)}

    @app.get("/risk-distribution")
    def risk_distribution():
        items = transaction_snapshot()
        counts = {
            tier.lower(): sum(item["risk_tier"].lower() == tier.lower() for item in items)
            for tier in ("Low", "Medium", "High", "Critical")
        }
        result = {**counts, "total": len(items), "flagged": sum(bool(item["flagged"]) for item in items)}
        if _scorer is not None and _scorer.tier_thresholds:
            result["thresholds"] = _scorer.tier_thresholds
        return result

    @app.get("/statistics")
    def statistics():
        distribution = risk_distribution()
        return {
            "total_transactions": distribution["total"],
            "low": distribution["low"],
            "medium": distribution["medium"],
            "high": distribution["high"],
            "critical": distribution["critical"],
            "flagged": distribution["flagged"],
        }

    @app.get("/stats")
    def stats():
        history = {"available": False, "source": None, "total_transactions": 0, "fraudulent_transactions": 0, "legitimate_transactions": 0, "fraud_rate": None, "fraud_trend": []}
        if DATABASE_PATH.is_file():
            try:
                uri = f"{DATABASE_PATH.as_uri()}?mode=ro"
                with sqlite3.connect(uri, uri=True, timeout=3) as connection:
                    totals = connection.execute(
                        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN is_fraud_actual=1 THEN 1 ELSE 0 END),0) "
                        "FROM transactions WHERE is_fraud_actual IS NOT NULL"
                    ).fetchone()
                    total, fraud = int(totals[0]), int(totals[1])
                    trend_rows = connection.execute(
                        "SELECT substr(txn_time,1,7) AS month, COUNT(*), "
                        "SUM(CASE WHEN is_fraud_actual=1 THEN 1 ELSE 0 END) "
                        "FROM transactions WHERE is_fraud_actual IS NOT NULL "
                        "GROUP BY month ORDER BY month"
                    ).fetchall()
                    history = {
                        "available": True,
                        "source": "upi_fraud.db ground-truth labels",
                        "total_transactions": total,
                        "fraudulent_transactions": fraud,
                        "legitimate_transactions": total - fraud,
                        "fraud_rate": fraud / total if total else 0,
                        "fraud_trend": [{"month": row[0], "total": row[1], "fraud": row[2], "legitimate": row[1] - row[2]} for row in trend_rows],
                    }
            except (sqlite3.Error, OSError):
                pass

        session = transaction_snapshot()
        tiers = {tier: sum(item["risk_tier"] == tier for item in session) for tier in ("Low", "Medium", "High", "Critical")}
        return {
            "history": history,
            "session": {
                "scored_transactions": len(session),
                "flagged_transactions": sum(item["flagged"] for item in session),
                "high_critical_transactions": sum(item["risk_tier"] in ("High", "Critical") for item in session),
                "risk_distribution": tiers,
                "amount_risk": [{"transaction_id": item["transaction_id"], "amount": item["amount"], "risk_score": item["ensemble_score"]} for item in session[:100]],
                "model_scores": [{"transaction_id": item["transaction_id"], "isolation_forest_score": item["isolation_forest_score"], "xgboost_score": item["xgboost_score"], "ensemble_score": item["ensemble_score"]} for item in session[:20]],
            },
        }

except ImportError:
    pass  # FastAPI not required for the standalone demo
