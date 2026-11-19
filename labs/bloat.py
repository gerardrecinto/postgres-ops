"""Dead tuples, a long transaction that blocks vacuum, and why VACUUM does not shrink the file."""
import psycopg

from .common import connect, scalar, PRIMARY


def _stats(c):
    r = c.execute("SELECT dead_tuple_count, pg_relation_size('hot')/1024/1024 FROM pgstattuple('hot')").fetchone()
    return {"dead_tuples": r[0], "table_mb": r[1]}


def run(rows=100_000):
    out = {}
    with connect(PRIMARY) as c, connect(PRIMARY, autocommit=False) as holder:
        c.execute("DROP TABLE IF EXISTS hot")
        c.execute("CREATE TABLE hot(id int primary key, v int, pad text)")
        c.execute("INSERT INTO hot SELECT g, 0, repeat('p',100) FROM generate_series(1,%s) g", (rows,))
        c.execute("ALTER TABLE hot SET (autovacuum_enabled = false)")  # keep the demo deterministic
        out["fresh"] = _stats(c)
        holder.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        holder.execute("SELECT count(*) FROM hot")  # snapshot held open until rollback
        for _ in range(3):
            c.execute("UPDATE hot SET v = v + 1")
        out["after_3_updates"] = _stats(c)
        c.execute("VACUUM hot")
        out["vacuum_while_txn_open"] = _stats(c)
        holder.rollback()
        c.execute("VACUUM hot")
        out["vacuum_after_txn_closed"] = _stats(c)
        c.execute("VACUUM FULL hot")
        out["after_vacuum_full"] = _stats(c)
        out["vacuum_note"] = "plain VACUUM frees space for reuse; only VACUUM FULL (ACCESS EXCLUSIVE) returns it to the OS"
    return out
