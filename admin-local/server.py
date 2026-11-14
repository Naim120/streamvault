import json
import os
import sys
import logging
import urllib.parse
import hmac
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

# Add root directory to python path and load root .env
BASE_DIR = Path(__file__).resolve().parent.parent
DOTENV_PATH = BASE_DIR / ".env"
if DOTENV_PATH.exists():
    load_dotenv(dotenv_path=DOTENV_PATH, override=True)

if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

ADMIN_LOCAL_DIR = Path(__file__).resolve().parent
if str(ADMIN_LOCAL_DIR) not in sys.path:
    sys.path.append(str(ADMIN_LOCAL_DIR))

# Silence noisy httpx/httpcore polling
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from backend.app.config import settings
from backend.app.utils.process_identity import set_process_identity
from modules.common import STATIC_DIR, ADMIN_SECRET
from modules import dashboard, download_manager, media, metadata, user_telemetry

# Set production OS process identity
set_process_identity("streamvault-admin")

if not settings.SUPABASE_URL or not (settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY):
    raise RuntimeError("CRITICAL: Supabase credentials are missing in Admin Server environment!")
logging.getLogger("admin-server").info("✓ Supabase credentials loaded & verified")

def _get_admin_session_token() -> str:
    key = (ADMIN_SECRET or "streamvault_admin_default_salt").encode("utf-8")
    return hmac.new(key, b"streamvault_admin_session_v1", hashlib.sha256).hexdigest()

def is_admin_authenticated(request: Request) -> bool:
    if not ADMIN_SECRET:
        return True
    x_sec = request.headers.get("x-admin-secret", "").strip()
    if x_sec and hmac.compare_digest(x_sec.encode("utf-8"), ADMIN_SECRET.encode("utf-8")):
        return True
    sess = request.cookies.get("streamvault_admin_session", "").strip()
    expected = _get_admin_session_token()
    if sess and hmac.compare_digest(sess.encode("utf-8"), expected.encode("utf-8")):
        return True
    return False

app = FastAPI(title="StreamVault Decoupled Local Admin Panel")

# Production Branding & Security Middleware
class StreamVaultAdminBrandingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Validate Host header to strictly prevent DNS Rebinding attacks (supports IPv4 & IPv6 loopback)
        host_hdr = request.headers.get("host", "").strip()
        try:
            parsed_host = urllib.parse.urlsplit(f"//{host_hdr}").hostname or ""
            raw_host = parsed_host.lower()
        except Exception:
            raw_host = host_hdr.split(":")[0].strip().lower()

        if raw_host not in ("127.0.0.1", "localhost", "::1", "testclient"):
            return Response(status_code=403, content="Forbidden: Invalid Host header for Local Admin Panel")

        sec_fetch_site = request.headers.get("sec-fetch-site", "").lower()
        sec_fetch_mode = request.headers.get("sec-fetch-mode", "").lower()
        origin = request.headers.get("origin", "").rstrip("/")

        # Allow top-level GET navigation (e.g. following links or typing URL in browser)
        is_top_level_nav = (request.method in ("GET", "HEAD") and sec_fetch_mode == "navigate")

        if not is_top_level_nav:
            if sec_fetch_site == "cross-site" or (origin and origin not in ("http://127.0.0.1:4000", "http://localhost:4000")):
                return Response(status_code=403, content="Forbidden: Cross-origin request to Local Admin Panel blocked")
        elif origin and origin not in ("http://127.0.0.1:4000", "http://localhost:4000"):
            if request.method not in ("GET", "HEAD"):
                return Response(status_code=403, content="Forbidden: Cross-origin request to Local Admin Panel blocked")

        # Inbound administrative authentication guard for /api/* routes
        path = request.url.path
        if path.startswith("/api/"):
            if path not in ("/api/admin/auth/login", "/api/admin/auth/logout"):
                if not is_admin_authenticated(request):
                    return Response(
                        status_code=401,
                        content=json.dumps({"detail": "Unauthorized: Admin authentication required"}),
                        media_type="application/json"
                    )

        response = await call_next(request)
        response.headers["Server"] = "StreamVault-Media-Engine/2.5"
        response.headers["X-StreamVault-Version"] = "2.5.0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self';"
        )
        return response

