#!/bin/sh
# A nightly dump of the database, kept for BACKUP_KEEP_DAYS.
#
# This is deliberately boring: pg_dump to a compressed file on a volume, and
# a retention sweep. It is not off-site. Copy /backups somewhere else — a
# backup on the same disk as the database protects you from a bad migration,
# not from losing the machine.
#
# Restore:
#   docker compose -f docker-compose.prod.yml exec -T postgres \
#     psql -U docuparse -d docuparse < /path/to/docuparse-YYYY-MM-DD.sql
set -e

KEEP="${BACKUP_KEEP_DAYS:-14}"
mkdir -p /backups

while true; do
	stamp=$(date -u +%Y-%m-%dT%H-%M-%SZ)
	target="/backups/docuparse-${stamp}.sql.gz"

	if pg_dump -h postgres -U docuparse -d docuparse | gzip > "$target.partial"; then
		# Named only once it is complete, so a dump interrupted halfway is
		# never mistaken for a restorable one.
		mv "$target.partial" "$target"
		echo "[backup] wrote $target ($(wc -c < "$target") bytes)"
	else
		echo "[backup] FAILED at $stamp" >&2
		rm -f "$target.partial"
	fi

	find /backups -name 'docuparse-*.sql.gz' -mtime "+$KEEP" -delete
	echo "[backup] sleeping 24h"
	sleep 86400
done
