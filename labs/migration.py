"""Schema changes under load: lock queueing, lock_timeout, batched backfill, concurrent index."""
import threading
import time

import psycopg

from .common import connect, scalar, PRIMARY


def setup(rows=300_000):
    with connect(PRIMARY) as c:
        c.execute("DROP TABLE IF EXISTS orders")
        c.execute("CREATE TABLE orders(id serial primary key, customer int, total numeric, created timestamptz default now())")
        c.execute("INSERT INTO orders(customer,total) SELECT (random()*1000)::int, random()*100 FROM generate_series(1,%s)", (rows,))
        c.execute("ANALYZE orders")


def _hold_reader(seconds, ready):
    with connect(PRIMARY, autocommit=False) as c:
        c.execute("SELECT count(*) FROM orders").fetchone()
        ready.set()
        time.sleep(seconds)
        c.rollback()


def _writer_latency(out):
    time.sleep(0.5)
    with connect(PRIMARY) as c:
        t0 = time.time()
        c.execute("INSERT INTO orders(customer,total) VALUES (1,1)")
        out["insert_seconds"] = round(time.time() - t0, 2)


def naive_alter(hold=3):
    """A long reader, then an ALTER that waits for it, then a plain INSERT stuck behind the ALTER."""
    c0 = connect(PRIMARY)
    c0.execute("ALTER TABLE orders DROP COLUMN IF EXISTS status")
    ready, res = threading.Event(), {}
    t_read = threading.Thread(target=_hold_reader, args=(hold, ready)); t_read.start(); ready.wait()
    t_write = threading.Thread(target=_writer_latency, args=(res,))
    def alter():
        with connect(PRIMARY) as c:
            t0 = time.time()
            c.execute("ALTER TABLE orders ADD COLUMN status text NOT NULL DEFAULT 'new'")
            res["alter_seconds"] = round(time.time() - t0, 2)
    t_alter = threading.Thread(target=alter)
    t_alter.start(); time.sleep(0.2); t_write.start()
    for t in (t_read, t_alter, t_write): t.join()
    c0.close()
    return res


def safe_alter(hold=3):
    """Same situation with lock_timeout: the ALTER gives up fast and the INSERT is never stuck."""
    with connect(PRIMARY) as c0:
        c0.execute("ALTER TABLE orders DROP COLUMN IF EXISTS status")
    ready, res = threading.Event(), {}
    t_read = threading.Thread(target=_hold_reader, args=(hold, ready)); t_read.start(); ready.wait()
    t_write = threading.Thread(target=_writer_latency, args=(res,))
    def alter():
        with connect(PRIMARY) as c:
            c.execute("SET lock_timeout = '300ms'")
            t0 = time.time()
            try:
                c.execute("ALTER TABLE orders ADD COLUMN status text NOT NULL DEFAULT 'new'")
                res["first_try"] = "ok"
            except psycopg.errors.LockNotAvailable:
                res["first_try"] = "lock_timeout"
            res["first_try_seconds"] = round(time.time() - t0, 2)
    t_alter = threading.Thread(target=alter)
    t_alter.start(); time.sleep(0.2); t_write.start()
    for t in (t_read, t_alter, t_write): t.join()
    with connect(PRIMARY) as c:  # reader is gone, retry succeeds
        c.execute("SET lock_timeout = '300ms'")
        c.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'new'")
        res["retry"] = "ok"
    return res


def batched_backfill(batch=50_000):
    with connect(PRIMARY) as c:
        c.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS region text")
        c.execute("UPDATE orders SET region = NULL")
        batches, t0 = 0, time.time()
        last = 0
        max_id = scalar(c, "SELECT max(id) FROM orders")
        while last < max_id:
            c.execute("UPDATE orders SET region = CASE WHEN customer < 500 THEN 'west' ELSE 'east' END WHERE id > %s AND id <= %s", (last, last + batch))
            last += batch; batches += 1
        return {"batches": batches, "unfilled": scalar(c, "SELECT count(*) FROM orders WHERE region IS NULL"),
                "seconds": round(time.time() - t0, 2)}


def concurrent_index_and_invalid_leftover():
    with connect(PRIMARY) as c:
        c.execute("DROP INDEX IF EXISTS orders_customer_idx")
        c.execute("SET statement_timeout = '1ms'")
        try:
            c.execute("CREATE INDEX CONCURRENTLY orders_customer_idx ON orders(customer)")
            cancelled = False
        except psycopg.errors.QueryCanceled:
            cancelled = True
        c.execute("RESET statement_timeout")
        valid_after_cancel = c.execute("SELECT indisvalid FROM pg_index WHERE indexrelid = 'orders_customer_idx'::regclass").fetchone()
        c.execute("DROP INDEX CONCURRENTLY IF EXISTS orders_customer_idx")
        c.execute("CREATE INDEX CONCURRENTLY orders_customer_idx ON orders(customer)")
        valid_after_retry = scalar(c, "SELECT indisvalid FROM pg_index WHERE indexrelid = 'orders_customer_idx'::regclass")
        return {"cancelled": cancelled,
                "index_left_behind_after_cancel": valid_after_cancel is not None,
                "indisvalid_after_cancel": valid_after_cancel[0] if valid_after_cancel else None,
                "indisvalid_after_retry": valid_after_retry}


def not_valid_constraint():
    with connect(PRIMARY) as c:
        c.execute("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_total_pos")
        c.execute("ALTER TABLE orders ADD CONSTRAINT orders_total_pos CHECK (total >= 0) NOT VALID")
        before = scalar(c, "SELECT convalidated FROM pg_constraint WHERE conname='orders_total_pos'")
        c.execute("ALTER TABLE orders VALIDATE CONSTRAINT orders_total_pos")
        after = scalar(c, "SELECT convalidated FROM pg_constraint WHERE conname='orders_total_pos'")
        return {"validated_on_add": before, "validated_after": after}
