import { fileURLToPath } from "node:url";

const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

/** @type {import("next").NextConfig} */
const nextConfig = {
  // The repo root also has lockfile-like files, so pin the workspace root explicitly.
  turbopack: { root: fileURLToPath(new URL(".", import.meta.url)) },
  async rewrites() {
    // Same origin for the browser: the session cookie stays HttpOnly and no CORS is needed.
    return [
      { source: "/api/v1/:path*", destination: `${apiOrigin}/api/v1/:path*` },
      { source: "/health/:path*", destination: `${apiOrigin}/health/:path*` },
    ];
  },
};

export default nextConfig;
