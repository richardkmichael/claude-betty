#!/usr/bin/env python
"""
Validate tool payloads against JSON schemas using uvx check-jsonschema.
This provides proper JSON Schema validation without dependencies.
"""

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

def connect_to_db(db_path: str) -> sqlite3.Connection:
    """Connect to the SQLite database."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def get_tool_usage_stats(conn: sqlite3.Connection) -> Dict[str, int]:
    """Get tool usage statistics.  Session, subagent and stop events have no tool, so they are
    left out."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT tool_name, COUNT(*) as count
        FROM tool_events
        WHERE tool_name IS NOT NULL
        GROUP BY tool_name
        ORDER BY count DESC
    """)
    
    return {row['tool_name']: row['count'] for row in cursor.fetchall()}

def extract_sample_payload(conn: sqlite3.Connection, tool_name: str, hook_event: str) -> str:
    """Extract a sample payload for validation."""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT payload 
        FROM tool_events 
        WHERE tool_name = ? AND hook_event_name = ?
        ORDER BY created_at DESC 
        LIMIT 1
    """, (tool_name, hook_event))
    
    row = cursor.fetchone()
    return row['payload'] if row else None

_HOOK_EVENT_SUFFIX = {
    "PreToolUse":         "pre_tool_use",
    "PostToolUse":        "post_tool_use",
    "PostToolUseFailure": "post_tool_use_failure",
    "PermissionRequest":  "permission_request",
}

def generate_schema_filename(tool_name: str, hook_event: str) -> str:
    """Generate schema filename using our naming convention."""
    hook_snake = _HOOK_EVENT_SUFFIX.get(hook_event, hook_event.lower())
    tool_lower = tool_name.lower().replace(" ", "_")
    return f"{tool_lower}-{hook_snake}.json"

def validate_with_uvx(schema_file: str, data_file: str) -> Tuple[bool, str]:
    """Validate data against schema using uvx check-jsonschema."""
    try:
        result = subprocess.run(
            ["uvx", "check-jsonschema", "--schemafile", schema_file, data_file],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        success = result.returncode == 0
        output = result.stdout + result.stderr if result.stderr else result.stdout
        
        return success, output.strip()
        
    except subprocess.TimeoutExpired:
        return False, "Validation timed out"
    except subprocess.CalledProcessError as e:
        return False, f"uvx error: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

def validate_all_tools(db_path: str, schema_dir: str = "schemas"):
    """Validate all tools using uvx check-jsonschema."""
    
    conn = connect_to_db(db_path)
    
    print("=== JSON SCHEMA VALIDATION WITH UVX ===\n")
    
    # Get tool usage stats
    usage_stats = get_tool_usage_stats(conn)
    print("Tool Usage Statistics:")
    for tool_name, count in usage_stats.items():
        print(f"  {tool_name}: {count} events")
    print()
    
    validation_results = {
        "total_tools": 0,
        "passed": 0,
        "failed": 0,
        "errors": []
    }
    
    for tool_name, count in usage_stats.items():
        print(f"## Validating {tool_name} ({count} events)")
        
        for hook_event in ["PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionRequest"]:
            validation_results["total_tools"] += 1
            
            # Generate schema filename
            schema_filename = generate_schema_filename(tool_name, hook_event)
            schema_path = Path(schema_dir) / schema_filename
            
            print(f"  {hook_event}: {schema_filename}")
            
            # Check if schema file exists
            if not schema_path.exists():
                print(f"    ❌ Schema file not found: {schema_path}")
                validation_results["failed"] += 1
                validation_results["errors"].append(f"{tool_name} {hook_event}: Schema file missing")
                continue
            
            # Extract sample payload
            payload = extract_sample_payload(conn, tool_name, hook_event)
            if not payload:
                print(f"    ⚠️  No sample data found")
                continue
            
            # Create temporary file for payload
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_file:
                temp_file.write(payload)
                temp_path = temp_file.name
            
            try:
                # Validate with uvx
                success, output = validate_with_uvx(str(schema_path), temp_path)
                
                if success:
                    print(f"    ✅ Validation passed")
                    validation_results["passed"] += 1
                else:
                    print(f"    ❌ Validation failed: {output}")
                    validation_results["failed"] += 1
                    validation_results["errors"].append(f"{tool_name} {hook_event}: {output}")
            
            finally:
                # Clean up temp file
                Path(temp_path).unlink(missing_ok=True)
        
        print()
    
    # Summary
    print("=== VALIDATION SUMMARY ===")
    print(f"Total validations: {validation_results['total_tools']}")
    print(f"✅ Passed: {validation_results['passed']}")
    print(f"❌ Failed: {validation_results['failed']}")
    
    if validation_results["errors"]:
        print("\nErrors:")
        for error in validation_results["errors"]:
            print(f"  - {error}")
    
    if validation_results["failed"] == 0:
        print("\n🎉 All validations passed! Schemas are correct.")
    else:
        print(f"\n⚠️  {validation_results['failed']} validations failed.")
    
    conn.close()
    return validation_results

def validate_single_tool(db_path: str, tool_name: str, hook_event: str, schema_dir: str = "schemas"):
    """Validate a single tool/hook combination."""
    
    conn = connect_to_db(db_path)
    
    schema_filename = generate_schema_filename(tool_name, hook_event)
    schema_path = Path(schema_dir) / schema_filename
    
    print(f"Validating {tool_name} {hook_event}")
    print(f"Schema: {schema_path}")
    
    if not schema_path.exists():
        print(f"❌ Schema file not found: {schema_path}")
        return False
    
    payload = extract_sample_payload(conn, tool_name, hook_event)
    if not payload:
        print("⚠️  No sample data found")
        return False
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as temp_file:
        temp_file.write(payload)
        temp_path = temp_file.name
    
    try:
        success, output = validate_with_uvx(str(schema_path), temp_path)
        
        if success:
            print("✅ Validation passed")
        else:
            print(f"❌ Validation failed: {output}")
        
        return success
    
    finally:
        Path(temp_path).unlink(missing_ok=True)
        conn.close()

if __name__ == "__main__":
    db_path = "tmp/tool-monitor.sqlite"
    schema_dir = "hook_schemas"  # Default to our schema directory
    
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    if len(sys.argv) > 2:
        schema_dir = sys.argv[2]
    
    if len(sys.argv) == 5:
        # Single tool validation: python validate_against_schemas.py db_path schema_dir tool_name hook_event
        validate_single_tool(sys.argv[1], sys.argv[3], sys.argv[4], sys.argv[2])
    elif len(sys.argv) == 4:
        # Single tool validation with default schema dir: python validate_against_schemas.py db_path tool_name hook_event
        validate_single_tool(sys.argv[1], sys.argv[2], sys.argv[3], schema_dir)
    else:
        # Validate all tools
        validate_all_tools(db_path, schema_dir)