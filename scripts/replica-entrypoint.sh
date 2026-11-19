#!/bin/bash
# Seed the replica from a base backup of the primary, then start it as a hot standby.
set -e
DATA=/var/lib/postgresql/data
if [ ! -s "$DATA/PG_VERSION" ]; then
  until pg_isready -h primary -U postgres -q; do sleep 1; done
  mkdir -p "$DATA" && chown postgres:postgres "$DATA" && chmod 700 "$DATA"
  gosu postgres pg_basebackup -d "host=primary user=replicator password=repl" \
    -D "$DATA" -R -X stream -C -S replica1
fi
exec gosu postgres postgres -c hot_standby=on -c hot_standby_feedback=off
