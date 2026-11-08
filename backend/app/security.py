import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import threading
import time
from collections import defaultdict
from typing import Optional
import httpx
from fastapi import Header, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import settings

logger = logging.getLogger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)

_EPHEMERAL_STREAM_SECRET: Optional[str] = None
_failed_admin_auth: dict[str, list[float]] = defaultdict(list)
_auth_lock = threading.Lock()

def get_stream_secret() -> bytes:
    """
    Retrieves the HMAC streaming secret from configuration.
    Refuses weak default secrets by generating a cryptographic 256-bit key if unset.
    """
    global _EPHEMERAL_STREAM_SECRET
    configured = getattr(settings, "HMAC_STREAM_SECRET", "") or os.getenv("STREAM_TOKEN_SECRET", "")
    if configured and len(configured) >= 32 and not configured.startswith("default") and not configured.startswith("change_me"):
        return configured.encode("utf-8")
    
    if not _EPHEMERAL_STREAM_SECRET:
        _EPHEMERAL_STREAM_SECRET = secrets.token_hex(32)
        logger.warning("STREAM_TOKEN_SECRET is unset or insecure; generated ephemeral 256-bit cryptographic secret for session.")
    return _EPHEMERAL_STREAM_SECRET.encode("utf-8")

def generate_stream_token(media_id: str, user_id: str, ttl_seconds: Optional[int] = None) -> tuple[str, int]:
    """
    Generates a secure, time-limited HMAC-SHA256 signature for streaming media bound to media_id and user_id.
    Returns (token_hex, expires_timestamp).
    """
    ttl = ttl_seconds or settings.STREAM_TOKEN_TTL_SECONDS
    expires = int(time.time()) + ttl
    message = f"{media_id}:{user_id}:{expires}".encode("utf-8")
    secret = get_stream_secret()
    token = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return token, expires

