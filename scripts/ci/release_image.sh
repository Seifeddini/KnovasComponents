#!/usr/bin/env bash
# What .github/workflows/release-images.yml builds for one image (V1-G10, FEAT-2026-11-05-knovas-brain-v1).
#
#   bash scripts/ci/release_image.sh <image> <owner> <branch> <commit>   >> "$GITHUB_OUTPUT"
#
# <image> is one of the workflow's choices: platform, knovas-link, sync-component. Prints KEY=value lines:
#   package     the GHCR package, the image name the deployment already uses:
#                 platform        docbridge-web-clientbundle (docker-compose.yml, service docbridge-web)
#                 knovas-link     knovas-link     (the Builder's site package, builder/sites.py CONTAINERS)
#                 sync-component  sync-component  (the same)
#   repository  ghcr.io/<owner, lower case>/<package>
#   context     the build context (its Dockerfile is the one the compose files build)
#   runner      whether the image installs brain-runner from the private knowledge base (yes: needs the build
#               secret kb_read_token; the release refuses to build it without)
#   short_tag   the first 12 characters of the commit
#   branch_tag  the branch, sanitised to a Docker tag: every character outside [A-Za-z0-9_.-] becomes "-", leading
#               "." and "-" are dropped, at most 128 characters; a branch that would read like a commit gets the
#               prefix "branch-", so it never shadows a commit tag
# Tags move; deploy by digest (the workflow's summary names it).
set -euo pipefail

fail() { echo "release_image: $*" >&2; exit 1; }

[[ $# -eq 4 ]] || fail "usage: release_image.sh <platform|knovas-link|sync-component> <owner> <branch> <commit>"
image="$1" owner="$2" branch="$3" commit="$4"

case "$image" in
  platform)       package=docbridge-web-clientbundle; context=KnovasPlatform/components/docbridge_integration; runner=yes ;;
  knovas-link)    package=knovas-link;                context=KnovasLink;                                     runner=no ;;
  sync-component) package=sync-component;             context=KnovasPlatform/components/sync_component;       runner=yes ;;
  *) fail "unknown image: $image (platform, knovas-link or sync-component)" ;;
esac

[[ "$owner" =~ ^[A-Za-z0-9][A-Za-z0-9-]*$ ]] || fail "not a repository owner: $owner"
[[ "$commit" =~ ^[0-9a-f]{40}$ ]] || fail "not a full commit sha: $commit"
[[ -n "$branch" ]] || fail "no branch"

branch_tag="$(printf '%s' "$branch" | tr -c 'A-Za-z0-9_.-' '-')"
branch_tag="${branch_tag#"${branch_tag%%[!.-]*}"}"
branch_tag="${branch_tag:0:128}"
[[ -n "$branch_tag" ]] || fail "the branch name leaves no tag: $branch"
if [[ "$branch_tag" =~ ^[0-9a-f]{7,40}$ ]]; then
  branch_tag="branch-${branch_tag}"
  branch_tag="${branch_tag:0:128}"
fi

echo "package=$package"
echo "repository=ghcr.io/${owner,,}/$package"
echo "context=$context"
echo "runner=$runner"
echo "short_tag=${commit:0:12}"
echo "branch_tag=$branch_tag"
