"""
Time-series anomaly detection for UPI fraud — catches VELOCITY /
BURST attacks that amount-based checks (IQR, XGBoost on a single
txn) can miss: a sender suddenly firing 20+ transactions in an hour
when their normal pattern is 1-3/day.

Method: STL decomposition (Seasonal-Trend-Loess) on the overall
hourly transaction-count series to learn the expected daily/weekly
seasonal pattern, then flag hours whose residual (actual - expected)
is a statistical outlier (z-score > threshold). Same technique
applies per-sender for a finer-grained velocity check.
"""
import sqlite3
import numpy as np
import pandas as pd
from statsmodels.tsa.seasonal import STL


def hourly_volume_anomalies(db_path="upi_fraud.db", z_threshold=3.0):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT txn_time FROM transactions", conn)
    df["txn_time"] = pd.to_datetime(df["txn_time"])

    hourly = df.set_index("txn_time").resample("h").size()
    hourly = hourly.asfreq("h", fill_value=0)

    # STL needs at least 2 full seasonal cycles; period=24 for daily seasonality
    stl = STL(hourly, period=24, robust=True)
    result = stl.fit()

    residual = result.resid
    resid_std = residual.std()
    z_scores = residual / resid_std

    anomalies = hourly[np.abs(z_scores) > z_threshold]
    print(f"Overall hourly volume: {len(anomalies)} anomalous hours out of {len(hourly)}")
    if len(anomalies):
        print(anomalies.head(10))

    conn.close()
    return result, z_scores


def per_sender_velocity_flags(db_path="upi_fraud.db", burst_threshold=10):
    """
    Two versions of the velocity check:

    (a) TRUE rolling-window check over actual transaction timestamps —
        this is what a real production system does, since real fraud
        rings actually fire repeated transactions close together.

    (b) Feature-based check using `sender_txn_count_24h` — this is
        included because THIS synthetic dataset encodes velocity
        fraud as a metadata field on one row (representing "this
        sender has made N txns in the last 24h") rather than
        generating N actual closely-timed rows. Real event-stream
        data won't have this shortcut, so (a) is what to keep.
    """
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(
        "SELECT transaction_id, sender_vpa, txn_time, sender_txn_count_24h FROM transactions",
        conn
    )
    df["txn_time"] = pd.to_datetime(df["txn_time"])
    df = df.sort_values("txn_time")

    # (a) True rolling-window check (production approach)
    flags = []
    for sender, grp in df.groupby("sender_vpa"):
        grp = grp.set_index("txn_time")
        counts = grp["transaction_id"].rolling("1h").count()
        burst_ids = grp.loc[counts > burst_threshold, "transaction_id"]
        flags.extend(burst_ids.tolist())

    print(f"(a) Rolling-window check: flagged {len(flags)} transactions "
          f"(sender fired >{burst_threshold} txns within a trailing 1hr window)")

    # (b) Feature-based check (matches how THIS synthetic dataset encodes velocity)
    feature_flags = df.loc[df["sender_txn_count_24h"] > burst_threshold, "transaction_id"].tolist()
    print(f"(b) Feature-based check: flagged {len(feature_flags)} transactions "
          f"(sender_txn_count_24h > {burst_threshold})")

    all_flags = list(set(flags) | set(feature_flags))
    if all_flags:
        cur = conn.cursor()
        cur.executemany(
            "UPDATE transactions SET timeseries_flag = 1 WHERE transaction_id = ?",
            [(f,) for f in all_flags]
        )
        conn.commit()

    # precision/recall vs ground truth
    truth = pd.read_sql("SELECT transaction_id, is_fraud_actual, fraud_type FROM transactions", conn)
    flagged_set = set(all_flags)
    truth["ts_flag"] = truth["transaction_id"].isin(flagged_set).astype(int)
    tp = ((truth.ts_flag == 1) & (truth.is_fraud_actual == 1)).sum()
    fp = ((truth.ts_flag == 1) & (truth.is_fraud_actual == 0)).sum()
    fn = ((truth.ts_flag == 0) & (truth.is_fraud_actual == 1)).sum()
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    print(f"Velocity check -> precision: {precision:.2%}, recall: {recall:.2%}")
    print("(By design this should catch ~all 'velocity_attack' fraud and "
          "almost nothing else — it's a narrow, high-precision specialist "
          "check, not a general-purpose detector.)")

    conn.close()


if __name__ == "__main__":
    print("=== Overall hourly volume anomaly (STL) ===")
    hourly_volume_anomalies()
    print("\n=== Per-sender velocity/burst flags ===")
    per_sender_velocity_flags()
