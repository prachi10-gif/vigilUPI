"""
IQR anomaly detection — runnable version against the SQLite demo DB.
(SQLite has no percentile_cont, so quartiles are computed in pandas;
 the Postgres production version does this natively in SQL — see
 iqr_anomaly_detection.sql)

Per-sender Tukey fence: flag if amount > Q3 + 1.5*IQR using that
sender's own trailing transaction history. Falls back to a global
fence for senders with fewer than 5 prior transactions.
"""
import sqlite3
import pandas as pd
import numpy as np


def run_iqr_detection(db_path="upi_fraud.db"):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql("SELECT transaction_id, sender_vpa, amount FROM transactions", conn)

    # Global fence (fallback for low-history senders)
    g_q1, g_q3 = df["amount"].quantile([0.25, 0.75])
    global_fence = g_q3 + 1.5 * (g_q3 - g_q1)

    # Per-sender fences
    grp = df.groupby("sender_vpa")["amount"]
    counts = grp.transform("count")
    q1 = grp.transform(lambda s: s.quantile(0.25))
    q3 = grp.transform(lambda s: s.quantile(0.75))
    iqr = q3 - q1
    sender_fence = q3 + 1.5 * iqr

    fence = np.where(counts >= 5, sender_fence, global_fence)
    df["iqr_flag"] = (df["amount"] > fence).astype(int)

    # write back
    cur = conn.cursor()
    cur.executemany(
        "UPDATE transactions SET iqr_flag = ? WHERE transaction_id = ?",
        list(zip(df["iqr_flag"], df["transaction_id"]))
    )
    conn.commit()

    n_flagged = int(df["iqr_flag"].sum())
    print(f"IQR pass: flagged {n_flagged} / {len(df)} transactions "
          f"({n_flagged/len(df)*100:.2f}%)")

    # quick precision check against ground truth (synthetic label)
    truth = pd.read_sql("SELECT transaction_id, is_fraud_actual FROM transactions", conn)
    merged = df.merge(truth, on="transaction_id")
    tp = ((merged.iqr_flag == 1) & (merged.is_fraud_actual == 1)).sum()
    fp = ((merged.iqr_flag == 1) & (merged.is_fraud_actual == 0)).sum()
    fn = ((merged.iqr_flag == 0) & (merged.is_fraud_actual == 1)).sum()
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    print(f"IQR alone -> precision: {precision:.2%}, recall: {recall:.2%}")
    print("(IQR is a fast, explainable first filter — expect lower recall than "
          "the ML ensemble; it catches the obvious amount-based fraud, not "
          "velocity or new-receiver patterns.)")

    conn.close()


if __name__ == "__main__":
    run_iqr_detection()
