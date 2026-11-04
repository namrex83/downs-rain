#!/usr/bin/env bash
# The published site (index.html, assets/ and data/) lives on the gh-pages branch as a single commit that is
# replaced on every update, so old maps and forecasts don't pile up in the repository's history.
#
#   bash .github/pages.sh get           clone the published site into site/ and link data -> site/data,
#                                       so the scripts read and write the published data as before
#   bash .github/pages.sh put "message" publish: the latest gh-pages, plus index.html and assets/ from main,
#                                       plus every data file this job wrote or deleted
#
# Several jobs (radar, storms, forecast, site) publish; each only adds its own changes on top of the latest
# gh-pages and pushes with --force-with-lease, retrying if another job published in between.
set -euo pipefail
REPO_URL="${PAGES_REPO_URL:-https://x-access-token:${GITHUB_TOKEN:-}@github.com/${GITHUB_REPOSITORY:-}.git}"
export GIT_AUTHOR_NAME="radar-bot" GIT_AUTHOR_EMAIL="radar-bot@users.noreply.github.com"
export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"

case "${1:-}" in
get)
  rm -rf site
  git clone -q --depth 1 --branch gh-pages --single-branch "$REPO_URL" site
  mkdir -p site/data
  rm -rf data && ln -s site/data data
  sleep 1; touch .jobstart                                  # files changed after this are this job's
  (cd site && find data -type f | sort) > .startlist
  ;;
put)
  msg="${2:-Update}"
  for attempt in 1 2 3 4 5 6; do
    git -C site fetch -q --depth 1 origin gh-pages
    base=$(git -C site rev-parse FETCH_HEAD)
    rm -rf pub; git -C site worktree prune
    git -C site worktree add -q --detach "$PWD/pub" "$base"   # a fresh copy of what's published now
    # the page itself always comes from the latest main, not this job's checkout, so a long job (the forecast
    # takes up to 20 minutes) can't put back an older page that was replaced while it ran
    git fetch -q --depth 1 origin main
    rm -rf pub/assets; git archive FETCH_HEAD index.html assets | tar -x -C pub; touch pub/.nojekyll
    (cd site && find data -type f -newer ../.jobstart) | while read -r f; do
      mkdir -p "pub/$(dirname "$f")"; cp "site/$f" "pub/$f"
    done
    comm -23 .startlist <(cd site && find data -type f | sort) | while read -r f; do rm -f "pub/$f"; done
    git -C pub add -A
    if git -C pub diff --cached --quiet; then echo "Nothing new to publish"; exit 0; fi
    tree=$(git -C pub write-tree)
    commit=$(git -C pub commit-tree "$tree" -m "$msg")       # no parent: gh-pages stays one commit
    if git -C pub push -q --force-with-lease="gh-pages:$base" origin "$commit:refs/heads/gh-pages"; then
      echo "Published: $msg"; exit 0
    fi
    echo "Someone else published first; trying again"; sleep $((attempt * 4))
  done
  echo "Couldn't publish"; exit 1
  ;;
*)
  echo "usage: pages.sh get | put MESSAGE"; exit 2
  ;;
esac
