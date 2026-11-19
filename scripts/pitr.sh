#!/bin/bash
# Point-in-time recovery in one throwaway container: base backup, WAL archive,
# a bad DELETE, then restore to the second before it. Run as the postgres user.
set -e
B=/usr/lib/postgresql/16/bin
W=/tmp/pitr; rm -rf $W; mkdir -p $W/archive $W/base
export PGPORT=5440 PGHOST=/tmp PGUSER=postgres
$B/initdb -D $W/data -A trust >/dev/null
cat >> $W/data/postgresql.conf <<CONF
archive_mode = on
archive_command = 'cp %p $W/archive/%f'
wal_level = replica
listen_addresses = ''
unix_socket_directories = '/tmp'
port = 5440
CONF
$B/pg_ctl -D $W/data -l $W/log -w start >/dev/null
psql -q -c "CREATE TABLE orders(id int primary key, total numeric)"
psql -q -c "INSERT INTO orders SELECT g, g*2 FROM generate_series(1,1000) g"
$B/pg_basebackup -D $W/base -X fetch -c fast
psql -q -c "INSERT INTO orders SELECT g, g*2 FROM generate_series(1001,1500) g"
sleep 2
T=$(psql -At -c "SELECT to_char(now(),'YYYY-MM-DD HH24:MI:SS.US TZ')")
sleep 2
psql -q -c "DELETE FROM orders"
echo "ROWS_AFTER_BAD_DELETE=$(psql -At -c 'SELECT count(*) FROM orders')"
psql -q -c "SELECT pg_switch_wal()" >/dev/null
sleep 2
$B/pg_ctl -D $W/data -m fast -w stop >/dev/null
mv $W/base $W/restore; chmod 700 $W/restore
cat >> $W/restore/postgresql.conf <<CONF
restore_command = 'cp $W/archive/%f %p'
recovery_target_time = '$T'
recovery_target_action = 'promote'
listen_addresses = ''
unix_socket_directories = '/tmp'
port = 5440
CONF
touch $W/restore/recovery.signal
$B/pg_ctl -D $W/restore -l $W/restore.log -w start >/dev/null
echo "RECOVERY_TARGET=$T"
echo "RESTORED_ROWS=$(psql -At -c 'SELECT count(*) FROM orders')"
$B/pg_ctl -D $W/restore -m fast -w stop >/dev/null
