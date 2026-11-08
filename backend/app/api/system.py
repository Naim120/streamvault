import asyncio
import logging
import math
import os
import psutil
import re
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from ..security import require_admin_secret
from ..database import db
from ..hardware import get_system_capabilities
from ..transcoder.worker import transcode_queue
from .telemetry import parse_user_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/system", tags=["System"])

_dashboard_summary_cache: Dict[str, Any] = {
    "data": None,
    "timestamp": 0.0
}
_dashboard_summary_lock = asyncio.Lock()

def _parse_env_bool(val: Optional[str]) -> Optional[bool]:
    if val is None:
        return None
    val_clean = str(val).strip().lower()
    if val_clean in ("true", "1", "yes", "y", "t"):
        return True
    if val_clean in ("false", "0", "no", "n", "f"):
        return False
    return None

def _parse_env_int(val: Optional[str]) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(str(val).strip())
    except ValueError:
        return None

_cached_settings: Optional[Dict[str, Any]] = None

async def init_system_settings() -> Dict[str, Any]:
    """
    Initializes runtime system_settings at server startup:
    Reads DEFAULT_ALLOCATED_CPU_CORES, DEFAULT_ALLOCATED_RAM_MB, DEFAULT_GPU_ENABLED,
    and DEFAULT_TRANSCODING_ENABLED from .env (if present) and seeds system_settings
    in Supabase and in-memory cache so choices made during ./install.sh immediately
    govern the transcoder on first boot.
    """
    global _cached_settings
    caps = get_system_capabilities()
    total_cores = int(caps.get("cpu_cores") or 4)
    has_gpu = bool(caps.get("has_nvenc") or caps.get("has_vaapi") or caps.get("has_qsv"))

    # Read hardware defaults configured in .env by install.sh
    env_cpu = _parse_env_int(os.getenv("DEFAULT_ALLOCATED_CPU_CORES"))
    env_ram = _parse_env_int(os.getenv("DEFAULT_ALLOCATED_RAM_MB"))
    env_gpu = _parse_env_bool(os.getenv("DEFAULT_GPU_ENABLED"))
    env_transcode = _parse_env_bool(os.getenv("DEFAULT_TRANSCODING_ENABLED"))

    initial_cpu = env_cpu if env_cpu is not None else max(1, total_cores // 2)
    initial_cpu = max(1, min(initial_cpu, total_cores))
    initial_ram = env_ram if env_ram is not None else 2048
    initial_ram = max(512, min(initial_ram, 16384))
    initial_gpu = (env_gpu and has_gpu) if env_gpu is not None else (has_gpu and not caps.get("is_low_spec"))
    initial_transcode = env_transcode if env_transcode is not None else False

    settings_rows = None
    try:
        settings_rows = await db.select("system_settings", {"id": "eq.1"})
    except Exception as e:
        logger.warning(f"Could not query system_settings from DB on startup: {e}")

    if not settings_rows or len(settings_rows) == 0:
        seed_data = {
            "id": 1,
            "transcoding_enabled": initial_transcode,
            "max_transcode_threads": initial_cpu,
            "hw_accel_type": "auto",
            "cpu_thread_limit": "auto",
            "allocated_cpu_cores": initial_cpu,
            "allocated_ram_mb": initial_ram,
            "gpu_enabled": initial_gpu
        }
        try:
            await db.insert("system_settings", seed_data)
            logger.info(f"Seeded system_settings in Supabase with hardware governor defaults: {seed_data}")
        except Exception as e:
            logger.warning(f"Could not insert seed system_settings into DB: {e}")
        res = seed_data
    else:
        res = dict(settings_rows[0])

    if "cpu_thread_limit" not in res or not res["cpu_thread_limit"]:
        res["cpu_thread_limit"] = "auto"
    if "allocated_cpu_cores" not in res or res["allocated_cpu_cores"] is None:
        res["allocated_cpu_cores"] = initial_cpu
    if "allocated_ram_mb" not in res or res["allocated_ram_mb"] is None:
        res["allocated_ram_mb"] = initial_ram
    if "gpu_enabled" not in res or res["gpu_enabled"] is None:
        res["gpu_enabled"] = initial_gpu
    elif not has_gpu:
        res["gpu_enabled"] = False

    hw_enabled = bool(res.get("gpu_enabled", False))
    gpu_active = bool(has_gpu and hw_enabled)

    # Expose transcode_mode, cpu_thread_limit, and active pipeline strategies for admin & client compatibility
    res["transcode_mode"] = "abr_transcode" if res.get("transcoding_enabled") else "lossless_remux"
    res["max_concurrent_transcodes"] = res.get("max_transcode_threads", initial_cpu)
    res["gpu_active"] = gpu_active
    res["has_gpu"] = has_gpu
    res["active_modes"] = {
        "stream_copy": "Direct Stream Copy (0% Video CPU)",
        "cpu_safe": f"CPU-Safe Compatibility Convert (Capped to {res['allocated_cpu_cores']} Threads / ~35% CPU)",
        "gpu_accelerated": "GPU Accelerated Convert" if gpu_active else None
    }
    _cached_settings = res
    return res

@router.get("/settings", dependencies=[Depends(require_admin_secret)])
@router.get("/settings/", dependencies=[Depends(require_admin_secret)])
async def get_settings():
    """
    Reads the active system configuration from memory cache (populated from Supabase).
    """
    global _cached_settings
    if _cached_settings is not None:
        return _cached_settings
    return await init_system_settings()

class UpdateSettingsPayload(BaseModel):
    model_config = {"extra": "allow"}

    transcoding_enabled: Optional[Any] = None
    transcode_mode: Optional[Any] = None
    max_transcode_threads: Optional[Any] = None
    max_concurrent_transcodes: Optional[Any] = None
    hw_accel_type: Optional[Any] = None
    cpu_thread_limit: Optional[Any] = None
    allocated_cpu_cores: Optional[Any] = None
    allocated_ram_mb: Optional[Any] = None
    gpu_enabled: Optional[Any] = None

@router.post("/settings", dependencies=[Depends(require_admin_secret)])
@router.post("/settings/", dependencies=[Depends(require_admin_secret)])
@router.patch("/settings", dependencies=[Depends(require_admin_secret)])
@router.patch("/settings/", dependencies=[Depends(require_admin_secret)])
@router.put("/settings", dependencies=[Depends(require_admin_secret)])
@router.put("/settings/", dependencies=[Depends(require_admin_secret)])
async def update_settings(payload: UpdateSettingsPayload):
    """
    Updates system settings. Checks hardware safety guardrails before allowing transcoding_enabled = true.
    Supports boolean transcoding_enabled, transcode_mode string, cpu_thread_limit,
    allocated_cpu_cores, allocated_ram_mb, and gpu_enabled.
    """
    global _cached_settings

    transcoding_enabled: Optional[bool] = None
    if payload.transcoding_enabled is not None:
        if isinstance(payload.transcoding_enabled, bool):
            transcoding_enabled = payload.transcoding_enabled
        else:
            transcoding_enabled = str(payload.transcoding_enabled).strip().lower() in ("true", "1", "yes", "on")

    if payload.transcode_mode is not None:
        transcoding_enabled = (str(payload.transcode_mode).strip().lower() == "abr_transcode")

    caps = get_system_capabilities()
    total_cores = int(caps.get("cpu_cores") or 4)
    has_gpu = bool(caps.get("has_nvenc") or caps.get("has_vaapi") or caps.get("has_qsv"))
    
    # Safety Check: if enabling transcoding on low-spec hardware
    if transcoding_enabled is True and caps.get("is_low_spec"):
        logger.warning("Enabling multi-bitrate transcoding on low-spec machine with warning.")

    update_dict: Dict[str, Any] = {}
    if transcoding_enabled is not None:
        update_dict["transcoding_enabled"] = transcoding_enabled

    threads_val = payload.max_transcode_threads if payload.max_transcode_threads is not None else payload.max_concurrent_transcodes
    if threads_val is not None:
        try:
            update_dict["max_transcode_threads"] = max(1, min(int(threads_val), total_cores))
        except (ValueError, TypeError):
            pass

    if payload.hw_accel_type is not None:
        update_dict["hw_accel_type"] = str(payload.hw_accel_type).strip()

    if payload.cpu_thread_limit is not None:
        clean_limit = str(payload.cpu_thread_limit).strip().lower()
        if clean_limit in ("1", "2", "auto"):
            update_dict["cpu_thread_limit"] = clean_limit

    if payload.allocated_cpu_cores is not None:
        try:
            val = int(payload.allocated_cpu_cores)
            update_dict["allocated_cpu_cores"] = max(1, min(val, total_cores))
        except (ValueError, TypeError):
            pass

    if payload.allocated_ram_mb is not None:
        try:
            val = int(payload.allocated_ram_mb)
            update_dict["allocated_ram_mb"] = max(512, min(val, 16384))
        except (ValueError, TypeError):
            pass

    if payload.gpu_enabled is not None:
        val = bool(payload.gpu_enabled) if not isinstance(payload.gpu_enabled, str) else payload.gpu_enabled.strip().lower() in ("true", "1", "yes", "on")
        update_dict["gpu_enabled"] = bool(val and has_gpu)

    if update_dict:
        await db.update("system_settings", "id", 1, update_dict)
        _cached_settings = None # Invalidate cache

    current_settings = await get_settings()
    return {
        "message": "System settings updated successfully",
        "settings": current_settings
    }

@router.post("/transcode/cancel", dependencies=[Depends(require_admin_secret)])
@router.post("/transcode/cancel/", dependencies=[Depends(require_admin_secret)])
async def cancel_active_transcode():
    """
    Cancels active transcode job, terminates FFmpeg process group immediately,
    cleans up incomplete partial rendition segments, and resets status to Idle.
    """
    result = await transcode_queue.cancel_active_job()
    return result

@router.get("/status", dependencies=[Depends(require_admin_secret)])
async def get_queue_status():
    """
    Returns active FFmpeg transcode progress, queue depth, and live host hardware capabilities.
    """
    queue_data = transcode_queue.get_status()
    caps = get_system_capabilities()
    return {
        **queue_data,
        "system": {
            "cpu_percent": caps.get("cpu_percent", 0.0),
            "cpu_cores": caps.get("cpu_cores", 1),
            "available_ram_gb": caps.get("available_ram_gb", 0.0),
            "total_ram_gb": caps.get("total_ram_gb", 0.0),
            "ram_percent": caps.get("ram_percent", 0.0),
            "free_disk_gb": caps.get("free_disk_gb", 0.0),
            "total_disk_gb": caps.get("total_disk_gb", 0.0),
            "disk_percent": caps.get("disk_percent", 0.0),
            "preferred_encoder": caps.get("preferred_encoder", "libx264"),
            "ffmpeg_available": caps.get("ffmpeg_available", False),
            "is_low_spec": caps.get("is_low_spec", False),
        }
    }

@router.get("/dashboard-summary", dependencies=[Depends(require_admin_secret)])
async def get_dashboard_summary():
    """
    Consolidated telemetry endpoint for Admin Panel protected by require_admin_secret.
    Returns host hardware metrics, settings, queue status, downloads, and platform KPIs in a single response.
    Cached in-memory for 2 seconds to eliminate redundant load during rapid dashboard polling.
    """
    now_ts = time.time()
    if _dashboard_summary_cache["data"] is not None and (now_ts - _dashboard_summary_cache["timestamp"]) < 2.0:
        return _dashboard_summary_cache["data"]

    async with _dashboard_summary_lock:
        now_ts = time.time()
        if _dashboard_summary_cache["data"] is not None and (now_ts - _dashboard_summary_cache["timestamp"]) < 2.0:
            return _dashboard_summary_cache["data"]

        from ..aria2.client import aria2_client
        from .aria2 import format_bytes, format_speed

        caps = get_system_capabilities()
        queue_data = transcode_queue.get_status()
        sett = await get_settings()

        # 1. Hardware breakdown
        try:
            vm = psutil.virtual_memory()
            ram_used_mb = round((vm.total - vm.available) / (1024 * 1024), 1)
            ram_total_mb = round(vm.total / (1024 * 1024), 1)
        except Exception:
            ram_used_mb = 0.0
            ram_total_mb = 0.0

        hardware = {
            "cpu_percent": caps.get("cpu_percent", 0.0),
            "ram_used_mb": ram_used_mb,
            "ram_total_mb": ram_total_mb,
            "ram_percent": caps.get("ram_percent", 0.0),
            "free_disk_gb": caps.get("free_disk_gb", 0.0),
            "total_disk_gb": caps.get("total_disk_gb", 0.0),
            "disk_percent": caps.get("disk_percent", 0.0),
            "preferred_encoder": caps.get("preferred_encoder", "libx264")
        }

        # 2. Transcoder breakdown
        transcoder = {
            "is_busy": queue_data.get("is_busy", False),
            "queue_length": queue_data.get("queue_length", 0),
            "active_job": queue_data.get("active_job"),
            "queued_jobs": queue_data.get("queued_jobs", [])
        }

        # 3. Settings breakdown
        def _safe_int(val, default):
            try:
                return int(val)
            except (ValueError, TypeError):
                return default

        raw_ctl = sett.get("cpu_thread_limit", "auto")
        ctl_val = "auto" if str(raw_ctl).strip().lower() == "auto" else _safe_int(raw_ctl, 4)

        settings_payload = {
            "transcoding_enabled": bool(sett.get("transcoding_enabled", True)),
            "allocated_cpu_cores": _safe_int(sett.get("allocated_cpu_cores"), 4),
            "allocated_ram_mb": _safe_int(sett.get("allocated_ram_mb"), 4096),
            "gpu_enabled": bool(sett.get("gpu_enabled", False)),
            "cpu_thread_limit": ctl_val
        }

        # 4. Platform KPIs breakdown
        kpis = {
            "registered_accounts": 0,
            "total_sessions": 0,
            "active_today": 0,
            "catalog_titles": 0,
            "online_now": 0,
            "streaming_now": 0,
            "total_watch_seconds": 0,
            "total_site_seconds": 0
        }
        try:
            users = await db.select("profiles", {"select": "id,last_seen_at"})
            kpis["registered_accounts"] = len(users) if users else 0
            _, total_logins = await db.select_with_count("user_login_logs", {"limit": "1"})
            kpis["total_sessions"] = total_logins
            media = await db.select("media_items", {"select": "id"})
            kpis["catalog_titles"] = len(media) if media else 0

            now_dt = datetime.now(timezone.utc)
            one_day_ago = now_dt - timedelta(hours=24)
            active_today_set = set()
            recent_logs = await db.select("user_login_logs", {
                "logged_in_at": f"gte.{one_day_ago.isoformat()}",
                "select": "user_id"
            })
            for r in (recent_logs or []):
                if r.get("user_id"):
                    active_today_set.add(r["user_id"])
            for u in (users or []):
                if u.get("id") and u.get("last_seen_at"):
                    try:
                        dt = datetime.fromisoformat(u["last_seen_at"].replace("Z", "+00:00"))
                        if dt >= one_day_ago:
                            active_today_set.add(u["id"])
                    except Exception:
                        pass
            kpis["active_today"] = len(active_today_set) if active_today_set else (1 if users else 0)

            try:
                rpc_kpis = await db.rpc("get_platform_telemetry_kpis")
                if isinstance(rpc_kpis, dict):
                    kpis["online_now"] = rpc_kpis.get("online_now_count", 0)
                    kpis["streaming_now"] = rpc_kpis.get("watching_now_count", 0)
                    kpis["total_watch_seconds"] = rpc_kpis.get("total_platform_watch_seconds", 0)
                    kpis["total_site_seconds"] = rpc_kpis.get("total_platform_site_seconds", 0)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"Error computing KPIs for dashboard-summary: {e}")

        # 5. Aria2 downloads
        downloads_list = []
        try:
            active, waiting, stopped = await aria2_client.tell_all_downloads(0, 10, 0, 15)
            for item in active + waiting + stopped:
                gid = item.get("gid")
                total_len = int(item.get("totalLength", 0))
                comp_len = int(item.get("completedLength", 0))
                speed = int(item.get("downloadSpeed", 0))
                pct = round((comp_len / total_len * 100), 1) if total_len > 0 else 0.0

                name = "Unknown Download"
                bt = item.get("bittorrent", {})
                info = bt.get("info", {})
                if "name" in info:
                    name = info["name"]
                elif item.get("files") and item["files"][0].get("path"):
                    name = item["files"][0]["path"].split("/")[-1]

                downloads_list.append({
                    "gid": gid,
                    "name": name,
                    "status": item.get("status"),
                    "total_bytes": total_len,
                    "completed_bytes": comp_len,
                    "total_fmt": format_bytes(total_len),
                    "completed_fmt": format_bytes(comp_len),
                    "speed_fmt": format_speed(speed),
                    "download_speed": format_speed(speed),
                    "speed_bps": speed,
                    "progress_percent": pct,
                    "progress_pct": pct,
                    "num_seeders": int(item.get("numSeeders", 0)),
                    "connections": int(item.get("connections", 0))
                })
        except Exception as e:
            logger.warning(f"Error gathering Aria2 downloads for dashboard: {e}")

        payload = {
            "hardware": hardware,
            "transcoder": transcoder,
            "settings": settings_payload,
            "kpis": kpis,
            "capabilities": caps,
            "queue": queue_data,
            "downloads": downloads_list
        }
        _dashboard_summary_cache["data"] = payload
        _dashboard_summary_cache["timestamp"] = now_ts
        return payload

