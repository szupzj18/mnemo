#!/bin/sh
# Run the Playwright suite inside the same Linux image CI uses, so screenshot
# baselines match. Pass --update to refresh e2e/__screenshots__.
#   pnpm e2e:docker            # verify
#   pnpm e2e:docker --update   # regenerate baselines
set -eu
cd "$(dirname "$0")/../.."
version=$(node -p "require('./web/node_modules/@playwright/test/package.json').version")
args=""
[ "${1:-}" = "--update" ] && args="--update-snapshots"
proxy=""
[ -n "${MNEMO_DOCKER_PROXY:-}" ] && proxy="-e HTTPS_PROXY=$MNEMO_DOCKER_PROXY -e HTTP_PROXY=$MNEMO_DOCKER_PROXY -e NO_PROXY=127.0.0.1,localhost"

# shellcheck disable=SC2086
docker run --rm --ipc=host $proxy \
  -e CI=1 -e NEXT_TELEMETRY_DISABLED=1 -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
  -v "$PWD":/work -v /work/web/node_modules -v /work/site/node_modules -w /work/web \
  "mcr.microsoft.com/playwright:v$version-noble" \
  sh -c "corepack enable && pnpm install --frozen-lockfile --store-dir /tmp/pnpm-store --config.confirmModulesPurge=false >/dev/null && (cd ../site && pnpm install --frozen-lockfile --store-dir /tmp/pnpm-store --config.confirmModulesPurge=false >/dev/null && SITE_BASE_PATH=/mnemo pnpm build >/dev/null) && pnpm exec playwright test $args"
