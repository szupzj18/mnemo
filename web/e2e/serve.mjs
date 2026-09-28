// Playwright webServer: a real `mnemo dashboard` over synthetic sessions.
// Builds a throwaway HOME with scripts/make-demo-home.py, indexes it, then
// serves the committed static export (mnemo/web_dist) on E2E_PORT.
import { execFileSync, spawn } from "node:child_process"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

const repo = resolve(import.meta.dirname, "..", "..")
const python = process.env.PYTHON || "python3"
const port = process.env.E2E_PORT || "7899"
const home = join(mkdtempSync(join(tmpdir(), "mnemo-e2e-")), "home")
const env = { ...process.env, HOME: home, TZ: "UTC", MNEMO_DASHBOARD_TOKEN: "" }

// Run the generator with the real HOME: some Pythons write $HOME/Library/Caches
// at startup, which would trip the generator's "not an empty dir" guard.
execFileSync(python, [join(repo, "scripts", "make-demo-home.py"), home], { stdio: "inherit" })
execFileSync(python, [join(repo, "bin", "mnemo"), "index"], { env, stdio: "inherit" })

const child = spawn(python, [join(repo, "bin", "mnemo"), "dashboard", "--no-open", "--port", port], { env, stdio: "inherit" })
const stop = () => child.kill("SIGTERM")
process.on("SIGTERM", stop)
process.on("SIGINT", stop)
child.on("exit", (code) => process.exit(code ?? 0))
