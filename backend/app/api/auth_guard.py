import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from typing import Optional
import urllib.parse
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from ..config import settings
from ..database import db
from ..security import extract_trusted_client_ip, get_active_cloudflared_origin, get_stream_secret, require_authenticated_user, revoke_user_tokens

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth Security Guard"])

# Thread-safe progressive reset state tracker & IP bucket limiter
# _email_reset_state[email] = {
#   "send_count": int,
#   "window_started_at": float,
#   "last_sent_at": float,
#   "cooldown_seconds": int,
#   "completed_at": float,
# }
_email_reset_state: dict[str, dict] = {}
_ip_buckets: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()

WINDOW_SECONDS = 900             # 15-minute reset window
MAX_RESETS_PER_WINDOW = 3        # Maximum 3 sends (1 initial + 2 resends) per 15 min
PROGRESSIVE_COOLDOWNS = {
    1: 60,                       # Send #1 -> 60s cooldown (2 attempts remaining)
    2: 180,                      # Send #2 -> 180s cooldown (1 attempt remaining)
    3: 900,                      # Send #3 -> 900s lock (0 attempts remaining)
}
IP_WINDOW_SECONDS = 900          # 15 minutes
IP_MAX_REQUESTS = 3              # Max 3 auth emails per 15 min per IP

# Allowed origin patterns
ALLOWED_ORIGIN_REGEX = re.compile(
    r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|"
    r"https?://192\.168\.\d+\.\d+(:\d+)?|"
    r"https?://10\.\d+\.\d+\.\d+(:\d+)?|"
    r"https?://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+(:\d+)?|"
    r"https://[a-zA-Z0-9_.-]+\.pages\.dev(:\d+)?|"
    r"https://[a-zA-Z0-9-]+\.trycloudflare\.com(:\d+)?|"
    r"capacitor://localhost)$"
)

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class AuthEmailRequest(BaseModel):
    model_config = {"populate_by_name": True}

    email: str = Field(..., description="Target email address")
    honeypot: Optional[str] = Field(None, alias="_honeypot", description="Spam prevention honeypot")
    origin: Optional[str] = Field(None, description="Client window.location.origin")
    client_origin: Optional[str] = Field(None, description="Client window.location.origin")


class ResetFlowStatusRequest(BaseModel):
    email: str = Field(..., description="Target email address")
    reset_flow_token: str = Field(..., description="HMAC reset flow token")


class PasswordResetCompleteRequest(BaseModel):
    email: Optional[str] = Field(None, description="Optional email confirmation")


def generate_reset_flow_token(email: str, initiated_ts: int) -> str:
    """Generates an opaque HMAC-SHA256 flow token bound to email and initiation timestamp."""
    clean_email = email.strip().lower()
    message = f"reset_flow:{clean_email}:{initiated_ts}".encode("utf-8")
    secret = get_stream_secret()
    sig = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return f"{initiated_ts}.{sig}"


def verify_reset_flow_token(email: str, reset_flow_token: str) -> Optional[int]:
    """Verifies reset_flow_token HMAC signature and 15-minute validity window."""
    if not reset_flow_token or "." not in reset_flow_token:
        return None
    try:
        ts_str, provided_sig = reset_flow_token.split(".", 1)
        initiated_ts = int(ts_str)
    except (ValueError, TypeError):
        return None

    now = int(time.time())
    if initiated_ts > now + 60 or (now - initiated_ts) > WINDOW_SECONDS:
        return None

    clean_email = email.strip().lower()
    message = f"reset_flow:{clean_email}:{initiated_ts}".encode("utf-8")
    secret = get_stream_secret()
    expected_sig = hmac.new(secret, message, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected_sig, provided_sig):
        return initiated_ts
    return None


def get_client_ip(request: Request) -> str:
    return extract_trusted_client_ip(request)


