#!/usr/bin/env bash

# Emit JSON describing a failed GitHub Actions workflow run.
#
# Usage: ci-failure-context.sh <run-id> [owner/repo]
#
# If owner/repo is omitted, it's inferred from the current working directory's
# git remote via `gh repo view`.
#
# Shape of the JSON (stdout):
#   {
#     "run_id":    "<id>",
#     "run_url":   "<url>",
#     "workflow":  "<workflow name>",
#     "branch":    "<head branch>",
#     "head_sha":  "<full SHA>",
#     "conclusion":"<failure|cancelled|...>",
#     "failed_jobs": [
#       {
#         "id":           <number>,
#         "name":         "<job name>",
#         "url":          "<job url>",
#         "failed_step":  "<step name or empty>",
#         "annotations":  [{"path","line","level","message"}...],
#         "log_tail":     "<last N lines of the failed log>"
#       },
#       ...
#     ]
#   }
#
# Requires: gh (authenticated), jq.

set -euo pipefail

log_tail_lines=40

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <run-id> [owner/repo]" >&2
  exit 64
fi

run_id="$1"
repo="${2:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}"

repo_args=(--repo "$repo")

run_json="$(gh run view "$run_id" "${repo_args[@]}" \
  --json databaseId,headBranch,headSha,workflowName,url,conclusion,jobs)"

resolved_repo="$(jq -r '.url' <<<"$run_json" \
  | sed -E 's|https://github.com/([^/]+/[^/]+)/.*|\1|')"

failed_jobs_json="$(jq -c '.jobs[] | select(.conclusion == "failure")' <<<"$run_json")"

failed_array='[]'
while IFS= read -r job; do
  [[ -z "$job" ]] && continue

  job_id="$(jq -r '.databaseId' <<<"$job")"
  job_name="$(jq -r '.name' <<<"$job")"
  job_url="$(jq -r '.url' <<<"$job")"
  failed_step="$(jq -r '[.steps[] | select(.conclusion == "failure") | .name] | first // ""' <<<"$job")"

  annotations="$(
    gh api "repos/$resolved_repo/check-runs/$job_id/annotations" \
      --jq '[.[] | {path, line: .start_line, level: .annotation_level, message}]' \
      2>/dev/null || echo '[]'
  )"

  log_tail="$(
    gh run view "$run_id" "${repo_args[@]}" --log-failed --job "$job_id" 2>/dev/null \
      | tail -n "$log_tail_lines" || true
  )"

  job_entry="$(jq -n \
    --argjson id "$job_id" \
    --arg name "$job_name" \
    --arg url "$job_url" \
    --arg failed_step "$failed_step" \
    --argjson annotations "$annotations" \
    --arg log_tail "$log_tail" \
    '{id: $id, name: $name, url: $url, failed_step: $failed_step, annotations: $annotations, log_tail: $log_tail}')"

  failed_array="$(jq --argjson entry "$job_entry" '. + [$entry]' <<<"$failed_array")"
done <<<"$failed_jobs_json"

jq -n \
  --arg run_id "$run_id" \
  --argjson run "$run_json" \
  --argjson failed_jobs "$failed_array" \
  '{
    run_id:      $run_id,
    run_url:     $run.url,
    workflow:    $run.workflowName,
    branch:      $run.headBranch,
    head_sha:    $run.headSha,
    conclusion:  $run.conclusion,
    failed_jobs: $failed_jobs
  }'
