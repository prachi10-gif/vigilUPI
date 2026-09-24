# Connecting Power BI / Tableau to the UPI Fraud DB

## 1. Database setup
Run `schema.sql` then `bi_views.sql` against your Postgres instance:
```bash
psql -h <host> -U <user> -d upi_fraud -f schema.sql
psql -h <host> -U <user> -d upi_fraud -f bi_views.sql
```
(For local testing without Postgres, `load_data.py` builds the same
tables in SQLite — but Power BI/Tableau connect far more easily to
Postgres, so use Postgres for the actual BI layer.)

## 2. Power BI
1. **Get Data → PostgreSQL database**
2. Server: `<host>`, Database: `upi_fraud`
3. Import mode (not DirectQuery) is fine at this data volume — refresh
   on a schedule (e.g. every 15 min) rather than live-querying for every
   dashboard interaction.
4. Load these views as separate tables: `vw_hourly_fraud_rate`,
   `vw_risk_tier_distribution`, `vw_top_flagged_receivers`,
   `vw_analyst_performance`, `vw_detection_method_effectiveness`,
   `vw_daily_summary`.
5. Suggested visuals:
   - **Line chart**: `vw_hourly_fraud_rate` (hour vs flagged_pct) — the headline trend
   - **Donut**: `vw_risk_tier_distribution` (risk_tier vs txn_count)
   - **Table + bar**: `vw_top_flagged_receivers` — surfaces likely mule accounts
   - **KPI cards**: `vw_daily_summary` (today's flagged_txns, amount_at_risk)
   - **Stacked bar**: `vw_detection_method_effectiveness` — shows which
     detector (IQR / time-series / ML) is pulling its weight, useful for
     tuning thresholds later

## 3. Tableau
1. **Connect → PostgreSQL**, same connection details
2. Drag each `vw_*` view onto the canvas as a logical table (no joins
   needed — they're pre-aggregated)
3. Same visual mapping as above; Tableau's native anomaly-detection
   feature (right-click a time series → "Explain Data") pairs well
   with `vw_hourly_fraud_rate` as a second opinion alongside the STL
   model in `timeseries_anomaly.py`

## 4. Refresh cadence
- `vw_hourly_fraud_rate`, `vw_daily_summary`: refresh every 15–30 min
- `vw_top_flagged_receivers`, `vw_detection_method_effectiveness`: hourly is fine
- `vw_analyst_performance`: daily, for ops reporting

## 5. Access note
Give the BI tool a **read-only** Postgres role scoped to these views only
— it never needs write access to `transactions` or `alerts`.
