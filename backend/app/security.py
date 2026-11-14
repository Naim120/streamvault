import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional
import httpx
from fastapi import Header, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from .config import settings
from .database import db

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
            f"http://127.0.0.1:{settings.MEDIA_SERVER_PORT}",
            f"http://localhost:{settings.MEDIA_SERVER_PORT}",
        }
        if origin_hdr not in allowed_admin_origins:
            raise HTTPException(status_code=403, detail="Forbidden: Cross-origin admin access blocked")

    now = time.time()
    cutoff = now - 1800.0  # 30-minute window

    configured_secret = (
        getattr(settings, "ADMIN_API_SECRET", "")
        or os.getenv("ADMIN_API_SECRET", "")
        or os.getenv("ADMIN_SECRET", "")
    ).strip()
    if not configured_secret:
        logger.error("ADMIN_API_SECRET is not configured on server")
        raise HTTPException(status_code=401, detail="Admin endpoint authentication is not configured")

    with _auth_lock:
        recent = [t for t in _failed_admin_auth[client_host] if t > cutoff]
        if recent:
            _failed_admin_auth[client_host] = recent
        else:
            _failed_admin_auth.pop(client_host, None)
        is_locked = len(recent) >= 5

    if is_locked:
        await asyncio.sleep(0.5)
        raise HTTPException(
            status_code=403,
            detail="Locked out due to repeated failed authentication attempts"
        )

    # Fallback to direct header lookup if dependency injection was None
    if not x_admin_secret:
        x_admin_secret = request.headers.get("x-admin-secret") or request.headers.get("X-Admin-Secret")

    # If authentic secret matches, reset failed attempts and allow access
    if x_admin_secret and hmac.compare_digest(
        x_admin_secret.strip().encode("utf-8"),
        configured_secret.strip().encode("utf-8")
    ):
        with _auth_lock:
            _failed_admin_auth.pop(client_host, None)
        return True

    # Otherwise, secret did NOT match or was missing:
    with _auth_lock:
        _failed_admin_auth[client_host] = [t for t in _failed_admin_auth[client_host] if t > cutoff]
        _failed_admin_auth[client_host].append(now)
        strikes = len(_failed_admin_auth[client_host])

    logger.warning(f"Admin auth mismatch: client_host={client_host}, received={repr(x_admin_secret)[:15]}, expected_len={len(configured_secret)}, strikes={strikes}")

    # Tarpit brute-force attempts uniformly before raising
    await asyncio.sleep(0.5)

    if strikes >= 5:
        raise HTTPException(
            status_code=403,
            detail="Locked out due to repeated failed authentication attempts"
        )

    raise HTTPException(status_code=401, detail="Unauthorized: Invalid or missing X-Admin-Secret header")

# ---------------------------------------------------------------------------
# Cryptographic JWT Verification Cache & Core Auth Verification
# ---------------------------------------------------------------------------
_verified_jwt_cache: dict[str, tuple[str, float, Optional[float]]] = {}
_user_revoked_before: dict[str, float] = {}
_revoked_token_hashes: dict[str, float] = {}
_jwt_cache_lock = threading.Lock()

def revoke_user_tokens(user_id: str, token: Optional[str] = None) -> None:
    now = time.time()
    with _jwt_cache_lock:
        prev = _user_revoked_before.get(str(user_id), 0.0)
        _user_revoked_before[str(user_id)] = max(prev, now)
        if token:
            token_hash = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
            _verified_jwt_cache.pop(token_hash, None)
            _revoked_token_hashes[token_hash] = now + 86400.0
        for k, entry in list(_verified_jwt_cache.items()):
            if entry[0] == str(user_id):
                _verified_jwt_cache.pop(k, None)
        for k, exp_at in list(_revoked_token_hashes.items()):
            if exp_at <= now:
                _revoked_token_hashes.pop(k, None)

def _decode_b64url(s: str) -> bytes:
    rem = len(s) % 4
    if rem > 0:
        s += "=" * (4 - rem)
    return base64.urlsafe_b64decode(s)

