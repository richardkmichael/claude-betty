#!/usr/bin/env python
"""
Migrate tool-monitor database from Schema 3.0 to Schema 3.1.

3.1 keys each FTS5 table by tool_events.id and keeps it in step on every change:
  - rowid = tool_events.id, replacing the unsearchable `event_id UNINDEXED` column
  - AFTER DELETE and AFTER UPDATE triggers join the existing AFTER INSERT trigger, so deleting or
    updating an event updates its FTS entry by rowid
  - Schema version bumped to 3.1.0

The migration runs while the hook is writing.  Every write transaction is kept short, well under
the hook's 5 second busy_timeout, with a pause after each so a waiting hook gets the lock:
  1. Create the new table beside the old one with its own triggers, and record the highest
     tool_events.id.  Rows above it are indexed by the new triggers.
  2. Backfill rows up to that id in small batches.  Progress is stored in schema_info in the same
     transaction as each batch, so an interrupted run resumes without indexing a row twice.
  3. Verify the new table against tool_events.
  4. In one transaction, swap the triggers and table names.
  5. Drop the old table.

Search keeps working throughout.  Rerunning after an interruption resumes where it stopped.

Usage:
    python tools/migration/migrate_to_v3_1.py /path/to/database.db
    python tools/migration/migrate_to_v3_1.py --integrity-check /path/to/copy.db

--integrity-check also runs FTS5's integrity-check on each new table.  It holds the write lock for
several seconds on a large table, long enough to make hook events fail, so use it only on a copy.
"""

import argparse
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path


TARGET_VERSION = "3.1.0"
TARGET_DESCRIPTION = (
    "V3.1: FTS5 trigram indexes keyed by tool_events.id and kept in step on insert, delete and "
    "update, expression indexes, expanded hook coverage"
)

# The trigram tokenizer requires SQLite 3.34.0.
MINIMUM_SQLITE = (3, 34, 0)

# (fts_table, fts_column, json_path, filter on tool_events written against `tool_name`)
FTS_TABLES = [
    ("fts_bash_command", "command",   "$.tool_input.command",   "tool_name = 'Bash'"),
    ("fts_file_path",    "file_path", "$.tool_input.file_path", "tool_name IN ('Read', 'Write', 'Edit')"),
    ("fts_grep_pattern", "pattern",   "$.tool_input.pattern",   "tool_name = 'Grep'"),
    ("fts_glob_pattern", "pattern",   "$.tool_input.pattern",   "tool_name = 'Glob'"),
    ("fts_url",          "url",       "$.tool_input.url",       "tool_name = 'WebFetch'"),
    ("fts_search_query", "query",     "$.tool_input.query",     "tool_name = 'WebSearch'"),
]

# Backfill batch size in matching rows.  Adjusted after each batch to keep transactions short.
INITIAL_BATCH_ROWS = 2000
MIN_BATCH_ROWS = 250
MAX_BATCH_ROWS = 8000
TARGET_TRANSACTION_SECONDS = 0.5

# Transactions longer than this risk hook events waiting on the lock; report them.
WARN_TRANSACTION_SECONDS = 2.0

# SQLite locks are not fair: a hook waiting in its busy handler retries about every 100ms, so
# back-to-back transactions starve it past its 5 second busy_timeout.  Pause after each
# transaction for at least this long, and at least as long as the transaction held the lock.
MIN_PAUSE_SECONDS = 0.15

# Rows deleted per transaction when emptying a retired FTS table's shadow tables.
DROP_BATCH_ROWS = 2000

STAGING_SUFFIX = "_v3_1_new"
RETIRED_SUFFIX = "_v3_0_old"
STATE_PREFIX = "migration_3_1."


class MigrationError(Exception):
    pass


def with_tool_name_prefix(condition: str, prefix: str) -> str:
    return condition.replace("tool_name", f"{prefix}.tool_name")


def create_table_sql(table: str, column: str) -> str:
    return f"CREATE VIRTUAL TABLE {table} USING fts5({column}, tokenize='trigram')"


