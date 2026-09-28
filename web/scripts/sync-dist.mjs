// Copies the static export (web/out) into mnemo/web_dist, which the Python
// dashboard serves and which is committed so installs need no Node toolchain.
// Also writes a hash of the frontend sources so CI can tell whether the
// committed build is stale (`node scripts/sync-dist.mjs --check`).
import { createHash } from "node:crypto"
import { cpSync, existsSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from "node:fs"
import { dirname, join, relative } from "node:path"
import { fileURLToPath } from "node:url"

const web = join(dirname(fileURLToPath(import.meta.url)), "..")
const out = join(web, "out")
const dist = join(web, "..", "mnemo", "web_dist")
const HASH_FILE = ".source-hash"
const INPUTS = ["src", "public", "package.json", "pnpm-lock.yaml", "next.config.ts", "postcss.config.mjs", "tsconfig.json", "components.json"]

function files(p) {
  if (!existsSync(p)) return []
  if (statSync(p).isFile()) return [p]
  return readdirSync(p)
    .sort()
    .flatMap((n) => files(join(p, n)))
}

export function sourceHash() {
  const h = createHash("sha256")
  // Unit tests do not affect the bundle, so editing them must not mark the build stale.
  for (const f of INPUTS.flatMap((i) => files(join(web, i))).filter((f) => !/\.test\.tsx?$/.test(f))) {
    h.update(relative(web, f).split("\\").join("/"))
    h.update("\0")
    h.update(readFileSync(f))
    h.update("\0")
  }
  return h.digest("hex")
}

if (process.argv.includes("--check")) {
  const want = sourceHash()
  const have = existsSync(join(dist, HASH_FILE)) ? readFileSync(join(dist, HASH_FILE), "utf8").trim() : "(missing)"
  if (want !== have) {
    console.error(`mnemo/web_dist is stale: built from ${have.slice(0, 12)}, sources are ${want.slice(0, 12)}.`)
    console.error("Run `pnpm build` in web/ and commit mnemo/web_dist.")
    process.exit(1)
  }
  console.log(`mnemo/web_dist is up to date (${want.slice(0, 12)}).`)
} else {
  if (!existsSync(join(out, "index.html"))) {
    console.error("web/out/index.html not found; run `next build` first.")
    process.exit(1)
  }
  rmSync(dist, { recursive: true, force: true })
  cpSync(out, dist, { recursive: true })
  writeFileSync(join(dist, HASH_FILE), sourceHash() + "\n")
  console.log(`synced ${files(dist).length} files to mnemo/web_dist`)
}
