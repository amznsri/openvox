/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // BUILD_OUTPUT switches between Next.js output modes at build time:
  //   undefined    → default (`next start` serves the .next/ directory)
  //   "standalone" → produces .next/standalone/server.js — what our
  //                  Docker dashboard image runs (`node server.js`).
  //   "export"     → produces out/ — fully-static, no Node runtime.
  //                  Future work: refactor agents/[id] route to use
  //                  query params so static export can handle runtime-
  //                  created IDs (Next.js requires generateStaticParams
  //                  returning all known IDs at build time, which we
  //                  can't enumerate). Once refactored, FastAPI can
  //                  serve the dashboard alongside the API in CLI mode.
  output: process.env.BUILD_OUTPUT || undefined,
  // trailingSlash: true makes Next.js's static export produce
  // `<route>/index.html` instead of flat `<route>.html` files. We
  // serve the export via FastAPI's StaticFiles which expects the
  // index.html convention — without this, hitting /dashboard/
  // returns 404 because there's no dashboard/index.html, only
  // dashboard.html sitting alongside dashboard/ as a sibling.
  // Affects only static-export builds; standalone / dev modes
  // are unaffected because they don't write to out/.
  trailingSlash: process.env.BUILD_OUTPUT === "export" || undefined,
  env: {
    // Defaults match docker-compose.yml NEXT_PUBLIC_API_URL / WS_URL.
    // Phase 1 PR-1 deleted the Node gateway at :3001; the FastAPI core
    // serves the same /api/v1/* and /ws/voice endpoints at :8000 directly.
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000",
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000",
  },
  // Local-first: silence the "no telemetry" badge.
  experimental: { instrumentationHook: false },
};

export default nextConfig;
