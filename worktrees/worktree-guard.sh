#!/bin/bash

# If no restriction set, allow everything
[[ -z "$CLAUDE_ALLOWED_WORKTREE" ]] && exit 0

# Parse CLAUDE_ALLOWED_WORKTREE: "<worktree> [--search=rewrite|filter]"
WORKTREE_NAME="${CLAUDE_ALLOWED_WORKTREE%% --*}"
SEARCH_MODE="filter"  # default
if [[ "$CLAUDE_ALLOWED_WORKTREE" == *"--search=rewrite"* ]]; then
  SEARCH_MODE="rewrite"
elif [[ "$CLAUDE_ALLOWED_WORKTREE" == *"--search=filter"* ]]; then
  SEARCH_MODE="filter"
fi

# Read JSON input
INPUT=$(cat)

# Extract tool name and fields from tool_input
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // ""')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // ""')
PATH_FIELD=$(echo "$INPUT" | jq -r '.tool_input.path // ""')
PATTERN=$(echo "$INPUT" | jq -r '.tool_input.pattern // ""')
COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // ""')
CWD=$(echo "$INPUT" | jq -r '.cwd // ""')

# Determine the path value to check
PATH_VALUE="$FILE_PATH"
[[ -z "$PATH_VALUE" ]] && PATH_VALUE="$PATH_FIELD"
[[ -z "$PATH_VALUE" ]] && PATH_VALUE="$PATTERN"
[[ -z "$PATH_VALUE" ]] && PATH_VALUE="$COMMAND"

# No path? Allow it
[[ -z "$PATH_VALUE" ]] && exit 0

# Find the git directory
# First, check for .git directory, then *.git bare repo directories in cwd
GIT_DIR=""
if [[ -n "$CWD" ]]; then
  # Check for standard .git directory first
  if [[ -d "$CWD/.git" ]]; then
    GIT_DIR="$CWD/.git"
  else
    # Look for *.git bare repo directories
    for d in "$CWD"/*.git; do
      if [[ -d "$d" ]]; then
        GIT_DIR="$d"
        break
      fi
    done
  fi
fi

# If no bare repo found, try git rev-parse from cwd
if [[ -z "$GIT_DIR" && -n "$CWD" ]]; then
  GIT_DIR=$(git -C "$CWD" rev-parse --git-common-dir 2>/dev/null)
fi

# If still no git dir found, allow (not in a git context)
[[ -z "$GIT_DIR" ]] && exit 0

GIT_DIR_NAME=$(basename "$GIT_DIR")

# Build list of forbidden directories (git dir + other worktrees)
FORBIDDEN_DIRS=("$GIT_DIR_NAME")
while IFS= read -r line; do
  if [[ "$line" == worktree\ * ]]; then
    wt="${line#worktree }"
    wt_name=$(basename "$wt")
    # Skip the allowed worktree
    [[ "$wt_name" == "$WORKTREE_NAME" ]] && continue
    # Skip if this is the bare repo itself (already in list)
    [[ "$wt_name" == "$GIT_DIR_NAME" ]] && continue
    FORBIDDEN_DIRS+=("$wt_name")
  fi
done < <(git -C "$GIT_DIR" worktree list --porcelain 2>/dev/null)

# For Glob: if path is empty/cwd, handle broad patterns based on SEARCH_MODE
if [[ "$TOOL_NAME" == "Glob" ]]; then
  if [[ -z "$PATH_FIELD" || "$PATH_FIELD" == "." || "$PATH_FIELD" == "$CWD" ]]; then
    # Broad patterns that would search everything
    if [[ "$PATTERN" == "*" || "$PATTERN" == "**"* || "$PATTERN" == "*/"* ]]; then
      cd "$CWD" 2>/dev/null || exit 0

      # Build grep pattern to exclude forbidden dirs
      EXCLUDE_PATTERN=""
      for forbidden in "${FORBIDDEN_DIRS[@]}"; do
        if [[ -n "$EXCLUDE_PATTERN" ]]; then
          EXCLUDE_PATTERN="$EXCLUDE_PATTERN|^$forbidden(/|$)"
        else
          EXCLUDE_PATTERN="^$forbidden(/|$)"
        fi
      done

      # List top-level items, exclude forbidden dirs, join with commas
      ALLOWED=$(ls -1 2>/dev/null | grep -Ev "$EXCLUDE_PATTERN" | tr '\n' ',' | sed 's/,$//')

      if [[ "$SEARCH_MODE" == "rewrite" ]]; then
        # Mode: rewrite - transform pattern and allow execution
        if [[ "$ALLOWED" == *","* ]]; then
          PREFIX="{$ALLOWED}"
        else
          PREFIX="$ALLOWED"
        fi

        # Construct new pattern
        if [[ "$PATTERN" == "**/"* ]]; then
          NEW_PATTERN="$PREFIX/${PATTERN#\*\*/}"
        elif [[ "$PATTERN" == "*/"* ]]; then
          NEW_PATTERN="$PREFIX/${PATTERN#\*/}"
        elif [[ "$PATTERN" == "*" ]]; then
          NEW_PATTERN="$PREFIX/*"
        else
          NEW_PATTERN="$PREFIX/**/$PATTERN"
        fi

        jq -n --arg pattern "$NEW_PATTERN" '{
          "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": {
              "pattern": $pattern
            }
          }
        }'
      else
        # Mode: filter (default) - block and return list
        REASON="Broad search filtered to allowed paths:"$'\n'"$(echo "$ALLOWED" | tr ',' '\n')"
        jq -n --arg reason "$REASON" '{
          "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": $reason
          }
        }'
      fi
      exit 0
    fi
  fi
fi

# Check if path references any forbidden directory
for forbidden in "${FORBIDDEN_DIRS[@]}"; do
  # Check by name or full path
  if [[ "$PATH_VALUE" == *"/$forbidden/"* ]] || \
     [[ "$PATH_VALUE" == *"/$forbidden" ]] || \
     [[ "$PATH_VALUE" == "$forbidden/"* ]] || \
     [[ "$PATH_VALUE" == "$forbidden" ]] || \
     [[ "$PATH_VALUE" == "$CWD/$forbidden"* ]]; then
    echo "Access denied: '$forbidden' is restricted (only '$WORKTREE_NAME' allowed)" >&2
    exit 2
  fi
done

exit 0