class _PayloadTooLargeError(Exception):
    pass

# Pure ASGI Request Body Size Limit Middleware (15MB for torrents/uploads, 64KB for JSON)
class RequestSizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("method") in ("POST", "PUT", "PATCH"):
            path = scope.get("path", "")
            is_upload = "/add" in path or "/torrent" in path or "/upload" in path or "/subtitles" in path
            max_bytes = (15 * 1024 * 1024) if is_upload else (64 * 1024)
            headers = dict(scope.get("headers", []))
            cl = headers.get(b"content-length")
            if cl:
                try:
                    cl_bytes = int(cl)
                except ValueError:
                    resp = Response(status_code=400, content=json.dumps({"detail": "Invalid Content-Length"}), media_type="application/json")
                    await resp(scope, receive, send)
                    return
                if cl_bytes < 0:
                    resp = Response(status_code=400, content=json.dumps({"detail": "Invalid Content-Length"}), media_type="application/json")
                    await resp(scope, receive, send)
                    return
                if cl_bytes > max_bytes:
                    resp = Response(status_code=413, content=json.dumps({"detail": f"Payload Too Large (max {max_bytes} bytes)"}), media_type="application/json")
                    await resp(scope, receive, send)
                    return
            total = 0
            exceeded = False
            response_started = False
            async def limited_receive():
                nonlocal total, exceeded
                msg = await receive()
                if msg["type"] == "http.request":
                    total += len(msg.get("body", b""))
                    if total > max_bytes:
                        exceeded = True
                        raise _PayloadTooLargeError()
                return msg
            async def tracked_send(message):
                nonlocal response_started
                if exceeded and message["type"] in ("http.response.start", "http.response.body"):
                    return
                if message["type"] == "http.response.start":
                    response_started = True
                await send(message)
            try:
                await self.app(scope, limited_receive, tracked_send)
            except* _PayloadTooLargeError:
                pass
            if exceeded and not response_started:
                resp = Response(status_code=413, content=json.dumps({"detail": f"Payload Too Large (max {max_bytes} bytes)"}), media_type="application/json")
                await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)

app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(StreamVaultAdminBrandingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:4000", "http://localhost:4000"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Secret"],
)

# Mount Routers
app.include_router(dashboard.router)
app.include_router(download_manager.router)
app.include_router(media.router)
app.include_router(metadata.router)
app.include_router(user_telemetry.router)

# Mount Static Files & Routes
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)

@app.get("/static/tailwind.min.css")
async def get_admin_tailwind_css():
    """Serves the locally compiled Tailwind stylesheet with 24-hour public cache headers"""
    css_file = STATIC_DIR / "tailwind.min.css"
    if not css_file.exists():
        raise HTTPException(status_code=404, detail="Tailwind stylesheet not found")
    return Response(
        content=css_file.read_bytes(),
        media_type="text/css",
        headers={"Cache-Control": "public, max-age=86400"}
    )

# HTML UI Construction
HTML_SHELL_TOP = r"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>StreamVault Decoupled Admin Panel (127.0.0.1:4000)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="/static/tailwind.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; background-color: #0c0c0c; color: #fff; }
    .mono { font-family: 'JetBrains Mono', monospace; }
    body.modal-open .animate-pulse,
    body.modal-open .animate-ping {
      animation: none !important;
    }
  </style>
