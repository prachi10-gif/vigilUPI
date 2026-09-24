"""
Train the UPI fraud detection ensemble:
  1. Isolation Forest (unsupervised) - catches novel/unseen anomaly patterns
  2. XGBoost (supervised)            - catches known fraud patterns with high precision
  3. Ensemble score = weighted blend of both, calibrated to a 0-100 risk score

Outputs (in ./model_artifacts/):
  - isolation_forest.joblib
  - xgboost_model.joblib
  - scaler.joblib
  - metrics.json
"""
import json
import numpy as np
import pandas as pd
import joblib
from pathlib import Path

from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    roc_auc_score, precision_recall_curve, average_precision_score,
    classification_report, confusion_matrix
)
import xgboost as xgb

from features import get_feature_matrix, FEATURE_COLUMNS

ARTIFACT_DIR = Path("model_artifacts")
ARTIFACT_DIR.mkdir(exist_ok=True)


def main(data_path="upi_transactions.csv"):
    print("Loading data...")
    df = pd.read_csv(data_path)
    y = df["is_fraud"].astype(int).values
    X = get_feature_matrix(df)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # ---------- Isolation Forest (unsupervised) ----------
    print("Training Isolation Forest...")
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)

    iso_forest = IsolationForest(
        n_estimators=200,
        contamination=0.02,       # matches our synthetic fraud rate
        max_samples="auto",
        random_state=42,
        n_jobs=-1,
    )
    iso_forest.fit(X_train_scaled[y_train == 0])  # train on "normal" txns only

    # score_samples: higher = more normal. Flip & normalize to 0-1 (higher = more anomalous)
    iso_raw = -iso_forest.score_samples(X_test_scaled)
    iso_score = (iso_raw - iso_raw.min()) / (iso_raw.max() - iso_raw.min())

    iso_auc = roc_auc_score(y_test, iso_score)
    print(f"Isolation Forest ROC-AUC: {iso_auc:.4f}")

    # ---------- XGBoost (supervised) ----------
    print("Training XGBoost...")
    scale_pos_weight = (y_train == 0).sum() / max((y_train == 1).sum(), 1)
    xgb_model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=scale_pos_weight,
        eval_metric="aucpr",
        random_state=42,
        n_jobs=-1,
    )
    xgb_model.fit(X_train, y_train)
    xgb_score = xgb_model.predict_proba(X_test)[:, 1]
    xgb_auc = roc_auc_score(y_test, xgb_score)
    xgb_ap = average_precision_score(y_test, xgb_score)
    print(f"XGBoost ROC-AUC: {xgb_auc:.4f} | Avg Precision: {xgb_ap:.4f}")

    # ---------- Ensemble ----------
    ensemble_score = 0.35 * iso_score + 0.65 * xgb_score
    ens_auc = roc_auc_score(y_test, ensemble_score)
    print(f"Ensemble ROC-AUC: {ens_auc:.4f}")

    # Pick an alert threshold that hits ~90% precision on the ensemble score
    precisions, recalls, thresholds = precision_recall_curve(y_test, ensemble_score)
    valid = np.where(precisions[:-1] >= 0.90)[0]
    chosen_threshold = thresholds[valid[0]] if len(valid) else 0.5
    y_pred = (ensemble_score >= chosen_threshold).astype(int)

    report = classification_report(y_test, y_pred, output_dict=True)
    cm = confusion_matrix(y_test, y_pred).tolist()
    print(f"\nChosen alert threshold: {chosen_threshold:.4f}")
    print(classification_report(y_test, y_pred))
    print("Confusion matrix [ [TN FP] [FN TP] ]:", cm)

    # ---------- Feature importance ----------
    importances = dict(zip(FEATURE_COLUMNS, xgb_model.feature_importances_.tolist()))
    importances = dict(sorted(importances.items(), key=lambda kv: -kv[1]))

    # ---------- Save artifacts ----------
    joblib.dump(iso_forest, ARTIFACT_DIR / "isolation_forest.joblib")
    joblib.dump(xgb_model, ARTIFACT_DIR / "xgboost_model.joblib")
    joblib.dump(scaler, ARTIFACT_DIR / "scaler.joblib")

    metrics = {
        "isolation_forest_auc": iso_auc,
        "xgboost_auc": xgb_auc,
        "xgboost_avg_precision": xgb_ap,
        "ensemble_auc": ens_auc,
        "alert_threshold": float(chosen_threshold),
        "ensemble_weights": {"isolation_forest": 0.35, "xgboost": 0.65},
        "classification_report": report,
        "confusion_matrix": cm,
        "feature_importance": importances,
        "feature_columns": FEATURE_COLUMNS,
    }
    with open(ARTIFACT_DIR / "metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"\nArtifacts saved to {ARTIFACT_DIR}/")
    return metrics


if __name__ == "__main__":
    main()
