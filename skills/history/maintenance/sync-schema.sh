#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: sync-schema.sh [--check] [<source-schema>]

Copy the session-transcript JSON Schema into this skill's references/ directory.

The schema is maintained in the claude-code-analysis repository, which derives it from the
Claude Code source and validates it against real transcripts.  This skill ships a verbatim copy
so it works without that repository.  Run this after the upstream schema changes.

Arguments:
  <source-schema>  path to the upstream transcript.schema.json
                   (default: $CLAUDE_CODE_ANALYSIS/analysis/schemas/transcript.schema.json,
                   where CLAUDE_CODE_ANALYSIS defaults to a claude-code-analysis checkout
                   beside this repository)

Options:
  --check     report whether the copy matches the source; exit 1 when it differs
  -h, --help  show this help
USAGE
}

skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(cd "${skill_dir}/../.." && pwd)"
target="${skill_dir}/references/claude-code-session-transcript.schema.json"
analysis_dir="${CLAUDE_CODE_ANALYSIS:-$(dirname "${repo_dir}")/claude-code-analysis}"
source_schema="${analysis_dir}/analysis/schemas/transcript.schema.json"
check=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) check=true; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) source_schema="$1"; shift ;;
  esac
done

if [[ ! -f "${source_schema}" ]]; then
  echo "Source schema not found: ${source_schema}" >&2
  echo "Pass its path, or set CLAUDE_CODE_ANALYSIS to a claude-code-analysis checkout." >&2
  exit 2
fi

schema_version() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("x-schema-version", "?"))' "$1"
}

if cmp -s "${source_schema}" "${target}"; then
  echo "Up to date: x-schema-version $(schema_version "${target}")"
  exit 0
fi

if ${check}; then
  echo "Out of date: skill has $(schema_version "${target}"), source has $(schema_version "${source_schema}")" >&2
  exit 1
fi

python3 -m json.tool "${source_schema}" > /dev/null
old_version="$(schema_version "${target}")"
cp "${source_schema}" "${target}"
echo "Synced: x-schema-version ${old_version} -> $(schema_version "${target}")"
echo "Review with: git -C '${repo_dir}' diff --stat -- '${target}'"
