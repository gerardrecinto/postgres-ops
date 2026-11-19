import os
import psycopg

PRIMARY = os.environ.get("PRIMARY_DSN", "host=localhost port=55432 dbname=lab user=postgres password=lab")
REPLICA = os.environ.get("REPLICA_DSN", "host=localhost port=55433 dbname=lab user=postgres password=lab")


def connect(dsn=PRIMARY, autocommit=True):
    return psycopg.connect(dsn, autocommit=autocommit)


def scalar(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()[0]
