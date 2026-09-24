"""Build score-normalization metadata from the saved model and a held-out split.

This loads the existing model artifacts; it does not fit or change either model.
Run from the project root after training (or to refresh score calibration).
"""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    classification_report,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

from features import get_feature_matrix


ROOT = Path(__file__).resolve().parent
ARTIFACT_DIR = ROOT / "model_artifacts"
OUTPUT_PATH = ARTIFACT_DIR / "risk_calibration.json"
CALIBRATION_SPLIT_SEED = 2026


def main():
    data = pd.read_csv(ROOT / "upi_transactions.csv")
    features = get_feature_matrix(data)
    labels = data["is_fraud"].astype(int).to_numpy()

    # Match the original train/test split, then split its held-out portion so
    # normalization references and thresholds are derived without refitting.
    _, X_test, _, y_test = train_test_split(
        features, labels, test_size=0.2, random_state=42, stratify=labels
    )
    X_reference, X_validation, y_reference, y_validation = train_test_split(
        X_test, y_test, test_size=0.5, random_state=CALIBRATION_SPLIT_SEED,
        stratify=y_test,
    )

    isolation_forest = joblib.load(ARTIFACT_DIR / "isolation_forest.joblib")
    xgboost_model = joblib.load(ARTIFACT_DIR / "xgboost_model.joblib")
    scaler = joblib.load(ARTIFACT_DIR / "scaler.joblib")

    iso_reference_raw = -isolation_forest.score_samples(scaler.transform(X_reference))
    iso_validation_raw = -isolation_forest.score_samples(scaler.transform(X_validation))
    iso_reference = np.sort(iso_reference_raw.astype(float))

    xgb_validation = xgboost_model.predict_proba(X_validation)[:, 1]

    def iso_percentile(raw_scores):
        # Mid-rank empirical CDF maps each anomaly score onto a stable 0..1
        # validation percentile without using the current request batch.
        below = np.searchsorted(iso_reference, raw_scores, side="left")
        above = np.searchsorted(iso_reference, raw_scores, side="right")
        return (below + above + 1) / (2 * len(iso_reference) + 2)

    weights = {"isolation_forest": 0.35, "xgboost": 0.65}
    validation_iso = iso_percentile(iso_validation_raw)
    validation_ensemble = weights["isolation_forest"] * validation_iso + weights["xgboost"] * xgb_validation

    precisions, recalls, thresholds = precision_recall_curve(y_validation, validation_ensemble)
    valid = np.flatnonzero(precisions[:-1] >= 0.90)
    if not len(valid):
        raise RuntimeError("No validation threshold reaches 90% precision")
    alert_threshold = float(thresholds[valid[0]])
    # Tier cutoffs are score-distribution quantiles from the held-out
    # validation partition; they describe relative risk, not fraud probability.
    low_medium, medium_high, high_critical = np.quantile(validation_ensemble, [0.95, 0.98, 0.995])
    tier_thresholds = {
        "low_medium": float(low_medium),
        "medium_high": float(medium_high),
        "high_critical": float(high_critical),
    }

    predictions = (validation_ensemble >= alert_threshold).astype(int)
    report = classification_report(y_validation, predictions, output_dict=True, zero_division=0)
    matrix = confusion_matrix(y_validation, predictions, labels=[0, 1]).tolist()
    tier_counts = {
        "Low": int((validation_ensemble < low_medium).sum()),
        "Medium": int(((validation_ensemble >= low_medium) & (validation_ensemble < medium_high)).sum()),
        "High": int(((validation_ensemble >= medium_high) & (validation_ensemble < high_critical)).sum()),
        "Critical": int((validation_ensemble >= high_critical).sum()),
    }

    calibration = {
        "version": 1,
        "source": "saved models scored on a deterministic held-out validation partition",
        "data_file": "upi_transactions.csv",
        "reference_rows": int(len(X_reference)),
        "validation_rows": int(len(X_validation)),
        "validation_fraud_rate": float(y_validation.mean()),
        "isolation_forest_normalization": "mid-rank empirical CDF of raw anomaly scores",
        "isolation_forest_reference_scores": iso_reference.tolist(),
        "xgboost_probability_calibration": "none; raw predict_proba retained",
        "ensemble_weights": weights,
        "alert_threshold_method": "lowest validation threshold with precision >= 0.90",
        "alert_threshold_precision_target": 0.90,
        "alert_threshold": alert_threshold,
        "risk_tier_method": "held-out ensemble score quantiles at 95%, 98%, and 99.5%",
        "risk_tier_quantiles": {"low_medium": 0.95, "medium_high": 0.98, "high_critical": 0.995},
        "risk_tier_thresholds": tier_thresholds,
        "validation_metrics": {
            "ensemble_auc": float(roc_auc_score(y_validation, validation_ensemble)),
            "xgboost_avg_precision": float(average_precision_score(y_validation, xgb_validation)),
            "xgboost_brier_score": float(brier_score_loss(y_validation, xgb_validation)),
            "classification_report": report,
            "confusion_matrix": matrix,
            "tier_counts": tier_counts,
        },
    }

    ARTIFACT_DIR.mkdir(exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as output:
        json.dump(calibration, output, indent=2)
    print(f"Saved validation score calibration to {OUTPUT_PATH}")
    print(f"Validation alert threshold (>=90% precision): {alert_threshold:.6f}")
    print("Risk tier cutoffs:", {key: round(value, 6) for key, value in tier_thresholds.items()})
    print("Validation tier counts:", tier_counts)
    print("Validation confusion matrix [ [TN, FP], [FN, TP] ]:", matrix)
    print(f"Validation ensemble ROC-AUC: {calibration['validation_metrics']['ensemble_auc']:.4f}")


if __name__ == "__main__":
    main()
