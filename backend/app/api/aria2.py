import asyncio
import base64
import ipaddress
import logging
import os
from pathlib import Path
import secrets
import socket
import subprocess
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from ..aria2.client import aria2_client
from ..aria2.monitor import aria2_monitor
from ..config import settings
from ..security import require_admin_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/aria2", tags=["Aria2 Downloader"])


def raise_structured_aria2_error(
    status_code: int,
    error_code: str,
    title: str,
    detail: str,
    suggestion: str
):
    """
    Raises an HTTPException containing a structured diagnostic payload for UI display.
    """
    raise HTTPException(
        status_code=status_code,
        detail={
            "error_code": error_code,
            "title": title,
            "detail": detail,
            "suggestion": suggestion
        }
    )


CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
METADATA_IPS = {"169.254.169.254", "fd00:ec2::254"}


def _validate_host_ips(hostname: str):
    """
    Resolves hostname and blocks SSRF targets: non-global, CGNAT (100.64.0.0/10),
    loopback, private, link-local, reserved, multicast, unspecified, and cloud metadata.
    """
    try:
        try:
            raw_ips = [ipaddress.ip_address(hostname)]
        except ValueError:
            addr_info = socket.getaddrinfo(hostname, None)
            raw_ips = [ipaddress.ip_address(addr[4][0]) for addr in addr_info]

        for ip in raw_ips:
            check_ip = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
            if (
                not check_ip.is_global
                or check_ip in CGNAT_NET
                or check_ip.is_private
                or check_ip.is_loopback
                or check_ip.is_link_local
                or check_ip.is_reserved
                or check_ip.is_multicast
                or check_ip.is_unspecified
                or str(check_ip) in METADATA_IPS
            ):
                raise_structured_aria2_error(
                    status_code=400,
                    error_code="SSRF_BLOCKED",
                    title="Blocked Internal Address",
                    detail=f"SSRF Security Violation: Direct access to local loopback, private subnet, or cloud metadata ({check_ip}) is blocked.",
                    suggestion="Ensure the download URL points to a public internet server and not an internal network address."
                )
    except HTTPException:
        raise
    except Exception as e:
        raise_structured_aria2_error(
            status_code=400,
            error_code="DNS_RESOLUTION_FAILED",
            title="Cannot Resolve Host",
            detail=f"Failed to resolve DNS hostname '{hostname}': {e}",
            suggestion="Verify that the domain name is spelled correctly and the remote server is online."
        )


