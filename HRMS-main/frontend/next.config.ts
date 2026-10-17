import type { NextConfig } from "next";

// B-17 follow-up: response security headers. The CSP restricts where the page may connect, load images and fonts
// from, forbids plugins, base-URL and form-target hijacking, and forbids framing. It deliberately sets NO
// default-src / script-src / style-src: the Next.js production build emits inline <script> tags (the RSC payload,
// `self.__next_f.push(...)`) and an inline style attribute, so a strict script policy needs per-request nonces
// (middleware + dynamic rendering). That design is documented for approval in
// docs/production-hardening/release-readiness.md; 'unsafe-inline' / 'unsafe-eval' are never added here.
// A CSP reduces script-injection impact; it cannot make an injected script harmless once it runs.

export function apiOrigin(baseUrl: string | undefined): string | null {
  try {
    return new URL(baseUrl || "http://localhost:8000").origin;
  } catch {
    return null;
  }
}

export function contentSecurityPolicy(env: { apiBaseUrl?: string; development: boolean }): string {
  const api = apiOrigin(env.apiBaseUrl);
  const connect = ["'self'", ...(api ? [api] : []), ...(env.development ? ["ws:", "wss:"] : [])];  // dev: HMR socket
  return [
    `connect-src ${connect.join(" ")}`,
    "img-src 'self' data: blob:",  // profile pictures and documents are shown from blob: URLs
    "font-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function securityHeaders(env: { apiBaseUrl?: string; development: boolean }): { key: string; value: string }[] {
  return [
    { key: "Content-Security-Policy", value: contentSecurityPolicy(env) },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=()" },
  ];
}

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: securityHeaders({
          apiBaseUrl: process.env.NEXT_PUBLIC_API_BASE_URL,
          development: process.env.NODE_ENV !== "production",
        }),
      },
    ];
  },
};

export default nextConfig;
