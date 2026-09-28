# mnemo website

The project landing page, deployed to GitHub Pages by `.github/workflows/pages.yml`. Next.js static export + Tailwind CSS, no component library.

```bash
pnpm install
pnpm dev                              # http://localhost:3000
SITE_BASE_PATH=/mnemo pnpm build      # what Pages serves, in out/
```

Screenshots and logos are copied from `docs/assets` at dev/build time (`scripts/copy-assets.mjs`), so refreshing the README images refreshes the site.