def _is_allowed(candidate: str) -> bool:
    if not candidate:
        return False
    try:
        parsed = urlparse(candidate.strip())
        if not parsed.scheme or not parsed.netloc:
            return False
        base = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
        host_name = parsed.hostname or ""

        # If candidate is a trycloudflare domain, strictly require match against the active tunnel for this host
        if "trycloudflare.com" in host_name:
            active_tunnel = get_active_cloudflared_origin()
            if active_tunnel and base == active_tunnel.rstrip("/"):
                return True
            pub_domain = (os.environ.get("PUBLIC_DOMAIN") or getattr(settings, "PUBLIC_DOMAIN", None) or "").strip().rstrip("/")
            if pub_domain and host_name == pub_domain:
                return True
            return False

        # If candidate is a pages.dev domain, validate against allowed regex
        if "pages.dev" in host_name:
            if ALLOWED_ORIGIN_REGEX.match(base):
                return True

        # If public domain or public site URL is configured, require match
        pub_domain = (os.environ.get("PUBLIC_DOMAIN") or getattr(settings, "PUBLIC_DOMAIN", None) or "").strip().rstrip("/")
        pub_site = (os.environ.get("PUBLIC_SITE_URL") or getattr(settings, "PUBLIC_SITE_URL", None) or "").strip().rstrip("/")
        if pub_domain and pub_domain not in ("localhost", "127.0.0.1"):
            return host_name == pub_domain or host_name.endswith(f".{pub_domain}")
        if pub_site and "localhost" not in pub_site and "127.0.0.1" not in pub_site:
            return base == pub_site.rstrip("/")

        if ALLOWED_ORIGIN_REGEX.match(base):
            return True
        configured = getattr(settings, "FRONTEND_ORIGIN", None)
        if configured and base == configured.rstrip("/"):
            return True
    except Exception:
        return False
    return False


def validate_and_resolve_origin(candidate: Optional[str], request: Request) -> str:
    """
    Validates origin in priority order:
    1. Configured PUBLIC_SITE_URL, PUBLIC_DOMAIN, or FRONTEND_ORIGIN (if non-localhost)
    2. Validated payload.client_origin / payload.origin from request body
    3. Origin header from browser request
    4. Reconstructed ${X-Forwarded-Proto}://${X-Forwarded-Host or Host}
    5. Referer header from request
    6. Fallback to localhost:5173
    """
    for env_key in ("PUBLIC_SITE_URL", "PUBLIC_DOMAIN", "FRONTEND_ORIGIN"):
        val = (os.environ.get(env_key) or getattr(settings, env_key, None) or "").strip().rstrip("/")
        if val:
            if not val.startswith("http://") and not val.startswith("https://"):
                val = f"https://{val}"
            try:
                parsed = urlparse(val)
                host_part = parsed.hostname or ""
                if host_part not in ("localhost", "127.0.0.1") and not host_part.startswith("127."):
                    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
            except Exception:
                pass

    # 2. Body payload origin
    if candidate and _is_allowed(candidate):
        parsed = urlparse(candidate.strip())
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    # 3. Browser Origin header
    origin_hdr = request.headers.get("origin")
    if origin_hdr and _is_allowed(origin_hdr):
        parsed = urlparse(origin_hdr.strip())
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    # 4. Reconstructed from proxy headers
    proto = request.headers.get("x-forwarded-proto") or request.headers.get("x-forwarded-scheme")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        scheme = proto if proto in ("http", "https") else ("https" if "trycloudflare.com" in host else "http")
        reconstructed = f"{scheme}://{host}"
        if _is_allowed(reconstructed):
            parsed = urlparse(reconstructed)
            return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    # 5. Referer header
    referer_hdr = request.headers.get("referer")
    if referer_hdr and _is_allowed(referer_hdr):
        parsed = urlparse(referer_hdr.strip())
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")

    return "http://localhost:5173"


