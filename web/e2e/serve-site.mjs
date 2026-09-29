// Playwright webServer for the mobile suite: the website's static export
// (site/out) served under /mnemo/, the way GitHub Pages serves it.
// Builds the export first when it is missing or older than its sources, so a
// local `pnpm e2e` never tests a stale site. CI builds it in a separate step.
import { execFileSync } from "node:child_process"
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs"
import { createServer } from "node:http"
import { extname, join, resolve, sep } from "node:path"

const repo = resolve(import.meta.dirname, "..", "..")
const site = join(repo, "site")
const out = join(site, "out")
const port = Number(process.env.E2E_SITE_PORT || "7896")
const base = "/mnemo"

// Newest mtime under these paths; the site reads docs/assets and the installer at build time.
const SOURCES = [join(site, "src"), join(site, "public"), join(site, "package.json"), join(site, "next.config.ts"), join(repo, "docs", "assets"), join(repo, "scripts", "install.sh")]
function newest(path) {
  const st = statSync(path)
  if (!st.isDirectory()) return st.mtimeMs
  return readdirSync(path).reduce((m, f) => Math.max(m, newest(join(path, f))), st.mtimeMs)
}

const built = join(out, "index.html")
if (!existsSync(built) || SOURCES.some((p) => newest(p) > statSync(built).mtimeMs)) {
  const run = (args) => execFileSync("pnpm", args, { cwd: site, stdio: "inherit", env: { ...process.env, SITE_BASE_PATH: base, NEXT_TELEMETRY_DISABLED: "1" } })
  if (!existsSync(join(site, "node_modules"))) run(["install", "--frozen-lockfile"])
  run(["build"])
}

const types = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
}

createServer((req, res) => {
  const url = new URL(req.url, "http://localhost")
  if (url.pathname === base) {
    res.writeHead(301, { location: base + "/" }).end()
    return
  }
  let file = null
  if (url.pathname.startsWith(base + "/")) {
    const rel = decodeURIComponent(url.pathname.slice(base.length))
    const path = resolve(out, "." + rel)
    if (path === out || path.startsWith(out + sep)) file = rel.endsWith("/") ? join(path, "index.html") : path
  }
  if (!file || !existsSync(file) || statSync(file).isDirectory()) {
    res.writeHead(404, { "content-type": "text/plain" }).end("not found")
    return
  }
  res.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" })
  res.end(req.method === "HEAD" ? undefined : readFileSync(file))
}).listen(port, "127.0.0.1", () => console.log(`site: http://127.0.0.1:${port}${base}/`))
