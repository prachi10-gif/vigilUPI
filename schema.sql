-- =========================================================
-- UPI Fraud Detection — Database Schema (PostgreSQL)
-- Star-schema style: one fact table (transactions), light
-- dimension context inline (kept simple since senders/receivers
-- are identified by VPA, not a huge separate dimension here).
-- =========================================================

CREATE TABLE IF NOT EXISTS senders (
    sender_vpa          TEXT PRIMARY KEY,
    home_city           TEXT,
    avg_amount_30d      NUMERIC(12,2),
    avg_txn_per_day     INTEGER,
    device_id           TEXT,
    created_at          TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id          UUID PRIMARY KEY,
    sender_vpa               TEXT REFERENCES senders(sender_vpa),
    receiver_vpa              TEXT NOT NULL,
    amount                    NUMERIC(12,2) NOT NULL,
    txn_time                  TIMESTAMP NOT NULL,
    device_id                 TEXT,
    location                  TEXT,
    sender_home_city          TEXT,
    sender_avg_amount_30d     NUMERIC(12,2),
    sender_txn_count_24h      INTEGER,
    is_new_receiver           BOOLEAN,
    -- ML ensemble output (populated by the real-time scorer)
    isolation_forest_score    FLOAT,
    xgboost_score              FLOAT,
    ensemble_score             FLOAT,
    -- Rule-based flags (populated by the SQL/IQR + time-series jobs below)
    iqr_flag                   BOOLEAN DEFAULT FALSE,
    timeseries_flag            BOOLEAN DEFAULT FALSE,
    model_flag                 BOOLEAN DEFAULT FALSE,
    risk_tier                  TEXT,              -- Low / Medium / High / Critical
    -- Analyst / ground truth
    is_fraud_actual             BOOLEAN,           -- ground truth if known (synthetic label or confirmed)
    fraud_type                  TEXT,
    analyst_action               TEXT,              -- Allow / Hold / Block / Verify
    analyst_reviewed_at          TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_txn_time ON transactions(txn_time);
CREATE INDEX IF NOT EXISTS idx_txn_sender ON transactions(sender_vpa);
CREATE INDEX IF NOT EXISTS idx_txn_risk_tier ON transactions(risk_tier);
CREATE INDEX IF NOT EXISTS idx_txn_ensemble_score ON transactions(ensemble_score);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id            SERIAL PRIMARY KEY,
    transaction_id       UUID REFERENCES transactions(transaction_id),
    triggered_by          TEXT,       -- 'iqr' / 'isolation_forest' / 'xgboost' / 'timeseries' / 'ensemble'
    risk_tier              TEXT,
    created_at              TIMESTAMP DEFAULT now(),
    status                  TEXT DEFAULT 'pending',  -- pending / reviewed / resolved
    analyst_action           TEXT,
    resolved_at               TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status);
