import asyncio
from datetime import datetime, timezone
import ipaddress
import logging
import time
from typing import Any, Dict, Optional, Tuple
import uuid
import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from ..database import db
from ..security import require_authenticated_user, extract_trusted_client_ip

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/telemetry", tags=["Telemetry"])

# In-memory IP geo cache to avoid duplicate external lookups
IP_GEO_CACHE: Dict[str, Tuple[str, str]] = {}
# Anti-cheat rate limiter: user_id -> last_heartbeat_timestamp
LAST_HEARTBEAT_TIME: Dict[str, float] = {}
# High-concurrency write-behind heartbeat buffer: user_id -> buffer dict
_buffer_lock = asyncio.Lock()
_heartbeat_buffer: Dict[str, dict] = {}


def resolve_dual_stack_ips(
    request: Request,
    client_ipv4: Optional[str] = None,
    client_ipv6: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extracts and validates dual-stack IPv4 and IPv6 addresses.
    Inspects Cloudflare / Proxy headers and request socket, mapping IPv4-mapped IPv6
    addresses (e.g. ::ffff:203.0.113.5) back to proper IPv4.
    """
    detected_ipv4: Optional[str] = None
    detected_ipv6: Optional[str] = None

    # 1. Validate client-reported dual-stack probe IPs (reject malicious strings)
    if client_ipv4:
        try:
            parsed_v4 = ipaddress.ip_address(client_ipv4.strip())
            if isinstance(parsed_v4, ipaddress.IPv4Address) and not parsed_v4.is_loopback:
                detected_ipv4 = str(parsed_v4)
        except ValueError:
            pass

    if client_ipv6:
        try:
            parsed_v6 = ipaddress.ip_address(client_ipv6.strip())
            if isinstance(parsed_v6, ipaddress.IPv6Address) and not parsed_v6.is_loopback:
                detected_ipv6 = str(parsed_v6)
        except ValueError:
            pass

    # 2. Inspect headers from closest proxy / CDN (only if peer connection is trusted loopback)
    peer_ip = request.client.host if request.client else "127.0.0.1"
    trusted_loopbacks = {"127.0.0.1", "::1", "localhost", "testclient"}
    candidates = []

    if peer_ip in trusted_loopbacks:
        for header_name in ["CF-Connecting-IPv6", "CF-Pseudo-IPv4", "CF-Connecting-IP", "X-Real-IP"]:
            val = request.headers.get(header_name)
            if val:
                candidates.append(val.strip())

        xff = request.headers.get("X-Forwarded-For")
        if xff:
            for part in xff.split(","):
                if part.strip():
                    candidates.append(part.strip())

    candidates.append(peer_ip)

    has_loopback_v4 = False
    has_loopback_v6 = False
    for cand in candidates:
        try:
            addr = ipaddress.ip_address(cand)
            if isinstance(addr, ipaddress.IPv6Address):
                if addr.ipv4_mapped:
                    if not detected_ipv4:
                        detected_ipv4 = str(addr.ipv4_mapped)
                elif addr.is_loopback:
                    has_loopback_v6 = True
                else:
                    if not detected_ipv6:
                        detected_ipv6 = str(addr)
            elif isinstance(addr, ipaddress.IPv4Address):
                if addr.is_loopback:
                    has_loopback_v4 = True
                else:
                    if not detected_ipv4:
                        detected_ipv4 = str(addr)
        except ValueError:
            continue

    if not detected_ipv4 and not detected_ipv6:
        detected_ipv4 = "127.0.0.1 (Local)"
        if has_loopback_v6:
            detected_ipv6 = "::1 (Local)"
    elif not detected_ipv4 and has_loopback_v4:
        detected_ipv4 = "127.0.0.1 (Local)"

    return detected_ipv4, detected_ipv6


async def resolve_country_and_city(
    ip: str,
    cf_country: Optional[str] = None,
    cf_city: Optional[str] = None
) -> Tuple[str, str]:
    """
    Resolves country and city with in-memory caching and private network awareness.
    """
    if cf_country and cf_country != "XX":
        country = cf_country
        city = cf_city or "Unknown"
        return country, city

    try:
        clean_ip = ip.replace(" (Local)", "").strip()
        ip_obj = ipaddress.ip_address(clean_ip)
        if ip_obj.is_private or ip_obj.is_loopback:
            return "Local Network", "Localhost"
    except ValueError:
        return "Unknown", "Unknown"

    if ip in IP_GEO_CACHE:
        return IP_GEO_CACHE[ip]

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            res = await client.get(f"http://ip-api.com/json/{ip}?fields=countryCode,country,city")
            if res.status_code == 200:
                data = res.json()
                country = data.get("country") or data.get("countryCode") or "Unknown"
                city = data.get("city") or "Unknown"
                IP_GEO_CACHE[ip] = (country, city)
                return country, city
    except Exception:
        pass

    return "Unknown", "Unknown"


def parse_user_agent(ua_str: str) -> Tuple[str, str, str]:
    """
    Parses User-Agent into (device_type, os_name, browser).
    device_type: Desktop | Mobile | Tablet | Smart TV
    """
    if not ua_str:
        return "Desktop", "Unknown OS", "Unknown Browser"

    ua = ua_str.lower()

    # 1. Device Type
    if any(k in ua for k in ["smart-tv", "smarttv", "googletv", "apple tv", "appletv", "roku", "tizen", "webos", "vizio", "hbbtv"]):
        device = "Smart TV"
    elif any(k in ua for k in ["ipad", "tablet", "playbook", "silk"]):
        device = "Tablet"
    elif any(k in ua for k in ["mobi", "iphone", "android", "ipod", "blackberry", "windows phone"]):
        device = "Mobile"
    else:
        device = "Desktop"

    # 2. OS Name
    if "windows nt 10.0" in ua:
        os_name = "Windows 10/11"
    elif "windows" in ua:
        os_name = "Windows"
    elif "mac os x" in ua or "macos" in ua:
        os_name = "macOS"
    elif "android" in ua:
        os_name = "Android"
    elif "iphone" in ua or "ipad" in ua:
        os_name = "iOS"
    elif "linux" in ua:
        os_name = "Linux"
    elif "crkey" in ua or "chromecast" in ua:
        os_name = "Chromecast"
    elif "tizen" in ua:
        os_name = "Tizen OS"
    elif "webos" in ua:
        os_name = "LG webOS"
    else:
        os_name = "Other"

    # 3. Browser
    if "edg/" in ua:
        browser = "Edge"
    elif "opr/" in ua or "opera" in ua:
        browser = "Opera"
    elif "chrome/" in ua and "safari" in ua:
        browser = "Chrome"
    elif "safari/" in ua and "chrome" not in ua:
        browser = "Safari"
    elif "firefox/" in ua:
        browser = "Firefox"
    else:
        browser = "Browser"

    return device, os_name, browser


class SessionStartPayload(BaseModel):
    client_ipv4: Optional[str] = None
    client_ipv6: Optional[str] = None
    device_type: Optional[str] = None
    email: Optional[str] = None


class HeartbeatPayload(BaseModel):
    session_log_id: Optional[str] = None
    delta_site_seconds: int = Field(0, ge=0, le=60)
    delta_watch_seconds: int = Field(0, ge=0, le=60)
    active_media_title: Optional[str] = Field(None, max_length=120)
    client_ipv4: Optional[str] = None
    client_ipv6: Optional[str] = None


class LogoutPayload(BaseModel):
    session_log_id: Optional[str] = None


@router.post("/session-start")
@router.post("/login")
async def record_session_start(
    payload: SessionStartPayload,
    request: Request,
    user_id: str = Depends(require_authenticated_user),
    cf_ipcountry: Optional[str] = Header(None, alias="CF-IPCountry"),
    cf_ipcity: Optional[str] = Header(None, alias="CF-IPCity"),
    user_agent: Optional[str] = Header(None, alias="User-Agent")
):
    """
    Authenticated session-start telemetry endpoint.
    Records dual-stack IP address, parsed device/browser details, and increments total_logins.
    """
    # 1. Resolve Dual-Stack IPv4 + IPv6
    ipv4, ipv6 = resolve_dual_stack_ips(request, payload.client_ipv4, payload.client_ipv6)
    primary_ip = ipv4 or ipv6 or "127.0.0.1"

    # 2. Determine Country & City
    country, city = await resolve_country_and_city(primary_ip, cf_ipcountry, cf_ipcity)

    # 3. Parse User-Agent
    device_detected, os_name, browser = parse_user_agent(user_agent or "")
    device_type = payload.device_type or device_detected

    # 4. Close any previous open sessions for this user so only the current session remains active
    try:
        await db.update_where(
            "user_login_logs",
            {"user_id": f"eq.{user_id}", "logged_out_at": "is.null"},
            {"logged_out_at": "now()"}
        )
    except Exception as e:
        logger.warning(f"Error closing previous sessions for user {user_id}: {e}")

    # 5. Ensure profile exists in public.profiles to satisfy fk_user_login_logs_profiles
    existing_prof = await db.select("profiles", {"id": f"eq.{user_id}"})
    if not existing_prof:
        profile_data = {
            "id": user_id,
            "email": payload.email,
            "display_name": (payload.email.split("@")[0] if payload.email else "User"),
            "last_login_at": "now()",
            "last_seen_at": "now()",
            "is_signed_out": False,
            "total_logins": 1,
            "last_ip": primary_ip,
            "last_ipv4": ipv4,
            "last_ipv6": ipv6,
            "last_country": country,
            "last_city": city,
            "last_device": device_type,
            "last_browser": browser,
            "current_activity": "Online — Browsing"
        }
        await db.insert("profiles", profile_data)
        existing_prof = [profile_data]

    # 6. Insert into public.user_login_logs
    log_entry = {
        "user_id": user_id,
        "ip_address": primary_ip,
        "ipv4_address": ipv4,
        "ipv6_address": ipv6,
        "country": country,
        "city": city,
        "user_agent": (user_agent or "")[:250],
        "device_type": device_type,
        "browser": browser,
        "os_name": os_name,
        "session_site_seconds": 0,
        "session_watch_seconds": 0,
        "last_heartbeat_at": "now()",
        "logged_in_at": "now()"
    }
    inserted_log = await db.insert("user_login_logs", log_entry)
    session_log_id = inserted_log.get("id") if inserted_log else None

    # 7. Update user profile metrics
    current_logins = int(existing_prof[0].get("total_logins") or 0) if existing_prof else 0

    profile_update = {
        "last_login_at": "now()",
        "last_seen_at": "now()",
        "is_signed_out": False,
        "total_logins": current_logins + 1,
        "last_ip": primary_ip,
        "last_ipv4": ipv4,
        "last_ipv6": ipv6,
        "last_country": country,
        "last_city": city,
        "last_device": device_type,
        "last_browser": browser,
        "current_activity": "Online — Browsing"
    }
    if payload.email:
        profile_update["email"] = payload.email

    await db.update("profiles", "id", user_id, profile_update)

    return {
        "session_log_id": str(session_log_id) if session_log_id else None,
        "ipv4": ipv4,
        "ipv6": ipv6,
        "country": country,
        "city": city,
        "device": device_type
    }


def get_current_catalog_version() -> int:
    try:
        from .metadata import get_catalog_version
        return get_catalog_version()
    except Exception:
        return int(time.time() * 1000)

@router.post("/heartbeat")
async def record_heartbeat(
    payload: HeartbeatPayload,
    request: Request,
    user_id: str = Depends(require_authenticated_user)
):
    """
    Authenticated telemetry heartbeat endpoint called periodically while the app is active.
    Utilizes write-behind batching in memory (_heartbeat_buffer) to eliminate SQL pressure under load.
    Flushes to database at most once every 30s per user, or immediately when user activity
    switches between browsing and watching.
    """
    now_ts = time.time()
    last_ts = LAST_HEARTBEAT_TIME.get(user_id, 0.0)
    if (now_ts - last_ts) < 4.0:
        return {"status": "cooldown_ignored", "catalog_version": get_current_catalog_version()}
    LAST_HEARTBEAT_TIME[user_id] = now_ts

    ipv4, ipv6 = resolve_dual_stack_ips(request, payload.client_ipv4, payload.client_ipv6)

    # Activity calculation
    if payload.active_media_title and payload.delta_watch_seconds > 0:
        clean_title = payload.active_media_title.strip()
        activity = f"Watching: {clean_title}" if not clean_title.lower().startswith("watching:") else clean_title
    else:
        activity = "Online — Browsing"

    should_flush = False
    rpc_payload = None

    async with _buffer_lock:
        entry = _heartbeat_buffer.get(user_id)
        if entry is None:
            # First heartbeat for this session: initialize buffer and flush immediately
            entry = {
                "user_id": user_id,
                "session_log_id": payload.session_log_id,
                "delta_site_seconds": payload.delta_site_seconds,
                "delta_watch_seconds": payload.delta_watch_seconds,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "current_activity": activity,
                "last_flushed_at": now_ts,
            }
            _heartbeat_buffer[user_id] = entry
            should_flush = True
        else:
            # Accumulate deltas in memory
            entry["delta_site_seconds"] += payload.delta_site_seconds
            entry["delta_watch_seconds"] += payload.delta_watch_seconds
            if payload.session_log_id:
                entry["session_log_id"] = payload.session_log_id
            if ipv4:
                entry["ipv4"] = ipv4
            if ipv6:
                entry["ipv6"] = ipv6

            activity_changed = (entry.get("current_activity") != activity)
            time_elapsed = now_ts - entry.get("last_flushed_at", 0)

            # Flush immediately if activity state switched (e.g. browsing -> watching) or every 30 seconds
            if activity_changed or time_elapsed >= 30.0:
                should_flush = True

        if should_flush:
            rpc_payload = {
                "p_user_id": user_id,
                "p_session_log_id": entry.get("session_log_id"),
                "p_delta_site_seconds": entry.get("delta_site_seconds", 0),
                "p_delta_watch_seconds": entry.get("delta_watch_seconds", 0),
                "p_ipv4": entry.get("ipv4"),
                "p_ipv6": entry.get("ipv6"),
                "p_current_activity": activity
            }
            # Reset accumulators for next batch
            entry["delta_site_seconds"] = 0
            entry["delta_watch_seconds"] = 0
            entry["current_activity"] = activity
            entry["last_flushed_at"] = now_ts

    if should_flush and rpc_payload:
        try:
            await db.rpc("record_user_telemetry_heartbeat", rpc_payload)
        except Exception as e:
            logger.warning(f"Error calling record_user_telemetry_heartbeat RPC: {e}")

    return {
        "status": "ok",
        "activity": activity,
        "buffered": not should_flush,
        "ipv4": ipv4,
        "ipv6": ipv6,
        "catalog_version": get_current_catalog_version()
    }


@router.post("/logout")
async def record_logout(
    payload: LogoutPayload,
    user_id: str = Depends(require_authenticated_user)
):
    """
    Authenticated session signout / tab close hook.
    Flushes any buffered heartbeat deltas, marks user as offline, and records logout timestamp.
    """
    # 1. Flush any pending buffered heartbeat deltas before logging out
    entry = None
    async with _buffer_lock:
        entry = _heartbeat_buffer.pop(user_id, None)

    if entry and (entry.get("delta_site_seconds", 0) > 0 or entry.get("delta_watch_seconds", 0) > 0):
        rpc_payload = {
            "p_user_id": user_id,
            "p_session_log_id": entry.get("session_log_id") or payload.session_log_id,
            "p_delta_site_seconds": entry.get("delta_site_seconds", 0),
            "p_delta_watch_seconds": entry.get("delta_watch_seconds", 0),
            "p_ipv4": entry.get("ipv4"),
            "p_ipv6": entry.get("ipv6"),
            "p_current_activity": "Offline"
        }
        try:
            await db.rpc("record_user_telemetry_heartbeat", rpc_payload)
        except Exception as e:
            logger.warning(f"Error flushing buffered heartbeat on logout for user {user_id}: {e}")

    # 2. Update profile status to Offline
    profile_update = {
        "is_signed_out": True,
        "last_logout_at": "now()",
        "current_activity": "Offline"
    }
    await db.update("profiles", "id", user_id, profile_update)

    if payload.session_log_id:
        try:
            valid_session_id = str(uuid.UUID(str(payload.session_log_id).strip()))
            await db.update_where(
                "user_login_logs",
                {"id": f"eq.{valid_session_id}", "user_id": f"eq.{user_id}"},
                {"logged_out_at": datetime.now(timezone.utc).isoformat()}
            )
        except ValueError:
            logger.warning(f"Invalid session_log_id format on logout: {payload.session_log_id}")
        except Exception as e:
            logger.warning(f"Error updating logout time for session {payload.session_log_id}: {e}")

    return {"status": "logged_out"}
