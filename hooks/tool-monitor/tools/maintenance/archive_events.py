#!/usr/bin/env python
"""
Archive old tool-monitor events into a compressed SQLite file and shrink the live database.

By default the script runs offline: every Claude Code session must be closed, so nothing writes to
the database while it works.  Offline steps:
  1. Copy events older than the cutoff into a new SQLite file, and verify the copy against the
     live database before anything is deleted.
  2. Delete those events in one statement.  The schema 3.1 delete triggers remove their FTS5
     entries.
  3. Fully merge each FTS5 index ('optimize'), dropping the deletion markers the deletes left.
  4. Compact: write the remaining data to a new file with VACUUM INTO, verify it, and swap it in.
     The previous file is kept as tool-monitor-old.sqlite until you delete it.
  5. Compress the archive with zstd, check it decompresses to the same bytes, and remove the
     uncompressed copy.

With --online, the script runs while Claude Code is in use.  Every write is a short transaction:
events are deleted in batches, the FTS5 indexes are merged in bounded steps, and freed pages are
returned to the disk with bounded PRAGMA incremental_vacuum calls, which requires
auto_vacuum=INCREMENTAL.  This is much slower than compacting offline.  With --pause, it also
pauses after each transaction so hooks from other sessions get the lock.

Progress is recorded in the live database's schema_info, so an interrupted run resumes where it
stopped when rerun.

Usage:
    python tools/maintenance/archive_events.py ~/.claude/tool-monitor.sqlite
    python tools/maintenance/archive_events.py --before 2026-10-01 ~/.claude/tool-monitor.sqlite
    python tools/maintenance/archive_events.py --days 60 --archive-dir /Volumes/backup \\
        ~/.claude/tool-monitor.sqlite
    python tools/maintenance/archive_events.py --online ~/.claude/tool-monitor.sqlite
"""

import argparse
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


FTS_TABLES = [
    "fts_bash_command", "fts_file_path", "fts_grep_pattern",
    "fts_glob_pattern", "fts_url", "fts_search_query",
]

# --online: work per transaction.  Each keeps a transaction to about a second or less.
DELETE_BATCH_ROWS = 2000
MERGE_PAGES = 500
VACUUM_PAGES = 2000

# --online: upper bound on merge calls per FTS table.  Each new event indexed while merging adds
# work, so under heavy concurrent writes the merge could otherwise run on indefinitely.  Stopping
# early leaves a valid index that is not fully merged.
MAX_MERGE_CALLS = 2000

# --online: transactions longer than this risk hook events waiting on the lock; report them.
WARN_TRANSACTION_SECONDS = 2.0

# --online --pause: SQLite locks are not fair, and a hook waiting in its busy handler retries about
# every 100ms, so back-to-back transactions can starve it past its 5 second busy_timeout.
PAUSE_SECONDS = 0.2

# Free space required, relative to the estimated size of the files the script writes.
SPACE_MARGIN = 1.2

STATE_KEY = "archive.in_progress"


class ArchiveError(Exception):
    pass


def mib(n: int) -> str:
    return f"{n / 1048576:,.1f} MB"


@contextmanager
def timed(label: str):
    start = time.monotonic()
    yield
    print(f"   {label}: {time.monotonic() - start:.0f}s")


def claude_sessions() -> list[str]:
    """Return 'pid path' for each running Claude Code process.

    Claude Code rewrites its argv, so match the executable path ps reports instead.
    """
    out = subprocess.run(["ps", "-axo", "pid=,comm="], capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines()
            if re.search(r"/claude$|/claude/versions/", line.strip())]


def open_by_others(paths: list[Path]) -> list[str]:
    """Return lsof output lines for any of `paths` held open.  lsof checks one file at a time,
    since it reports failure when any listed file is unopened."""
    held = []
    for path in paths:
        if path.exists():
            result = subprocess.run(["lsof", "--", str(path)], capture_output=True, text=True)
            if result.returncode == 0:
                held.extend(result.stdout.strip().splitlines()[1:])
    return held