def evaluate_reset_preflight(email: str, client_ip: str) -> dict:
    """
    Checks progressive cooldowns, 3-send window cap, completion state, and IP limits.
    Atomically reserves the slot if allowed.
    Returns:
      - {"type": "already_completed", "payload": ...} if password was already reset after last send
      - {"type": "rate_limited", "content": ..., "retry_after": int} if throttled (429)
      - {"type": "reserved", "payload": ...} if reserved and allowed
    """
    now = time.time()
    with _rate_lock:
        state = _email_reset_state.get(email)

        if state:
            window_started_at = float(state.get("window_started_at", now))
            if (now - window_started_at) >= WINDOW_SECONDS:
                state["send_count"] = 0
                state["window_started_at"] = now
                state["last_sent_at"] = 0.0
                state["cooldown_seconds"] = 0
                state["completed_at"] = 0.0

            last_sent_at = float(state.get("last_sent_at", 0.0))
            completed_at = float(state.get("completed_at", 0.0))
            if last_sent_at > 0 and completed_at >= last_sent_at and (now - completed_at) < WINDOW_SECONDS:
                return {
                    "type": "already_completed",
                    "payload": {
                        "status": "already_completed",
                        "password_reset_completed": True,
                        "message": "Your password has already been updated! Please sign in with your new password.",
                        "cooldown_seconds": 0,
                        "attempts_remaining": 0,
                        "max_attempts_reached": False,
                    },
                }

            send_count = int(state.get("send_count", 0))
            if send_count >= MAX_RESETS_PER_WINDOW:
                remaining_window = max(1, int(WINDOW_SECONDS - (now - float(state["window_started_at"]))) + 1)
                return {
                    "type": "rate_limited",
                    "retry_after": remaining_window,
                    "content": {
                        "detail": "Maximum password reset links (3/3) have been sent for this session. Please check your inbox and spam folder, or try again in 15 minutes.",
                        "retry_after_seconds": remaining_window,
                        "attempts_remaining": 0,
                        "max_attempts_reached": True,
                    },
                }

            active_cooldown = int(state.get("cooldown_seconds", 0))
            if float(state.get("last_sent_at", 0.0)) > 0 and active_cooldown > 0:
                elapsed = now - float(state["last_sent_at"])
                if elapsed < active_cooldown:
                    retry_after = max(1, int(active_cooldown - elapsed) + 1)
                    attempts_rem = max(0, MAX_RESETS_PER_WINDOW - send_count)
                    return {
                        "type": "rate_limited",
                        "retry_after": retry_after,
                        "content": {
                            "detail": f"Please wait {retry_after} seconds before requesting another email.",
                            "retry_after_seconds": retry_after,
                            "attempts_remaining": attempts_rem,
                            "max_attempts_reached": (attempts_rem == 0),
                        },
                    }

        # Global Per-IP rate limit (3 requests / 15m across distinct target emails)
        q = _ip_buckets[client_ip]
        cutoff = now - IP_WINDOW_SECONDS
        while q and q[0] <= cutoff:
            q.popleft()

        if len(q) >= IP_MAX_REQUESTS:
            oldest = q[0]
            retry_after = max(1, int(IP_WINDOW_SECONDS - (now - oldest)) + 1)
            return {
                "type": "rate_limited",
                "retry_after": retry_after,
                "content": {
                    "detail": f"Too many requests from this network. Please try again in {retry_after} seconds.",
                    "retry_after_seconds": retry_after,
                },
            }

        if not state:
            state = {
                "send_count": 0,
                "window_started_at": now,
                "last_sent_at": 0.0,
                "cooldown_seconds": 0,
                "completed_at": 0.0,
            }
            _email_reset_state[email] = state

        if float(state.get("completed_at", 0.0)) > 0 and now >= float(state["completed_at"]):
            state["completed_at"] = 0.0

        new_count = int(state.get("send_count", 0)) + 1
        next_cooldown = PROGRESSIVE_COOLDOWNS.get(new_count, WINDOW_SECONDS)
        state["send_count"] = new_count
        state["last_sent_at"] = now
        state["cooldown_seconds"] = next_cooldown
        q.append(now)

    attempts_remaining = max(0, MAX_RESETS_PER_WINDOW - new_count)
    return {
        "type": "reserved",
        "payload": {
            "status": "sent",
            "message": "If an account is registered with that email, a password reset link has been dispatched.",
            "cooldown_seconds": next_cooldown,
            "attempts_remaining": attempts_remaining,
            "max_attempts_reached": (attempts_remaining == 0),
            "reset_flow_token": generate_reset_flow_token(email, int(now)),
        },
    }


