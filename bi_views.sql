-- =========================================================
-- BI-ready aggregate views (Postgres).
-- Point Power BI / Tableau at these views directly — they
-- do the heavy aggregation in the DB so the BI tool stays fast.
-- =========================================================

-- 1. Hourly fraud rate over time (for a trend line chart)
CREATE OR REPLACE VIEW vw_hourly_fraud_rate AS
SELECT
    date_trunc('hour', txn_time) AS hour,
    COUNT(*) AS total_txns,
    SUM(CASE WHEN risk_tier IN ('High','Critical') THEN 1 ELSE 0 END) AS flagged_txns,
    ROUND(100.0 * SUM(CASE WHEN risk_tier IN ('High','Critical') THEN 1 ELSE 0 END)
          / NULLIF(COUNT(*), 0), 2) AS flagged_pct
FROM transactions
GROUP BY 1
ORDER BY 1;

-- 2. Risk tier distribution (for a donut/bar chart)
CREATE OR REPLACE VIEW vw_risk_tier_distribution AS
SELECT
    COALESCE(risk_tier, 'Unscored') AS risk_tier,
    COUNT(*) AS txn_count,
    ROUND(AVG(amount), 2) AS avg_amount
FROM transactions
GROUP BY 1;

-- 3. Top flagged receivers (for a table/bar chart — potential mule accounts)
CREATE OR REPLACE VIEW vw_top_flagged_receivers AS
SELECT
    receiver_vpa,
    COUNT(*) AS flagged_count,
    SUM(amount) AS total_amount_flagged,
    ROUND(AVG(ensemble_score), 3) AS avg_risk_score
FROM transactions
WHERE risk_tier IN ('High', 'Critical')
GROUP BY receiver_vpa
ORDER BY flagged_count DESC
LIMIT 50;

-- 4. Analyst review performance (for an ops KPI card)
CREATE OR REPLACE VIEW vw_analyst_performance AS
SELECT
    date_trunc('day', analyst_reviewed_at) AS review_day,
    analyst_action,
    COUNT(*) AS actions_taken,
    AVG(EXTRACT(EPOCH FROM (analyst_reviewed_at - txn_time)) / 60) AS avg_response_minutes
FROM transactions
WHERE analyst_reviewed_at IS NOT NULL
GROUP BY 1, 2;

-- 5. Fraud pattern breakdown vs detection method (which check caught what)
CREATE OR REPLACE VIEW vw_detection_method_effectiveness AS
SELECT
    fraud_type,
    COUNT(*) AS total_fraud_txns,
    SUM(CASE WHEN iqr_flag THEN 1 ELSE 0 END) AS caught_by_iqr,
    SUM(CASE WHEN timeseries_flag THEN 1 ELSE 0 END) AS caught_by_timeseries,
    SUM(CASE WHEN model_flag THEN 1 ELSE 0 END) AS caught_by_ml_model,
    SUM(CASE WHEN risk_tier IN ('High','Critical') THEN 1 ELSE 0 END) AS caught_overall
FROM transactions
WHERE is_fraud_actual = TRUE
GROUP BY fraud_type;

-- 6. Daily volume + amount summary (for the main dashboard landing KPI cards)
CREATE OR REPLACE VIEW vw_daily_summary AS
SELECT
    date_trunc('day', txn_time) AS day,
    COUNT(*) AS total_txns,
    SUM(amount) AS total_amount,
    SUM(CASE WHEN risk_tier IN ('High','Critical') THEN 1 ELSE 0 END) AS flagged_txns,
    SUM(CASE WHEN risk_tier IN ('High','Critical') THEN amount ELSE 0 END) AS amount_at_risk
FROM transactions
GROUP BY 1
ORDER BY 1;
