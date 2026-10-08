import type { NextConfig } from "next";

// The browser only ever talks to this server; /api/* is passed through to FastAPI, so the
// session cookie stays same-origin (no CORS) and uploads stream straight to the backend.
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // Every screen shows per-user data behind a login: nothing to prerender or cache on the
  // server, so the caching features are off.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/api/:path*` }];
  },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
