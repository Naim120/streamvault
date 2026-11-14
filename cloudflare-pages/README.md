# StreamVault — Free Cloudflare Pages (.pages.dev) Domain Setup

This guide explains how to get a **permanent, free `https://<your-project>.pages.dev` domain** for your StreamVault server without purchasing a custom domain.

---

### How It Works

```
[Web Browser / Phone]
        │ (HTTPS)
        ▼
[https://your-project.pages.dev] (Cloudflare Edge Pages Worker: _worker.js)
        │ (Encrypted QUIC Tunnel)
        ▼
[https://*.trycloudflare.com] (Local cloudflared daemon on your server)
        │ (Loopback)
        ▼
[http://127.0.0.1:8080] (StreamVault Nginx Gateway + FastAPI Media Server)
```

1. **Cloudflare Pages** gives you a free, permanent subdomain (`your-project.pages.dev`).
2. The `_worker.js` script in this folder acts as a high-performance reverse proxy that forwards all web traffic, API calls, and video streaming chunks to your home/remote server's active Cloudflare tunnel.
3. Your local server runs `cloudflared tunnel --url http://127.0.0.1:8080` (managed automatically by `./start.sh prod`).

---

### 2-Minute Deployment Steps

#### Step 1: Create a Free Cloudflare Pages Project
1. Log in to your free [Cloudflare Dashboard](https://dash.cloudflare.com).
2. Go to **Workers & Pages** &rarr; **Create application** &rarr; **Pages** tab.
3. Choose **Direct Upload** (or connect your GitHub repository).
4. Enter your project name (e.g. `my-streamvault`). Your public domain will be:
   `https://my-streamvault.pages.dev`
5. Upload the `cloudflare-pages` folder from your StreamVault repository (or run `npx wrangler pages deploy cloudflare-pages --project-name my-streamvault`).

#### Step 2: Configure Environment Variable
In Cloudflare Dashboard &rarr; **Workers & Pages** &rarr; your Pages project &rarr; **Settings** &rarr; **Environment variables**:
- Add variable: `BACKEND_TUNNEL_URL`
- Value: Your active tunnel URL printed by `./start.sh prod` (e.g., `https://random-words.trycloudflare.com`).

*(Optional: If you provide your Cloudflare Account ID and API Token during `./install.sh`, StreamVault updates this variable automatically whenever you start the server!)*

#### Step 3: Whitelist Supabase Auth Redirect
In your [Supabase Dashboard](https://supabase.com/dashboard) &rarr; **Project Settings** &rarr; **Authentication** &rarr; **URL Configuration**:
- Set **Site URL**: `https://your-project.pages.dev`
- Add to **Redirect URLs**: `https://your-project.pages.dev/**`