</head>
<body class="h-screen w-screen overflow-hidden flex flex-col md:flex-row bg-[#0c0c0c] text-white">
  <!-- Left Collapsible Sidebar -->
  <aside id="sidebar" class="bg-[#121212] border-r border-neutral-800 w-full md:w-64 h-full flex flex-col flex-shrink-0 transition-all duration-300 overflow-y-auto z-30">
    <div class="p-4 border-b border-neutral-800/80 flex items-center justify-between">
      <div id="brand-text" class="flex items-center gap-2">
        <span class="px-2 py-0.5 bg-red-600 text-white font-mono font-black text-[10px] rounded tracking-wider">ADMIN</span>
        <span class="font-black text-sm tracking-wide text-white">StreamVault</span>
      </div>
      <button onclick="toggleSidebar()" class="p-1.5 rounded-lg text-neutral-400 hover:text-white hover:bg-neutral-800 transition cursor-pointer" title="Toggle Sidebar">
        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path></svg>
      </button>
    </div>

    <!-- Navigation (5 Tabs) -->
    <nav class="p-3 space-y-1.5 flex-1">
      <button onclick="switchTab('dashboard')" id="nav-dashboard" data-tooltip="Dashboard" onmouseenter="showSidebarTooltip(this)" onmouseleave="hideSidebarTooltip()" class="nav-btn relative group w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold bg-red-600 text-white transition cursor-pointer shadow-lg shadow-red-600/20">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"></path></svg>
        <span class="nav-label">Dashboard</span>
      </button>

      <button onclick="switchTab('aria2')" id="nav-aria2" data-tooltip="Aria2c Downloader" onmouseenter="showSidebarTooltip(this)" onmouseleave="hideSidebarTooltip()" class="nav-btn relative group w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition cursor-pointer">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"></path></svg>
        <span class="nav-label">Aria2c Downloader</span>
      </button>

      <button onclick="switchTab('media')" id="nav-media" data-tooltip="Media (Ingest &amp; Queue)" onmouseenter="showSidebarTooltip(this)" onmouseleave="hideSidebarTooltip()" class="nav-btn relative group w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition cursor-pointer">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 4v16M17 4v16M3 8h4m10 0h4M3 12h18M3 16h4m10 0h4M4 20h16a1 1 0 001-1V5a1 1 0 00-1-1H4a1 1 0 00-1 1v14a1 1 0 001 1z"></path></svg>
        <span class="nav-label">Media (Ingest &amp; Queue)</span>
      </button>

      <button onclick="switchTab('metadata')" id="nav-metadata" data-tooltip="Metadata Manager" onmouseenter="showSidebarTooltip(this)" onmouseleave="hideSidebarTooltip()" class="nav-btn relative group w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition cursor-pointer">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 7h.01M7 3h5c.512 0 1.024.195 1.414.586l7 7a2 2 0 010 2.828l-7 7a2 2 0 01-2.828 0l-7-7A1.994 1.994 0 013 12V7a4 4 0 014-4z"></path></svg>
        <span class="nav-label">Metadata Manager</span>
      </button>

      <button onclick="switchTab('users')" id="nav-users" data-tooltip="User Telemetry" onmouseenter="showSidebarTooltip(this)" onmouseleave="hideSidebarTooltip()" class="nav-btn relative group w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition cursor-pointer">
        <svg class="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>
        <span class="nav-label">User Telemetry</span>
      </button>
    </nav>

    <div class="p-4 border-t border-neutral-800 text-[11px] text-neutral-500 flex items-center gap-2">
      <div id="connection-indicator" class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></div>
      <span id="connection-status" class="nav-label">Checking System...</span>
    </div>
  </aside>

  <!-- Main Content Wrapper with guaranteed vertical scroll -->
  <div class="flex-1 h-screen overflow-y-auto overflow-x-hidden flex flex-col min-w-0">
    <!-- Top Bar with Compact Status Chips -->
    <header class="h-14 bg-[#121212]/90 border-b border-neutral-800 px-4 sm:px-6 flex items-center justify-between sticky top-0 z-20 backdrop-blur-md flex-shrink-0">
      <div id="section-title" class="text-xs font-bold uppercase tracking-wider text-neutral-300">Operations Dashboard</div>

      <!-- Compact Telemetry Chips on Top-Right -->
      <div class="flex items-center gap-2 sm:gap-3 text-[11px]">
        <div class="bg-[#181818] border border-neutral-800 px-2.5 py-1 rounded-lg text-neutral-300 flex items-center gap-1.5">
          <span class="text-neutral-500 font-medium">CPU:</span>
          <span id="top-cpu" class="font-bold text-white">--%</span>
        </div>
        <div class="hidden sm:flex bg-[#181818] border border-neutral-800 px-2.5 py-1 rounded-lg text-neutral-300 items-center gap-1.5">
          <span class="text-neutral-500 font-medium">RAM:</span>
          <span id="top-ram" class="font-bold text-emerald-400">-- GB free</span>
        </div>
        <div class="hidden md:flex bg-[#181818] border border-neutral-800 px-2.5 py-1 rounded-lg text-neutral-300 items-center gap-1.5">
          <span class="text-neutral-500 font-medium">Disk:</span>
          <span id="top-disk" class="font-bold text-cyan-400">-- GB free</span>
        </div>
        <div id="top-encoder" class="bg-purple-500/10 border border-purple-500/30 px-2.5 py-1 rounded-lg text-purple-400 font-mono font-bold uppercase text-[10px]">
          Detecting...
        </div>
        <button onclick="handleAdminLogout()" title="Sign Out of Admin Session" class="px-2.5 py-1 bg-neutral-800 hover:bg-red-600/30 text-neutral-400 hover:text-red-300 border border-neutral-700 rounded-lg text-xs font-semibold transition cursor-pointer flex items-center gap-1.5 shadow">
          <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"></path></svg>
          <span class="hidden sm:inline">Sign Out</span>
        </button>
      </div>
    </header>

    <!-- Main Workspace Container -->
    <main class="p-4 sm:p-6 md:p-8 space-y-6 max-w-7xl w-full mx-auto flex-1 pb-24">
      
