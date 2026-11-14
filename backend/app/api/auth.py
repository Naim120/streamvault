import datetime
import logging
import os
import re
import socket
import threading
import time
from collections import defaultdict, deque
from typing import Optional
import urllib.parse
import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from ..config import settings
from ..database import db
from ..security import extract_trusted_client_ip, get_active_cloudflared_origin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth & Verification State Machine"])

# Server-Side Rate Limit Constants
COOLDOWN_SECONDS = 120            # 2 minutes between verification emails for same email
MAX_SENDS_PER_HOUR = 5            # Max 5 verification emails per hour per email
MAX_IP_REQUESTS_PER_15M = 10      # Max 10 verification requests per 15 min per IP
LINK_EXPIRY_SECONDS = 900         # 15 minutes: old verification links expire

# In-memory IP rate limiter
_ip_buckets: dict[str, deque] = defaultdict(deque)
_rate_lock = threading.Lock()
_email_send_state: dict[str, dict] = {}

def reserve_verification_slot(client_ip: str, email: str, state: Optional[dict] = None):
    now = time.time()
    with _rate_lock:
        q = _ip_buckets[client_ip]
        while q and now - q[0] > 900:
            q.popleft()
        if len(q) >= MAX_IP_REQUESTS_PER_15M:
            retry_after = int(900 - (now - q[0])) + 1
            raise HTTPException(status_code=429, detail=f"Too many requests from this network. Please try again in {retry_after} seconds.", headers={"Retry-After": str(retry_after)})
        entry = _email_send_state.get(email)
        if entry:
            if entry.get("in_flight"):
                raise HTTPException(status_code=429, detail="Please wait before requesting another verification email.", headers={"Retry-After": str(COOLDOWN_SECONDS)})
            last_sent = float(entry.get("last_sent_at", 0.0))
            if last_sent > 0 and (now - last_sent) < COOLDOWN_SECONDS:
                rem = max(1, int(COOLDOWN_SECONDS - (now - last_sent)) + 1)
                raise HTTPException(status_code=429, detail=f"Please wait {rem}s before requesting another link.", headers={"Retry-After": str(rem)})
            if (now - float(entry.get("window_started_at", now))) >= 3600:
                entry["window_started_at"] = now
                entry["send_count"] = 0
            if int(entry.get("send_count", 0)) >= MAX_SENDS_PER_HOUR:
                rem = max(1, int(3600 - (now - float(entry["window_started_at"]))) + 1)
                raise HTTPException(status_code=429, detail=f"Maximum verification requests reached for this hour ({MAX_SENDS_PER_HOUR}/hour).", headers={"Retry-After": str(rem)})
        if state is not None:
            check_email_hourly_limit(state)
        if not entry:
            entry = {"window_started_at": now, "send_count": 0, "last_sent_at": 0.0, "in_flight": False}
            _email_send_state[email] = entry
        entry["in_flight"] = True
        q.append(now)

def complete_verification_slot(email: str, dispatched: bool):
    now = time.time()
    with _rate_lock:
        entry = _email_send_state.get(email)
        if not entry:
            return
        entry["in_flight"] = False
        if dispatched:
            if (now - float(entry.get("window_started_at", now))) >= 3600:
                entry["window_started_at"] = now
                entry["send_count"] = 0
            entry["last_sent_at"] = now
            entry["send_count"] = int(entry.get("send_count", 0)) + 1

# Anti-enumeration tracker: limits distinct target emails probed per IP within 15 minutes
_ip_probed_emails: dict[str, dict[str, float]] = defaultdict(dict)
_probe_lock = threading.Lock()
MAX_DISTINCT_EMAILS_PER_IP_15M = 5

