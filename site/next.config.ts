import type { NextConfig } from "next"

// Static export for GitHub Pages. SITE_BASE_PATH is "/mnemo" when served from
// https://<user>.github.io/mnemo, empty for a custom domain or local preview.
const basePath = process.env.SITE_BASE_PATH ?? ""

const nextConfig: NextConfig = {
  output: "export",
  basePath,
  trailingSlash: true,
  images: { unoptimized: true },
  env: { NEXT_PUBLIC_BASE_PATH: basePath },
}

export default nextConfig
