import logging
import re
import threading
import time
from collections import defaultdict, deque
from typing import Optional
import urllib.parse
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from ..config import settings
from ..security import extract_trusted_client_ip

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth Security Guard"])

# Thread-safe rate limiter state
_email_timestamps: dict[str, float] = {}
_ip_buckets: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()

EMAIL_COOLDOWN_SECONDS = 60
IP_WINDOW_SECONDS = 900  # 15 minutes
IP_MAX_REQUESTS = 3       # Max 3 auth emails per 15 min per IP

# Allowed origin patterns
ALLOWED_ORIGIN_REGEX = re.compile(
    r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|"
    r"https://[a-zA-Z0-9_.-]+\.trycloudflare\.com(:\d+)?|"
    r"https?://192\.168\.\d+\.\d+(:\d+)?|"
    r"https?://10\.\d+\.\d+\.\d+(:\d+)?|"
    r"https?://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+(:\d+)?|"
    r"capacitor://localhost)$"
)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class AuthEmailRequest(BaseModel):
    model_config = {"populate_by_name": True}

    email: str = Field(..., description="Target email address")
    honeypot: Optional[str] = Field(None, alias="_honeypot", description="Spam prevention honeypot")
    origin: Optional[str] = Field(None, description="Client window.location.origin")
    client_origin: Optional[str] = Field(None, description="Client window.location.origin")


def get_client_ip(request: Request) -> str:
    return extract_trusted_client_ip(request)


def _is_allowed(candidate: str) -> bool:
    if not candidate:
        return False
    try:
        parsed = urlparse(candidate.strip())
        if not parsed.scheme or not parsed.netloc:
            return False
        base = f"{parsed.scheme}://{parsed.netloc}"
        if ALLOWED_ORIGIN_REGEX.match(base):
            return True
        configured = getattr(settings, "FRONTEND_ORIGIN", None)
        if configured and base.rstrip("/") == configured.rstrip("/"):
            return True
    except Exception:
        return False
    return False


def validate_and_resolve_origin(candidate: Optional[str], request: Request) -> str:
    """
    Validates origin in priority order:
    1. Validated payload.client_origin / payload.origin from request body
    2. Origin header from browser request
    3. Reconstructed ${X-Forwarded-Proto}://${X-Forwarded-Host or Host}
    4. Referer header from request
    5. Configured FRONTEND_ORIGIN
    6. Fallback to localhost:5173
    """
    # 1. Body payload origin
    if candidate and _is_allowed(candidate):
        parsed = urlparse(candidate.strip())
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    # 2. Browser Origin header
    origin_hdr = request.headers.get("origin")
    if origin_hdr and _is_allowed(origin_hdr):
        parsed = urlparse(origin_hdr.strip())
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    # 3. Reconstructed from proxy headers
    proto = request.headers.get("x-forwarded-proto") or request.headers.get("x-forwarded-scheme")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        scheme = proto if proto in ("http", "https") else ("https" if "trycloudflare.com" in host else "http")
        reconstructed = f"{scheme}://{host}"
        if _is_allowed(reconstructed):
            parsed = urlparse(reconstructed)
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    # 4. Referer header
    referer_hdr = request.headers.get("referer")
    if referer_hdr and _is_allowed(referer_hdr):
        parsed = urlparse(referer_hdr.strip())
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    # 5. Configured FRONTEND_ORIGIN
    configured = getattr(settings, "FRONTEND_ORIGIN", None)
    if configured and _is_allowed(configured):
        return configured.rstrip("/")

    return "http://localhost:5173"


def check_rate_limits(email: str, client_ip: str):
    now = time.time()
    with _rate_lock:
        # 1. Per-email rate limit (1 request / 60s)
        last_req = _email_timestamps.get(email, 0.0)
        email_elapsed = now - last_req
        if email_elapsed < EMAIL_COOLDOWN_SECONDS:
            retry_after = int(EMAIL_COOLDOWN_SECONDS - email_elapsed) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {retry_after} seconds before requesting another email.",
                headers={"Retry-After": str(retry_after)}
            )

        # 2. Per-IP rate limit (3 requests / 15m)
        q = _ip_buckets[client_ip]
        cutoff = now - IP_WINDOW_SECONDS
        while q and q[0] <= cutoff:
            q.popleft()

        if len(q) >= IP_MAX_REQUESTS:
            oldest = q[0]
            retry_after = int(IP_WINDOW_SECONDS - (now - oldest)) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests from this network. Please try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)}
            )


