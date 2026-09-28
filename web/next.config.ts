import type { NextConfig } from "next"

// Production builds are a static export served by the Python dashboard
// (mnemo/dashboard.py) from mnemo/web_dist. `next dev` instead proxies /api to
// a running `mnemo dashboard` (static exports cannot use rewrites).
const isDev = process.env.NODE_ENV === "development"
const apiOrigin = process.env.MNEMO_API ?? "http://127.0.0.1:7787"

const nextConfig: NextConfig = {
  output: isDev ? undefined : "export",
  trailingSlash: true,
  images: { unoptimized: true },
  poweredByHeader: false,
  ...(isDev
    ? {
        // Otherwise /api/status is redirected to /api/status/ before the rewrite.
        skipTrailingSlashRedirect: true,
        async rewrites() {
          return [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }]
        },
      }
    : {}),
}

export default nextConfig