async def verify_supabase_jwt(token: str) -> str:
    """
    Cryptographically verifies a Supabase JWT token:
    1. Fast in-memory cache lookup (<0.01ms) with revocation enforcement.
    2. Local HS256 HMAC-SHA256 signature verification if SUPABASE_JWT_SECRET is configured.
    3. Asymmetric (ES256/RS256) fallback verification via Supabase GoTrue (/auth/v1/user).
    Raises HTTPException(401) on forged, expired, or invalid tokens.
    """
    if not token or not isinstance(token, str):
        raise HTTPException(status_code=401, detail="Authentication required: Missing Bearer token")

    clean_token = token.strip()
    now = time.time()
    token_hash = hashlib.sha256(clean_token.encode("utf-8")).hexdigest()

    # 1. Fast cache and explicit revocation check
    with _jwt_cache_lock:
        revoked_until = _revoked_token_hashes.get(token_hash)
        if revoked_until is not None:
            if now < revoked_until:
                raise HTTPException(status_code=401, detail="Token has been revoked")
            _revoked_token_hashes.pop(token_hash, None)
        cached = _verified_jwt_cache.get(token_hash)
        if cached:
            user_id, expires_at, cached_iat = cached
            revoked_before = _user_revoked_before.get(user_id, 0.0)
            if revoked_before > 0.0 and (cached_iat is None or cached_iat <= revoked_before):
                _verified_jwt_cache.pop(token_hash, None)
                raise HTTPException(status_code=401, detail="Token has been revoked")
            if now < expires_at:
                return user_id
            else:
                _verified_jwt_cache.pop(token_hash, None)

    parts = clean_token.split(".")
    if len(parts) != 3:
        raise HTTPException(status_code=401, detail="Invalid authentication token format")

    header_b64, payload_b64, sig_b64 = parts

    # Parse header and payload structure without trusting unverified claims
    try:
        raw_header = json.loads(_decode_b64url(header_b64).decode("utf-8"))
        raw_payload = json.loads(_decode_b64url(payload_b64).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=401, detail="Malformed JWT token")

    if not isinstance(raw_header, dict) or not isinstance(raw_payload, dict):
        raise HTTPException(status_code=401, detail="Malformed JWT token")

    exp = raw_payload.get("exp")
    if exp is None or isinstance(exp, bool):
        raise HTTPException(status_code=401, detail="Token missing required exp claim")
    try:
        exp_val = float(exp)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token expiration claim")
    if now > exp_val:
        raise HTTPException(status_code=401, detail="Token has expired")

    nbf = raw_payload.get("nbf")
    if nbf is not None:
        if isinstance(nbf, bool):
            raise HTTPException(status_code=401, detail="Invalid token not-before claim")
        try:
            nbf_val = float(nbf)
        except (TypeError, ValueError):
            raise HTTPException(status_code=401, detail="Invalid token not-before claim")
        if now < nbf_val:
            raise HTTPException(status_code=401, detail="Token not yet valid")

    aud = raw_payload.get("aud")
    if not (aud == "authenticated" or (isinstance(aud, list) and "authenticated" in aud)):
        raise HTTPException(status_code=401, detail="Invalid token audience")

    expected_iss = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1" if settings.SUPABASE_URL else ""
    if expected_iss and raw_payload.get("iss") and raw_payload.get("iss") != expected_iss:
        raise HTTPException(status_code=401, detail="Invalid token issuer")

    user_id = raw_payload.get("sub")
    if not user_id or not isinstance(user_id, str):
        raise HTTPException(status_code=401, detail="Token missing required sub claim")

    iat_val: Optional[float] = None
    iat = raw_payload.get("iat")
    if iat is not None and not isinstance(iat, bool):
        try:
            iat_val = float(iat)
        except (TypeError, ValueError):
            pass

    revoked_before = _user_revoked_before.get(str(user_id), 0.0)
    if revoked_before > 0.0 and (iat_val is None or iat_val <= revoked_before):
        raise HTTPException(status_code=401, detail="Token has been revoked")

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
        # Check using PyJWT
        try:
            import jwt
            options = {
                "require": ["exp", "sub"],
                "verify_exp": True,
                "verify_nbf": True,
                "verify_aud": True,
                "verify_iss": bool(expected_iss),
            }
            decoded = jwt.decode(
                clean_token,
                jwt_secret,
                algorithms=["HS256"],
                audience="authenticated",
                issuer=expected_iss if expected_iss else None,
                options=options,
            )
            user_id = decoded.get("sub")
            verified = bool(user_id)
        except ImportError:
            if raw_header.get("alg") == "HS256":
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
            client = db.get_http_client()
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                user_id = data.get("id") or user_id
                if user_id:
                    revoked_before = _user_revoked_before.get(str(user_id), 0.0)
                    if revoked_before > 0.0 and (iat_val is None or iat_val <= revoked_before):
                        raise HTTPException(status_code=401, detail="Token has been revoked")
                    verified = True
            else:
                logger.debug(f"GoTrue token verification returned {resp.status_code}")
        except HTTPException:
            raise
        except Exception as e:
            logger.warning(f"GoTrue token verification network notice: {e}")

    if not verified or not user_id:
        raise HTTPException(status_code=401, detail="Invalid or forged authentication token")

    # Cache successful cryptographic verification for min(exp, now + 300) seconds
    cache_ttl = min(float(exp_val), now + 300.0)
    with _jwt_cache_lock:
        _verified_jwt_cache[token_hash] = (str(user_id), cache_ttl, iat_val)

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


def get_active_cloudflared_origin() -> Optional[str]:
    """
    Returns the exact active Cloudflare Quick Tunnel origin for THIS host
    if one is running and logged, preventing acceptance of arbitrary attacker *.trycloudflare.com tunnels.
    """
    try:
        log_paths = [
            Path(settings.BASE_DIR) / "logs" / "cloudflared.log",
            Path(os.getcwd()) / "logs" / "cloudflared.log",
            Path(settings.BASE_DIR).parent / "logs" / "cloudflared.log",
        ]
        for log_path in log_paths:
            if log_path.is_file():
                content = log_path.read_text(encoding="utf-8", errors="ignore")
                matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                if matches:
                    return matches[-1].rstrip("/")
    except Exception:
        pass
    return None

