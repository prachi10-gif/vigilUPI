"""
Loads upi_transactions.csv into a local SQLite database so the whole
pipeline (IQR view, time-series job, Power BI/Tableau connection) can
be demoed without standing up Postgres.

For production, point DATABASE_URL at your Postgres instance and run
schema.sql there instead — the table layout is identical.

Usage: python load_data.py --csv upi_transactions.csv --db upi_fraud.db
"""
import argparse
import sqlite3
import pandas as pd


DDL = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    sender_vpa TEXT,
    receiver_vpa TEXT,
    amount REAL,
    txn_time TEXT,
    device_id TEXT,
    location TEXT,
    sender_home_city TEXT,
    sender_avg_amount_30d REAL,
    sender_txn_count_24h INTEGER,
    is_new_receiver INTEGER,
    isolation_forest_score REAL,
    xgboost_score REAL,
    ensemble_score REAL,
    iqr_flag INTEGER DEFAULT 0,
    timeseries_flag INTEGER DEFAULT 0,
    model_flag INTEGER DEFAULT 0,
    risk_tier TEXT,
    is_fraud_actual INTEGER,
    fraud_type TEXT,
    analyst_action TEXT,
    analyst_reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_txn_time ON transactions(txn_time);
CREATE INDEX IF NOT EXISTS idx_txn_sender ON transactions(sender_vpa);
CREATE INDEX IF NOT EXISTS idx_txn_risk_tier ON transactions(risk_tier);
"""


def load(csv_path="upi_transactions.csv", db_path="upi_fraud.db"):
    df = pd.read_csv(csv_path)
    df = df.rename(columns={"timestamp": "txn_time", "is_fraud": "is_fraud_actual"})

    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)

    cols = ["transaction_id", "sender_vpa", "receiver_vpa", "amount", "txn_time",
            "device_id", "location", "sender_home_city", "sender_avg_amount_30d",
            "sender_txn_count_24h", "is_new_receiver", "is_fraud_actual", "fraud_type"]

    # Insert into the DDL-defined table (not via to_sql's own schema, which
    # would drop the extra scoring/flag columns the DDL defines).
    conn.execute("DELETE FROM transactions")
    placeholders = ", ".join(["?"] * len(cols))
    conn.executemany(
        f"INSERT INTO transactions ({', '.join(cols)}) VALUES ({placeholders})",
        df[cols].itertuples(index=False, name=None)
    )

    # re-create indexes after to_sql (replace drops them)
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_txn_time ON transactions(txn_time);
        CREATE INDEX IF NOT EXISTS idx_txn_sender ON transactions(sender_vpa);
    """)
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"Loaded {count} rows into {db_path}")
    conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="upi_transactions.csv")
    parser.add_argument("--db", default="upi_fraud.db")
    args = parser.parse_args()
    load(args.csv, args.db)
