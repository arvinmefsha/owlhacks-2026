-- Tiger Data / TimescaleDB features. Runs after schema.sql, one statement at a time.

CREATE EXTENSION IF NOT EXISTS timescaledb;

SELECT create_hypertable('pose_frames', by_range('ts'), if_not_exists => TRUE, migrate_data => TRUE);

SELECT create_hypertable('dive_metrics', by_range('recorded_at'), if_not_exists => TRUE, migrate_data => TRUE);

-- Daily averages per diver and metric. Real-time mode (materialized_only = false) merges in rows
-- newer than the last refresh, so a dive shows up on the Progress page immediately.
CREATE MATERIALIZED VIEW IF NOT EXISTS daily_metric_avg
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 day', recorded_at) AS bucket,
    diver_id,
    metric,
    avg(value) AS avg_value,
    avg(score) AS avg_score,
    count(*) AS dives
FROM dive_metrics
GROUP BY bucket, diver_id, metric
WITH NO DATA;

SELECT add_continuous_aggregate_policy(
    'daily_metric_avg',
    start_offset => INTERVAL '90 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists => TRUE
);
