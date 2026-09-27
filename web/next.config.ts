import type { NextConfig } from "next";

// The browser only talks to /api on this server; Next forwards those requests to FastAPI,
// so the API (and the keys it holds) is never exposed directly. API_URL is read from
// web/.env.local; local development currently targets the current backend on port 8000.
const apiUrl = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/:path*` }];
  },
  experimental: {
    // Allow large local uploads; inference runs asynchronously after submission.
    proxyTimeout: 120_000,
    // Rewrites buffer the request body and truncate it at 10 MB by default, which cuts off dive videos.
    // Matches the API's own upload cap (MAX_UPLOAD_MB).
    proxyClientMaxBodySize: "200mb",
  },
  // Comma-separated hosts allowed to load the dev server, e.g. a phone on the LAN: DEV_ORIGINS=192.168.1.20
  allowedDevOrigins: process.env.DEV_ORIGINS?.split(",").map((s) => s.trim()).filter(Boolean),
};

export default nextConfig;
