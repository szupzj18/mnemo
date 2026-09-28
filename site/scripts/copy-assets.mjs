// The site reuses the repo's README assets (logo, dashboard screenshots) so
// they never drift; copy them into public/img before dev and build.
import { cpSync, mkdirSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"

const site = join(dirname(fileURLToPath(import.meta.url)), "..")
const src = join(site, "..", "docs", "assets")
const dest = join(site, "public", "img")
mkdirSync(dest, { recursive: true })
// The installer is served from the site root: /install.sh
cpSync(join(site, "..", "scripts", "install.sh"), join(site, "public", "install.sh"))
for (const f of ["logo.svg", "logo-dark.svg", "logo-small.svg", "search.png", "search-dark.png", "session.png", "session-dark.png"]) {
  cpSync(join(src, f), join(dest, f))
}
