"""Print every lab result as key: value lines. Used by scripts/demo.sh."""
import json
import sys

from . import bloat, migration, plans, replication


def emit(name, d):
    print(f"## {name}")
    for k, v in d.items():
        print(f"{k}: {json.dumps(v) if isinstance(v, (dict, list)) else v}")
    print()


if __name__ == "__main__":
    emit("replication basic", replication.basic_replication())
    emit("replication lag and stale read", replication.lag_and_stale_read())
    migration.setup()
    emit("migration naive ALTER", migration.naive_alter())
    emit("migration ALTER with lock_timeout", migration.safe_alter())
    emit("migration batched backfill", migration.batched_backfill())
    emit("migration concurrent index", migration.concurrent_index_and_invalid_leftover())
    emit("migration NOT VALID constraint", migration.not_valid_constraint())
    plans.setup()
    for k, v in plans.run().items():
        print(f"plans {k}: {json.dumps(v)}")
    print()
    emit("bloat", bloat.run())
    emit("replication slot retention", replication.slot_retention())
    emit("replication failover", replication.failover())
