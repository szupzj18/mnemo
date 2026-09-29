// Playwright webServer: a real `mnemo dashboard` over synthetic sessions.
// Builds a throwaway HOME with scripts/make-demo-home.py, indexes it, then
// serves the committed static export (mnemo/web_dist) on E2E_PORT.
//
// With --mesh this device ("laptop") also gets neighbors: other HOMEs on this
// machine reached through the "local" transport instead of SSH.
//   laptop ─▶ devbox-a (relays) ─▶ devbox-b
//          └▶ devbox-down (never comes up)
// devbox-b runs its own, older copy of the code, so it shows as needing an update.
import { execFileSync, spawn } from "node:child_process"
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

const repo = resolve(import.meta.dirname, "..", "..")
const python = process.env.PYTHON || "python3"
const port = process.env.E2E_PORT || "7899"
const root = mkdtempSync(join(tmpdir(), "mnemo-e2e-"))
const home = join(root, "home")
// Link services (mnemo link --install) run as plain background processes here.
const env = { ...process.env, HOME: home, TZ: "UTC", MNEMO_DASHBOARD_TOKEN: "", MNEMO_SERVICE_MANAGER: "background" }

const mnemo = (args, h = home) =>
  execFileSync(python, [join(repo, "bin", "mnemo"), ...args], { env: { ...env, HOME: h }, stdio: "inherit" })
// Run the generator with the real HOME: some Pythons write $HOME/Library/Caches
// at startup, which would trip the generator's "not an empty dir" guard.
const demo = (h) => execFileSync(python, [join(repo, "scripts", "make-demo-home.py"), h], { stdio: "inherit" })

demo(home)
mnemo(["index"])

if (process.argv.includes("--mesh")) {
  const link = (h, neighbors) => {
    mkdirSync(join(h, ".mnemo"), { recursive: true })
    const remotes = neighbors.map(([name, nh]) => ({ name, host: "local:" + name, transport: "local", home: nh }))
    writeFileSync(join(h, ".mnemo", "remotes.json"), JSON.stringify({ remotes }))
  }
  const a = join(root, "devbox-a")
  const b = join(root, "devbox-b")
  writeFileSync(join(root, "blocker"), "") // a HOME under a file cannot exist, even for root
  for (const h of [a, b]) {
    demo(h)
    mnemo(["index"], h)
  }
  execFileSync(python, ["-c", [
    "import sys; sys.path.insert(0, sys.argv[1]); from mnemo import remote",
    "remote._copy_tree(sys.argv[1], sys.argv[2])",
    "open(sys.argv[2] + '/mnemo/__init__.py', 'a').write('# an older build\\n')",
  ].join("\n"), repo, join(b, "mnemo")], { stdio: "inherit" })
  mnemo(["node", "--name", "laptop"])
  mnemo(["node", "--name", "build-a", "--forward", "on"], a)
  mnemo(["node", "--name", "build-b"], b)
  link(home, [
    ["devbox-a", a],
    ["devbox-down", join(root, "blocker", "home")],
  ])
  link(a, [["devbox-b", b]])
}

const child = spawn(python, [join(repo, "bin", "mnemo"), "dashboard", "--no-open", "--port", port], { env, stdio: "inherit" })
const stop = () => {
  // Links the tests switched on outlive the dashboard; stop them with it.
  for (const name of ["devbox-a", "devbox-down"]) {
    try {
      execFileSync(python, [join(repo, "bin", "mnemo"), "link", name, "--uninstall"], { env, stdio: "ignore" })
    } catch {}
  }
  child.kill("SIGTERM")
}
process.on("SIGTERM", stop)
process.on("SIGINT", stop)
child.on("exit", (code) => {
  if (process.argv.includes("--mesh")) stop()
  process.exit(code ?? 0)
})
