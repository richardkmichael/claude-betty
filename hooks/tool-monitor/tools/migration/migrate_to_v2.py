#!/usr/bin/env python
"""
Migrate existing tool-monitor database from Schema V1 to Schema V2.

This script safely transforms databases from the old Bash-focused schema 
to the new minimal + schema-as-data design.

Usage:
    python tools/migration/migrate_to_v2.py /path/to/database.db
    python tools/migration/migrate_to_v2.py /path/to/database.db --force
"""

import sqlite3
import sys
import os
import shutil
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List


def backup_database(db_path: str) -> str:
    """Create a backup of the database before migration."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.v1_backup_{timestamp}"
    
    print(f"Creating backup: {backup_path}")
    shutil.copy2(db_path, backup_path)
    
    return backup_path


def detect_schema_version(db_path: str) -> Optional[str]:
    """Detect current schema version."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check for schema_info table (V2)
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='schema_info'
        """)
        
        if cursor.fetchone():
            cursor.execute("SELECT value FROM schema_info WHERE key='version'")
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else "2.0.0"
        
        # Check for V1 characteristics
        cursor.execute("""
            SELECT sql FROM sqlite_master 
            WHERE type='table' AND name='tool_events'
        """)
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            return None
            
        table_sql = result[0]
        
        # V1 has tool-specific generated columns
        if any(field in table_sql for field in ["command TEXT", "description TEXT", "command_word TEXT"]):
            conn.close()
            return "1.x"
        
        conn.close()
        return "unknown"
        
    except sqlite3.Error as e:
        print(f"Error detecting schema: {e}")
        return None


def get_tool_categories() -> Dict[str, str]:
    """Map tools to their categories."""
    return {
        "Bash": "System",
        "Edit": "File", 
        "MultiEdit": "File",
        "Write": "File",
        "Read": "File",
        "LS": "File",
        "Glob": "Search",
        "Grep": "Search", 
        "Task": "Workflow",
        "TodoWrite": "Workflow",
        "ExitPlanMode": "Workflow",
        "WebSearch": "Web",
        "WebFetch": "Web",
        "NotebookRead": "File"
    }


def load_schema_files(schema_dir: str = "hook_schemas") -> List[Dict]:
    """Load all JSON schema files from the schema directory."""
    schema_files = []
    schema_path = Path(schema_dir)
    
    if not schema_path.exists():
        print(f"⚠️  Schema directory not found: {schema_dir}")
        return []
    
    categories = get_tool_categories()
    
    # Find all JSON schema files
    for schema_file in schema_path.glob("*.json"):
        if schema_file.name == "index.json":
            continue  # Skip index file
            
        try:
            # Parse filename: {toolname}-{hook_event}.json
            name_parts = schema_file.stem.split("-")
            if len(name_parts) < 2:
                continue
                
            # Reconstruct tool name and hook event
            hook_part = name_parts[-1]  # pre_tool_use or post_tool_use
            tool_parts = name_parts[:-1]  # everything before the hook
            
            tool_name = "".join(word.capitalize() for word in tool_parts)
            
            if hook_part == "pre_tool_use":
                hook_event = "PreToolUse"
            elif hook_part == "post_tool_use":
                hook_event = "PostToolUse"
            elif hook_part == "post_tool_use_failure":
                hook_event = "PostToolUseFailure"
            elif hook_part == "permission_request":
                hook_event = "PermissionRequest"
            else:
                continue
            
            # Load schema content
            with open(schema_file, 'r') as f:
                schema_content = f.read()
                # Validate it's valid JSON
                json.loads(schema_content)
            
            schema_files.append({
                "tool_name": tool_name,
                "hook_event": hook_event,
                "schema_json": schema_content,
                "category": categories.get(tool_name, "Unknown"),
                "file_path": str(schema_file)
            })
            
        except (json.JSONDecodeError, IOError) as e:
            print(f"⚠️  Failed to load {schema_file}: {e}")
            continue
    
    return schema_files


def create_schema_tables(cursor: sqlite3.Cursor):
    """Create the schema-as-data tables."""
    
    # Create tool_schemas table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tool_schemas (
            tool_name TEXT NOT NULL,
            hook_event TEXT NOT NULL,        -- 'PreToolUse' or 'PostToolUse'
            schema_version TEXT NOT NULL,
            schema_json TEXT NOT NULL,       -- Complete JSON Schema document
            category TEXT,                   -- File, Search, System, Web, Workflow
            description TEXT,                -- Human-readable description
            common_fields TEXT,              -- JSON array of frequently analyzed field paths
            
            -- Generated filename using our naming convention
            file_name TEXT GENERATED ALWAYS AS (
                lower(tool_name) || '-' || lower(replace(hook_event, 'ToolUse', '_tool_use')) || '.json'
            ) STORED,
            
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (tool_name, hook_event, schema_version)
        )
    """)
    
    # Create schema_versions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_versions (
            tool_name TEXT NOT NULL,
            hook_event TEXT NOT NULL,        -- 'PreToolUse' or 'PostToolUse'
            version TEXT NOT NULL,
            active_from DATE NOT NULL,       -- When this schema version became active
            active_to DATE,                  -- When it was superseded (NULL = current)
            notes TEXT,                      -- Migration notes, breaking changes, etc.
            PRIMARY KEY (tool_name, hook_event, version),
            FOREIGN KEY (tool_name, hook_event, version) REFERENCES tool_schemas(tool_name, hook_event, schema_version)
        )
    """)
    
    # Create index for schema lookups
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_schema_active 
        ON schema_versions(tool_name, hook_event, active_from, active_to)
    """)


