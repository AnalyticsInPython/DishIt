#!/usr/bin/env bash
#
# Publish the locally built canonical database to Turso.
#
# Run after the pipeline, in this order:
#     python3 data/db/load_db.py [--rebuild]
#     python3 data/calculate/calculate.py data/db/dishit.db
#     ./data/db/push_to_turso.sh
#
# First time only, create the database and mint credentials:
#     turso auth login
#     turso db create dishit
#     turso db show dishit --url      # -> TURSO_DATABASE_URL
#     turso db tokens create dishit   # -> TURSO_AUTH_TOKEN
#
set -euo pipefail

DB="${1:-data/db/dishit.db}"
NAME="${TURSO_DB_NAME:-dishit}"

die() { printf 'push_to_turso: %s\n' "$1" >&2; exit 1; }

command -v turso   >/dev/null || die "turso CLI not found. brew install tursodatabase/tap/turso"
command -v sqlite3 >/dev/null || die "sqlite3 not found"
command -v python3 >/dev/null || die "python3 not found"
[ -f "$DB" ] || die "no database at $DB — run data/db/load_db.py first"

[ "$(sqlite3 "$DB" 'PRAGMA integrity_check;')" = "ok" ] || die "$DB failed integrity_check"

TABLES="restaurants restaurant_types dishes reviews review_media dish_mentions"

if [ "$(sqlite3 "$DB" 'SELECT COUNT(*) FROM dish_mentions;')" -eq 0 ]; then
    printf 'push_to_turso: warning — dish_mentions is empty, so every sentiment score\n'
    printf '               will be zero. Run data/calculate/calculate.py first.\n' >&2
fi

turso db list | awk '{print $1}' | grep -qx "$NAME" || {
    echo "==> creating database $NAME"
    turso db create "$NAME"
}

# Replace the remote contents with a dump, rather than `turso db import`.
#
# `turso db import` only creates a NEW database, so re-publishing would mean
# `turso db destroy` first — and that invalidates every token minted for the
# database, breaking anything already deployed against it. Dump-and-restore keeps
# the database, its URL and its tokens stable across every push, which matters
# because load_db.py --rebuild regenerates the file routinely.
#
# It also sidesteps the importer's WAL requirement entirely: load_db.py creates
# the file in `delete` journal mode, so an import path would have to convert a
# copy to WAL and checkpoint it on every single push.
DUMP="$(mktemp -t dishit-dump)"
trap 'rm -f "$DUMP"' EXIT

{
    echo "PRAGMA foreign_keys=OFF;"
    # Drop what is already there, views before tables, so the dump's CREATEs land
    # on a clean database. Generated from the local schema so a new table added to
    # schema.sql is handled without editing this script.
    sqlite3 "$DB" "
        SELECT 'DROP ' || CASE type WHEN 'view' THEN 'VIEW' ELSE 'TABLE' END ||
               ' IF EXISTS \"' || name || '\";'
        FROM sqlite_master
        WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'
        ORDER BY CASE type WHEN 'view' THEN 0 ELSE 1 END;"
    # Not `sqlite3 "$DB" .dump`: since SQLite 3.47 the CLI wraps any text holding a
    # newline or a non-ASCII character in unistr(), and libSQL has no such function,
    # so the push dies on the first review body that contains a line break or a
    # curly quote. Python's iterdump() walks the same data and emits plain quoted
    # literals, which both engines read.
    python3 - "$DB" <<'PY'
import sqlite3
import sys

with sqlite3.connect(sys.argv[1]) as conn:
    for statement in conn.iterdump():
        print(statement)
PY
} > "$DUMP"

echo "==> pushing $(wc -c < "$DUMP" | tr -d ' ') bytes to $NAME"
turso db shell "$NAME" < "$DUMP"

echo "==> verifying row counts"
status=0
for table in $TABLES; do
    local_n="$(sqlite3 "$DB" "SELECT COUNT(*) FROM $table;")"
    # The shell prints a formatted table; take the last bare number it emits.
    remote_n="$(turso db shell "$NAME" "SELECT COUNT(*) FROM $table;" \
                | grep -oE '[0-9]+' | tail -1)"
    if [ "$local_n" = "$remote_n" ]; then
        printf '    %-18s %8s  ok\n' "$table" "$local_n"
    else
        printf '    %-18s local=%-8s remote=%-8s MISMATCH\n' "$table" "$local_n" "$remote_n"
        status=1
    fi
done

[ "$status" -eq 0 ] || die "row counts differ — the push did not land cleanly"
echo "==> $NAME is up to date"