def connect(path: Path, cache_mb: int) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute(f"PRAGMA cache_size=-{cache_mb * 1024}")
    return conn


class Archiver:
    def __init__(self, args):
        self.db_path: Path = args.database
        self.new_path = self.db_path.with_name(f"{self.db_path.stem}-new{self.db_path.suffix}")
        self.old_path = self.db_path.with_name(f"{self.db_path.stem}-old{self.db_path.suffix}")
        self.archive_dir: Path = args.archive_dir
        self.days: int = args.days
        self.before: str | None = args.before
        self.online: bool = args.online
        self.pause: bool = args.pause
        self.cache_mb: int = args.cache_mb
        self.conn = connect(self.db_path, self.cache_mb)
        self.longest_transaction = 0.0

    # --- helpers ---------------------------------------------------------------------------------

    def scalar(self, sql: str, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return row[0] if row else None

    def write_transaction(self, label: str, work) -> float:
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
        if self.online and elapsed > WARN_TRANSACTION_SECONDS:
            print(f"   WARNING: {label} held the write lock for {elapsed:.2f}s")
        if self.pause:
            time.sleep(max(PAUSE_SECONDS, elapsed))
        return elapsed

    def get_state(self):
        value = self.scalar("SELECT value FROM schema_info WHERE key = ?", (STATE_KEY,))
        if value is None:
            return None
        path, bound = value.rsplit("|", 1)
        return Path(path), int(bound)

    def set_state(self, archive: Path, bound: int) -> None:
        self.write_transaction("recording progress", lambda: self.conn.execute(
            "INSERT OR REPLACE INTO schema_info (key, value, updated_at) "
            "VALUES (?, ?, CURRENT_TIMESTAMP)", (STATE_KEY, f"{archive}|{bound}")))

    def clear_state(self) -> None:
        self.write_transaction("clearing progress", lambda: self.conn.execute(
            "DELETE FROM schema_info WHERE key = ?", (STATE_KEY,)))

    def file_bytes(self) -> int:
        return os.path.getsize(self.db_path)

    # --- checks ----------------------------------------------------------------------------------

    def check_schema(self) -> None:
        has_delete_trigger = self.scalar(
            "SELECT count(*) FROM sqlite_schema WHERE type = 'trigger' "
            "AND name = 'trg_fts_bash_command_delete'")
        if not has_delete_trigger:
            raise ArchiveError(
                "the database has no FTS delete triggers; run "
                "tools/migration/migrate_to_v3_1.py first")
        if self.online and self.scalar("PRAGMA auto_vacuum") != 2:
            raise ArchiveError(
                "--online needs auto_vacuum=INCREMENTAL to return freed space to the disk; "
                "run offline instead, with every Claude Code session closed")

    def check_no_leftovers(self, resuming: bool) -> None:
        if self.old_path.exists():
            raise ArchiveError(
                f"{self.old_path} exists from an earlier compaction; check the live database "
                f"works, then delete it")
        if self.new_path.exists():
            if not resuming:
                raise ArchiveError(f"{self.new_path} exists; inspect it and remove it first")
            # A partial compaction output from the interrupted run; the live file is untouched.
            for path in (self.new_path, Path(f"{self.new_path}-journal")):
                path.unlink(missing_ok=True)

    # --- steps -----------------------------------------------------------------------------------

    def plan(self):
        """Determine the id bound and archive path for a new run, or None if nothing is old."""
        if self.before:
            cutoff_sql, cutoff = "?", f"{self.before} 00:00:00"
        else:
            cutoff_sql, cutoff = "datetime('now', ?)", f"-{self.days} days"
        bound = self.scalar(
            f"SELECT min(id) FROM tool_events WHERE created_at >= {cutoff_sql}", (cutoff,))
        if bound is None:
            bound = self.scalar("SELECT coalesce(max(id), 0) + 1 FROM tool_events")
        first, last, count = self.conn.execute(
            "SELECT min(created_at), max(created_at), count(*) FROM tool_events WHERE id < ?",
            (bound,)).fetchone()
        if not count:
            return None
        name = f"tool-monitor-archive-{first[:10]}_{last[:10]}.sqlite"
        return self.archive_dir / name, bound, count

    def check_space(self, count: int) -> None:
        total = self.scalar("SELECT count(*) FROM tool_events")
        size = self.file_bytes()
        archive_estimate = int(size * count / total * SPACE_MARGIN)
        needs = {self.archive_dir: archive_estimate}
        if not self.online:
            compact_estimate = int(size * (total - count) / total * SPACE_MARGIN)
            needs[self.db_path.parent] = needs.get(self.db_path.parent, 0) + compact_estimate
        for directory, needed in needs.items():
            free = shutil.disk_usage(directory).free
            print(f"   {directory}: need about {mib(needed)}, {mib(free)} free")
            if free < needed:
                raise ArchiveError(f"not enough free space in {directory}")

    def copy(self, archive: Path, bound: int, count: int) -> None:
        for existing in (archive, Path(f"{archive}.zst"), Path(f"{archive}-journal")):
            if existing.exists():
                raise ArchiveError(f"{existing} already exists; move it aside first")
        print(f"   {count:,} events below id {bound}")
        self.check_space(count)

        # The copy reads the live database and writes only the archive file.
        self.conn.execute("ATTACH ? AS archive", (str(archive),))
        try:
            with timed("copied"):
                self.conn.executescript("""
                    CREATE TABLE archive.tool_events (
                        id INTEGER PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at DATETIME
                    );
                    CREATE TABLE archive.archive_info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                """)
                self.conn.execute(
                    "INSERT INTO archive.tool_events (id, session_id, payload, created_at) "
                    "SELECT id, session_id, payload, created_at FROM main.tool_events "
                    "WHERE id < ?", (bound,))

            with timed("verified the copy"):
                check_sql = ("SELECT count(*), min(id), max(id), sum(id), sum(length(payload)), "
                             "sum(length(session_id)), sum(length(created_at)) "
                             "FROM {}.tool_events")
                source = self.conn.execute(check_sql.format("main") + " WHERE id < ?",
                                           (bound,)).fetchone()
                copied = self.conn.execute(check_sql.format("archive")).fetchone()
                if source != copied:
                    raise ArchiveError(f"archive does not match the source: {source} vs {copied}")

            version = self.scalar("SELECT value FROM main.schema_info WHERE key = 'version'")
            info = {
                "source": str(self.db_path),
                "source_schema_version": version,
                "id_below": str(bound),
                "event_count": str(count),
                "first_created_at": self.scalar("SELECT min(created_at) FROM archive.tool_events"),
                "last_created_at": self.scalar("SELECT max(created_at) FROM archive.tool_events"),
                "archived_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            self.conn.executemany(
                "INSERT INTO archive.archive_info (key, value) VALUES (?, ?)", info.items())
        except BaseException:
            self.conn.execute("DETACH archive")
            archive.unlink(missing_ok=True)
            raise
        self.conn.execute("DETACH archive")
        print(f"   {count:,} events match: counts, ids and payload lengths")

    def check_archive_holds(self, archive: Path, bound: int) -> None:
        """Before deleting, confirm every remaining event below the bound is in the archive."""
        self.conn.execute("ATTACH ? AS archive", (str(archive),))
        try:
            missing = self.scalar(
                "SELECT count(*) FROM main.tool_events m WHERE m.id < ? AND NOT EXISTS "
                "(SELECT 1 FROM archive.tool_events a WHERE a.id = m.id)", (bound,))
        finally:
            self.conn.execute("DETACH archive")
        if missing:
            raise ArchiveError(f"{missing} events below id {bound} are not in {archive}")

    def delete(self, bound: int) -> None:
        start = time.monotonic()
        if not self.online:
            deleted = 0

            def work():
                nonlocal deleted
                deleted = self.conn.execute(
                    "DELETE FROM tool_events WHERE id < ?", (bound,)).rowcount

            self.write_transaction("deleting events", work)
            print(f"   deleted {deleted:,} events: {time.monotonic() - start:.0f}s")
            return

        total = 0
        last_report = start
        while True:
            deleted = 0

            def work():
                nonlocal deleted
                deleted = self.conn.execute(
                    "DELETE FROM tool_events WHERE id IN "
                    "(SELECT id FROM tool_events WHERE id < ? ORDER BY id LIMIT ?)",
                    (bound, DELETE_BATCH_ROWS)).rowcount

            self.write_transaction("deleting events", work)
            total += deleted
            if deleted == 0:
                break
            if time.monotonic() - last_report >= 10:
                print(f"   ... {total:,} deleted")
                last_report = time.monotonic()
        print(f"   deleted {total:,} events: {time.monotonic() - start:.0f}s")

    def merge_fts(self) -> None:
        start = time.monotonic()
        if not self.online:
            for table in FTS_TABLES:
                self.write_transaction(f"optimizing {table}", lambda: self.conn.execute(
                    f"INSERT INTO {table}({table}) VALUES ('optimize')"))
            print(f"   merged FTS indexes: {time.monotonic() - start:.0f}s")
            return

        # A negative page count merges segments at every level, as 'optimize' does, but stops
        # after that many pages, so each call is a bounded transaction.  A call that does no work
        # changes fewer than two rows.
        for table in FTS_TABLES:
            table_start = time.monotonic()
            last_report = table_start
            for calls in range(1, MAX_MERGE_CALLS + 1):
                before = self.conn.total_changes
                self.write_transaction(f"merging {table}", lambda: self.conn.execute(
                    f"INSERT INTO {table}({table}, rank) VALUES ('merge', ?)", (-MERGE_PAGES,)))
                if self.conn.total_changes - before < 2:
                    break
                if time.monotonic() - last_report >= 10:
                    print(f"   ... {table}: {calls} merge steps")
                    last_report = time.monotonic()
            else:
                print(f"   {table}: stopped after {MAX_MERGE_CALLS} merge steps without "
                      f"finishing; the index is valid but not fully merged")
        print(f"   merged FTS indexes: {time.monotonic() - start:.0f}s")

    def vacuum_online(self) -> None:
        start = time.monotonic()
        freed = self.scalar("PRAGMA freelist_count")
        page_size = self.scalar("PRAGMA page_size")
        last_report = start
        while remaining := self.scalar("PRAGMA freelist_count"):
            # incremental_vacuum frees one page per row stepped, so read every row.
            self.write_transaction("incremental_vacuum", lambda: self.conn.execute(
                f"PRAGMA incremental_vacuum({VACUUM_PAGES})").fetchall())
            if time.monotonic() - last_report >= 10:
                print(f"   ... {mib(remaining * page_size)} left")
                last_report = time.monotonic()
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        print(f"   returned {mib(freed * page_size)} to the disk: "
              f"{time.monotonic() - start:.0f}s")

    def compact_offline(self) -> None:
        """Write a compact copy with VACUUM INTO, verify it, and swap it in.

        The live file is untouched until the copy has been verified, and is kept as -old.
        """
        db, new, old = self.db_path, self.new_path, self.old_path
        size_before = self.file_bytes()

        # Close the connection so SQLite folds the WAL into the main file and removes -wal and
        # -shm; a WAL left beside the database would be replayed into the swapped-in file.
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.conn.close()
        for sidecar in (Path(f"{db}-wal"), Path(f"{db}-shm")):
            if sidecar.exists():
                raise ArchiveError(f"{sidecar} remains after closing the database; investigate "
                                   f"before compacting")

        source = connect(db, self.cache_mb)
        try:
            with timed("wrote the compact copy"):
                source.execute("VACUUM INTO ?", (str(new),))

            with timed("verified the compact copy"):
                target = connect(new, self.cache_mb)
                try:
                    checks = {
                        "auto_vacuum": "PRAGMA auto_vacuum",
                        "events": "SELECT count(*), coalesce(max(id), 0) FROM tool_events",
                        "schema objects": "SELECT group_concat(type || ':' || n) FROM "
                                          "(SELECT type, count(*) AS n FROM sqlite_schema "
                                          "GROUP BY type ORDER BY type)",
                    }
                    for label, sql in checks.items():
                        expected = source.execute(sql).fetchone()
                        actual = target.execute(sql).fetchone()
                        if expected != actual:
                            raise ArchiveError(f"compact copy differs in {label}: "
                                               f"{expected} vs {actual}")
                    quick_check = target.execute("PRAGMA quick_check").fetchone()[0]
                    if quick_check != "ok":
                        raise ArchiveError(f"compact copy fails quick_check: {quick_check}")
                finally:
                    target.close()
        except BaseException:
            source.close()
            new.unlink(missing_ok=True)
            raise
        source.close()

        held = open_by_others([db, Path(f"{db}-wal"), Path(f"{db}-shm")])
        if held:
            new.unlink(missing_ok=True)
            raise ArchiveError("the database was opened during compaction:\n" + "\n".join(held))

        os.chmod(new, stat.S_IMODE(os.stat(db).st_mode))
        os.rename(db, old)
        try:
            os.rename(new, db)
        except OSError:
            os.rename(old, db)
            raise
        self.conn = connect(db, self.cache_mb)
        print(f"   swapped in: {mib(size_before)} -> {mib(self.file_bytes())}; "
              f"previous file kept as {old}")

    def compress(self, archive: Path) -> Path:
        compressed = Path(f"{archive}.zst")
        if compressed.exists() and not archive.exists():
            print(f"   already compressed: {compressed}")
            return compressed
        with timed("compressed"):
            subprocess.run(["zstd", "-19", "-T0", "-q", "-f", str(archive), "-o", str(compressed)],
                           check=True)
        with timed("checked decompression"):
            roundtrip = subprocess.run(
                f"zstd -dc '{compressed}' | cmp -s - '{archive}'", shell=True)
            if roundtrip.returncode != 0:
                raise ArchiveError(f"{compressed} does not decompress to {archive}; both kept")
        archive.unlink()
        print(f"   {mib(os.path.getsize(compressed))}")
        return compressed

    # --- orchestration ---------------------------------------------------------------------------

    def run(self) -> None:
        started = time.monotonic()
        self.check_schema()
        size_before = self.file_bytes()

        state = self.get_state()
        self.check_no_leftovers(resuming=state is not None)
        if state:
            archive, bound = state
            print(f"Resuming the archival into {archive} (events below id {bound})")
        else:
            planned = self.plan()
            if planned is None:
                print("No events older than the cutoff; nothing to archive.")
                return
            archive, bound, count = planned
            print(f"\nCopying to {archive}:")
            self.copy(archive, bound, count)
            self.set_state(archive, bound)

        if archive.exists():
            print("\nDeleting archived events from the live database:")
            self.check_archive_holds(archive, bound)
            self.delete(bound)

        print("\nMerging FTS indexes:")
        self.merge_fts()

        if self.online:
            print("\nReturning freed space to the disk:")
            self.vacuum_online()
        else:
            print("\nCompacting:")
            self.compact_offline()

        print("\nCompressing the archive:")
        compressed = self.compress(archive)
        self.clear_state()

        print(f"\nLive database: {mib(size_before)} -> {mib(self.file_bytes())}, "
              f"{self.scalar('SELECT count(*) FROM tool_events'):,} events kept")
        print(f"Archive: {compressed}")
        if self.online:
            print(f"Longest write transaction: {self.longest_transaction:.2f}s")
        print(f"Total: {time.monotonic() - started:.0f}s")
        if self.old_path.exists():
            print(f"\nStart Claude Code, make a tool call, and confirm the hook wrote to the "
                  f"database:\n\n"
                  f"    sqlite3 {self.db_path} \"SELECT count(*), max(created_at) FROM tool_events;\"\n\n"
                  f"Then delete the previous file:\n\n"
                  f"    rm {self.old_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive tool-monitor events older than a cutoff into a zstd-compressed "
                    "SQLite file, then shrink the live database.  Runs offline by default, with "
                    "every Claude Code session closed.  Rerun the same command to resume after an "
                    "interruption.")
    parser.add_argument("database", type=Path)
    cutoff = parser.add_mutually_exclusive_group()
    cutoff.add_argument("--days", type=int, default=30,
                        help="keep events from the last DAYS days (default: 30)")
    cutoff.add_argument("--before", metavar="YYYY-MM-DD",
                        help="archive events created before this date (UTC)")
    parser.add_argument("--archive-dir", type=Path,
                        help="directory for the archive file (default: the database's directory)")
    parser.add_argument("--online", action="store_true",
                        help="run while Claude Code is in use, in short transactions; much slower, "
                             "and needs auto_vacuum=INCREMENTAL")
    parser.add_argument("--pause", action="store_true",
                        help="with --online, pause after each transaction so hooks from other "
                             "sessions get the lock")
    parser.add_argument("--ignore-sessions", action="store_true",
                        help="skip the check for running Claude Code sessions, for a database "
                             "the hook does not write to, such as a copy")
    parser.add_argument("--cache-mb", type=int, default=1024,
                        help="SQLite page cache in MB (default: 1024)")
    args = parser.parse_args()

    if args.pause and not args.online:
        parser.error("--pause applies only with --online")
    if args.before:
        # The cutoff is compared as text against created_at, so it must be exactly YYYY-MM-DD.
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.before):
                raise ValueError
            datetime.strptime(args.before, "%Y-%m-%d")
        except ValueError:
            parser.error(f"--before must be a date as YYYY-MM-DD, not {args.before!r}")

    args.database = args.database.expanduser().resolve()
    if not args.database.is_file():
        print(f"Database file not found: {args.database}")
        sys.exit(1)
    args.archive_dir = (args.archive_dir or args.database.parent).expanduser().resolve()
    if not args.archive_dir.is_dir():
        print(f"Archive directory not found: {args.archive_dir}")
        sys.exit(1)
    if shutil.which("zstd") is None:
        print("zstd is not on PATH")
        sys.exit(1)

    if not args.online and not args.ignore_sessions:
        sessions = claude_sessions()
        if sessions:
            print("Claude Code sessions are running:")
            for session in sessions:
                print(f"  {session}")
            print("Close them all and rerun, or use --online to archive while they run.")
            sys.exit(1)
        held = open_by_others([args.database, Path(f"{args.database}-wal"),
                               Path(f"{args.database}-shm")])
        if held:
            print("The database is held open:")
            for line in held:
                print(f"  {line}")
            sys.exit(1)

    mode = "online" if args.online else "offline"
    keep = f"archiving before {args.before}" if args.before else f"keeping {args.days} days"
    print(f"=== ARCHIVING {args.database} ({keep}, {mode}) ===")
    try:
        Archiver(args).run()
    except (ArchiveError, sqlite3.Error, subprocess.CalledProcessError, OSError) as e:
        print(f"\nArchival stopped: {e}")
        print("Rerun the same command to resume.")
        sys.exit(1)
    print("\nArchival complete.")


if __name__ == "__main__":
    main()
