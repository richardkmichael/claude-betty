#!/usr/bin/env python
"""
Check the database schema version and compatibility.

This tool helps identify what schema version a database is using
and whether it's compatible with the current tools.

Usage:
    python tools/validation/check_schema_version.py monitor.db
"""

import sqlite3
import sys
from typing import Optional, Dict

def get_schema_version(db_path: str) -> Optional[Dict[str, str]]:
    """Get schema version information from database."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if schema_info table exists
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name='schema_info'
        """)
        
        if not cursor.fetchone():
            conn.close()
            return None
        
        # Get all schema info
        cursor.execute("SELECT key, value FROM schema_info")
        schema_info = dict(cursor.fetchall())
        
        conn.close()
        return schema_info
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None

def detect_legacy_schema(db_path: str) -> Optional[str]:
    """Detect legacy schema version by analyzing table structure."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if tool_events table exists
        cursor.execute("""
            SELECT sql FROM sqlite_master 
            WHERE type='table' AND name='tool_events'
        """)
        
        result = cursor.fetchone()
        if not result:
            conn.close()
            return "unknown"
        
        table_sql = result[0]
        
        # Check for V1 characteristics (tool-specific columns)
        if any(field in table_sql for field in ["command TEXT", "description TEXT", "command_word TEXT"]):
            version = "1.x (legacy)"
        elif "schema_info" not in table_sql:
            version = "2.0 (no version tracking)"
        else:
            version = "unknown"
        
        conn.close()
        return version
        
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return None

def check_database_schema(db_path: str):
    """Check and report database schema version."""
    print(f"=== SCHEMA VERSION CHECK: {db_path} ===\n")
    
    # Try to get explicit version first
    schema_info = get_schema_version(db_path)
    
    if schema_info:
        print("✅ Schema version found:")
        for key, value in schema_info.items():
            print(f"   {key}: {value}")
        
        version = schema_info.get('version', 'unknown')
        description = schema_info.get('description', 'No description')
        
        print(f"\n📋 Current schema: {version}")
        print(f"📝 Description: {description}")
        
        # Check compatibility
        if version.startswith('3.'):
            print("\n✅ Compatible with current tools (V3)")
            print("   - tools/migration/migrate_to_v3.py: Not needed")
        elif version.startswith('2.'):
            print("\n⚠️  V2 schema — migration available")
            print("   - tools/migration/migrate_to_v3.py: Run to upgrade to V3")
        else:
            print(f"\n⚠️  Unknown version: {version}")
            print("   Schema compatibility uncertain")
    
    else:
        print("❌ No explicit schema version found")
        
        # Try to detect legacy version
        legacy_version = detect_legacy_schema(db_path)
        
        if legacy_version:
            print(f"🔍 Detected schema: {legacy_version}")
            
            if legacy_version == "1.x (legacy)":
                print("\n📋 This appears to be a V1 database")
                print("🔧 Migration available:")
                print("   python tools/migration/migrate_to_v2.py " + db_path)
                print("\n⚠️  Current tools may not work correctly with V1 schema")
                
            elif legacy_version == "2.0 (no version tracking)":
                print("\n📋 This appears to be an early V2 database without version tracking")
                print("✅ Likely compatible with current tools")
                print("💡 Consider adding schema_info table for better version tracking")
            
        else:
            print("❌ Could not determine schema version")
            print("   Database may be corrupted or not a tool-monitor database")

def get_table_info(db_path: str):
    """Show basic table information for debugging."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print("\n=== TABLE INFORMATION ===")
        
        # List all tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        
        print(f"Tables: {', '.join(tables)}")
        
        # Show tool_events structure if it exists
        if 'tool_events' in tables:
            cursor.execute("PRAGMA table_info(tool_events)")
            columns = cursor.fetchall()
            
            print("\ntool_events columns:")
            for col in columns:
                col_name, col_type = col[1], col[2]
                print(f"   {col_name}: {col_type}")
        
        # Show record count
        if 'tool_events' in tables:
            cursor.execute("SELECT COUNT(*) FROM tool_events")
            count = cursor.fetchone()[0]
            print(f"\nRecord count: {count}")
        
        conn.close()
        
    except sqlite3.Error as e:
        print(f"Error getting table info: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python check_schema_version.py <database.db>")
        print()
        print("This tool checks the schema version of a tool-monitor database")
        print("and provides compatibility information for current tools.")
        sys.exit(1)
    
    db_path = sys.argv[1]
    
    check_database_schema(db_path)
    
    if "--verbose" in sys.argv:
        get_table_info(db_path)