"""

# Media and Metadata tabs are self-contained in modules

HTML_SHELL_CLOSE = r"""
    </main>
  </div>
"""

CONFIRM_MODAL_HTML = r"""
  <!-- STREAMVAULT REUSABLE WEBAPP-THEMED CONFIRMATION MODAL -->
  <div id="admin-confirm-modal" class="fixed inset-0 z-[120] bg-black/85 hidden items-center justify-center p-4">
    <div class="bg-[#141414] border border-neutral-700/80 rounded-3xl w-full max-w-md p-6 sm:p-7 space-y-4 shadow-2xl relative my-auto">
      <div class="flex items-start gap-3.5">
        <div id="admin-confirm-icon-badge" class="w-10 h-10 rounded-2xl bg-red-600/20 text-red-500 border border-red-500/30 flex items-center justify-center text-lg flex-shrink-0">
          🗑️
        </div>
        <div class="flex-1 min-w-0">
          <h3 id="admin-confirm-title" class="text-sm font-bold text-white mb-1">Confirm Action</h3>
          <p id="admin-confirm-message" class="text-xs text-neutral-300 leading-relaxed break-words"></p>
        </div>
      </div>
      <div class="flex items-center justify-end gap-2.5 pt-2 border-t border-neutral-800">
        <button type="button" id="admin-confirm-cancel-btn" class="px-4 py-2 bg-neutral-800 hover:bg-neutral-700 text-neutral-300 rounded-xl text-xs font-semibold cursor-pointer transition">
          Cancel
        </button>
        <button type="button" id="admin-confirm-ok-btn" class="px-5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold transition cursor-pointer shadow-lg shadow-red-600/30">
          Confirm
        </button>
      </div>
    </div>
  </div>
"""

GLOBAL_SCRIPT_HEADER = r"""
  <script>