@router.get("/telemetry", dependencies=[Depends(require_admin_secret)])
@router.get("/metrics", dependencies=[Depends(require_admin_secret)])
async def get_system_telemetry():
    """
    Returns platform-wide operations metrics: registered users, exact database total sessions, active today, and media count.
    """
    try:
        users = await db.select("profiles", {"select": "id,last_seen_at"})
        _, total_logins = await db.select_with_count("user_login_logs", {"limit": "1"})
        media = await db.select("media_items", {"select": "id"})

        now = datetime.now(timezone.utc)
        one_day_ago = now - timedelta(hours=24)
        active_today_set = set()

        recent_logs = await db.select("user_login_logs", {
            "logged_in_at": f"gte.{one_day_ago.isoformat()}",
            "select": "user_id"
        })
        for r in (recent_logs or []):
            if r.get("user_id"):
                active_today_set.add(r["user_id"])

        for u in (users or []):
            if u.get("id") and u.get("last_seen_at"):
                try:
                    dt = datetime.fromisoformat(u["last_seen_at"].replace("Z", "+00:00"))
                    if dt >= one_day_ago:
                        active_today_set.add(u["id"])
                except Exception:
                    pass

        active_count = len(active_today_set) if active_today_set else (1 if users else 0)

        return {
            "total_users": len(users) if users else 0,
            "total_visits": total_logins,
            "active_today": active_count,
            "total_media": len(media) if media else 0
        }
    except Exception as e:
        logger.error(f"Error computing system telemetry metrics: {e}")
        return {"total_users": 0, "total_visits": 0, "active_today": 0, "total_media": 0}


