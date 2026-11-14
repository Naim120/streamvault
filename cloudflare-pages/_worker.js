/**
 * StreamVault - Free Cloudflare Pages Reverse Proxy Worker
 * Exposes StreamVault over a free permanent https://<project>.pages.dev domain
 * without needing to own a custom domain.
 */
export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Target origin from Pages environment variable or fallback placeholder
    const targetOrigin = (env.BACKEND_TUNNEL_URL || "__TUNNEL_ORIGIN_PLACEHOLDER__").replace(/\/$/, "");

    if (!targetOrigin || targetOrigin.includes("PLACEHOLDER")) {
      return new Response(
        `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>StreamVault — Gateway Waiting</title>
  <style>
    body { background: #0a0a0a; color: #f5f5f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }
    .card { max-width: 540px; background: #141414; border: 1px solid #262626; border-radius: 16px; padding: 36px; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.6); }
    h1 { color: #e50914; font-size: 24px; margin: 0 0 12px 0; font-weight: 800; letter-spacing: -0.5px; }
    p { color: #a3a3a3; font-size: 14px; line-height: 1.6; margin: 0 0 18px 0; }
    code { background: #262626; color: #4ade80; padding: 3px 8px; border-radius: 6px; font-family: monospace; font-size: 13px; }
    .badge { display: inline-block; background: rgba(229,9,20,0.15); color: #f87171; border: 1px solid rgba(229,9,20,0.3); border-radius: 9999px; padding: 4px 12px; font-size: 12px; font-weight: 600; margin-bottom: 16px; }
    .step { background: #1f1f1f; border-radius: 8px; padding: 12px 16px; text-align: left; margin-bottom: 10px; font-size: 13px; color: #d4d4d4; }
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">Cloudflare Pages Gateway Active</div>
    <h1>StreamVault Server Tunnel Pending</h1>
    <p>Your free Cloudflare Pages domain is operational, but your local StreamVault media server has not reported its active tunnel address yet.</p>
    <div class="step">1. Run <code>./start.sh prod</code> on your StreamVault host machine.</div>
    <div class="step">2. Copy the active tunnel URL printed in terminal (e.g. <code>https://...trycloudflare.com</code>).</div>
    <div class="step">3. In Cloudflare Pages Settings &rarr; Environment variables, set <code>BACKEND_TUNNEL_URL</code>.</div>
  </div>
</body>
</html>`,
        { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
      );
    }

    const targetUrl = new URL(url.pathname + url.search, targetOrigin);

    // Forward all headers and append proxy identifiers
    const proxyHeaders = new Headers(request.headers);
    proxyHeaders.set("X-Forwarded-Host", url.host);
    proxyHeaders.set("X-Forwarded-Proto", "https");

    // Pass trusted Cloudflare client geo & IP headers
    if (request.cf) {
      if (request.cf.country) proxyHeaders.set("CF-IPCountry", request.cf.country);
      if (request.cf.city) proxyHeaders.set("CF-IPCity", request.cf.city);
    }

    const proxyRequest = new Request(targetUrl, {
      method: request.method,
      headers: proxyHeaders,
      body: request.body,
      redirect: "manual",
    });

    try {
      const response = await fetch(proxyRequest);
      const responseHeaders = new Headers(response.headers);

      // Ensure redirect Location headers stay on the Pages domain instead of leaking the tunnel origin
      const locationHeader = responseHeaders.get("Location");
      if (locationHeader) {
        try {
          const locUrl = new URL(locationHeader, targetOrigin);
          const rewrittenLoc = new URL(locUrl.pathname + locUrl.search + locUrl.hash, url.origin);
          responseHeaders.set("Location", rewrittenLoc.toString());
        } catch (_) {}
      }

      // Ensure proper CORS and security headers pass through
      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: responseHeaders,
      });
    } catch (err) {
      return new Response(
        `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>StreamVault — Server Offline</title>
  <style>
    body { background: #0a0a0a; color: #f5f5f5; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; box-sizing: border-box; }
    .card { max-width: 500px; background: #141414; border: 1px solid #262626; border-radius: 16px; padding: 36px; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.6); }
    h1 { color: #f87171; font-size: 22px; margin: 0 0 12px 0; font-weight: 700; }
    p { color: #a3a3a3; font-size: 14px; line-height: 1.6; margin: 0 0 18px 0; }
    code { background: #262626; color: #fbbf24; padding: 3px 8px; border-radius: 6px; font-family: monospace; font-size: 13px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Local Server Offline</h1>
    <p>Unable to connect to your home/server tunnel origin at <code>${targetOrigin}</code>.</p>
    <p>Ensure StreamVault is running on your server using <code>./start.sh status</code>.</p>
  </div>
</body>
</html>`,
        { status: 502, headers: { "Content-Type": "text/html; charset=utf-8" } }
      );
    }
  },
};