def create_triggers_sql(trigger_base: str, table: str, column: str, json_path: str,
                        condition: str) -> list[str]:
    """Insert, delete and update triggers that keep `table` in step with tool_events."""
    new_condition = with_tool_name_prefix(condition, "NEW")
    old_condition = with_tool_name_prefix(condition, "OLD")
    return [
        f"""
        CREATE TRIGGER {trigger_base}_insert
        AFTER INSERT ON tool_events
        WHEN {new_condition}
        BEGIN
            INSERT INTO {table}(rowid, {column})
            VALUES (NEW.id, json_extract(NEW.payload, '{json_path}'));
        END
        """,
        f"""
        CREATE TRIGGER {trigger_base}_delete
        AFTER DELETE ON tool_events
        WHEN {old_condition}
        BEGIN
            DELETE FROM {table} WHERE rowid = OLD.id;
        END
        """,
        f"""
        CREATE TRIGGER {trigger_base}_update
        AFTER UPDATE OF payload ON tool_events
        WHEN ({old_condition}) OR ({new_condition})
        BEGIN
            DELETE FROM {table} WHERE rowid = OLD.id AND {old_condition};
            INSERT INTO {table}(rowid, {column})
            SELECT NEW.id, json_extract(NEW.payload, '{json_path}') WHERE {new_condition};
        END
        """,
    ]


