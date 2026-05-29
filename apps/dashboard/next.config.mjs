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
    // IMPORTANT: do NOT default these to localhost:8000.
    //
    // The wheel build (BUILD_OUTPUT=export) ships these EMPTY on
    // purpose so api.ts falls back to the dashboard's own origin —
    // making it work on whatever port the daemon picked (8000, 8001,
    // …). See the long note in src/lib/api.ts.
    //
    // Docker mode (BUILD_OUTPUT=standalone) DOES need an explicit value
    // because the dashboard (:3000) and API (:8000) are different
    // origins — it passes one in via a build ARG (Dockerfile +
    // docker-compose.yml). When that ARG is set, process.env has it
    // here and we honour it; when unset, "" → same-origin fallback.
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "",
    NEXT_PUBLIC_WS_URL: process.env.NEXT_PUBLIC_WS_URL || "",
  },
  // Local-first: silence the "no telemetry" badge.
  experimental: { instrumentationHook: false },
};

export default nextConfig;
