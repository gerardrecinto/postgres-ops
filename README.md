# postgres-ops-lab

PostgreSQL operations on a laptop: streaming replication, failover, point-in-time recovery, schema changes under load, query plans and bloat, all run against a real primary and replica in Docker. An OpenTofu/Terraform module for RDS is included and validated, not applied.

[Pages](https://gerardrecinto.github.io/postgres-ops-lab/) | [Recorded output](docs/DEMO_OUTPUT.txt)

## Run it

```bash
./scripts/demo.sh                                   # whole run, about 40 seconds, tears down after
docker compose up -d --wait && pip install -r requirements.txt && pytest -q   # 11 tests
```

Needs Docker and Python 3.10+. Host ports 55432 (primary) and 55433 (replica).

## Observed

| Scenario | What happened |
|---|---|
| Replica after 1,000 rows on primary | Same 1,000 rows, `pg_is_in_recovery()` true, a write fails with `ReadOnlySqlTransaction` |
| Replay paused, 5,000 rows written | Replica still serves 1,000 rows, primary shows 1.68 MB behind in `pg_stat_replication`, caught up in 0.2 s after resume |
| Long reader, then `ALTER TABLE ... ADD COLUMN`, then a plain `INSERT` | The ALTER waited 3.0 s for the reader and the INSERT behind it waited 2.3 s |
| Same with `lock_timeout = '300ms'` | ALTER gave up after 0.3 s, the INSERT took 0 s, retry succeeded once the reader finished |
| `CREATE INDEX CONCURRENTLY` cancelled | An INVALID index stays behind (`indisvalid = false`). Drop it and run again |
| `CHECK ... NOT VALID`, then `VALIDATE CONSTRAINT` | Added unvalidated, validated in a separate step |
| 1M rows, customer 1 owns 90% | Rare customer: Index Scan 0.05 ms. Customer 1: Seq Scan 57 ms, because an index would not help |
| Full vs partial index for `status = 'pending'` (1% of rows) | 6.96 MB vs 0.24 MB |
| 600k rows flipped to pending, no `ANALYZE` | Planner estimated 15,002 rows, 604,042 came back. After `ANALYZE`: 603,133 |
| 3 full-table updates with a REPEATABLE READ transaction open | 300,000 dead tuples, `VACUUM` removed none. After the transaction ended: 0 dead, file still 53 MB. `VACUUM FULL`: 13 MB |
| Replica stopped, 100k rows written, replication slot kept | Primary retained 63.9 MB of WAL, back to 0 after the replica caught up |
| Primary stopped, `pg_promote()` on the replica | Replica accepts writes, no longer in recovery |
| Base backup, 500 more rows, `DELETE FROM orders`, restore to the second before | 0 rows after the delete, 1,500 rows after recovery |

Numbers come from one laptop and are not benchmarks. Full output in [docs/DEMO_OUTPUT.txt](docs/DEMO_OUTPUT.txt).

## Trade-offs

| Choice | Alternative | What it costs | When I would switch |
|---|---|---|---|
| Physical streaming replication | Logical replication | Replica is the whole cluster, same major version, no partial tables | Upgrading majors with little downtime, or replicating a subset of tables |
| Replication slot for the replica | No slot, WAL archive only | A dead replica makes the primary keep WAL (63.9 MB here after 100k rows), which fills disk if nobody notices | A replica that can be rebuilt cheaply, with `max_slot_wal_keep_size` as the backstop |
| `lock_timeout` plus retry for DDL | Run DDL and wait | A migration can fail and has to be retried | Never for tables under write load |
| Batched `UPDATE` backfill | One statement | Slower overall, more WAL churn, needs a way to resume | Tables small enough that one statement finishes in seconds |
| `VACUUM`, `VACUUM FULL` only on a window | `pg_repack` | `VACUUM FULL` takes ACCESS EXCLUSIVE and rewrites the table | Large hot tables where a lock is not acceptable (needs the extension, not run here) |
| Base backup plus WAL archive | Snapshot-only backups | More moving parts to monitor, archive gaps break recovery | RDS, where `backup_retention_period` gives the same window managed |

## RDS module

[`infra/rds/main.tf`](infra/rds/main.tf): Multi-AZ PostgreSQL 16, encrypted gp3, master password in Secrets Manager (`manage_master_user_password`), 14-day PITR window, parameter group that logs slow statements and lock waits and forces TLS, read replicas, and alarms for replica lag, free storage and transaction ID age. `terraform validate` passes. It has not been applied and no AWS resources were created.

Replication is asynchronous and promotion is a manual `pg_promote()`.
