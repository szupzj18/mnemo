# mnemo web UI

The dashboard served by `mnemo dashboard`: Next.js (App Router, static export) + shadcn/ui (Base UI) + Tailwind CSS 4.

End users never need Node: `pnpm build` exports the site and copies it to `../mnemo/web_dist`, which is committed and served by the stdlib Python server in `mnemo/dashboard.py`. The server injects the per-launch API token into every HTML page.

## Develop

```bash
pnpm install

# terminal 1: the Python API with a fixed token
MNEMO_DASHBOARD_TOKEN=dev mnemo dashboard --no-open        # http://127.0.0.1:7787

# terminal 2: hot-reloading UI that proxies /api to it
NEXT_PUBLIC_MNEMO_TOKEN=dev pnpm dev                       # http://localhost:3000
```

`MNEMO_API` changes the proxied origin (default `http://127.0.0.1:7787`).

## Check and ship

| Command | What it does |
|---|---|
| `pnpm lint` / `pnpm typecheck` | ESLint (incl. React Compiler rules) and `tsc` |
| `pnpm test` | Vitest unit tests for `src/lib` (formatting, transcript timeline, body tokenizer) |
| `pnpm build` | Static export, then sync to `mnemo/web_dist` with a source hash |
| `pnpm check:dist` | Fails if `mnemo/web_dist` was not rebuilt after a source change (CI runs this) |
| `pnpm e2e` | Playwright against a real `mnemo dashboard` over synthetic sessions, plus a phone-width suite (`e2e/mobile.spec.ts`) over the website's static export; the export is built on first run and rebuilt when `site/` changes |
| `pnpm e2e:docker [--update]` | Same, in CI's Linux image; `--update` refreshes screenshot baselines |

Screenshot baselines live in `e2e/__screenshots__` and are compared only on Linux (CI or `e2e:docker`); on macOS the suite runs every functional assertion and skips pixel diffs. Behind a proxy, set `MNEMO_DOCKER_PROXY=http://host.docker.internal:<port>` for `e2e:docker`.

Always commit `mnemo/web_dist` together with the source change that produced it.
