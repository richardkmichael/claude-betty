#!/usr/bin/env bash
#
# Report fixup commits on the review branch that will not fold as intended.
#
# Usage: check-fixup-targets.sh <base> <pr-head>
#
#   <base>     the commit the author's branch was cut from
#   <pr-head>  the head of the author's branch this review was written against
#
# Run from the review branch. Silence, and exit 0, means every fixup resolves to a
# commit on the author's branch. What it reports:
#
#   no target  the root subject matches no commit on the author's branch, so
#              --autosquash folds it nowhere and it survives the rebase as a stray
#   chained    the fixup targets another fixup rather than the root. It still
#              folds into the root, but it was written on top of another fix, so
#              the two likely need a pick-order dependency recorded in the plan.
#
# Matching is by subject, because that is what --autosquash matches on: the target
# SHA passed to `git commit --fixup` is not recorded anywhere in the commit.

set -u

if [[ $# -ne 2 ]]; then
    echo "usage: ${0##*/} <base> <pr-head>" >&2
    exit 2
fi
base=$1 pr_head=$2

# Resolve both refs up front. Without this a mistyped base yields an empty commit
# range, which reads exactly like a clean result.
for ref in "$base" "$pr_head"; do
    git rev-parse --verify --quiet "$ref^{commit}" >/dev/null || {
        echo "${0##*/}: not a commit: $ref" >&2
        exit 2
    }
done

targets=$(git log --format=%s "$base".."$pr_head") || exit 1

problems=0
while read -r sha subject; do
    # Strip every fixup!/squash!/amend! prefix to reach the root subject. Matching
    # only the first one reports each chained fixup as targetless when it is not.
    root=$subject
    depth=0
    while :; do
        case $root in
            "fixup! "*|"squash! "*|"amend! "*) root=${root#* }; depth=$((depth + 1)) ;;
            *) break ;;
        esac
    done

    [[ "$depth" -eq 0 ]] && continue

    if ! grep -Fxq "$root" <<<"$targets"; then
        echo "$sha  no target on the author's branch: $subject"
        problems=$((problems + 1))
    elif [[ "$depth" -gt 1 ]]; then
        echo "$sha  chained; retarget at the root: $subject"
        problems=$((problems + 1))
    fi
done < <(git log --reverse --format='%h %s' "$base"..HEAD)

[[ "$problems" -eq 0 ]]
