"""Streaming replication: stale reads, lag in bytes, slot WAL retention, promotion."""
import subprocess
import time

import psycopg

from .common import connect, scalar, PRIMARY, REPLICA


def wait_for(fn, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    return False


def setup(p):
    p.execute("DROP TABLE IF EXISTS kv")
    p.execute("CREATE TABLE kv(id serial primary key, payload text)")
    p.execute("INSERT INTO kv(payload) SELECT 'row' FROM generate_series(1,1000)")


def basic_replication():
    with connect(PRIMARY) as p, connect(REPLICA) as r:
        setup(p)
        converged = wait_for(lambda: scalar(r, "SELECT count(*) FROM kv") == 1000
                             if r.execute("SELECT to_regclass('kv')").fetchone()[0] else False)
        in_recovery = scalar(r, "SELECT pg_is_in_recovery()")
        try:
            r.execute("INSERT INTO kv(payload) VALUES ('x')")
            write_error = None
        except psycopg.errors.ReadOnlySqlTransaction as e:
            write_error = type(e).__name__
        return {"replica_rows": scalar(r, "SELECT count(*) FROM kv"), "converged": converged,
                "replica_in_recovery": in_recovery, "replica_write_error": write_error}


def lag_and_stale_read():
    """Pause WAL replay on the replica, write on the primary, show the stale read and the lag."""
    with connect(PRIMARY) as p, connect(REPLICA) as r:
        r.execute("SELECT pg_wal_replay_pause()")
        p.execute("INSERT INTO kv(payload) SELECT repeat('a',200) FROM generate_series(1,5000)")
        time.sleep(1)
        lag_bytes = scalar(p, "SELECT pg_wal_lsn_diff(sent_lsn, replay_lsn)::bigint FROM pg_stat_replication")
        stale_rows = scalar(r, "SELECT count(*) FROM kv")
        primary_rows = scalar(p, "SELECT count(*) FROM kv")
        t0 = time.time()
        r.execute("SELECT pg_wal_replay_resume()")
        caught_up = wait_for(lambda: scalar(r, "SELECT count(*) FROM kv") == primary_rows)
        return {"primary_rows": primary_rows, "replica_rows_while_paused": stale_rows,
                "lag_bytes_while_paused": lag_bytes, "caught_up": caught_up,
                "catch_up_seconds": round(time.time() - t0, 2)}


def slot_retention():
    """A stopped replica with a slot makes the primary keep WAL. The classic disk-fill cause."""
    with connect(PRIMARY) as p:
        before = scalar(p, "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint FROM pg_replication_slots WHERE slot_name='replica1'")
        subprocess.run(["docker", "compose", "stop", "replica"], check=True, capture_output=True)
        p.execute("INSERT INTO kv(payload) SELECT repeat('b',500) FROM generate_series(1,100000)")
        p.execute("CHECKPOINT")
        row = p.execute("SELECT active, pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint FROM pg_replication_slots WHERE slot_name='replica1'").fetchone()
        subprocess.run(["docker", "compose", "start", "replica"], check=True, capture_output=True)
        with_retry = wait_for(lambda: _replica_ready(), 60)
        with connect(REPLICA) as r:
            wait_for(lambda: scalar(r, "SELECT count(*) FROM kv") == scalar(p, "SELECT count(*) FROM kv"), 60)
            caught = scalar(r, "SELECT count(*) FROM kv") == scalar(p, "SELECT count(*) FROM kv")
        after = scalar(p, "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint FROM pg_replication_slots WHERE slot_name='replica1'")
        return {"slot_active_while_replica_down": row[0], "retained_wal_bytes_while_down": row[1],
                "retained_wal_bytes_before": before, "retained_wal_bytes_after_catch_up": after,
                "replica_restarted": with_retry, "replica_caught_up": caught}


def _replica_ready():
    try:
        with connect(REPLICA) as r:
            return scalar(r, "SELECT 1") == 1
    except psycopg.OperationalError:
        return False


def failover():
    """Stop the primary, promote the replica, write to it."""
    with connect(REPLICA) as r:
        rows_before = scalar(r, "SELECT count(*) FROM kv")
        subprocess.run(["docker", "compose", "stop", "primary"], check=True, capture_output=True)
        r.execute("SELECT pg_promote(wait => true)")
        recovering = scalar(r, "SELECT pg_is_in_recovery()")
        r.execute("INSERT INTO kv(payload) VALUES ('after-failover')")
        return {"rows_before": rows_before, "still_in_recovery": recovering,
                "rows_after_write": scalar(r, "SELECT count(*) FROM kv")}