class Migrator:
    def __init__(self, db_path: str, integrity_check: bool = False):
        self.conn = sqlite3.connect(db_path, isolation_level=None, timeout=10)
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.integrity_check = integrity_check
        self.longest_transaction = 0.0

    # --- helpers ---------------------------------------------------------------------------------

    def scalar(self, sql: str, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def exists(self, kind: str, name: str) -> bool:
        return bool(self.scalar(
            "SELECT count(*) FROM sqlite_schema WHERE type = ? AND name = ?", (kind, name)))

    def get_state(self, key: str):
        return self.scalar("SELECT value FROM schema_info WHERE key = ?", (STATE_PREFIX + key,))

    def set_state(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO schema_info (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)", (STATE_PREFIX + key, str(value)))

    def write_transaction(self, label: str, work) -> float:
        """Run `work` in one IMMEDIATE transaction and return its duration.

        Pauses after committing, so a hook waiting on the lock gets it before the next transaction.
        """
        start = time.monotonic()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            work()
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        elapsed = time.monotonic() - start
        self.longest_transaction = max(self.longest_transaction, elapsed)
        if elapsed > WARN_TRANSACTION_SECONDS:
            print(f"   WARNING: {label} held the write lock for {elapsed:.2f}s")
        time.sleep(max(MIN_PAUSE_SECONDS, elapsed))
        return elapsed

    # --- per-table steps -------------------------------------------------------------------------

    def is_migrated(self, table: str) -> bool:
        return self.exists("trigger", f"trg_{table}_delete")

    def stage(self, table: str, column: str, json_path: str, condition: str) -> None:
        staging = table + STAGING_SUFFIX
        if self.exists("table", staging):
            print(f"   resuming: {staging} exists, backfilled to id "
                  f"{self.get_state(table + '.backfilled_to')} of {self.get_state(table + '.watermark')}")
            return

        def work():
            self.conn.execute(create_table_sql(staging, column))
            for sql in create_triggers_sql(f"trg_{staging}", staging, column, json_path, condition):
                self.conn.execute(sql)
            # Taken after the triggers exist, inside the same transaction: every id above the
            # watermark is indexed by the new triggers, every id at or below it by the backfill.
            watermark = self.scalar("SELECT coalesce(max(id), 0) FROM tool_events")
            self.set_state(table + ".watermark", watermark)
            self.set_state(table + ".backfilled_to", 0)

        self.write_transaction(f"staging {table}", work)
        print(f"   created {staging} and its triggers; backfill up to id "
              f"{self.get_state(table + '.watermark')}")

    def backfill(self, table: str, column: str, json_path: str, condition: str) -> None:
        staging = table + STAGING_SUFFIX
        watermark = int(self.get_state(table + ".watermark"))
        batch_rows = INITIAL_BATCH_ROWS
        total = 0
        started = time.monotonic()
        last_report = started

        while True:
            backfilled_to = int(self.get_state(table + ".backfilled_to"))
            if backfilled_to >= watermark:
                break

            def work():
                nonlocal batch_end, inserted
                # Batch end is the id of the batch_rows-th matching row, so each batch covers a
                # bounded number of rows regardless of how sparse the tool is.
                batch_end = self.scalar(
                    f"SELECT id FROM tool_events WHERE {condition} AND id > ? AND id <= ? "
                    f"ORDER BY id LIMIT 1 OFFSET ?",
                    (backfilled_to, watermark, batch_rows - 1)) or watermark
                cursor = self.conn.execute(
                    f"INSERT INTO {staging}(rowid, {column}) "
                    f"SELECT id, json_extract(payload, '{json_path}') FROM tool_events "
                    f"WHERE {condition} AND id > ? AND id <= ?",
                    (backfilled_to, batch_end))
                inserted = cursor.rowcount
                self.set_state(table + ".backfilled_to", batch_end)

            batch_end = 0
            inserted = 0
            elapsed = self.write_transaction(f"backfilling {table}", work)
            total += inserted

            if elapsed > TARGET_TRANSACTION_SECONDS * 2:
                batch_rows = max(MIN_BATCH_ROWS, batch_rows // 2)
            elif elapsed < TARGET_TRANSACTION_SECONDS / 2:
                batch_rows = min(MAX_BATCH_ROWS, batch_rows * 2)

            now = time.monotonic()
            if now - last_report >= 10:
                print(f"   ... {total} rows, up to id {batch_end} of {watermark}")
                last_report = now

        print(f"   backfilled {total} rows in {time.monotonic() - started:.1f}s")

    def verify(self, table: str, column: str, json_path: str, condition: str) -> None:
        """Compare the staged table against tool_events in one read snapshot."""
        staging = table + STAGING_SUFFIX
        self.conn.execute("BEGIN")
        try:
            expected = self.scalar(f"SELECT count(*) FROM tool_events WHERE {condition}")
            actual = self.scalar(f"SELECT count(*) FROM {staging}")
            missing = self.scalar(
                f"SELECT count(*) FROM tool_events WHERE {condition} "
                f"AND id NOT IN (SELECT rowid FROM {staging})")
            orphaned = self.scalar(
                f"SELECT count(*) FROM {staging} "
                f"WHERE rowid NOT IN (SELECT id FROM tool_events WHERE {condition})")
            mismatched = self.scalar(
                f"SELECT count(*) FROM {staging} f JOIN tool_events e ON e.id = f.rowid "
                f"WHERE f.{column} IS NOT json_extract(e.payload, '{json_path}')")
        finally:
            self.conn.execute("COMMIT")

        if (expected, missing, orphaned, mismatched) != (actual, 0, 0, 0):
            raise MigrationError(
                f"{staging} does not match tool_events: expected {expected} rows, found {actual}, "
                f"{missing} missing, {orphaned} orphaned, {mismatched} with different text")
        print(f"   verified: {actual} rows, none missing, orphaned or with different text")

        if self.integrity_check:
            start = time.monotonic()
            self.conn.execute(f"INSERT INTO {staging}({staging}) VALUES ('integrity-check')")
            print(f"   integrity-check ok ({time.monotonic() - start:.2f}s)")

    def swap(self, table: str, column: str, json_path: str, condition: str) -> None:
        staging = table + STAGING_SUFFIX
        retired = table + RETIRED_SUFFIX

        def work():
            for trigger in (f"trg_{table}",
                            f"trg_{staging}_insert", f"trg_{staging}_delete", f"trg_{staging}_update"):
                self.conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            self.conn.execute(f"ALTER TABLE {table} RENAME TO {retired}")
            self.conn.execute(f"ALTER TABLE {staging} RENAME TO {table}")
            for sql in create_triggers_sql(f"trg_{table}", table, column, json_path, condition):
                self.conn.execute(sql)
            self.conn.execute(
                "DELETE FROM schema_info WHERE key IN (?, ?)",
                (STATE_PREFIX + table + ".watermark", STATE_PREFIX + table + ".backfilled_to"))

        elapsed = self.write_transaction(f"swapping {table}", work)
        print(f"   swapped {staging} into place as {table} ({elapsed:.2f}s)")

    def drop_retired(self, table: str) -> None:
        """Drop the retired FTS table without one long transaction.

        A single DROP of a large FTS table frees every page in one transaction, which measured
        over 6 seconds for a 1 GB table.  The retired table has no triggers and is never queried,
        so its shadow tables are emptied first in short batches, leaving little for the DROP.
        """
        retired = table + RETIRED_SUFFIX
        if not self.exists("table", retired):
            return

        started = time.monotonic()
        for shadow in (f"{retired}_data", f"{retired}_content", f"{retired}_docsize"):
            if not self.exists("table", shadow):
                continue
            while True:
                deleted = 0

                def work():
                    nonlocal deleted
                    deleted = self.conn.execute(
                        f"DELETE FROM {shadow} WHERE rowid IN "
                        f"(SELECT rowid FROM {shadow} LIMIT ?)", (DROP_BATCH_ROWS,)).rowcount

                self.write_transaction(f"emptying {shadow}", work)
                if deleted < DROP_BATCH_ROWS:
                    break

        elapsed = self.write_transaction(
            f"dropping {retired}", lambda: self.conn.execute(f"DROP TABLE {retired}"))
        print(f"   dropped {retired} in {time.monotonic() - started:.1f}s "
              f"(final DROP {elapsed:.2f}s)")

    # --- orchestration ---------------------------------------------------------------------------

    def migrate(self) -> None:
        version = self.scalar("SELECT value FROM schema_info WHERE key = 'version'")
        if version is None or not version.startswith("3."):
            raise MigrationError(
                f"expected a 3.x database, found version {version!r}; "
                "run migrate_to_v3.py first")

        for table, column, json_path, condition in FTS_TABLES:
            print(f"\n{table}:")
            if self.is_migrated(table):
                print("   already migrated")
            else:
                self.stage(table, column, json_path, condition)
                self.backfill(table, column, json_path, condition)
                self.verify(table, column, json_path, condition)
                self.swap(table, column, json_path, condition)
            self.drop_retired(table)

        def finish():
            self.conn.execute(
                "UPDATE schema_info SET value = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE key = 'version'", (TARGET_VERSION,))
            self.conn.execute(
                "UPDATE schema_info SET value = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE key = 'description'", (TARGET_DESCRIPTION,))
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_info (key, value) VALUES ('migrated_from', ?)",
                (version,))
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_info (key, value) VALUES ('migration_date', ?)",
                (datetime.now().isoformat(),))

        if version != TARGET_VERSION:
            self.write_transaction("updating schema_info", finish)
        print(f"\nSchema version: {TARGET_VERSION}")

        freelist = self.scalar("PRAGMA freelist_count")
        page_size = self.scalar("PRAGMA page_size")
        print(f"Free pages: {freelist} ({freelist * page_size / 1048576:.1f} MB), "
              f"returned to the disk by PRAGMA incremental_vacuum")
        print(f"Longest write transaction: {self.longest_transaction:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate a tool-monitor database from schema 3.0 to 3.1 (FTS5 tables keyed "
                    "by tool_events.id, with delete and update triggers).  Safe to run while the "
                    "hook is writing, and to rerun after an interruption.")
    parser.add_argument("database")
    parser.add_argument(
        "--integrity-check", action="store_true",
        help="also run FTS5 integrity-check on each new table; holds the write lock long enough "
             "to make hook events fail, so use only on a copy")
    args = parser.parse_args()
    db_path = args.database

    if not Path(db_path).is_file():
        print(f"Database file not found: {db_path}")
        sys.exit(1)

    if sqlite3.sqlite_version_info < MINIMUM_SQLITE:
        print(f"SQLite {sqlite3.sqlite_version} is too old; the trigram tokenizer requires "
              f"{'.'.join(map(str, MINIMUM_SQLITE))}")
        sys.exit(1)

    print(f"=== MIGRATING TO SCHEMA {TARGET_VERSION}: {db_path} (SQLite {sqlite3.sqlite_version}) ===")
    try:
        Migrator(db_path, integrity_check=args.integrity_check).migrate()
    except (MigrationError, sqlite3.Error) as e:
        print(f"\nMigration stopped: {e}")
        print("The database is consistent.  Fix the cause and rerun to resume.")
        sys.exit(1)
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