# ==============================================================================
# USER TELEMETRY & PAGINATED AUDIT ANALYTICS ENDPOINTS
# ==============================================================================

SENSITIVE_PROFILE_FIELDS = {"password_hash", "stripe_customer_id", "stripe_subscription_id"}

@router.get("/analytics/users", dependencies=[Depends(require_admin_secret)])
async def get_analytics_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    search: str = Query("", max_length=64),
    status_filter: str = Query("all"),
    sort_by: str = Query("last_seen_at"),
    sort_dir: str = Query("desc")
):
    """
    Paginated user directory with live online/watching computation and platform KPI metrics.
    """
    # 1. Fetch global platform KPIs
    kpis = {
        "total_registered_users": 0,
        "online_now_count": 0,
        "watching_now_count": 0,
        "total_platform_watch_seconds": 0,
        "total_platform_site_seconds": 0,
        "total_login_sessions": 0,
        "total_visits": 0
    }
    try:
        rpc_kpis = await db.rpc("get_platform_telemetry_kpis")
        if isinstance(rpc_kpis, dict):
            kpis.update(rpc_kpis)
    except Exception as e:
        logger.warning(f"Could not fetch telemetry KPIs via RPC: {e}")

    try:
        _, total_logins = await db.select_with_count("user_login_logs", {"limit": "1"})
        kpis["total_login_sessions"] = total_logins
        kpis["total_visits"] = total_logins
    except Exception as e:
        logger.warning(f"Could not count user_login_logs: {e}")

    # 2. Build PostgREST parameters for profiles
    allowed_sorts = {
        "last_seen_at": "last_seen_at",
        "total_watch_seconds": "total_watch_seconds",
        "total_site_seconds": "total_site_seconds",
        "first_joined_at": "first_joined_at",
        "total_logins": "total_logins"
    }
    sort_col = allowed_sorts.get(sort_by, "last_seen_at")
    dir_suffix = "desc.nullslast" if sort_dir.lower() == "desc" else "asc.nullsfirst"

    params: Dict[str, str] = {
        "order": f"{sort_col}.{dir_suffix}",
        "limit": str(page_size),
        "offset": str((page - 1) * page_size)
    }

    # Search filter (email, display_name, last_ipv4, last_ipv6, last_country) with strict PostgREST sanitization
    clean_search = re.sub(r'[<>[\](),*\\;"\'%_]', ' ', search)[:64].strip()
    if clean_search:
        params["or"] = (
            f"(email.ilike.*{clean_search}*,"
            f"display_name.ilike.*{clean_search}*,"
            f"last_ipv4.ilike.*{clean_search}*,"
            f"last_ipv6.ilike.*{clean_search}*,"
            f"last_country.ilike.*{clean_search}*)"
        )

    # Status filter (using 90s online threshold)
    now_utc = datetime.now(timezone.utc)
    ninety_sec_ago_iso = (now_utc - timedelta(seconds=90)).isoformat()
    if status_filter == "online":
        params["is_signed_out"] = "is.false"
        params["last_seen_at"] = f"gte.{ninety_sec_ago_iso}"
    elif status_filter == "watching":
        params["is_signed_out"] = "is.false"
        params["last_seen_at"] = f"gte.{ninety_sec_ago_iso}"
        params["current_activity"] = "like.Watching:*"
    elif status_filter == "offline":
        params["or"] = f"(is_signed_out.is.true,last_seen_at.lt.{ninety_sec_ago_iso},last_seen_at.is.null)"

    items, total_count = await db.select_with_count("profiles", params)

    # 3. Compute dynamic online/watching status and scrub sensitive fields
    for row in items:
        for field in SENSITIVE_PROFILE_FIELDS:
            row.pop(field, None)
        last_seen = None
        if row.get("last_seen_at"):
            try:
                last_seen = datetime.fromisoformat(row["last_seen_at"].replace("Z", "+00:00"))
            except Exception:
                pass
        is_signed_out = bool(row.get("is_signed_out", False))
        is_recent = bool(last_seen and (now_utc - last_seen).total_seconds() <= 90)
        is_online = (not is_signed_out) and is_recent
        act = row.get("current_activity") or "Offline"
        is_watching = is_online and act.startswith("Watching:")

        row["is_online"] = is_online
        row["is_watching"] = is_watching
        if not is_online:
            if last_seen:
                diff_sec = max(0, int((now_utc - last_seen).total_seconds()))
                if diff_sec < 60:
                    ago = "just now"
                elif diff_sec < 3600:
                    ago = f"{diff_sec // 60}m ago"
                elif diff_sec < 86400:
                    ago = f"{diff_sec // 3600}h ago"
                else:
                    ago = f"{diff_sec // 86400}d ago"
                row["current_activity"] = f"Offline • Last seen {ago}"
            else:
                row["current_activity"] = "Offline (Never)"

    total_pages = max(1, math.ceil(total_count / page_size)) if total_count > 0 else 1

    return {
        "kpis": kpis,
        "items": items,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }


