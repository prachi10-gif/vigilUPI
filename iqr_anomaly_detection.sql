-- =========================================================
-- IQR (Interquartile Range) Anomaly Detection — pure SQL
-- Flags a transaction if its amount is a statistical outlier
-- relative to that SENDER's own historical amounts (per-sender
-- baseline, not global — a rickshaw driver's ₹5000 txn and a
-- business owner's ₹5000 txn mean very different things).
--
-- Rule: flag if amount > Q3 + 1.5 * IQR  (classic Tukey fence)
-- Run this periodically (e.g. hourly) or as a view for live use.
-- =========================================================

-- Step 1: per-sender quartiles over trailing 30 days
CREATE OR REPLACE VIEW sender_amount_quartiles AS
SELECT
    sender_vpa,
    percentile_cont(0.25) WITHIN GROUP (ORDER BY amount) AS q1,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY amount) AS q3
FROM transactions
WHERE txn_time >= now() - INTERVAL '30 days'
GROUP BY sender_vpa
HAVING COUNT(*) >= 5;   -- need enough history for quartiles to mean anything

-- Step 2: flag view — join each transaction against its sender's fence
CREATE OR REPLACE VIEW iqr_flagged_transactions AS
SELECT
    t.transaction_id,
    t.sender_vpa,
    t.amount,
    q.q1,
    q.q3,
    (q.q3 - q.q1) AS iqr,
    (q.q3 + 1.5 * (q.q3 - q.q1)) AS upper_fence,
    CASE
        WHEN t.amount > (q.q3 + 1.5 * (q.q3 - q.q1)) THEN TRUE
        ELSE FALSE
    END AS iqr_flag
FROM transactions t
JOIN sender_amount_quartiles q ON t.sender_vpa = q.sender_vpa;

-- Step 3: apply the flag back onto the transactions table (batch job)
UPDATE transactions t
SET iqr_flag = f.iqr_flag
FROM iqr_flagged_transactions f
WHERE t.transaction_id = f.transaction_id;

-- Bonus: global IQR fallback for brand-new senders with no history
-- (falls back to a cross-population fence so day-1 users aren't blind spots)
CREATE OR REPLACE VIEW global_amount_fence AS
SELECT
    percentile_cont(0.25) WITHIN GROUP (ORDER BY amount) AS global_q1,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY amount) AS global_q3,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY amount)
        + 1.5 * (percentile_cont(0.75) WITHIN GROUP (ORDER BY amount)
                 - percentile_cont(0.25) WITHIN GROUP (ORDER BY amount)) AS global_upper_fence
FROM transactions
WHERE txn_time >= now() - INTERVAL '30 days';
