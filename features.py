"""
Feature engineering — shared by training script and the real-time
scoring service, so train/serve feature logic never drifts apart.
"""
import pandas as pd
import numpy as np


FEATURE_COLUMNS = [
    "amount",
    "amount_vs_avg_ratio",
    "hour",
    "is_odd_hour",
    "sender_txn_count_24h",
    "is_new_receiver",
    "is_location_mismatch",
    "log_amount",
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["hour"] = df["timestamp"].dt.hour
    df["is_odd_hour"] = df["hour"].between(0, 4).astype(int)
    df["amount_vs_avg_ratio"] = df["amount"] / df["sender_avg_amount_30d"].replace(0, 1)
    df["is_new_receiver"] = df["is_new_receiver"].astype(int)
    if "sender_home_city" in df.columns and "location" in df.columns:
        known_locations = (
            df["location"].notna()
            & df["sender_home_city"].notna()
            & df["location"].astype(str).str.strip().ne("")
            & df["sender_home_city"].astype(str).str.strip().ne("")
        )
        df["is_location_mismatch"] = (
            known_locations & df["location"].ne(df["sender_home_city"])
        ).astype(int)
    elif "is_location_mismatch" not in df.columns:
        # Not observable at inference without a stored sender home-city lookup;
        # default to 0 (unknown / assume match) rather than fabricate a value.
        df["is_location_mismatch"] = 0
    df["log_amount"] = np.log1p(df["amount"])
    return df


def get_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    feat_df = build_features(df)
    return feat_df[FEATURE_COLUMNS]
