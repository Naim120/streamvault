import collections
import hmac
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from .config import settings
from .api import stream, system, telemetry, aria2, metadata, auth_guard, auth
from .transcoder.worker import transcode_queue
from .aria2.monitor import aria2_monitor
from .utils.process_identity import set_process_identity, ensure_branded_symlinks
from .security import extract_trusted_client_ip, get_active_cloudflared_origin

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
# Silence noisy outbound polling logs from httpx and httpcore
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger("streamvault")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Set production OS process identity
    set_process_identity("streamvault-api")
    ensure_branded_symlinks(BASE_DIR)

    # Startup sequence
    logger.info("Starting StreamVault Media Server...")
    
    # Supabase credentials sanity check
    if not settings.SUPABASE_URL or not (settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY):
        raise RuntimeError("CRITICAL: Supabase credentials (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY/ANON_KEY) are missing in environment!")
    # Initialize hardware governor settings from .env / Supabase
    try:
        await system.init_system_settings()
    except Exception as e:
        logger.warning(f"Initial system_settings load notice: {e}")

    # Synchronize Supabase Auth settings (site_url & uri_allow_list) to reachable LAN IP / domain
    try:
        await auth.sync_supabase_auth_settings()
    except Exception as e:
        logger.warning(f"Initial supabase auth settings sync notice: {e}")

    # Backfill exact video playlist durations for episodes and movies
    try:
        from .api.metadata import backfill_episode_durations
        await backfill_episode_durations()
    except Exception as e:
        logger.warning(f"Initial episode/media duration backfill notice: {e}")

    transcode_queue.start_worker()
    aria2_monitor.start()
    yield
    # Shutdown sequence
    logger.info("Shutting down StreamVault Media Server...")
    from .database import db
    try:
        await db.aclose()
    except Exception:
        pass
    if transcode_queue.worker_task and not transcode_queue.worker_task.done():
        transcode_queue.worker_task.cancel()
    if aria2_monitor.monitor_task and not aria2_monitor.monitor_task.done():
        aria2_monitor.monitor_task.cancel()

APP_ENV = os.getenv("APP_ENV", "production").lower()
ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "false").lower() == "true"

# Docs are strictly disabled in production unless explicitly enabled in local development
if APP_ENV == "development" and ENABLE_API_DOCS:
    docs_url = "/api/docs"
    redoc_url = "/api/redoc"
    openapi_url = "/api/openapi.json"
else:
    docs_url = None
    redoc_url = None
    openapi_url = None

app = FastAPI(
    title="StreamVault Media Engine API",
    version="2.5.0",
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url,
    description="Production-grade, low-overhead self-hostable video streaming media server",
    lifespan=lifespan
)

# Middleware guard returning 404 for docs if accessed over external proxies or in production
class DocsSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in ("/api/docs", "/api/redoc", "/api/openapi.json"):
            if not (APP_ENV == "development" and ENABLE_API_DOCS):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            # Never expose docs over Cloudflare Tunnel, Nginx external proxy, or external headers
            if (
                request.headers.get("CF-Connecting-IP")
                or request.headers.get("X-Forwarded-For")
                or request.headers.get("X-Real-IP")
            ):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
        return await call_next(request)