"""

GLOBAL_SHELL_JS = r"""    function escapeHtml(str) {
      if (str === null || str === undefined) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    async function handleAdminLogout() {
      try {
        await fetch('/api/admin/auth/logout', { method: 'POST' });
      } catch (e) {}
      window.location.reload();
    }

    function updateModalOpenClass() {
      const mediaModal = document.getElementById('media-modal');
      const ingestModal = document.getElementById('ingest-config-modal');
      const deleteModal = document.getElementById('delete-modal');
      const userModal = document.getElementById('user-detail-modal');
      const confirmModal = document.getElementById('admin-confirm-modal');
      const isAnyOpen = (mediaModal && !mediaModal.classList.contains('hidden')) ||
                        (ingestModal && !ingestModal.classList.contains('hidden')) ||
                        (deleteModal && !deleteModal.classList.contains('hidden')) ||
                        (userModal && !userModal.classList.contains('hidden')) ||
                        (confirmModal && !confirmModal.classList.contains('hidden'));
      if (isAnyOpen) {
        document.body.classList.add('modal-open');
      } else {
        document.body.classList.remove('modal-open');
      }
    }
    window.updateModalOpenClass = updateModalOpenClass;

    function setButtonProcessing(btn, isProcessing, processingText = '⏳ Processing...') {
      if (!btn) return;
      if (isProcessing) {
        btn.dataset.origHtml = btn.innerHTML;
        btn.disabled = true;
        btn.classList.add('opacity-60', 'cursor-not-allowed', 'pointer-events-none');
        btn.innerHTML = `
          <svg class="animate-spin h-3.5 w-3.5 text-current inline mr-1.5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"></path>
          </svg>
          <span>${processingText}</span>
        `;
      } else {
        btn.disabled = false;
        btn.classList.remove('opacity-60', 'cursor-not-allowed', 'pointer-events-none');
        if (btn.dataset.origHtml) {
          btn.innerHTML = btn.dataset.origHtml;
        }
      }
    }
    window.setButtonProcessing = setButtonProcessing;

    function showAdminConfirm({ title = 'Confirm Action', message = 'Are you sure?', confirmText = 'Confirm', cancelText = 'Cancel', isDanger = true }) {
      return new Promise((resolve) => {
        const modal = document.getElementById('admin-confirm-modal');
        const titleEl = document.getElementById('admin-confirm-title');
        const msgEl = document.getElementById('admin-confirm-message');
        const okBtn = document.getElementById('admin-confirm-ok-btn');
        const cancelBtn = document.getElementById('admin-confirm-cancel-btn');
        const iconBadge = document.getElementById('admin-confirm-icon-badge');
        if (!modal || !okBtn || !cancelBtn) {
          resolve(false);
          return;
        }
        titleEl.innerText = title;
        msgEl.innerHTML = escapeHtml(message).replace(/\n/g, '<br/>');
        okBtn.innerText = confirmText;
        cancelBtn.innerText = cancelText;
        if (iconBadge) {
          iconBadge.className = isDanger
            ? 'w-10 h-10 rounded-2xl bg-red-600/20 text-red-500 border border-red-500/30 flex items-center justify-center text-lg flex-shrink-0'
            : 'w-10 h-10 rounded-2xl bg-amber-500/20 text-amber-400 border border-amber-500/30 flex items-center justify-center text-lg flex-shrink-0';
          iconBadge.innerText = isDanger ? '🗑️' : '⚠️';
        }
        okBtn.className = isDanger
          ? 'px-5 py-2 bg-red-600 hover:bg-red-700 text-white rounded-xl text-xs font-bold transition cursor-pointer shadow-lg shadow-red-600/30'
          : 'px-5 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-bold transition cursor-pointer shadow-lg shadow-emerald-600/30';

        modal.classList.remove('hidden');
        modal.classList.add('flex');
        updateModalOpenClass();

        const cleanup = (result) => {
          modal.classList.add('hidden');
          modal.classList.remove('flex');
          updateModalOpenClass();
          okBtn.onclick = null;
          cancelBtn.onclick = null;
          resolve(result);
        };

        okBtn.onclick = () => cleanup(true);
        cancelBtn.onclick = () => cleanup(false);
      });
    }
    window.showAdminConfirm = showAdminConfirm;

    let currentMode = 'abr_transcode';
    let catalog = [];

    let sidebarCollapsed = false;

    function showSidebarTooltip(btnEl) {
      if (!sidebarCollapsed) return;
      const tooltip = document.getElementById('sidebar-hover-tooltip');
      const text = btnEl.getAttribute('data-tooltip');
      if (!tooltip || !text) return;
      const rect = btnEl.getBoundingClientRect();
      tooltip.innerText = text;
      tooltip.style.left = `${rect.right + 10}px`;
      tooltip.style.top = `${rect.top + (rect.height / 2) - 14}px`;
      tooltip.classList.remove('hidden');
    }

    function hideSidebarTooltip() {
      const tooltip = document.getElementById('sidebar-hover-tooltip');
      if (tooltip) tooltip.classList.add('hidden');
    }

    function toggleSidebar() {
      const s = document.getElementById('sidebar');
      const brand = document.getElementById('brand-text');
      const labels = document.querySelectorAll('.nav-label');
      const navButtons = document.querySelectorAll('.nav-btn');
      sidebarCollapsed = !sidebarCollapsed;
      hideSidebarTooltip();
      if (sidebarCollapsed) {
        s.style.width = '68px';
        if (brand) brand.style.display = 'none';
        labels.forEach(l => l.style.display = 'none');
        navButtons.forEach(btn => btn.classList.add('justify-center'));
      } else {
        s.style.width = '256px';
        if (brand) brand.style.display = 'flex';
        labels.forEach(l => l.style.display = 'inline');
        navButtons.forEach(btn => btn.classList.remove('justify-center'));
      }
    }

    function switchTab(tab) {
      hideSidebarTooltip();
      ['dashboard', 'aria2', 'media', 'metadata', 'users'].forEach(t => {
        const sec = document.getElementById(`tab-${t}`);
        const btn = document.getElementById(`nav-${t}`);
        if (sec) sec.classList.add('hidden');
        if (btn) {
          btn.className = `nav-btn relative group w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold text-neutral-400 hover:text-white hover:bg-neutral-800 transition cursor-pointer ${sidebarCollapsed ? 'justify-center' : ''}`;
        }
      });
      document.getElementById(`tab-${tab}`)?.classList.remove('hidden');
      const activeBtn = document.getElementById(`nav-${tab}`);
      if (activeBtn) {
        activeBtn.className = `nav-btn relative group w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-xs font-semibold bg-red-600 text-white transition cursor-pointer shadow-lg shadow-red-600/20 ${sidebarCollapsed ? 'justify-center' : ''}`;
      }

      const titles = {
        dashboard: 'Operations Dashboard',
        aria2: 'Aria2c Download Manager',
        media: 'Media Ingestion & Transcoding Queue',
        metadata: 'Published Catalog & Metadata Manager',
        users: 'User Telemetry & Audit Logs'
      };
      document.getElementById('section-title').innerText = titles[tab] || 'Dashboard';
      if (tab === 'aria2') fetchDownloads();
      if (tab === 'media') { fetchIncomingFiles(); updateQueueStatus(); }
      if (tab === 'metadata') loadCatalog();
      if (tab === 'users') {
        loadMetrics();
        loadAnalyticsData();
      }
    }
"""

GLOBAL_TOAST_JS = r"""    function showAdminToast(optsOrMessage, maybeType = 'success') {
      let type = 'success';
      let title = '';
      let message = '';
      let suggestion = '';
      let duration = 5000;

      if (typeof optsOrMessage === 'object' && optsOrMessage !== null) {
        type = optsOrMessage.type || 'info';
        title = optsOrMessage.title || '';
        message = optsOrMessage.message || '';
        suggestion = optsOrMessage.suggestion || '';
        duration = optsOrMessage.duration || 5000;
      } else {
        message = String(optsOrMessage || '');
        type = maybeType || 'success';
      }

      let container = document.getElementById('toast-container') || document.getElementById('admin-toast-container');
      if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.className = 'fixed top-5 right-5 z-[9999] flex flex-col gap-2.5 max-w-sm pointer-events-none';
        document.body.appendChild(container);
      }

      const toast = document.createElement('div');
      const isErr = type === 'error';
      const isWarn = type === 'warning';
      const isInfo = type === 'info';

      toast.className = `pointer-events-auto px-4 py-3 rounded-xl shadow-2xl backdrop-blur-xl border text-xs transition-all duration-300 transform translate-y-0 opacity-100 flex flex-col gap-1.5 ${
        isErr 
          ? 'bg-red-950/95 border-red-500/60 text-red-200' 
          : isWarn 
          ? 'bg-amber-950/95 border-amber-500/60 text-amber-200' 
          : isInfo
          ? 'bg-blue-950/95 border-blue-500/60 text-blue-200'
          : 'bg-emerald-950/95 border-emerald-500/60 text-emerald-200'
      }`;

      const dotColor = isErr ? 'bg-red-500' : isWarn ? 'bg-amber-500' : isInfo ? 'bg-blue-400' : 'bg-emerald-400';
      toast.innerHTML = `
        <div class="flex items-start gap-2.5">
          <span class="w-2.5 h-2.5 rounded-full ${dotColor} animate-pulse mt-0.5 flex-shrink-0"></span>
          <div class="flex-1 min-w-0">
            ${title ? `<div class="font-bold text-white text-[11px] mb-0.5">${escapeHtml(title)}</div>` : ''}
            <div class="text-[11px] font-medium leading-relaxed">${escapeHtml(message).split('\\n').join('<br/>')}</div>
            ${suggestion ? `<div class="mt-1 text-[10px] text-amber-300 font-mono bg-black/40 p-1.5 rounded border border-amber-500/30">💡 ${escapeHtml(suggestion)}</div>` : ''}
          </div>
          <button type="button" onclick="this.closest('.pointer-events-auto')?.remove()" class="text-neutral-400 hover:text-white text-xs cursor-pointer ml-1">&times;</button>
        </div>
      `;
      container.appendChild(toast);
      setTimeout(() => {
        toast.classList.add('opacity-0', '-translate-y-2');
        setTimeout(() => toast.remove(), 350);
      }, duration);
    }