@router.get("/analytics/login-logs", dependencies=[Depends(require_admin_secret)])
async def get_analytics_login_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=5, le=100),
    user_id: Optional[str] = Query(None),
    search: str = Query("", max_length=64)
):
    """
    Paginated historical login and session audit logs with embedded user profile info (no 50-row cap).
    """
    params: Dict[str, str] = {
        "select": "*,profiles(email,display_name,is_signed_out,last_seen_at)",
        "order": "logged_in_at.desc",
        "limit": str(page_size),
        "offset": str((page - 1) * page_size)
    }

    if user_id:
        try:
            val_uuid = str(uuid.UUID(user_id.strip()))
            params["user_id"] = f"eq.{val_uuid}"
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid user_id UUID")

    clean_search = re.sub(r'[<>[\](),*\\;"\'%_]', ' ', search)[:64].strip()
    if clean_search:
        params["or"] = (
            f"(ip_address.ilike.*{clean_search}*,"
            f"ipv4_address.ilike.*{clean_search}*,"
            f"ipv6_address.ilike.*{clean_search}*,"
            f"country.ilike.*{clean_search}*,"
            f"city.ilike.*{clean_search}*,"
            f"device_type.ilike.*{clean_search}*,"
            f"browser.ilike.*{clean_search}*)"
        )

    items, total_count = await db.select_with_count("user_login_logs", params)

    now_utc = datetime.now(timezone.utc)
    seen_users = set()
    for log in items:
        prof = log.get("profiles") or {}
        prof_signed_out = bool(prof.get("is_signed_out", False))

        login_dt = None
        if log.get("logged_in_at"):
            try:
                login_dt = datetime.fromisoformat(log["logged_in_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        logout_dt = None
        if log.get("logged_out_at"):
            try:
                logout_dt = datetime.fromisoformat(log["logged_out_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        hb_dt = None
        if log.get("last_heartbeat_at"):
            try:
                hb_dt = datetime.fromisoformat(log["last_heartbeat_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        most_recent_activity_dt = hb_dt or login_dt
        age_seconds = (now_utc - most_recent_activity_dt).total_seconds() if most_recent_activity_dt else 999999

        uid = log.get("user_id")
        is_latest_for_user = (uid not in seen_users)
        if uid:
            seen_users.add(uid)

        is_active = (
            is_latest_for_user and
            (not logout_dt) and
            (not prof_signed_out) and
            (age_seconds <= 90)
        )

        if is_active:
            session_state = "Active Session"
        elif logout_dt:
            session_state = "Signed Out"
        else:
            session_state = "Session Ended"

        log["is_active_session"] = is_active
        log["session_state"] = session_state

        # Estimate duration if session_site_seconds is 0
        site_sec = int(log.get("session_site_seconds") or 0)
        if site_sec == 0 and login_dt:
            end_dt = logout_dt or hb_dt
            if end_dt and end_dt > login_dt:
                site_sec = max(0, int((end_dt - login_dt).total_seconds()))
                log["session_site_seconds"] = site_sec

        # User agent fallback
        if not log.get("os_name") or not log.get("browser"):
            dev, osn, brw = parse_user_agent(log.get("user_agent") or "")
            log["device_type"] = log.get("device_type") or dev
            log["os_name"] = log.get("os_name") or osn
            log["browser"] = log.get("browser") or brw

    total_pages = max(1, math.ceil(total_count / page_size)) if total_count > 0 else 1

    return {
        "items": items,
        "total_count": total_count,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages
    }


@router.get("/analytics/users/{user_id}/detail", dependencies=[Depends(require_admin_secret)])
async def get_user_drilldown_detail(user_id: str):
    """
    Single-user analytics drill-down: profile metrics, recent login sessions, watch history, and progress.
    """
    try:
        val_uuid = str(uuid.UUID(user_id.strip()))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id UUID format")

    profiles = await db.select("profiles", {"id": f"eq.{val_uuid}"})
    if not profiles:
        raise HTTPException(status_code=404, detail="User not found")
    profile = profiles[0]
    for field in SENSITIVE_PROFILE_FIELDS:
        profile.pop(field, None)

    # Compute live online status
    now_utc = datetime.now(timezone.utc)
    last_seen = None
    if profile.get("last_seen_at"):
        try:
            last_seen = datetime.fromisoformat(profile["last_seen_at"].replace("Z", "+00:00"))
        except Exception:
            pass
    is_signed_out = bool(profile.get("is_signed_out", False))
    is_online = (not is_signed_out) and bool(last_seen and (now_utc - last_seen).total_seconds() <= 90)
    profile["is_online"] = is_online
    if not is_online:
        if last_seen:
            diff_sec = max(0, int((now_utc - last_seen).total_seconds()))
            if diff_sec < 60:
                ago = "just now"
            elif diff_sec < 3600:
                ago = f"{diff_sec // 60}m ago"
            elif diff_sec < 86400:
                ago = f"{diff_sec // 3600}h ago"
            else:
                ago = f"{diff_sec // 86400}d ago"
            profile["current_activity"] = f"Offline • Last seen {ago}"
        else:
            profile["current_activity"] = "Offline (Never)"

    # Recent login sessions (up to 10)
    sessions = await db.select("user_login_logs", {
        "user_id": f"eq.{val_uuid}",
        "order": "logged_in_at.desc",
        "limit": "10"
    })
    for s_idx, log in enumerate(sessions):
        login_dt = None
        if log.get("logged_in_at"):
            try:
                login_dt = datetime.fromisoformat(log["logged_in_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        logout_dt = None
        if log.get("logged_out_at"):
            try:
                logout_dt = datetime.fromisoformat(log["logged_out_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        hb_dt = None
        if log.get("last_heartbeat_at"):
            try:
                hb_dt = datetime.fromisoformat(log["last_heartbeat_at"].replace("Z", "+00:00"))
            except Exception:
                pass

        most_recent_activity_dt = hb_dt or login_dt
        age_seconds = (now_utc - most_recent_activity_dt).total_seconds() if most_recent_activity_dt else 999999

        is_active = (
            (s_idx == 0) and
            (not logout_dt) and
            (not is_signed_out) and
            (age_seconds <= 90)
        )

        if is_active:
            session_state = "Active Session"
        elif logout_dt:
            session_state = "Signed Out"
        else:
            session_state = "Session Ended"

        log["is_active_session"] = is_active
        log["session_state"] = session_state

        site_sec = int(log.get("session_site_seconds") or 0)
        if site_sec == 0 and login_dt:
            end_dt = logout_dt or hb_dt
            if end_dt and end_dt > login_dt:
                site_sec = max(0, int((end_dt - login_dt).total_seconds()))
                log["session_site_seconds"] = site_sec

        if not log.get("os_name") or not log.get("browser"):
            dev, osn, brw = parse_user_agent(log.get("user_agent") or "")
            log["device_type"] = log.get("device_type") or dev
            log["os_name"] = log.get("os_name") or osn
            log["browser"] = log.get("browser") or brw

    # Watch progress / history
    progress_rows = await db.select("watch_progress", {
        "user_id": f"eq.{val_uuid}",
        "order": "updated_at.desc",
        "limit": "10"
    })

    # Enrich progress items with titles
    enriched_progress = []
    for item in progress_rows:
        media_title = "Unknown Title"
        m_id = item.get("media_id")
        if m_id:
            m_items = await db.select("media_items", {"id": f"eq.{m_id}", "select": "title,type"})
            if m_items:
                media_title = m_items[0].get("title", media_title)
        
        ep_title = None
        ep_id = item.get("episode_id")
        if ep_id:
            episodes = await db.select("episodes", {"id": f"eq.{ep_id}", "select": "title,episode_number,season_number"})
            if episodes:
                ep = episodes[0]
                ep_title = f"S{ep.get('season_number', 1)}:E{ep.get('episode_number', 1)} — {ep.get('title', '')}"

        pos = float(item.get("position_seconds") or 0.0)
        dur = float(item.get("duration_seconds") or 1.0)
        pct = round(min(100.0, max(0.0, (pos / dur) * 100.0)), 1) if dur > 0 else 0.0

        enriched_progress.append({
            **item,
            "media_title": media_title,
            "episode_title": ep_title,
            "completion_percent": pct
        })

    # Custom lists count
    custom_lists = await db.select("custom_lists", {"user_id": f"eq.{val_uuid}", "select": "id"})
    watchlist_count = len(custom_lists)

    return {
        "profile": profile,
        "recent_sessions": sessions,
        "recent_watch_progress": enriched_progress,
        "custom_lists_count": watchlist_count
    }