# 1. Sliding-Window In-Memory Thread-Safe Rate Limiter Middleware
class SlidingWindowRateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.stream_buckets: dict[str, deque] = defaultdict(deque)
        self.auth_buckets: dict[str, deque] = defaultdict(deque)
        self.general_buckets: dict[str, deque] = defaultdict(deque)
        self.admin_internal_buckets: dict[str, deque] = defaultdict(deque)
        self.lock = threading.Lock()
        self.last_cleanup = time.time()

    def _cleanup_expired(self, now: float):
        if now - self.last_cleanup < 60.0:
            return
        self.last_cleanup = now
        cutoff = now - 60.0
        for bucket in (self.stream_buckets, self.auth_buckets, self.general_buckets, self.admin_internal_buckets):
            empty_keys = []
            for ip, q in bucket.items():
                while q and q[0] <= cutoff:
                    q.popleft()
                if not q:
                    empty_keys.append(ip)
            for ip in empty_keys:
                del bucket[ip]

    def _get_client_ip(self, request: Request) -> str:
        return extract_trusted_client_ip(request)

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        client_host = request.client.host if request.client else "127.0.0.1"
        client_ip = self._get_client_ip(request)
        now = time.time()

        # Check for verified local admin loopback with valid X-Admin-Secret
        is_loopback = client_host in ("127.0.0.1", "::1", "localhost", "testclient")
        has_proxy_headers = bool(
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Forwarded-For")
            or request.headers.get("X-Real-IP")
        )
        admin_secret_hdr = request.headers.get("X-Admin-Secret", "").strip()
        configured_secret = (
            getattr(settings, "ADMIN_API_SECRET", "")
            or os.getenv("ADMIN_API_SECRET", "")
            or os.getenv("ADMIN_SECRET", "")
        ).strip()

        is_authenticated_admin_loopback = (
            is_loopback
            and not has_proxy_headers
            and bool(admin_secret_hdr)
            and bool(configured_secret)
            and hmac.compare_digest(admin_secret_hdr, configured_secret)
        )

        # Route rate limit classification
        if is_authenticated_admin_loopback:
            limit = 600  # Max 600 req/min for verified internal admin loopback (prevents 429 on dashboard polling)
            bucket_map = self.admin_internal_buckets
        elif path.startswith("/api/stream"):
            limit = 600  # Max 600 req/min for HLS segments & playlists
            bucket_map = self.stream_buckets
        elif path.startswith("/api/system"):
            limit = 20   # Strictly 20 req/min for unauthenticated / non-admin system queries
            bucket_map = self.auth_buckets
        elif (
            path.startswith("/api/telemetry")
            or path.startswith("/api/aria2")
            or request.method in ("POST", "PUT", "PATCH", "DELETE")
        ):
            limit = 30   # Max 30 req/min for auth, mutations, and telemetry
            bucket_map = self.auth_buckets
        else:
            limit = 120  # Max 120 req/min for general API & catalog queries
            bucket_map = self.general_buckets

        with self.lock:
            self._cleanup_expired(now)
            q = bucket_map[client_ip]
            cutoff = now - 60.0
            while q and q[0] <= cutoff:
                q.popleft()

            if len(q) >= limit:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too Many Requests: Rate limit exceeded. Please slow down."},
                    headers={"Retry-After": "10"}
                )

            q.append(now)

        return await call_next(request)

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
                if cl_bytes < 0 or cl_bytes > max_bytes:
                    resp = Response(
                        status_code=413 if cl_bytes > max_bytes else 400,
                        content=json.dumps({"detail": f"Payload Too Large (max {max_bytes} bytes)" if cl_bytes > max_bytes else "Invalid Content-Length"}),
                        media_type="application/json"
                    )
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
                resp = Response(
                    status_code=413,
                    content=json.dumps({"detail": f"Payload Too Large (max {max_bytes} bytes)"}),
                    media_type="application/json"
                )
                await resp(scope, receive, send)
            return

        await self.app(scope, receive, send)

def _safe_base_url(request: Request) -> str:
    raw_proto = (request.headers.get("X-Forwarded-Proto") or "").strip().lower()
    scheme = raw_proto if raw_proto in ("http", "https") else (request.url.scheme if request.url.scheme in ("http", "https") else "http")
    raw_host = (request.headers.get("Host") or request.url.netloc or "localhost:8080").strip()
    host = raw_host if re.fullmatch(r"[A-Za-z0-9.\-:[\]]+", raw_host) else "localhost:8080"
    return f"{scheme}://{host}".rstrip("/")

# Build Dynamic CSP Directives (single-origin relative routing)
_csp_connect = [
    "'self'",
    "https://*.supabase.co",
    "wss://*.supabase.co",
    "https://api4.ipify.org",
    "https://api6.ipify.org",
    "https://ipv4.icanhazip.com",
    "https://ipv6.icanhazip.com",
    "capacitor://localhost",
    "http://192.168.*:*",
]

_csp_media = [
    "'self'",
    "blob:",
    "capacitor://localhost",
    "http://192.168.*:*",
]

# Inject configured external origins, LAN hostnames, and public URLs from .env
for _ext in (
    *settings.cors_origins_list,
    os.getenv("FRONTEND_ORIGIN", ""),
    os.getenv("PUBLIC_API_URL", ""),
    settings.STREAM_PUBLIC_BASE_URL,
):
    _val = _ext.strip()
    if _val:
        if _val not in _csp_connect:
            _csp_connect.append(_val)
        if _val not in _csp_media:
            _csp_media.append(_val)

# 3. StreamVault Security & Production Headers Middleware
class StreamVaultSecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["Server"] = "StreamVault-Media-Engine/2.5"
        response.headers["X-StreamVault-Version"] = "2.5.0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"

        active_cf = get_active_cloudflared_origin()
        extra_cf = f" {active_cf}" if active_cf else ""
        if request.url.path in ("/api/docs", "/api/redoc", "/api/openapi.json"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fastapi.tiangolo.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fastapi.tiangolo.com; "
                "img-src 'self' data: https://cdn.jsdelivr.net https://fastapi.tiangolo.com https://image.tmdb.org https://*.media-amazon.com https://images.unsplash.com; "
                f"media-src {' '.join(_csp_media)}{extra_cf}; "
                f"connect-src {' '.join(_csp_connect)}{extra_cf}; "
                "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
                "object-src 'none'; "
                "base-uri 'self';"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https://image.tmdb.org https://*.media-amazon.com https://images.unsplash.com; "
                f"media-src {' '.join(_csp_media)}{extra_cf}; "
                f"connect-src {' '.join(_csp_connect)}{extra_cf}; "
                "frame-src https://www.youtube.com https://www.youtube-nocookie.com; "
                "object-src 'none'; "
                "base-uri 'self';"
            )
        return response

app.add_middleware(StreamVaultSecurityHeadersMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(SlidingWindowRateLimiterMiddleware)
app.add_middleware(DocsSecurityMiddleware)

# Strict CORS Allowlist (No Wildcards)
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4000",
    "http://127.0.0.1:4000",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "capacitor://localhost",
    "http://localhost",
    "https://localhost",
]
for extra_origin in settings.cors_origins_list:
    if extra_origin and extra_origin not in ALLOWED_ORIGINS:
        ALLOWED_ORIGINS.append(extra_origin)

frontend_env_origin = os.getenv("FRONTEND_ORIGIN")
if frontend_env_origin and frontend_env_origin not in ALLOWED_ORIGINS:
    ALLOWED_ORIGINS.append(frontend_env_origin.strip())

if settings.PUBLIC_DOMAIN:
    pub_dom = settings.PUBLIC_DOMAIN.strip().lower()
    for scheme in ("https://", "http://"):
        origin_candidate = f"{scheme}{pub_dom}"
        if origin_candidate not in ALLOWED_ORIGINS:
            ALLOWED_ORIGINS.append(origin_candidate)


class DynamicCORSMiddleware(CORSMiddleware):
    """
    Statically allows approved origins and LAN subnets.
    Only allows a trycloudflare.com origin if it matches THIS host's active tunnel,
    rejecting any third-party attacker-controlled *.trycloudflare.com tunnels.
    Also validates any configured public domain (e.g. *.pages.dev) and native APK schemes.
    """
    def is_allowed_origin(self, origin: str) -> bool:
        if super().is_allowed_origin(origin):
            return True
        if origin in ("capacitor://localhost", "http://localhost", "https://localhost"):
            return True
        if origin and re.match(r"^https://[a-zA-Z0-9_.-]+\.pages\.dev(:\d+)?$", origin):
            return True
        if origin and "trycloudflare.com" in origin:
            active_tunnel = get_active_cloudflared_origin()
            if active_tunnel and origin.rstrip("/") == active_tunnel.rstrip("/"):
                return True
        if origin and settings.PUBLIC_DOMAIN and settings.PUBLIC_DOMAIN.lower() in origin.lower():
            return True
        return False


app.add_middleware(
    DynamicCORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|100\.\d+\.\d+\.\d+)(:\d+)?|capacitor://localhost|https://[a-zA-Z0-9_.-]+\.pages\.dev(:\d+)?)$",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

@app.get("/api/ping")
async def ping():
    """
    Public connectivity and discovery verification beacon for Android TV / JioOS and native clients.
    """
    return {"status": "ok", "service": "streamvault", "version": "2.6.0"}

# Include API Routers
app.include_router(stream.router)
app.include_router(system.router)
app.include_router(telemetry.router)
app.include_router(aria2.router)
app.include_router(metadata.router)
app.include_router(auth_guard.router)
app.include_router(auth.router)

@app.get("/")
async def root():
    resp = {
        "app": settings.APP_NAME,
        "status": "online"
    }
    if APP_ENV == "development" and ENABLE_API_DOCS:
        resp["docs_url"] = "/api/docs"
    return resp

@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "transcode_queue": transcode_queue.get_status()
    }

@app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
async def get_sitemap(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200, media_type="application/xml")
    base_url = _safe_base_url(request)

    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{base_url}/</loc>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{base_url}/legal/terms</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
  <url>
    <loc>{base_url}/legal/privacy</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
  <url>
    <loc>{base_url}/legal/dmca</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
  <url>
    <loc>{base_url}/legal/cookies</loc>
    <changefreq>monthly</changefreq>
    <priority>0.5</priority>
  </url>
</urlset>"""
    return Response(content=xml_content.strip(), media_type="application/xml")

@app.get("/robots.txt")
async def get_robots(request: Request):
    base_url = _safe_base_url(request)
    content = f"""User-agent: *
Allow: /
Allow: /legal/
Disallow: /api/
Disallow: /home
Sitemap: {base_url}/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.MEDIA_SERVER_HOST,
        port=settings.MEDIA_SERVER_PORT,
        reload=True
    )