def check_account_enumeration_rate_limit(client_ip: str, email: str):
    now = time.time()
    clean_email = email.strip().lower()
    with _probe_lock:
        probes = _ip_probed_emails[client_ip]
        expired = [em for em, ts in probes.items() if now - ts > 900]
        for em in expired:
            del probes[em]
        if clean_email in probes:
            probes[clean_email] = now
            return
        if len(probes) >= MAX_DISTINCT_EMAILS_PER_IP_15M:
            raise HTTPException(
                status_code=429,
                detail="Too many distinct account queries from this network. Please try again later.",
                headers={"Retry-After": "60"}
            )
        probes[clean_email] = now

# Email regex
EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# Allowed origin patterns for redirect
ALLOWED_ORIGIN_REGEX = re.compile(
    r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|"
    r"https?://192\.168\.\d+\.\d+(:\d+)?|"
    r"https?://10\.\d+\.\d+\.\d+(:\d+)?|"
    r"https?://172\.(1[6-9]|2\d|3[01])\.\d+\.\d+(:\d+)?|"
    r"capacitor://localhost)$"
)


def get_client_ip(request: Request) -> str:
    return extract_trusted_client_ip(request)


def check_ip_rate_limit(client_ip: str):
    now = time.time()
    with _rate_lock:
        q = _ip_buckets[client_ip]
        while q and now - q[0] > 900:
            q.popleft()
        if len(q) >= MAX_IP_REQUESTS_PER_15M:
            oldest = q[0]
            retry_after = int(900 - (now - oldest)) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests from this network. Please try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)}
            )


def record_ip_success(client_ip: str):
    now = time.time()
    with _rate_lock:
        _ip_buckets[client_ip].append(now)


def calculate_elapsed_seconds(dt_str: Optional[str]) -> Optional[int]:
    if not dt_str:
        return None
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        dt = datetime.datetime.fromisoformat(clean_str)
        now = datetime.datetime.now(datetime.timezone.utc)
        elapsed = int((now - dt).total_seconds())
        return max(0, elapsed)
    except Exception as e:
        logger.warning(f"Error parsing datetime '{dt_str}': {e}")
        return None


async def inspect_email_auth_state(email: str) -> dict:
    url = f"{settings.SUPABASE_URL.rstrip('/')}/rest/v1/rpc/admin_inspect_email_auth_state"
    key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    client = db.get_http_client()
    try:
        resp = await client.post(url, headers=headers, json={"p_email": email})
        if resp.status_code == 200:
            res = resp.json()
            if isinstance(res, dict):
                return res
            elif isinstance(res, list) and len(res) > 0:
                return res[0]
        logger.warning(f"admin_inspect_email_auth_state returned {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.error(f"Error calling admin_inspect_email_auth_state RPC: {e}")
    return {"exists": False, "is_verified": False}


async def record_email_dispatched(email: str, client_ip: str, state: dict, force_new: bool = False):
    send_count = state.get("send_count_window") or 0
    window_started = state.get("window_started_at")
    reset_window = False

    if force_new or not window_started:
        reset_window = True
        new_count = 1
    else:
        window_elapsed = calculate_elapsed_seconds(window_started)
        if window_elapsed is not None and window_elapsed >= 3600:
            reset_window = True
            new_count = 1
        else:
            new_count = send_count + 1

    url = f"{settings.SUPABASE_URL.rstrip('/')}/rest/v1/auth_email_rate_limits"
    key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payload = {
        "email": email,
        "last_sent_at": now_iso,
        "send_count_window": new_count,
        "last_ip": client_ip,
        "updated_at": now_iso
    }
    if reset_window:
        payload["window_started_at"] = now_iso

    try:
        client = db.get_http_client()
        await client.post(url, headers=headers, json=payload)
    except Exception as e:
        logger.warning(f"Failed to record auth_email_rate_limits for {email}: {e}")


def check_email_hourly_limit(state: dict):
    send_count = state.get("send_count_window") or 0
    window_started = state.get("window_started_at")
    if window_started:
        window_elapsed = calculate_elapsed_seconds(window_started)
        if window_elapsed is not None and window_elapsed < 3600:
            if send_count >= MAX_SENDS_PER_HOUR:
                retry_after = 3600 - window_elapsed
                raise HTTPException(
                    status_code=429,
                    detail=f"Maximum verification requests reached for this hour ({MAX_SENDS_PER_HOUR}/hour). Please try again in {retry_after} seconds.",
                    headers={"Retry-After": str(retry_after)}
                )


async def dispatch_supabase_verification_email(email: str, redirect_to: Optional[str] = None) -> bool:
    url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/resend"
    key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"
    }
    body = {"type": "signup", "email": email}
    if redirect_to:
        body["options"] = {"email_redirect_to": redirect_to}
        body["redirect_to"] = redirect_to

    client = db.get_http_client()
    resp = await client.post(url, headers=headers, json=body)
    if resp.status_code < 400:
        return True
    err_msg = resp.text
    try:
        err_json = resp.json()
        err_msg = err_json.get("msg") or err_json.get("error_description") or err_json.get("message") or resp.text
    except Exception:
        pass
    if resp.status_code == 429 or "rate" in err_msg.lower():
        raise HTTPException(
            status_code=429,
            detail="Too many verification attempts with email provider. Please wait a minute before trying again.",
            headers={"Retry-After": "120"}
        )
    logger.error(f"Supabase resend failed ({resp.status_code}): {err_msg}")
    raise HTTPException(status_code=resp.status_code, detail=err_msg)