"""

GLOBAL_SCRIPT_KICKOFF = r"""
    initMultiSelects();
    pollAdminState();
    loadCatalog();
    fetchIncomingFiles();
  </script>
"""

HTML_SHELL_BOTTOM = r"""
  <!-- Floating Sidebar Tooltip (Shown when sidebar is collapsed) -->
  <div id="sidebar-hover-tooltip" class="fixed z-[9999] hidden pointer-events-none px-3 py-1.5 rounded-lg bg-neutral-900 border border-neutral-700 text-white text-xs font-bold whitespace-nowrap shadow-2xl"></div>

  <!-- Toast Notification Container -->
  <div id="admin-toast-container" class="fixed top-5 right-5 z-[9999] flex flex-col gap-2.5 max-w-sm pointer-events-none"></div>
</body>
</html>
"""

HTML_CONTENT = "\n".join([
    HTML_SHELL_TOP,
    dashboard.DASHBOARD_HTML,
    download_manager.DOWNLOAD_MANAGER_HTML,
    media.MEDIA_HTML,
    metadata.METADATA_HTML,
    user_telemetry.USER_TELEMETRY_HTML,
    HTML_SHELL_CLOSE,
    user_telemetry.USER_DETAIL_MODAL_HTML,
    metadata.METADATA_MODALS_HTML,
    media.INGEST_MODAL_HTML,
    CONFIRM_MODAL_HTML,
    GLOBAL_SCRIPT_HEADER,
    GLOBAL_SHELL_JS,
    GLOBAL_TOAST_JS,
    dashboard.DASHBOARD_JS,
    download_manager.DOWNLOAD_MANAGER_JS,
    media.MEDIA_INGEST_JS,
    metadata.METADATA_JS,
    user_telemetry.USER_TELEMETRY_JS,
    GLOBAL_SCRIPT_KICKOFF,
    HTML_SHELL_BOTTOM
])

HTML_LOGIN_SHELL = r"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>StreamVault Admin Authentication (127.0.0.1:4000)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="stylesheet" href="/static/tailwind.min.css">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; background-color: #0c0c0c; color: #fff; }
    .mono { font-family: 'JetBrains Mono', monospace; }
  </style>
</head>
<body class="h-screen w-screen flex items-center justify-center bg-[#0c0c0c] p-4 text-white">
  <div class="bg-[#141414] border border-neutral-800 rounded-3xl p-8 max-w-md w-full space-y-6 shadow-2xl relative">
    <div class="flex items-center gap-3">
      <span class="px-2.5 py-0.5 bg-red-600 text-white font-mono font-black text-xs rounded tracking-wider">ADMIN</span>
      <h1 class="text-xl font-black text-white tracking-wide">StreamVault</h1>
    </div>
    <div class="space-y-1">
      <h2 class="text-base font-bold text-neutral-200">Local Operations Authentication</h2>
      <p class="text-xs text-neutral-400 leading-relaxed">Enter your configured <code class="mono text-red-400">ADMIN_API_SECRET</code> to unlock the local operations dashboard.</p>
    </div>
    <form id="admin-login-form" onsubmit="handleAdminLogin(event)" class="space-y-4">
      <div class="space-y-1.5">
        <label for="admin-secret" class="text-xs font-semibold text-neutral-400">Admin Secret</label>
        <input type="password" id="admin-secret" required autofocus placeholder="Paste ADMIN_API_SECRET..." class="w-full bg-[#1e1e1e] border border-neutral-700 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-red-500 font-mono transition">
      </div>
      <div id="login-error" class="hidden text-xs text-red-400 bg-red-950/40 border border-red-800/60 rounded-xl p-3 font-semibold"></div>
      <button type="submit" id="login-btn" class="w-full bg-red-600 hover:bg-red-700 text-white font-bold py-2.5 rounded-xl text-sm transition cursor-pointer shadow-lg shadow-red-600/20">
        Unlock Dashboard
      </button>
    </form>
  </div>
  <script>
    async function handleAdminLogin(e) {
      e.preventDefault();
      const secret = document.getElementById('admin-secret').value.trim();
      const errEl = document.getElementById('login-error');
      const btn = document.getElementById('login-btn');
      errEl.classList.add('hidden');
      btn.disabled = true;
      btn.innerText = 'Verifying...';
      try {
        const res = await fetch('/api/admin/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ secret })
        });
        if (res.ok) {
          window.location.reload();
        } else {
          const data = await res.json().catch(() => ({}));
          errEl.innerText = data.detail || 'Invalid admin secret. Check your .env file.';
          errEl.classList.remove('hidden');
          btn.disabled = false;
          btn.innerText = 'Unlock Dashboard';
        }
      } catch (err) {
        errEl.innerText = 'Connection error. Ensure Admin server is running.';
        errEl.classList.remove('hidden');
        btn.disabled = false;
        btn.innerText = 'Unlock Dashboard';
      }
    }
  </script>
</body>
</html>
"""

@app.post("/api/admin/auth/login")
async def admin_auth_login(request: Request):
    try:
        data = await request.json()
        secret = (data.get("secret") or "").strip()
    except Exception:
        secret = ""

    if not ADMIN_SECRET or hmac.compare_digest(secret.encode("utf-8"), ADMIN_SECRET.encode("utf-8")):
        resp = JSONResponse(content={"status": "ok", "message": "Authenticated"})
        resp.set_cookie(
            "streamvault_admin_session",
            _get_admin_session_token(),
            httponly=True,
            samesite="lax",
            path="/"
        )
        return resp
    raise HTTPException(status_code=401, detail="Invalid admin secret")

@app.post("/api/admin/auth/logout")
async def admin_auth_logout():
    resp = JSONResponse(content={"status": "ok", "message": "Logged out"})
    resp.delete_cookie("streamvault_admin_session", path="/")
    return resp

@app.get("/", response_class=HTMLResponse)
async def admin_root(request: Request):
    if not is_admin_authenticated(request):
        return HTMLResponse(content=HTML_LOGIN_SHELL, status_code=200)
    return HTMLResponse(content=HTML_CONTENT, status_code=200)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("admin-local.server:app", host="127.0.0.1", port=4000, reload=True)
