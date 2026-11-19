#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 -U postgres -d lab <<SQL
CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'repl';
CREATE EXTENSION IF NOT EXISTS pgstattuple;
SQL
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
