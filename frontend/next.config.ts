import type { NextConfig } from "next";

const API = process.env.CLIPFORGE_API_URL ?? "http://127.0.0.1:8000";

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "same-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  {
    key: "Content-Security-Policy",
    value: [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "media-src 'self' blob:",
      "font-src 'self' data:",
      "connect-src 'self'",
      "object-src 'none'",
      "base-uri 'self'",
      "frame-ancestors 'none'",
      "form-action 'self'",
    ].join("; "),
  },
];

const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  experimental: {
    // Only used when this server proxies /api itself (development, or no Caddy in front):
    // multi-GB uploads take longer than the 30 s default.
    proxyTimeout: 60 * 60 * 1000,
    // A forwarded request body is held in memory and cut off at this size (10 MB by default), so
    // videos are uploaded in pieces of 8 MB (lib/api.ts: uploadInPieces). This leaves room for
    // those pieces and for small forms such as CSV imports.
    proxyClientMaxBodySize: "32mb",
  },
  async rewrites() {
    // Same-origin API: the session cookie never has to cross origins.
    return [{ source: "/api/:path*", destination: `${API}/api/:path*` }];
  },
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
};

export default nextConfig;
