#!/usr/bin/env bash
# For the move to the gh-pages branch: copies the published site (index.html, assets/, data/) from the latest
# main onto gh-pages as one fresh commit, so the site looks the same whichever branch GitHub Pages serves.
set -euo pipefail
URL="https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
git fetch -q origin main
rm -rf "$RUNNER_TEMP/mirror" && mkdir -p "$RUNNER_TEMP/mirror"
git archive FETCH_HEAD index.html assets data | tar -x -C "$RUNNER_TEMP/mirror"
cd "$RUNNER_TEMP/mirror" && touch .nojekyll
git init -q && git add -A
git -c user.name=radar-bot -c user.email=radar-bot@users.noreply.github.com commit -qm "Site from main $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q -f "$URL" HEAD:refs/heads/gh-pages && echo "Mirrored to gh-pages"
