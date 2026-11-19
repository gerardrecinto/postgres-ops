"""Reading plans: selectivity, partial indexes, stale statistics."""
import json

from .common import connect, scalar, PRIMARY


def _plan(c, sql, params=None):
    j = c.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql, params).fetchone()[0]
    root = j[0]["Plan"]
    node = root
    while "Plans" in node and node["Node Type"] in ("Limit", "Aggregate", "Gather", "Sort"):
        node = node["Plans"][0]
    return {"node": node["Node Type"], "ms": round(j[0]["Execution Time"], 2),
            "est_rows": node["Plan Rows"], "actual_rows": node["Actual Rows"]}


def setup(rows=1_000_000):
    with connect(PRIMARY) as c:
        c.execute("DROP TABLE IF EXISTS events")
        c.execute("CREATE TABLE events(id serial primary key, customer int, status text, payload text) WITH (autovacuum_enabled = false)")
        # customer 1 owns 90% of rows (skew); 1% of rows are 'pending'
        c.execute("""INSERT INTO events(customer,status,payload)
                     SELECT CASE WHEN random()<0.9 THEN 1 ELSE 2+(random()*5000)::int END,
                            CASE WHEN random()<0.01 THEN 'pending' ELSE 'done' END, repeat('x',40)
                     FROM generate_series(1,%s)""", (rows,))


def run():
    out = {}
    with connect(PRIMARY) as c:
        c.execute("SET max_parallel_workers_per_gather = 0")
        c.execute("SET track_io_timing = on")
        # statistics before ANALYZE: the planner has defaults for a fresh table
        out["before_analyze_rare_customer"] = _plan(c, "SELECT * FROM events WHERE customer = 4000")
        c.execute("ANALYZE events")
        c.execute("CREATE INDEX events_customer_idx ON events(customer)")
        out["rare_customer_with_index"] = _plan(c, "SELECT * FROM events WHERE customer = 4000")
        out["hot_customer_with_index"] = _plan(c, "SELECT * FROM events WHERE customer = 1")
        c.execute("CREATE INDEX events_pending_full ON events(status)")
        c.execute("CREATE INDEX events_pending_partial ON events(id) WHERE status = 'pending'")
        out["index_bytes"] = {
            "full_status_index": scalar(c, "SELECT pg_relation_size('events_pending_full')"),
            "partial_pending_index": scalar(c, "SELECT pg_relation_size('events_pending_partial')")}
        out["pending_count_with_partial"] = _plan(c, "SELECT count(*) FROM events WHERE status = 'pending'")
        # statistics gone stale after a bulk change: 600k rows flip to pending without ANALYZE
        c.execute("UPDATE events SET status='pending' WHERE id % 5 < 3")
        out["stale_stats_pending_count"] = _plan(c, "SELECT count(*) FROM events WHERE status = 'pending'")
        c.execute("ANALYZE events")
        out["after_analyze_pending_count"] = _plan(c, "SELECT count(*) FROM events WHERE status = 'pending'")
    return out