def populate_schema_tables(cursor: sqlite3.Cursor, schema_files: List[Dict]):
    """Populate the schema tables with loaded schema files."""
    
    migration_date = datetime.now().date().isoformat()
    schema_version = "1.0.0"  # Initial schema version
    
    for schema_data in schema_files:
        tool_name = schema_data["tool_name"]
        hook_event = schema_data["hook_event"]
        
        # Generate description from schema content
        try:
            schema_obj = json.loads(schema_data["schema_json"])
            description = schema_obj.get("description", f"JSON Schema for {tool_name} {hook_event}")
        except:
            description = f"JSON Schema for {tool_name} {hook_event}"
        
        # Extract common fields from schema (simplified approach)
        common_fields = json.dumps([
            "$.session_id",
            "$.hook_event_name", 
            "$.tool_name",
            "$.cwd",
            "$.transcript_path"
        ])
        
        # Insert into tool_schemas
        cursor.execute("""
            INSERT OR REPLACE INTO tool_schemas 
            (tool_name, hook_event, schema_version, schema_json, category, description, common_fields)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            tool_name,
            hook_event, 
            schema_version,
            schema_data["schema_json"],
            schema_data["category"],
            description,
            common_fields
        ))
        
        # Insert into schema_versions (mark as current active version)
        cursor.execute("""
            INSERT OR REPLACE INTO schema_versions
            (tool_name, hook_event, version, active_from, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (
            tool_name,
            hook_event,
            schema_version, 
            migration_date,
            f"Initial schema loaded during V2 migration from {schema_data['file_path']}"
        ))


def migrate_to_v2(db_path: str, force: bool = False) -> bool:
    """Migrate database from V1 to V2 schema."""
    
    # Detect current version
    current_version = detect_schema_version(db_path)
    
    if current_version is None:
        print("❌ Could not detect database schema version")
        return False
    
    if current_version.startswith("2."):
        print(f"✅ Database is already V2 (version {current_version})")
        return True
    
    if current_version != "1.x" and not force:
        print(f"⚠️  Unknown schema version: {current_version}")
        print("Use --force to proceed anyway")
        return False
    
    print(f"🔄 Migrating from schema {current_version} to V2...")
    
    # Create backup
    backup_path = backup_database(db_path)
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Begin transaction
        cursor.execute("BEGIN TRANSACTION")
        
        # Step 1: Create new V2 tables
        print("Creating V2 schema tables...")
        
        # Create schema_info table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_info (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Insert schema version
        cursor.execute("""
            INSERT OR REPLACE INTO schema_info (key, value) 
            VALUES ('version', '2.0.0')
        """)
        cursor.execute("""
            INSERT OR REPLACE INTO schema_info (key, value) 
            VALUES ('description', 'Minimal schema with schema-as-data design')
        """)
        cursor.execute("""
            INSERT OR REPLACE INTO schema_info (key, value) 
            VALUES ('migrated_from', ?)
        """, (current_version,))
        cursor.execute("""
            INSERT OR REPLACE INTO schema_info (key, value) 
            VALUES ('migration_date', ?)
        """, (datetime.now().isoformat(),))
        
        # Step 2: Backup existing data
        print("Backing up existing tool_events data...")
        cursor.execute("""
            CREATE TEMPORARY TABLE tool_events_backup AS 
            SELECT id, session_id, payload, created_at FROM tool_events
        """)
        
        # Step 3: Drop old table and recreate with V2 schema
        print("Recreating tool_events table with V2 schema...")
        cursor.execute("DROP TABLE tool_events")
        
        # Create V2 tool_events table (only stable hook contract fields)
        cursor.execute("""
            CREATE TABLE tool_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                
                -- Stable hook contract fields only (guaranteed by Claude Code)
                hook_event_name TEXT GENERATED ALWAYS AS (json_extract(payload, '$.hook_event_name')) STORED,
                tool_name TEXT GENERATED ALWAYS AS (json_extract(payload, '$.tool_name')) STORED,
                cwd TEXT GENERATED ALWAYS AS (json_extract(payload, '$.cwd')) STORED,
                transcript_path TEXT GENERATED ALWAYS AS (json_extract(payload, '$.transcript_path')) STORED,
                
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Step 4: Restore data
        print("Restoring event data...")
        cursor.execute("""
            INSERT INTO tool_events (id, session_id, payload, created_at)
            SELECT id, session_id, payload, created_at FROM tool_events_backup
        """)
        
        # Step 5: Create V2 indexes
        print("Creating V2 indexes...")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_session_id ON tool_events(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tool_name ON tool_events(tool_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hook_event_name ON tool_events(hook_event_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_created_at ON tool_events(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_cwd ON tool_events(cwd)")
        
        # Step 6: Create schema-as-data tables
        print("Creating schema-as-data tables...")
        create_schema_tables(cursor)
        
        # Step 7: Load schema files into database
        print("Loading JSON Schema files into database...")
        schema_files = load_schema_files()
        if schema_files:
            populate_schema_tables(cursor, schema_files)
            print(f"   Loaded {len(schema_files)} schema files")
        else:
            print("   ⚠️  No schema files found - schema-as-data tables created but empty")
        
        # Step 8: Verify migration
        cursor.execute("SELECT COUNT(*) FROM tool_events")
        event_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM tool_events_backup")
        backup_count = cursor.fetchone()[0]
        
        if event_count != backup_count:
            raise Exception(f"Data loss detected: {backup_count} → {event_count}")
        
        # Get schema count for reporting
        cursor.execute("SELECT COUNT(*) FROM tool_schemas")
        schema_count = cursor.fetchone()[0]
        
        # Commit transaction
        cursor.execute("COMMIT")
        conn.close()
        
        print(f"✅ Migration completed successfully!")
        print(f"   Events migrated: {event_count}")
        print(f"   Schemas loaded: {schema_count}")
        print(f"   Backup created: {backup_path}")
        print(f"   Schema version: 2.0.0")
        
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        print("Rolling back changes...")
        
        try:
            cursor.execute("ROLLBACK")
            conn.close()
        except:
            pass
        
        # Restore from backup
        print(f"Restoring from backup: {backup_path}")
        shutil.copy2(backup_path, db_path)
        
        return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python migrate_to_v2.py <database.db> [--force]")
        print()
        print("This tool migrates a tool-monitor database from V1 to V2 schema.")
        print("A backup is automatically created before migration.")
        print()
        print("Options:")
        print("  --force    Proceed with migration even if schema version is unknown")
        sys.exit(1)
    
    db_path = sys.argv[1]
    force = "--force" in sys.argv
    
    if not os.path.exists(db_path):
        print(f"❌ Database file not found: {db_path}")
        sys.exit(1)
    
    print(f"=== MIGRATING TO SCHEMA V2: {db_path} ===")
    print()
    
    success = migrate_to_v2(db_path, force)
    
    if success:
        print()
        print("🎉 Migration completed successfully!")
        print("You can now use the V2 tools:")
        print(f"  python tools/validation/check_schema_version.py {db_path}")
    else:
        print()
        print("💥 Migration failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()