async def ensure_aria2_daemon_running() -> bool:
    """
    Checks if the Aria2 JSON-RPC daemon is listening on port 6800.
    If unreachable, attempts to spawn it once using ~/.aria2/aria2.conf before failing.
    Always ensures an RPC secret is active, never exposing credentials via cmdline.
    """
    for _ in range(2):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.6)
        res = sock.connect_ex(("127.0.0.1", settings.ARIA2_RPC_PORT))
        sock.close()
        if res == 0:
            return True

        # Try to spawn daemon securely
        try:
            conf_dir = Path.home() / ".aria2"
            conf_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(conf_dir, 0o700)
            except Exception:
                pass

            conf_path = conf_dir / "aria2.conf"
            secret = settings.ARIA2_RPC_SECRET
            if not secret and conf_path.exists():
                try:
                    for line in conf_path.read_text(encoding="utf-8").splitlines():
                        if line.strip().startswith("rpc-secret="):
                            secret = line.strip().split("=", 1)[1].strip()
                            break
                except Exception:
                    pass

            if not secret:
                secret = secrets.token_hex(24)

            settings.ARIA2_RPC_SECRET = secret
            aria2_client.secret = secret

            if not conf_path.exists():
                fd = os.open(conf_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(
                        f"dir={settings.ARIA2_DOWNLOAD_DIR}\n"
                        "enable-rpc=true\n"
                        "rpc-listen-all=false\n"
                        f"rpc-listen-port={settings.ARIA2_RPC_PORT}\n"
                        "rpc-allow-origin-all=false\n"
                        f"rpc-secret={secret}\n"
                        "seed-time=0\n"
                        "bt-stop-timeout=600\n"
                        "follow-torrent=mem\n"
                    )
            else:
                try:
                    os.chmod(conf_path, 0o600)
                except Exception:
                    pass

            subprocess.Popen(["aria2c", f"--conf-path={conf_path}", "-D"])
            await asyncio.sleep(1.0)
        except Exception as e:
            logger.warning(f"Could not auto-spawn aria2c daemon: {e}")
            break

    return False


def validate_download_uri(uri: Optional[str]) -> str:
    """
    Validates download URI against empty inputs, invalid schemes, invalid magnet hashes,
    and SSRF / private network violations.
    """
    cleaned = (uri or "").strip()
    if not cleaned:
        raise_structured_aria2_error(
            status_code=400,
            error_code="EMPTY_INPUT",
            title="Empty Download Input",
            detail="No download URL or BitTorrent Magnet link was provided.",
            suggestion="Paste a valid HTTP(S) video URL or BitTorrent Magnet link."
        )

    # Magnet URI Validation
    if cleaned.lower().startswith("magnet:"):
        if "xt=urn:btih:" not in cleaned.lower():
            raise_structured_aria2_error(
                status_code=400,
                error_code="INVALID_MAGNET_URI",
                title="Invalid Magnet Link",
                detail="The magnet link is missing a valid BitTorrent info-hash parameter (xt=urn:btih:...).",
                suggestion="Ensure the magnet link starts with 'magnet:?xt=urn:btih:' followed by a 40-character hex or 32-character base32 hash."
            )
        # Validate embedded tracker or webseed URLs against SSRF
        parsed_mag = urlparse(cleaned)
        qs = parse_qs(parsed_mag.query)
        for param in ("tr", "ws", "xs", "as"):
            for val in qs.get(param, []):
                val_clean = unquote(val).strip()
                if val_clean.lower().startswith(("http://", "https://")):
                    p = urlparse(val_clean)
                    if p.hostname:
                        _validate_host_ips(p.hostname)
        return cleaned

    # HTTP / HTTPS Validation
    parsed = urlparse(cleaned)
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise_structured_aria2_error(
            status_code=400,
            error_code="UNSUPPORTED_SCHEME",
            title="Unsupported Protocol",
            detail=f"The link protocol '{scheme}://' is not supported by StreamVault Downloader.",
            suggestion="Provide a direct HTTP, HTTPS, or BitTorrent Magnet link."
        )

    hostname = parsed.hostname
    if not hostname:
        raise_structured_aria2_error(
            status_code=400,
            error_code="INVALID_URL",
            title="Invalid Download URL",
            detail="The download URL is missing a valid domain name or hostname.",
            suggestion="Double-check the URL format (e.g., https://example.com/video.mp4)."
        )

    # SSRF Protection: Resolve IP addresses and block private/loopback/cloud metadata/CGNAT
    _validate_host_ips(hostname)
    return cleaned


def _bdecode(data: bytes, max_depth: int = 50) -> tuple[Any, int]:
    """
    Decodes a single bencoded value starting at index 0.
    Returns (decoded_value, end_index).
    Raises ValueError on syntax or boundary errors.
    """
    if not data:
        raise ValueError("Empty bencode stream")
    if max_depth <= 0:
        raise ValueError("Exceeded maximum recursion depth for bencode structure")

    first = data[0:1]
    if first == b"i":
        end = data.find(b"e", 1)
        if end == -1:
            raise ValueError("Unterminated bencode integer")
        int_str = data[1:end].decode("ascii", errors="strict")
        if int_str.startswith("-0") or (int_str.startswith("0") and len(int_str) > 1):
            raise ValueError(f"Invalid bencode integer encoding: {int_str}")
        return int(int_str), end + 1
    elif first == b"l":
        items = []
        idx = 1
        while idx < len(data) and data[idx:idx+1] != b"e":
            val, consumed = _bdecode(data[idx:], max_depth - 1)
            items.append(val)
            idx += consumed
        if idx >= len(data) or data[idx:idx+1] != b"e":
            raise ValueError("Unterminated bencode list")
        return items, idx + 1
    elif first == b"d":
        res = {}
        idx = 1
        while idx < len(data) and data[idx:idx+1] != b"e":
            k, consumed = _bdecode(data[idx:], max_depth - 1)
            if not isinstance(k, bytes):
                raise ValueError("Bencode dictionary keys must be byte strings")
            idx += consumed
            if idx >= len(data):
                raise ValueError("Missing value for bencode dictionary key")
            v, v_consumed = _bdecode(data[idx:], max_depth - 1)
            res[k] = v
            idx += v_consumed
        if idx >= len(data) or data[idx:idx+1] != b"e":
            raise ValueError("Unterminated bencode dictionary")
        return res, idx + 1
    elif first.isdigit():
        colon = data.find(b":")
        if colon == -1:
            raise ValueError("Malformed bencode string length")
        length = int(data[:colon].decode("ascii", errors="strict"))
        if length < 0:
            raise ValueError("Negative bencode string length")
        start = colon + 1
        end = start + length
        if end > len(data):
            raise ValueError("Bencode string exceeds buffer length")
        return data[start:end], end
    else:
        raise ValueError(f"Unexpected bencode token: {first!r}")


MAX_TORRENT_BYTES = 15 * 1024 * 1024  # 15 MB


def validate_torrent_bytes(content: bytes, filename: str = "file.torrent") -> bytes:
    """
    Validates uploaded .torrent file structure, size, Bencode dictionary format,
    and validates tracker/webseed URLs against SSRF.
    """
    if not filename.lower().endswith(".torrent"):
        raise_structured_aria2_error(
            status_code=400,
            error_code="INVALID_TORRENT_FILE",
            title="Invalid Torrent Extension",
            detail=f"Uploaded file '{filename}' does not have a .torrent extension.",
            suggestion="Ensure you are selecting a valid BitTorrent (.torrent) file."
        )

    if len(content) == 0:
        raise_structured_aria2_error(
            status_code=400,
            error_code="INVALID_TORRENT_FILE",
            title="Empty Torrent File",
            detail="The uploaded .torrent file contains 0 bytes.",
            suggestion="Download the .torrent file again and verify its file size before uploading."
        )

    if len(content) > MAX_TORRENT_BYTES:
        raise_structured_aria2_error(
            status_code=400,
            error_code="INVALID_TORRENT_FILE",
            title="Torrent File Too Large",
            detail=f"The uploaded torrent file ({len(content) / (1024*1024):.1f} MB) exceeds the maximum limit of 15 MB.",
            suggestion="Standard .torrent files are typically under 500 KB. Verify the selected file."
        )

    clean_bytes = content.strip()
    try:
        decoded, consumed = _bdecode(clean_bytes)
        if not isinstance(decoded, dict):
            raise ValueError("Root bencoded value must be a dictionary")
        if consumed != len(clean_bytes):
            raise ValueError("Trailing data after bencoded dictionary")
    except Exception as e:
        raise_structured_aria2_error(
            status_code=400,
            error_code="INVALID_TORRENT_FILE",
            title="Corrupted Torrent Structure",
            detail=f"The uploaded file is not a valid Bencoded BitTorrent dictionary: {e}",
            suggestion="Ensure the .torrent file is intact and not corrupted, truncated, or a text document."
        )

    # Extract announce and webseed URLs and validate them against SSRF
    urls_to_check: List[str] = []
    ann = decoded.get(b"announce")
    if isinstance(ann, bytes):
        urls_to_check.append(ann.decode("utf-8", errors="replace"))

    ann_list = decoded.get(b"announce-list")
    if isinstance(ann_list, list):
        for tier in ann_list:
            if isinstance(tier, list):
                for tracker in tier:
                    if isinstance(tracker, bytes):
                        urls_to_check.append(tracker.decode("utf-8", errors="replace"))
            elif isinstance(tier, bytes):
                urls_to_check.append(tier.decode("utf-8", errors="replace"))

    for key in (b"url-list", b"httpseeds"):
        seeds = decoded.get(key)
        if isinstance(seeds, bytes):
            urls_to_check.append(seeds.decode("utf-8", errors="replace"))
        elif isinstance(seeds, list):
            for s in seeds:
                if isinstance(s, bytes):
                    urls_to_check.append(s.decode("utf-8", errors="replace"))

    for u in urls_to_check:
        u_clean = u.strip()
        if u_clean.lower().startswith(("http://", "https://")):
            validate_download_uri(u_clean)

    return content


def format_bytes(bytes_num: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"


def format_speed(bps: int) -> str:
    return f"{format_bytes(bps)}/s"


def format_eta(remaining_bytes: int, speed_bps: int) -> str:
    if speed_bps <= 0 or remaining_bytes <= 0:
        return "Unknown"
    secs = int(remaining_bytes / speed_bps)
    mins, sec = divmod(secs, 60)
    hrs, mins = divmod(mins, 60)
    if hrs > 0:
        return f"{hrs}h {mins}m"
    if mins > 0:
        return f"{mins}m {sec}s"
    return f"{sec}s"


@router.get("/stats", dependencies=[Depends(require_admin_secret)])
async def get_aria2_stats():
    """
    Returns global download and upload speed and item counts.
    """
    try:
        stats = await aria2_client.get_global_stat()
        if not stats:
            return {
                "download_speed_raw": 0,
                "upload_speed_raw": 0,
                "download_speed_fmt": "0.0 B/s",
                "upload_speed_fmt": "0.0 B/s",
                "num_active": 0,
                "num_waiting": 0,
                "num_stopped": 0
            }
        down_speed = int(stats.get("downloadSpeed", 0))
        up_speed = int(stats.get("uploadSpeed", 0))
        return {
            "download_speed_raw": down_speed,
            "upload_speed_raw": up_speed,
            "download_speed_fmt": format_speed(down_speed),
            "upload_speed_fmt": format_speed(up_speed),
            "num_active": int(stats.get("numActive", 0)),
            "num_waiting": int(stats.get("numWaiting", 0)),
            "num_stopped": int(stats.get("numStopped", 0))
        }
    except Exception as e:
        logger.warning(f"Error reading Aria2 stats: {e}")
        return {
            "download_speed_raw": 0,
            "upload_speed_raw": 0,
            "download_speed_fmt": "0.0 B/s",
            "upload_speed_fmt": "0.0 B/s",
            "num_active": 0,
            "num_waiting": 0,
            "num_stopped": 0
        }


@router.get("/downloads", dependencies=[Depends(require_admin_secret)])
async def list_downloads():
    """
    Returns formatted list of active, waiting, and completed downloads.
    Automatically purges superseded metadata fetching tasks.
    """
    try:
        active, waiting, stopped = await aria2_client.tell_all_downloads(0, 20, 0, 30)
    except Exception as e:
        logger.warning(f"Error communicating with Aria2 for download list: {e}")
        return []

    all_raw = active + waiting + stopped
    results = []

    for item in all_raw:
        gid = item.get("gid")
        total_len = int(item.get("totalLength", 0))
        comp_len = int(item.get("completedLength", 0))
        speed = int(item.get("downloadSpeed", 0))
        status = item.get("status", "")
        followed_by = item.get("followedBy")

        name = "Unknown Download"
        bt = item.get("bittorrent", {})
        info = bt.get("info", {})
        if "name" in info:
            name = info["name"]
        elif item.get("files") and item["files"][0].get("path"):
            name = item["files"][0]["path"].split("/")[-1]

        # Auto-clean completed metadata tasks when followedBy exists
        if followed_by and status == "complete":
            try:
                await aria2_client.remove_download_result(gid)
            except Exception:
                pass
            continue

        is_metadata = name.startswith("[METADATA]") or (status == "active" and total_len == 0)

        # If completedLength == totalLength and totalLength > 0, report as 100%
        if total_len > 0 and comp_len >= total_len and status != "error":
            pct = 100.0
        elif total_len > 0:
            pct = round((comp_len / total_len * 100), 1)
        else:
            pct = 0.0

        remaining = max(0, total_len - comp_len)
        binding = aria2_monitor.metadata_bindings.get(gid)

        results.append({
            "gid": gid,
            "name": name,
            "status": status,
            "is_metadata": is_metadata,
            "errorMessage": item.get("errorMessage") or None,
            "errorCode": item.get("errorCode") or None,
            "total_bytes": total_len,
            "completed_bytes": comp_len,
            "total_fmt": format_bytes(total_len) if total_len > 0 else "0 B",
            "completed_fmt": format_bytes(comp_len) if comp_len > 0 else "0 B",
            "progress_pct": pct,
            "speed_bps": speed,
            "download_speed": format_speed(speed),
            "eta": format_eta(remaining, speed),
            "connections": int(item.get("connections", 0)),
            "seeders": int(item.get("numSeeders", 0)),
            "metadata_bound": binding is not None,
            "tmdb_id": binding.get("tmdb_id") if binding else None
        })

    return results


@router.post("/add", dependencies=[Depends(require_admin_secret)])
async def add_download(request: Request):
    """
    Adds a new download from URL, Magnet URI, or uploaded .torrent file with structured error feedback.
    Supports JSON, application/x-www-form-urlencoded, and multipart/form-data.
    """
    # 1. Ensure Daemon is alive or attempt to start it
    daemon_online = await ensure_aria2_daemon_running()
    if not daemon_online:
        raise_structured_aria2_error(
            status_code=503,
            error_code="ARIA2_DAEMON_OFFLINE",
            title="Download Service Offline",
            detail="The Aria2c background download service is not responding on port 6800.",
            suggestion="Restart the service from your terminal using './start.sh' or 'aria2c --conf-path=~/.aria2/aria2.conf -D'."
        )

    content_type = request.headers.get("content-type", "")
    uri: Optional[str] = None
    tmdb_id: Optional[int] = None
    media_type: str = "movie"
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    raw_torrent_bytes: Optional[bytes] = None
    torrent_filename = "file.torrent"

    if "application/json" in content_type:
        try:
            body = await request.json()
            uri = body.get("uri")
            tmdb_id = body.get("tmdb_id")
            media_type = body.get("media_type") or body.get("type") or "movie"
            season_number = body.get("season_number")
            episode_number = body.get("episode_number")
        except Exception:
            pass
    elif "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        uri = form.get("uri")
        if form.get("tmdb_id"):
            try:
                tmdb_id = int(form.get("tmdb_id"))
            except ValueError:
                pass
        media_type = form.get("media_type") or form.get("type") or "movie"
        if form.get("season_number"):
            try:
                season_number = int(form.get("season_number"))
            except ValueError:
                pass
        if form.get("episode_number"):
            try:
                episode_number = int(form.get("episode_number"))
            except ValueError:
                pass

        uploaded_f = form.get("torrent_file") or form.get("file")
        if uploaded_f and hasattr(uploaded_f, "read"):
            raw_torrent_bytes = await uploaded_f.read(MAX_TORRENT_BYTES + 1)
            if len(raw_torrent_bytes) > MAX_TORRENT_BYTES:
                raise_structured_aria2_error(
                    status_code=400,
                    error_code="INVALID_TORRENT_FILE",
                    title="Torrent File Too Large",
                    detail="The uploaded torrent file exceeds the maximum limit of 15 MB.",
                    suggestion="Standard .torrent files are typically under 500 KB. Verify the selected file."
                )
            torrent_filename = getattr(uploaded_f, "filename", "file.torrent") or "file.torrent"

    gid = None

    if raw_torrent_bytes:
        valid_bytes = validate_torrent_bytes(raw_torrent_bytes, torrent_filename)
        try:
            gid = await aria2_client.add_torrent(valid_bytes)
        except ConnectionError as ce:
            raise_structured_aria2_error(
                status_code=503,
                error_code="ARIA2_DAEMON_OFFLINE",
                title="Download Service Offline",
                detail=f"Aria2c RPC daemon connection failed: {ce}",
                suggestion="Verify that aria2c is running and port 6800 is not blocked."
            )
        except RuntimeError as re:
            raise_structured_aria2_error(
                status_code=400,
                error_code="ARIA2_RPC_REJECTED",
                title="Torrent Rejected",
                detail=str(re),
                suggestion="The torrent file was rejected by the engine. Check if the torrent already exists in the queue."
            )
    elif uri:
        safe_uri = validate_download_uri(uri)
        try:
            gid = await aria2_client.add_uri([safe_uri])
        except ConnectionError as ce:
            raise_structured_aria2_error(
                status_code=503,
                error_code="ARIA2_DAEMON_OFFLINE",
                title="Download Service Offline",
                detail=f"Aria2c RPC daemon connection failed: {ce}",
                suggestion="Verify that aria2c is running and port 6800 is not blocked."
            )
        except RuntimeError as re:
            raise_structured_aria2_error(
                status_code=400,
                error_code="ARIA2_RPC_REJECTED",
                title="Download Link Rejected",
                detail=str(re),
                suggestion="The download link was rejected by the engine. Verify URL validity or tracker availability."
            )
    else:
        raise_structured_aria2_error(
            status_code=400,
            error_code="EMPTY_INPUT",
            title="Empty Download Input",
            detail="No download link, Magnet URI, or .torrent file was provided in the request.",
            suggestion="Paste a video URL or Magnet link, or choose a .torrent file to upload."
        )

    if not gid:
        raise_structured_aria2_error(
            status_code=502,
            error_code="ARIA2_RPC_REJECTED",
            title="Download Initiation Failed",
            detail="The download daemon received the request but did not return a valid download GID.",
            suggestion="Check the Aria2 logs or verify that the download source is reachable."
        )

    # Bind metadata if TMDB ID provided
    if tmdb_id:
        aria2_monitor.bind_metadata(
            gid=gid,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number
        )

    return {
        "status": "success",
        "gid": gid,
        "metadata_bound": tmdb_id is not None
    }


@router.post("/upload-torrent", dependencies=[Depends(require_admin_secret)])
async def upload_torrent_file(
    torrent_file: UploadFile = File(...),
    tmdb_id: Optional[int] = Form(None),
    media_type: str = Form("movie"),
    season_number: Optional[int] = Form(None),
    episode_number: Optional[int] = Form(None)
):
    """
    Dedicated endpoint for uploading .torrent files with structured error validation.
    """
    daemon_online = await ensure_aria2_daemon_running()
    if not daemon_online:
        raise_structured_aria2_error(
            status_code=503,
            error_code="ARIA2_DAEMON_OFFLINE",
            title="Download Service Offline",
            detail="The Aria2c background download service is not responding on port 6800.",
            suggestion="Restart the service from your terminal using './start.sh' or 'aria2c --conf-path=~/.aria2/aria2.conf -D'."
        )

    raw_bytes = await torrent_file.read(MAX_TORRENT_BYTES + 1)
    if len(raw_bytes) > MAX_TORRENT_BYTES:
        raise_structured_aria2_error(
            status_code=400,
            error_code="INVALID_TORRENT_FILE",
            title="Torrent File Too Large",
            detail="The uploaded torrent file exceeds the maximum limit of 15 MB.",
            suggestion="Standard .torrent files are typically under 500 KB. Verify the selected file."
        )
    valid_bytes = validate_torrent_bytes(raw_bytes, torrent_file.filename or "file.torrent")
    try:
        gid = await aria2_client.add_torrent(valid_bytes)
    except ConnectionError as ce:
        raise_structured_aria2_error(
            status_code=503,
            error_code="ARIA2_DAEMON_OFFLINE",
            title="Download Service Offline",
            detail=f"Aria2c RPC daemon connection failed: {ce}",
            suggestion="Verify that aria2c is running and port 6800 is not blocked."
        )
    except RuntimeError as re:
        raise_structured_aria2_error(
            status_code=400,
            error_code="ARIA2_RPC_REJECTED",
            title="Torrent Rejected",
            detail=str(re),
            suggestion="The torrent file was rejected by the engine. Check if the torrent already exists in the queue."
        )

    if not gid:
        raise_structured_aria2_error(
            status_code=502,
            error_code="ARIA2_RPC_REJECTED",
            title="Download Initiation Failed",
            detail="The download daemon received the request but did not return a valid download GID.",
            suggestion="Check the Aria2 logs or verify that the download source is reachable."
        )

    if tmdb_id:
        aria2_monitor.bind_metadata(
            gid=gid,
            tmdb_id=tmdb_id,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number
        )

    return {
        "status": "success",
        "gid": gid,
        "metadata_bound": tmdb_id is not None
    }


@router.post("/pause/{gid}", dependencies=[Depends(require_admin_secret)])
async def pause_download(gid: str):
    try:
        res = await aria2_client.pause(gid)
        return {"status": "paused", "gid": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/unpause/{gid}", dependencies=[Depends(require_admin_secret)])
async def unpause_download(gid: str):
    try:
        res = await aria2_client.unpause(gid)
        return {"status": "resumed", "gid": res}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _execute_two_stage_removal(gid: str) -> Dict[str, Any]:
    aria2_monitor.metadata_bindings.pop(gid, None)
    status_info = None
    try:
        status_info = await aria2_client.tell_status(gid)
    except Exception:
        pass

    curr_status = (status_info.get("status") if status_info else "").lower()

    if curr_status in ("active", "waiting", "paused"):
        try:
            await aria2_client.force_remove(gid)
        except Exception as e:
            logger.debug(f"force_remove notice on {gid}: {e}")
        try:
            await aria2_client.remove_download_result(gid)
        except Exception:
            pass
        return {"status": "removed", "gid": gid}

    # For complete, error, removed, or unknown status
    try:
        await aria2_client.remove_download_result(gid)
    except Exception as e:
        logger.debug(f"remove_download_result notice on {gid}: {e}")
        try:
            await aria2_client.force_remove(gid)
        except Exception:
            pass

    return {"status": "removed", "gid": gid}


@router.delete("/remove/{gid}", dependencies=[Depends(require_admin_secret)])
@router.post("/remove/{gid}", dependencies=[Depends(require_admin_secret)])
@router.delete("/{gid}", dependencies=[Depends(require_admin_secret)])
async def remove_download(gid: str):
    return await _execute_two_stage_removal(gid)


@router.post("/clear-completed", dependencies=[Depends(require_admin_secret)])
@router.delete("/completed", dependencies=[Depends(require_admin_secret)])
async def clear_completed_downloads():
    try:
        await aria2_client.purge_download_result()
    except Exception as e:
        logger.debug(f"purge_download_result notice: {e}")
    try:
        stopped = await aria2_client.tell_stopped(0, 100)
        for s in stopped:
            s_gid = s.get("gid")
            if s_gid:
                aria2_monitor.metadata_bindings.pop(s_gid, None)
                try:
                    await aria2_client.remove_download_result(s_gid)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"Failed clearing stopped downloads: {e}")
    return {"status": "cleared"}


@router.post("/retry/{gid}", dependencies=[Depends(require_admin_secret)])
async def retry_download(gid: str):
    status_info = None
    try:
        status_info = await aria2_client.tell_status(gid)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Download {gid} not found: {e}")

    uris = []
    files = status_info.get("files", [])
    for f in files:
        for u in f.get("uris", []):
            if u.get("uri"):
                uris.append(u["uri"])

    binding = aria2_monitor.metadata_bindings.pop(gid, None)

    try:
        await aria2_client.remove_download_result(gid)
    except Exception:
        pass

    if uris:
        validated_uris = [validate_download_uri(u) for u in uris]
        new_gid = await aria2_client.add_uri([validated_uris[0]])
        if binding and new_gid:
            aria2_monitor.bind_metadata(
                gid=new_gid,
                tmdb_id=binding.get("tmdb_id"),
                media_type=binding.get("media_type", "movie"),
                season_number=binding.get("season_number"),
                episode_number=binding.get("episode_number")
            )
        return {"status": "retrying", "old_gid": gid, "new_gid": new_gid}
    else:
        try:
            res = await aria2_client.unpause(gid)
            return {"status": "resumed", "gid": res}
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Cannot retry download {gid}: {e}")