def verify_stream_token(media_id: str, token: str, expires: int, user_id: str, alt_media_id: Optional[str] = None) -> bool:
    """
    Verifies that the stream token has not expired and the HMAC signature is authentic using constant-time comparison.
    Supports optional alt_media_id to accept tokens signed for either parent series or episode.
    """
    now = int(time.time())
    if now > expires:
        return False
    
    secret = get_stream_secret()
    expected_message = f"{media_id}:{user_id}:{expires}".encode("utf-8")
    expected_token = hmac.new(secret, expected_message, hashlib.sha256).hexdigest()
    if hmac.compare_digest(expected_token, token):
        return True

    if alt_media_id:
        alt_message = f"{alt_media_id}:{user_id}:{expires}".encode("utf-8")
        alt_expected = hmac.new(secret, alt_message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(alt_expected, token):
            return True

    return False

def generate_segment_token(target_id: str, bucket_seconds: int = 21600) -> tuple[str, int]:
    """
    Generates a time-bucketed content token for .ts segments that is identical for all viewers
    watching the same media during the same 6-hour window, enabling Cloudflare CDN edge caching.
    """
    now = int(time.time())
    e = ((now // bucket_seconds) + 1) * bucket_seconds
    message = f"seg:{target_id}:{e}".encode("utf-8")
    secret = get_stream_secret()
    st = hmac.new(secret, message, hashlib.sha256).hexdigest()
    return st, e

def verify_segment_token(target_id: str, st: str, e: int, alt_target_id: Optional[str] = None) -> bool:
    """
    Verifies a time-bucketed segment token with constant-time HMAC comparison and 1-hour grace window.
    """
    now = int(time.time())
    if now > e + 3600:
        return False

    secret = get_stream_secret()
    for tid in (target_id, alt_target_id):
        if not tid:
            continue
        msg = f"seg:{tid}:{e}".encode("utf-8")
        expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, st):
            return True
    return False

def extract_trusted_client_ip(request: Request) -> str:
    """
    Extracts the authenticated, anti-spoofed client IP:
    1. Checks the direct peer IP from the TCP socket connection.
    2. Only trusts forwarded headers (CF-Connecting-IP, X-Real-IP, X-Forwarded-For)
       if the direct peer connection originates from local loopback (127.0.0.1, ::1, testclient).
    3. Validates that any extracted string is a syntactically valid IPv4/IPv6 address.
    """
    peer_ip = request.client.host if request.client else "127.0.0.1"
    trusted_loopbacks = {"127.0.0.1", "::1", "localhost", "testclient"}
    if peer_ip not in trusted_loopbacks:
        return peer_ip

    candidates = []
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip and cf_ip.strip():
        candidates.append(cf_ip.strip())

    real_ip = request.headers.get("X-Real-IP")
    if real_ip and real_ip.strip():
        candidates.append(real_ip.strip())

    xff = request.headers.get("X-Forwarded-For")
    if xff and xff.strip():
        first_xff = xff.split(",")[0].strip()
        if first_xff:
            candidates.append(first_xff)

    for cand in candidates:
        try:
            parsed = ipaddress.ip_address(cand)
            return str(parsed)
        except ValueError:
            continue

    return peer_ip

async def require_admin_secret(
    request: Request,
    x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret")
) -> bool:
    """
    Three-layer protection for admin endpoints:
    1. Rejects external/proxy traffic with 404 (loopback only).
    2. Enforces 5-strike lockout for 30 minutes against brute-force attacks.
    3. Performs constant-time HMAC secret comparison with 0.5s tarpit on failure.
    """
    client_host = request.client.host if request.client else ""
    has_proxy_headers = bool(
        request.headers.get("CF-Connecting-IP")
        or request.headers.get("X-Forwarded-For")
        or request.headers.get("X-Real-IP")
    )
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient") or has_proxy_headers:
        # Hide endpoint existence from public internet/proxies
        raise HTTPException(status_code=404, detail="Not Found")

    # Guard against cross-site browser requests to loopback admin routes
    origin_hdr = request.headers.get("origin", "").rstrip("/")
    if origin_hdr:
        allowed_admin_origins = {
            "http://127.0.0.1:4000",
            "http://localhost:4000",
            "http://127.0.0.1:8080",
            "http://localhost:8080",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        }
        if origin_hdr not in allowed_admin_origins:
            raise HTTPException(status_code=403, detail="Forbidden: Cross-origin admin access blocked")

    now = time.time()
    cutoff = now - 1800.0  # 30-minute window

    with _auth_lock:
        _failed_admin_auth[client_host] = [t for t in _failed_admin_auth[client_host] if t > cutoff]
        if len(_failed_admin_auth[client_host]) >= 5:
            raise HTTPException(
                status_code=403,
                detail="Locked out due to repeated failed authentication attempts"
            )

    configured_secret = getattr(settings, "ADMIN_API_SECRET", "") or os.getenv("ADMIN_API_SECRET", "")
    if not configured_secret:
        logger.error("ADMIN_API_SECRET is not configured on server")
        raise HTTPException(status_code=401, detail="Admin endpoint authentication is not configured")

    # Fallback to direct header lookup if dependency injection was None
    if not x_admin_secret:
        x_admin_secret = request.headers.get("x-admin-secret") or request.headers.get("X-Admin-Secret")

    if not x_admin_secret or not hmac.compare_digest(x_admin_secret.strip(), configured_secret.strip()):
        logger.warning(f"Admin auth mismatch: client_host={client_host}, received={repr(x_admin_secret)[:15]}, expected_len={len(configured_secret)}")
        with _auth_lock:
            _failed_admin_auth[client_host].append(now)
        await asyncio.sleep(0.5)  # Tarpit brute-force scripts
        raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing X-Admin-Secret header")

    # Successful authentication resets failed attempts for this loopback client
    with _auth_lock:
        _failed_admin_auth.pop(client_host, None)

    return True

# ---------------------------------------------------------------------------
# Cryptographic JWT Verification Cache & Core Auth Verification
# ---------------------------------------------------------------------------
_verified_jwt_cache: dict[str, tuple[str, float]] = {}
_jwt_cache_lock = threading.Lock()

def _decode_b64url(s: str) -> bytes:
    rem = len(s) % 4
    if rem > 0:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s)

async def verify_supabase_jwt(token: str) -> str:
    """
    Cryptographically verifies a Supabase JWT token:
    1. Fast in-memory cache lookup (<0.01ms).
    2. Local HS256 HMAC-SHA256 signature verification if SUPABASE_JWT_SECRET is configured.
    3. Asymmetric (ES256/RS256) fallback verification via Supabase GoTrue (/auth/v1/user).
    Raises HTTPException(401) on forged, expired, or invalid tokens.
    """
    if not token or not isinstance(token, str):
        raise HTTPException(status_code=401, detail="Authentication required: Missing Bearer token")

    clean_token = token.strip()
    now = time.time()
    token_hash = hashlib.sha256(clean_token.encode("utf-8")).hexdigest()

    # 1. Fast cache check
    with _jwt_cache_lock:
        cached = _verified_jwt_cache.get(token_hash)
        if cached:
            user_id, expires_at = cached
            if now < expires_at:
                return user_id
            else:
                _verified_jwt_cache.pop(token_hash, None)

    parts = clean_token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid authentication token format")

    header_b64, payload_b64, sig_b64 = parts

    # Parse payload structure without trusting unverified claims
    try:
        raw_payload = json.loads(_decode_b64url(payload_b64).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed JWT payload")

    exp = raw_payload.get("exp")
    if exp is not None:
        try:
            exp_val = float(exp)
            if now > exp_val:
                raise HTTPException(status_code=401, detail="Token has expired")
        except ValueError:
            raise HTTPException(status_code=401, detail="Invalid token expiration claim")
    else:
        exp_val = now + 3600.0

    user_id = raw_payload.get("sub") or raw_payload.get("id")
    verified = False

    # 2. HS256 local cryptographic verification if secret configured
    jwt_secret = getattr(settings, "SUPABASE_JWT_SECRET", "") or os.getenv("SUPABASE_JWT_SECRET", "")
    is_valid_secret = bool(
        jwt_secret
        and len(jwt_secret) >= 16
        and not jwt_secret.startswith("change_me")
        and not jwt_secret.startswith("placeholder")
    )

    if is_valid_secret:
        # Check using PyJWT if installed
        try:
            import jwt
            decoded = jwt.decode(
                clean_token,
                jwt_secret,
                algorithms=["HS256"],
                options={"verify_aud": False, "verify_exp": True}
            )
            user_id = decoded.get("sub") or decoded.get("id")
            verified = True
        except ImportError:
            # Fallback to standard library HMAC-SHA256
            try:
                signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
                expected_sig = hmac.new(jwt_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
                actual_sig = _decode_b64url(sig_b64)
                if hmac.compare_digest(expected_sig, actual_sig):
                    verified = True
            except Exception:
                verified = False
        except Exception as e:
            logger.debug(f"PyJWT local verification notice: {e}")
            verified = False

    # 3. Fallback / Asymmetric (ES256/RS256) Verification via Supabase GoTrue
    if not verified:
        url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/user"
        anon_key = settings.SUPABASE_ANON_KEY
        headers = {
            "apikey": anon_key,
            "Authorization": f"Bearer {clean_token}"
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    user_id = data.get("id") or user_id
                    if user_id:
                        verified = True
                else:
                    logger.debug(f"GoTrue token verification returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"GoTrue token verification network notice: {e}")

    if not verified or not user_id:
        raise HTTPException(status_code=401, detail="Invalid or forged authentication token")

    # Cache successful cryptographic verification for min(exp, now + 300) seconds
    cache_ttl = min(float(exp_val), now + 300.0)
    with _jwt_cache_lock:
        _verified_jwt_cache[token_hash] = (str(user_id), cache_ttl)

    return str(user_id)

async def get_current_user_id(credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme)) -> Optional[str]:
    """
    Extracts verified user_id from Bearer token if valid. Returns None if absent or unverified.
    """
    if not credentials or not credentials.credentials:
        return None
    try:
        return await verify_supabase_jwt(credentials.credentials)
    except HTTPException:
        return None

async def require_authenticated_user(credentials: Optional[HTTPAuthorizationCredentials] = Security(bearer_scheme)) -> str:
    """
    Validates Supabase JWT Bearer token cryptographically and returns authenticated user UUID string.
    Raises 401 if missing, invalid, forged, or expired.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Authentication required: Missing Bearer token")
    return await verify_supabase_jwt(credentials.credentials)