def record_request_success(email: str, client_ip: str):
    now = time.time()
    with _rate_lock:
        _email_timestamps[email] = now
        _ip_buckets[client_ip].append(now)


def record_ip_penalty(client_ip: str):
    """Increments IP bucket on failed probes to throttle brute-force scanners"""
    now = time.time()
    with _rate_lock:
        _ip_buckets[client_ip].append(now)


async def fetch_user_auth_status(email: str) -> dict:
    """
    Queries Supabase RPC get_user_auth_status via Service Role Key
    Returns: {"user_exists": bool, "email_confirmed": bool, "user_id": str | None}
    """
    url = f"{settings.SUPABASE_URL.rstrip('/')}/rest/v1/rpc/get_user_auth_status"
    key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(url, headers=headers, json={"user_email": email})
            if resp.status_code == 200:
                rows = resp.json()
                if isinstance(rows, list) and len(rows) > 0:
                    return rows[0]
                elif isinstance(rows, dict):
                    return rows
            logger.warning(f"get_user_auth_status returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error calling get_user_auth_status RPC: {e}")

    # Fallback: check check_user_email_exists
    fallback_url = f"{settings.SUPABASE_URL.rstrip('/')}/rest/v1/rpc/check_user_email_exists"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(fallback_url, headers=headers, json={"user_email": email})
            if resp.status_code == 200:
                exists = bool(resp.json())
                return {"user_exists": exists, "email_confirmed": exists, "user_id": None}
        except Exception as e:
            logger.error(f"Error calling fallback check_user_email_exists RPC: {e}")

    return {"user_exists": False, "email_confirmed": False, "user_id": None}


@router.post("/forgot-password")
async def forgot_password(payload: AuthEmailRequest, request: Request):
    """
    Production-hardened password reset endpoint:
    - Enforces 60-second per-email rate limiting and 3-req/15min per-IP rate limiting
    - Verifies user exists in auth.users before sending any recovery email
    - Silently drops automated bots hitting hidden honeypot
    - Directs recovery link to the validated requesting origin
    """
    client_ip = get_client_ip(request)

    # 1. Honeypot check for bots
    if payload.honeypot and payload.honeypot.strip():
        logger.warning(f"Honeypot triggered from IP {client_ip} on forgot-password")
        return {"message": "If the email is registered, a password reset link has been dispatched.", "cooldown_seconds": 60}

    # 2. Email normalization and syntax validation
    cleaned_email = payload.email.strip().lower()
    if not EMAIL_REGEX.match(cleaned_email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    # 3. Check rate limits
    check_rate_limits(cleaned_email, client_ip)

    # 4. User existence verification
    user_status = await fetch_user_auth_status(cleaned_email)
    if not user_status.get("user_exists"):
        record_ip_penalty(client_ip)
        return {
            "message": "If an account is registered with that email, a password reset link has been dispatched.",
            "cooldown_seconds": 60
        }

    # 5. Construct validated dynamic redirect URL
    candidate_origin = payload.client_origin or payload.origin
    origin = validate_and_resolve_origin(candidate_origin, request)
    redirect_url = f"{origin.rstrip('/')}/?mode=reset-password"

    # 6. Dispatch recovery email via Supabase GoTrue
    encoded_redirect = urllib.parse.quote(redirect_url, safe="")
    recover_endpoint = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/recover?redirect_to={encoded_redirect}"
    headers = {
        "apikey": settings.SUPABASE_ANON_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_ANON_KEY}",
        "Content-Type": "application/json"
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                recover_endpoint,
                headers=headers,
                json={
                    "email": cleaned_email,
                    "redirect_to": redirect_url
                }
            )
            if resp.status_code >= 400:
                err_msg = resp.text
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("msg") or err_json.get("error_description") or err_json.get("message") or resp.text
                except Exception:
                    pass
                logger.error(f"Supabase recover returned {resp.status_code}: {err_msg}")
                if "rate" in err_msg.lower() or "too many" in err_msg.lower() or resp.status_code == 429:
                    raise HTTPException(
                        status_code=429,
                        detail="Too many reset attempts. Please wait a minute before trying again.",
                        headers={"Retry-After": "60"}
                    )
                raise HTTPException(status_code=resp.status_code, detail=err_msg)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to dispatch recovery email: {e}")
            raise HTTPException(status_code=500, detail="Internal server error sending recovery link")

    # 7. Record success in rate limiters
    record_request_success(cleaned_email, client_ip)

    return {
        "message": "If an account is registered with that email, a password reset link has been dispatched.",
        "cooldown_seconds": 60
    }