def record_reset_attempt(email: str, client_ip: str) -> dict:
    """
    Records a password reset send (for both existing and non-existent emails to preserve anti-enumeration),
    updates progressive cooldown state, and returns the standardized response payload with reset_flow_token.
    """
    now = time.time()
    with _rate_lock:
        state = _email_reset_state.get(email)
        if not state or (now - float(state.get("window_started_at", now))) >= WINDOW_SECONDS:
            state = {
                "send_count": 0,
                "window_started_at": now,
                "last_sent_at": 0.0,
                "cooldown_seconds": 0,
                "completed_at": float(state.get("completed_at", 0.0)) if state else 0.0,
            }
            _email_reset_state[email] = state

        if float(state.get("completed_at", 0.0)) > 0 and now >= float(state["completed_at"]):
            state["completed_at"] = 0.0

        state["send_count"] = int(state.get("send_count", 0)) + 1
        send_count = state["send_count"]
        next_cooldown = PROGRESSIVE_COOLDOWNS.get(send_count, WINDOW_SECONDS)
        attempts_remaining = max(0, MAX_RESETS_PER_WINDOW - send_count)

        state["last_sent_at"] = now
        state["cooldown_seconds"] = next_cooldown
        _ip_buckets[client_ip].append(now)

    initiated_ts = int(now)
    flow_token = generate_reset_flow_token(email, initiated_ts)
    return {
        "status": "sent",
        "message": "If an account is registered with that email, a password reset link has been dispatched.",
        "cooldown_seconds": next_cooldown,
        "attempts_remaining": attempts_remaining,
        "max_attempts_reached": (attempts_remaining == 0),
        "reset_flow_token": flow_token,
    }


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
    client = db.get_http_client()
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
    - Enforces progressive per-email backoff (60s -> 180s -> 900s lock after 3 sends in 15m)
    - Enforces 3-req/15min per-IP rate limiting
    - Short-circuits already-completed password resets without dispatching extra emails
    - Preserves strict anti-enumeration (identical schema & flow token for non-existent emails)
    """
    client_ip = get_client_ip(request)

    # 1. Honeypot check for bots
    if payload.honeypot and payload.honeypot.strip():
        logger.warning(f"Honeypot triggered from IP {client_ip} on forgot-password")
        fake_ts = int(time.time())
        return {
            "status": "sent",
            "message": "If an account is registered with that email, a password reset link has been dispatched.",
            "cooldown_seconds": 60,
            "attempts_remaining": 2,
            "max_attempts_reached": False,
            "reset_flow_token": f"{fake_ts}.{secrets.token_hex(32)}",
        }

    # 2. Email normalization and syntax validation
    cleaned_email = payload.email.strip().lower()
    if not EMAIL_REGEX.match(cleaned_email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    # 3. Check progressive rate limits, 3-send cap, IP cap, and prior completion
    preflight = evaluate_reset_preflight(cleaned_email, client_ip)
    if preflight["type"] == "already_completed":
        return preflight["payload"]
    if preflight["type"] == "rate_limited":
        return JSONResponse(
            status_code=429,
            content=preflight["content"],
            headers={"Retry-After": str(preflight["retry_after"])},
        )

    # 4. User existence verification (uniform response + progressive tracking to preserve anti-enumeration)
    user_status = await fetch_user_auth_status(cleaned_email)
    if not user_status.get("user_exists"):
        return preflight["payload"]

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
    client = db.get_http_client()
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
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Too many reset attempts. Please wait a minute before trying again.",
                        "retry_after_seconds": 60,
                    },
                    headers={"Retry-After": "60"},
                )
            raise HTTPException(status_code=resp.status_code, detail=err_msg)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to dispatch recovery email: {e}")
        raise HTTPException(status_code=500, detail="Internal server error sending recovery link")

    return preflight["payload"]


@router.post("/password-reset-complete")
async def password_reset_complete(
    payload: Optional[PasswordResetCompleteRequest] = Body(None),
    user_id: str = Depends(require_authenticated_user),
):
    """
    Marks the authenticated user's active password reset flow as completed so other open
    browser tabs or devices stop their resend timers and close the Forgot Password view.
    Strictly derives user email from server-trusted user_id to prevent cross-user tampering.
    """
    verified_email: Optional[str] = None

    # Exclusively resolve authoritative email from Supabase Auth Admin user lookup
    if settings.SUPABASE_SERVICE_ROLE_KEY:
        try:
            url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/admin/users/{user_id}"
            headers = {
                "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
                "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
            }
            resp = await db.get_http_client().get(url, headers=headers)
            if resp.status_code == 200:
                u_data = resp.json()
                if u_data.get("email"):
                    verified_email = str(u_data["email"]).strip().lower()
        except Exception as e:
            logger.warning(f"Admin user lookup warning in password_reset_complete: {e}")

    if not verified_email:
        raise HTTPException(status_code=404, detail="Authenticated user email not found")

    # Invalidate user cached JWT tokens
    revoke_user_tokens(user_id)

    now = time.time()
    with _rate_lock:
        state = _email_reset_state.get(verified_email)
        if not state:
            state = {
                "send_count": 0,
                "window_started_at": now,
                "last_sent_at": 0.0,
                "cooldown_seconds": 0,
                "completed_at": now,
            }
            _email_reset_state[verified_email] = state
        else:
            state["completed_at"] = now
            state["send_count"] = 0
            state["cooldown_seconds"] = 0

    return {"status": "completed", "password_reset_completed": True}


@router.post("/reset-flow-status")
async def reset_flow_status(payload: ResetFlowStatusRequest):
    """
    Cross-device polling endpoint verifying an HMAC reset_flow_token issued during forgot-password.
    Reports whether the reset has completed without enabling email enumeration.
    """
    cleaned_email = payload.email.strip().lower()
    if not EMAIL_REGEX.match(cleaned_email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    initiated_ts = verify_reset_flow_token(cleaned_email, payload.reset_flow_token)
    if initiated_ts is None:
        raise HTTPException(status_code=403, detail="Invalid or expired reset flow token.")

    now = time.time()
    with _rate_lock:
        state = _email_reset_state.get(cleaned_email, {})
        completed_at = float(state.get("completed_at", 0.0))
        is_completed = completed_at >= float(initiated_ts) and completed_at > 0.0

        if is_completed:
            return {
                "password_reset_completed": True,
                "attempts_remaining": 0,
                "max_attempts_reached": False,
                "cooldown_remaining_seconds": 0,
            }

        window_started_at = float(state.get("window_started_at", now))
        if state and (now - window_started_at) >= WINDOW_SECONDS:
            send_count = 0
            cooldown_remaining = 0
        else:
            send_count = int(state.get("send_count", 0))
            last_sent_at = float(state.get("last_sent_at", 0.0))
            cooldown_sec = int(state.get("cooldown_seconds", 0))
            elapsed = now - last_sent_at if last_sent_at > 0 else WINDOW_SECONDS
            cooldown_remaining = max(0, int(cooldown_sec - elapsed) + 1) if (last_sent_at > 0 and elapsed < cooldown_sec) else 0

        attempts_remaining = max(0, MAX_RESETS_PER_WINDOW - send_count)
        return {
            "password_reset_completed": False,
            "attempts_remaining": attempts_remaining,
            "max_attempts_reached": (attempts_remaining <= 0),
            "cooldown_remaining_seconds": cooldown_remaining,
        }
