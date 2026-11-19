"""Run against the compose stack: docker compose up -d --wait && pytest -q
Order matters: the failover test stops the primary and must run last."""
import subprocess
from pathlib import Path

from labs import bloat, migration, plans, replication

ROOT = Path(__file__).resolve().parent.parent


def test_replica_follows_and_rejects_writes():
    r = replication.basic_replication()
    assert r["converged"] and r["replica_rows"] == 1000
    assert r["replica_in_recovery"] is True
    assert r["replica_write_error"] == "ReadOnlySqlTransaction"


def test_paused_replay_gives_stale_read_and_lag_then_catches_up():
    r = replication.lag_and_stale_read()
    assert r["replica_rows_while_paused"] == 1000 < r["primary_rows"]
    assert r["lag_bytes_while_paused"] > 100_000
    assert r["caught_up"]


def test_naive_alter_blocks_writers_and_lock_timeout_does_not():
    migration.setup(rows=50_000)
    naive = migration.naive_alter(hold=3)
    assert naive["insert_seconds"] > 1.5 and naive["alter_seconds"] > 2.5
    safe = migration.safe_alter(hold=3)
    assert safe["first_try"] == "lock_timeout" and safe["insert_seconds"] < 0.5
    assert safe["retry"] == "ok"


def test_backfill_in_batches_fills_every_row():
    r = migration.batched_backfill(batch=20_000)
    assert r["unfilled"] == 0 and r["batches"] >= 2


def test_cancelled_concurrent_index_leaves_invalid_index_until_dropped():
    r = migration.concurrent_index_and_invalid_leftover()
    assert r["cancelled"] and r["indisvalid_after_cancel"] is False
    assert r["indisvalid_after_retry"] is True


def test_not_valid_constraint_validates_in_a_second_step():
    r = migration.not_valid_constraint()
    assert r["validated_on_add"] is False and r["validated_after"] is True


def test_plans_follow_selectivity_and_partial_index_is_small():
    plans.setup(rows=200_000)
    r = plans.run()
    assert r["rare_customer_with_index"]["node"] == "Index Scan"
    assert r["hot_customer_with_index"]["node"] == "Seq Scan"
    assert r["index_bytes"]["partial_pending_index"] * 10 < r["index_bytes"]["full_status_index"]
    stale = r["stale_stats_pending_count"]
    assert stale["actual_rows"] > 10 * stale["est_rows"]
    fresh = r["after_analyze_pending_count"]
    assert abs(fresh["actual_rows"] - fresh["est_rows"]) < 0.1 * fresh["actual_rows"]


def test_open_transaction_blocks_vacuum_and_only_vacuum_full_shrinks():
    r = bloat.run(rows=30_000)
    assert r["after_3_updates"]["dead_tuples"] > 0
    assert r["vacuum_while_txn_open"]["dead_tuples"] == r["after_3_updates"]["dead_tuples"]
    assert r["vacuum_after_txn_closed"]["dead_tuples"] == 0
    assert r["vacuum_after_txn_closed"]["table_mb"] == r["after_3_updates"]["table_mb"]
    assert r["after_vacuum_full"]["table_mb"] < r["vacuum_after_txn_closed"]["table_mb"]


def test_pitr_restores_rows_deleted_by_mistake():
    out = subprocess.run(
        ["docker", "run", "--rm", "--user", "postgres", "-v", f"{ROOT}/scripts:/scripts:ro",
         "postgres:16", "bash", "/scripts/pitr.sh"],
        check=True, capture_output=True, text=True).stdout
    assert "ROWS_AFTER_BAD_DELETE=0" in out
    assert "RESTORED_ROWS=1500" in out


def test_stopped_replica_makes_primary_retain_wal():
    r = replication.slot_retention()
    assert r["slot_active_while_replica_down"] is False
    assert r["retained_wal_bytes_while_down"] > 10_000_000
    assert r["replica_caught_up"] and r["retained_wal_bytes_after_catch_up"] < 1_000_000


def test_failover_promotes_replica_and_accepts_writes():  # keep last
    r = replication.failover()
    assert r["still_in_recovery"] is False
    assert r["rows_after_write"] == r["rows_before"] + 1