SUPABASE_MANAGEMENT_TOKEN = os.environ.get("SUPABASE_MANAGEMENT_TOKEN", "").strip()
SUPABASE_PROJECT_REF = os.environ.get("SUPABASE_PROJECT_REF", "").strip()


def get_primary_lan_ip() -> str:
    """
    Detect the primary LAN IPv4 address of the host machine
    without sending any external network packets.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def resolve_best_redirect_origin(request: Optional[Request] = None, candidate: Optional[str] = None) -> str:
    """
    Resolves the best reachable base URL for email verification links:
    1. First check environment variables PUBLIC_SITE_URL, PUBLIC_DOMAIN, APP_PUBLIC_URL,
       VITE_PUBLIC_SITE_URL, or STREAM_PUBLIC_BASE_URL (if set and not localhost/127.0.0.1).
    2. Check if a live cloudflared tunnel is running in logs/cloudflared.log.
    3. Check candidate origin passed in request body (if valid non-localhost).
    4. If request is provided and Host header is non-localhost, use request origin.
    5. Otherwise, detect the host machine's primary LAN IPv4 address (e.g. 192.168.x.x) and combine with :8080.
    """
    # 1. Environment variables
    for env_key in ("PUBLIC_SITE_URL", "PUBLIC_DOMAIN", "FRONTEND_ORIGIN", "APP_PUBLIC_URL", "VITE_PUBLIC_SITE_URL", "STREAM_PUBLIC_BASE_URL"):
        val = (os.environ.get(env_key) or getattr(settings, env_key, None) or "").strip().rstrip("/")
        if val:
            if not val.startswith("http://") and not val.startswith("https://"):
                val = f"https://{val}"
            try:
                parsed = urllib.parse.urlparse(val)
                host_part = parsed.hostname or ""
                if host_part not in ("localhost", "127.0.0.1") and not host_part.startswith("127."):
                    return val
            except Exception:
                pass

    # 2. Check if an active Cloudflare quick tunnel is running and logged
    active_cf = get_active_cloudflared_origin()
    if active_cf:
        return active_cf

    # 3. Candidate origin from body / headers (if non-localhost and allowed)
    if candidate:
        cand_clean = candidate.strip().rstrip("/")
        try:
            parsed_c = urllib.parse.urlparse(cand_clean)
            cand_host = parsed_c.hostname or ""
            if cand_host and cand_host not in ("localhost", "127.0.0.1") and not cand_host.startswith("127."):
                if "trycloudflare.com" in cand_host:
                    if active_cf and cand_clean == active_cf:
                        return cand_clean
                elif ALLOWED_ORIGIN_REGEX.match(cand_clean):
                    return cand_clean
        except Exception:
            pass

    # 4. If request is provided, inspect scheme and Host header
    if request:
        host_hdr = request.headers.get("Host", "localhost:8080").strip()
        scheme = "https" if request.url.scheme == "https" or request.headers.get("X-Forwarded-Proto") == "https" else "http"

        if ":" in host_hdr:
            host_name, port_str = host_hdr.split(":", 1)
            port_suffix = f":{port_str}"
        else:
            host_name = host_hdr
            port_suffix = ""

        # If Host is a remote domain or LAN IP (not localhost), validate before using
        if host_name not in ("localhost", "127.0.0.1") and not host_name.startswith("127."):
            candidate_origin = f"{scheme}://{host_hdr}".rstrip("/")
            public_domain = (os.environ.get("PUBLIC_DOMAIN") or getattr(settings, "PUBLIC_DOMAIN", None) or "").strip().rstrip("/")
            public_site = (os.environ.get("PUBLIC_SITE_URL") or getattr(settings, "PUBLIC_SITE_URL", None) or "").strip().rstrip("/")

            is_valid_origin = False
            if "trycloudflare.com" in host_name:
                is_valid_origin = bool(active_cf and candidate_origin == active_cf)
            else:
                is_valid_origin = bool(
                    ALLOWED_ORIGIN_REGEX.match(candidate_origin)
                    or (public_domain and (host_name == public_domain or host_name.endswith(f".{public_domain}")))
                    or (public_site and candidate_origin == public_site)
                )
            if is_valid_origin:
                return candidate_origin
            logger.warning(f"Rejected untrusted Host header redirect origin: {candidate_origin}")

        # Otherwise Host is localhost -> resolve primary LAN IP
        lan_ip = get_primary_lan_ip()
        if lan_ip and lan_ip != "127.0.0.1":
            active_port = port_suffix if port_suffix else ":8080"
            return f"{scheme}://{lan_ip}{active_port}"

    # 5. Default fallback to primary LAN IP
    lan_ip = get_primary_lan_ip()
    if lan_ip and lan_ip != "127.0.0.1":
        return f"http://{lan_ip}:8080"
    return "http://localhost:8080"


async def sync_supabase_auth_settings():
    """
    Automatically synchronizes Supabase Auth project configuration (site_url and uri_allow_list)
    so confirmation emails never send localhost:8080 and redirect to reachable LAN IP or domain.
    """
    token = SUPABASE_MANAGEMENT_TOKEN
    project_ref = SUPABASE_PROJECT_REF
    if not token or not project_ref:
        return

    origin = resolve_best_redirect_origin()
    lan_ip = get_primary_lan_ip()

    allow_list = [
        "http://localhost:8080/**",
        "http://localhost:5173/**",
        "http://localhost:3000/**",
        "http://127.0.0.1:8080/**",
        "http://127.0.0.1:5173/**",
        "http://127.0.0.1:3000/**",
        f"http://{lan_ip}:8080/**",
        f"http://{lan_ip}/**",
    ]
    pub_domain = (os.environ.get("PUBLIC_DOMAIN") or getattr(settings, "PUBLIC_DOMAIN", None) or "").strip().rstrip("/")
    if pub_domain:
        allow_list.append(f"https://{pub_domain}/**")
    if origin and f"{origin}/**" not in allow_list:
        allow_list.append(f"{origin}/**")

    uri_allow_list_str = ",".join(dict.fromkeys(allow_list))

    patch_url = f"https://api.supabase.com/v1/projects/{project_ref}/config/auth"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {
        "site_url": origin,
        "uri_allow_list": uri_allow_list_str
    }

    try:
        client = db.get_http_client()
        resp = await client.patch(patch_url, headers=headers, json=payload)
        if resp.status_code == 200:
            logger.info(f"Synchronized Supabase Auth site_url to {origin} and updated uri_allow_list")
        else:
            logger.warning(f"Failed to sync Supabase Auth config ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.warning(f"Could not sync Supabase Auth config: {e}")


def validate_and_resolve_origin(candidate: Optional[str], request: Request) -> str:
    return resolve_best_redirect_origin(request, candidate)


# ==============================================================================
# REQUEST MODELS
# ==============================================================================

class EmailPrecheckRequest(BaseModel):
    email: str


class ResendVerificationRequest(BaseModel):
    model_config = {"populate_by_name": True}

    email: str
    redirect_to: Optional[str] = None
    client_origin: Optional[str] = None
    origin: Optional[str] = None
    honeypot: Optional[str] = Field(None, alias="_honeypot")


class VerificationStatusRequest(BaseModel):
    email: str


# ==============================================================================
# ENDPOINTS
# ==============================================================================

@router.get("/redirect-origin")
async def get_redirect_origin(request: Request):
    """
    Returns the resolved reachable base URL for email verification and OAuth callbacks.
    Falls back to host machine primary LAN IPv4 if accessed via localhost.
    """
    origin = resolve_best_redirect_origin(request)
    return {"origin": origin}


@router.post("/signup-precheck")
async def signup_precheck(payload: EmailPrecheckRequest, request: Request):
    """
    Server-side email verification state inspector before sign-up:
    - Case 1: already_verified -> return 200 with guidance to sign in
    - Case 2: unverified -> check if old link expired (>15 min) and resend if needed
    - Case 3: new_user -> return 200 with 120s initial cooldown
    """
    client_ip = get_client_ip(request)
    email = payload.email.strip().lower()
    if not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    check_account_enumeration_rate_limit(client_ip, email)

    state = await inspect_email_auth_state(email)
    exists = bool(state.get("exists"))
    is_verified = bool(state.get("is_verified"))

    # Case 1 — User Exists AND is_verified == True:
    if exists and is_verified:
        return {
            "status": "already_verified",
            "exists": True,
            "is_verified": True,
            "message": "This email is already registered and verified. Please sign in to your account."
        }

    # Case 2 — User Exists AND is_verified == False (Unverified Account):
    if exists and not is_verified:
        conf_sent = state.get("confirmation_sent_at")
        elapsed_seconds = calculate_elapsed_seconds(conf_sent)

        # Subcase 2A: elapsed_seconds >= 900 (15+ mins ago — Old Link Expired) OR confirmation_sent_at is None
        if elapsed_seconds is None or elapsed_seconds >= LINK_EXPIRY_SECONDS:
            email_sent = False
            try:
                reserve_verification_slot(client_ip, email, state)
                try:
                    # Trigger fresh verification email with reachable redirect URL
                    resolved_origin = resolve_best_redirect_origin(request)
                    redirect_url = f"{resolved_origin.rstrip('/')}/home"
                    await dispatch_supabase_verification_email(email, redirect_url)
                    await record_email_dispatched(email, client_ip, state)
                    email_sent = True
                finally:
                    complete_verification_slot(email, email_sent)
            except Exception as e:
                logger.warning(f"Auto-resend during signup_precheck skipped/failed: {e}")

            return {
                "status": "unverified_expired_resent" if email_sent else "unverified_existing",
                "exists": True,
                "is_verified": False,
                "email_sent": email_sent,
                "cooldown_remaining_seconds": COOLDOWN_SECONDS if email_sent else 0,
                "message": (
                    "Your previous verification link had expired. We have sent a fresh verification link to your email."
                    if email_sent
                    else "This email is registered and awaiting verification. Please check your inbox or request a new link."
                )
            }

        # Subcase 2B: 120 <= elapsed_seconds < 900 (Link Still Valid, Cooldown Passed)
        if elapsed_seconds >= COOLDOWN_SECONDS:
            return {
                "status": "unverified_existing",
                "exists": True,
                "is_verified": False,
                "email_sent": False,
                "cooldown_remaining_seconds": 0,
                "last_sent_seconds_ago": elapsed_seconds,
                "message": "This email is already registered and awaiting verification. Check your inbox or click Resend below for a new link."
            }

        # Subcase 2C: elapsed_seconds < 120 (Link Just Sent Recently)
        cooldown_remaining = max(1, COOLDOWN_SECONDS - elapsed_seconds)
        return {
            "status": "unverified_cooldown",
            "exists": True,
            "is_verified": False,
            "email_sent": False,
            "cooldown_remaining_seconds": cooldown_remaining,
            "last_sent_seconds_ago": elapsed_seconds,
            "message": "A verification link was just sent to this email. Please check your inbox."
        }

    # Case 3 — Brand New Email (exists == False):
    await record_email_dispatched(email, client_ip, state, force_new=True)
    return {
        "status": "new_user",
        "exists": False,
        "is_verified": False,
        "cooldown_remaining_seconds": COOLDOWN_SECONDS
    }


@router.post("/resend-verification")
async def resend_verification(payload: ResendVerificationRequest, request: Request):
    """
    Enforces strict server-side 120s cooldown and 5-per-hour limit on verification email resends.
    """
    client_ip = get_client_ip(request)

    if payload.honeypot and payload.honeypot.strip():
        logger.warning(f"Honeypot triggered from IP {client_ip} on resend-verification")
        return {"status": "sent", "cooldown_remaining_seconds": COOLDOWN_SECONDS, "message": "Verification email dispatched."}

    email = payload.email.strip().lower()
    if not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    check_account_enumeration_rate_limit(client_ip, email)
    reserve_verification_slot(client_ip, email)
    dispatched = False

    try:
        state = await inspect_email_auth_state(email)
        exists = bool(state.get("exists"))
        is_verified = bool(state.get("is_verified"))

        if not exists:
            raise HTTPException(status_code=404, detail="No pending account found for this email. Please sign up first.")

        if is_verified:
            return {
                "status": "already_verified",
                "is_verified": True,
                "message": "This email is already verified! Please sign in."
            }

        # If is_verified == False: enforce server-side 120s cooldown and 5/hr limit
        conf_sent = state.get("confirmation_sent_at")
        elapsed_seconds = calculate_elapsed_seconds(conf_sent)

        if elapsed_seconds is not None and elapsed_seconds < COOLDOWN_SECONDS:
            remaining = COOLDOWN_SECONDS - elapsed_seconds
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"Please wait {remaining}s before requesting another link.",
                    "cooldown_remaining_seconds": remaining
                },
                headers={"Retry-After": str(remaining)}
            )

        check_email_hourly_limit(state)

        candidate_origin = payload.redirect_to or payload.client_origin or payload.origin
        origin = resolve_best_redirect_origin(request, candidate_origin)
        redirect_url = f"{origin.rstrip('/')}/home"

        await dispatch_supabase_verification_email(email, redirect_url)
        await record_email_dispatched(email, client_ip, state)
        dispatched = True

        return {
            "status": "sent",
            "cooldown_remaining_seconds": COOLDOWN_SECONDS,
            "message": "A fresh verification link has been sent to your email."
        }
    finally:
        complete_verification_slot(email, dispatched)


@router.post("/verification-status")
async def verification_status(payload: VerificationStatusRequest, request: Request):
    """
    Checks the real-time server-side verification status of an email account.
    Throttled by client IP to prevent automated account discovery sweeps.
    """
    client_ip = get_client_ip(request)
    email = payload.email.strip().lower()
    if not EMAIL_REGEX.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    check_account_enumeration_rate_limit(client_ip, email)

    state = await inspect_email_auth_state(email)
    exists = bool(state.get("exists"))
    is_verified = bool(state.get("is_verified"))
    conf_sent = state.get("confirmation_sent_at")
    elapsed_seconds = calculate_elapsed_seconds(conf_sent)

    cooldown_remaining = 0
    if elapsed_seconds is not None and elapsed_seconds < COOLDOWN_SECONDS:
        cooldown_remaining = max(0, COOLDOWN_SECONDS - elapsed_seconds)

    return {
        "exists": exists,
        "is_verified": is_verified,
        "cooldown_remaining_seconds": cooldown_remaining,
        "last_sent_seconds_ago": elapsed_seconds
    }

