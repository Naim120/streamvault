import logging
import mimetypes
import os
import re
import uuid
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from ..config import settings
from ..security import (
    generate_stream_token,
    verify_stream_token,
    generate_segment_token,
    verify_segment_token,
    get_current_user_id
)
from ..database import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stream", tags=["Streaming"])

ALLOWED_STREAM_EXTENSIONS = {".m3u8", ".ts", ".m4s", ".mp4", ".aac", ".vtt"}

# Initialize MIME types for HLS, Audio, Video and WebVTT
mimetypes.add_type("application/vnd.apple.mpegurl", ".m3u8")
mimetypes.add_type("video/mp2t", ".ts")
mimetypes.add_type("video/iso.segment", ".m4s")
mimetypes.add_type("audio/aac", ".aac")
mimetypes.add_type("text/vtt", ".vtt")
mimetypes.add_type("video/mp4", ".mp4")

# Resolution cache to avoid repeated DB lookups for segments
_SERIES_EPISODE_CACHE: dict[str, str] = {}

async def check_media_published(target_id: str) -> bool:
    """
    Verifies that the target movie, episode, or series is published (is_published == True).
    If an episode is targeted, verifies both the episode and its parent series are published.
    Returns False if hidden or not found.
    Uses in-memory catalog cache for sub-millisecond zero-network lookup.
    """
    try:
        from .metadata import get_cached_catalog_data
        media_items, episodes, _ = await get_cached_catalog_data()
        if media_items or episodes:
            target_str = str(target_id).strip().lower()
            # 1. Check media_items (Movie or Series)
            for m in media_items:
                if str(m.get("id", "")).strip().lower() == target_str:
                    return bool(m.get("is_published", False))

            # 2. Check episodes
            for ep in episodes:
                if str(ep.get("id", "")).strip().lower() == target_str:
                    if not ep.get("is_published", False):
                        return False
                    parent_id = str(ep.get("media_id", "")).strip().lower()
                    if parent_id:
                        for p in media_items:
                            if str(p.get("id", "")).strip().lower() == parent_id:
                                return bool(p.get("is_published", False))
                        return False
                    return False
            return False

        # Fallback to Supabase if catalog cache is unpopulated
        # 1. Check media_items (Movie or Series)
        m = await db.select("media_items", {"id": f"eq.{target_id}", "limit": "1"})
        if m and len(m) > 0:
            return bool(m[0].get("is_published", False))

        # 2. Check episodes
        ep = await db.select("episodes", {"id": f"eq.{target_id}", "limit": "1"})
        if ep and len(ep) > 0:
            if not ep[0].get("is_published", False):
                return False
            parent_id = ep[0].get("media_id")
            if parent_id:
                p = await db.select("media_items", {"id": f"eq.{parent_id}", "limit": "1"})
                if p and len(p) > 0:
                    return bool(p[0].get("is_published", False))
                return False
            return False
    except Exception as e:
        logger.warning(f"Error checking publication status for {target_id}: {e}")
        return False

    return False


async def resolve_stream_target(target_id: str, user_id: Optional[str] = None) -> tuple[str, Path]:
    """
    Resolves target_id to the actual library directory containing master.m3u8.
    If target_id is directly a movie or episode UUID with files on disk, returns it immediately.
    If target_id is a series UUID:
      1. Checks user's active watch_progress to resume their current episode (or next sequential episode if completed).
      2. If no progress, resolves to the first published episode (Season 1 Episode 1).
    """
    library_dir = Path(settings.MEDIA_LIBRARY_DIR) / target_id
    if (library_dir / "master.m3u8").exists():
        return target_id, library_dir

    # Check resolution cache
    cache_key = f"{target_id}:{user_id or 'anon'}"
    if cache_key in _SERIES_EPISODE_CACHE:
        cached_id = _SERIES_EPISODE_CACHE[cache_key]
        if await check_media_published(cached_id):
            cached_dir = Path(settings.MEDIA_LIBRARY_DIR) / cached_id
            if (cached_dir / "master.m3u8").exists():
                return cached_id, cached_dir
        else:
            _SERIES_EPISODE_CACHE.pop(cache_key, None)

    # 1. Check user watch progress for active or next episode
    if user_id and user_id != "anonymous":
        try:
            prog = await db.select(
                "watch_progress",
                {
                    "user_id": f"eq.{user_id}",
                    "media_id": f"eq.{target_id}",
                    "episode_id": "not.is.null",
                    "order": "updated_at.desc",
                    "limit": "1"
                }
            )
            if prog and len(prog) > 0 and prog[0].get("episode_id"):
                row = prog[0]
                res_ep_id = row["episode_id"]
                ep_chk = await db.select("episodes", {"id": f"eq.{res_ep_id}", "media_id": f"eq.{target_id}", "is_published": "eq.true", "limit": "1"})
                if ep_chk:
                    pos = float(row.get("position_seconds") or 0.0)
                    dur = float(row.get("duration_seconds") or 0.0)
                    is_completed = bool(row.get("is_completed", False)) or (dur > 0 and (pos >= dur - 30 or pos / dur >= 0.95))

                    if is_completed:
                        # Episode was completed -> find next sequential published episode
                        curr_ep = ep_chk[0]
                        curr_s = curr_ep.get("season_number", 1)
                        curr_e = curr_ep.get("episode_number", 1)
                        all_eps = await db.select(
                            "episodes",
                            {
                                "media_id": f"eq.{target_id}",
                                "is_published": "eq.true",
                                "order": "season_number.asc,episode_number.asc"
                            }
                        )
                        for ep in (all_eps or []):
                            s_num = ep.get("season_number", 1)
                            e_num = ep.get("episode_number", 1)
                            if s_num > curr_s or (s_num == curr_s and e_num > curr_e):
                                next_ep_id = ep["id"]
                                next_dir = Path(settings.MEDIA_LIBRARY_DIR) / next_ep_id
                                if (next_dir / "master.m3u8").exists():
                                    _SERIES_EPISODE_CACHE[cache_key] = next_ep_id
                                    logger.info(f"Resolved completed episode {res_ep_id} to next episode {next_ep_id}")
                                    return next_ep_id, next_dir
                                break

                    res_dir = Path(settings.MEDIA_LIBRARY_DIR) / res_ep_id
                    if (res_dir / "master.m3u8").exists():
                        _SERIES_EPISODE_CACHE[cache_key] = res_ep_id
                        logger.info(f"Resolved series {target_id} to user resume episode {res_ep_id}")
                        return res_ep_id, res_dir
        except Exception as e:
            logger.warning(f"Error checking watch progress for series resolution: {e}")

    # 2. Fallback: First published episode (S1:E1)
    try:
        eps = await db.select(
            "episodes",
            {
                "media_id": f"eq.{target_id}",
                "is_published": "eq.true",
                "order": "season_number.asc,episode_number.asc",
                "limit": "1"
            }
        )
        if eps and len(eps) > 0:
            first_ep_id = eps[0]["id"]
            ep_dir = Path(settings.MEDIA_LIBRARY_DIR) / first_ep_id
            if (ep_dir / "master.m3u8").exists():
                _SERIES_EPISODE_CACHE[cache_key] = first_ep_id
                _SERIES_EPISODE_CACHE[f"{target_id}:anon"] = first_ep_id
                logger.info(f"Resolved series {target_id} to first episode stream {first_ep_id}")
                return first_ep_id, ep_dir
    except Exception as e:
        logger.error(f"Error resolving series target {target_id}: {e}")

    return target_id, library_dir

_PLAYLIST_DURATION_CACHE: dict[str, tuple[float, float]] = {}

def get_hls_playlist_duration(target_dir: Path) -> float:
    """
    Fast helper that reads video/playlist.m3u8 (or playlist.m3u8) in target_dir
    and sums all #EXTINF:<seconds>, tags with mtime-based in-memory caching.
    Takes <0.005ms when cached.
    """
    try:
        cand_paths = [
            target_dir / "video" / "playlist.m3u8",
            target_dir / "playlist.m3u8"
        ]
        playlist_path = None
        for p in cand_paths:
            if p.exists():
                playlist_path = p
                break
        if not playlist_path:
            cand = list(target_dir.glob("*/playlist.m3u8"))
            if cand:
                playlist_path = cand[0]
            else:
                return 0.0

        p_str = str(playlist_path)
        stat = playlist_path.stat()
        mtime = stat.st_mtime
        if p_str in _PLAYLIST_DURATION_CACHE:
            cached_mtime, cached_dur = _PLAYLIST_DURATION_CACHE[p_str]
            if cached_mtime == mtime:
                return cached_dur

        total_sec = 0.0
        with open(playlist_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if line.startswith("#EXTINF:"):
                    val = line[8:].split(",")[0].strip()
                    try:
                        total_sec += float(val)
                    except ValueError:
                        pass
        _PLAYLIST_DURATION_CACHE[p_str] = (mtime, total_sec)
        return total_sec
    except Exception as e:
        logger.debug(f"Error calculating playlist duration for {target_dir}: {e}")
        return 0.0


@router.get("/token")
async def get_stream_access_token(
    media_id: str = Query(..., description="Target media_id or episode_id"),
    user_id: Optional[str] = Query(None, description="Current authenticated user UUID"),
    authenticated_user_id: Optional[str] = Depends(get_current_user_id)
):
    """
    Issues an HMAC-SHA256 signed playback token for authorized streaming, resolves active episode,
    and returns resume_position_seconds, duration_seconds, resolved_episode, and tracks in one single round-trip.
    Rejects hidden or unpublished media with 404 Not Found.
    """
    is_pub = await check_media_published(media_id)
    if not is_pub:
        raise HTTPException(status_code=404, detail="Media not found or unavailable")

    # Enforce cryptographic identity: user_id is strictly derived from verified JWT
    if authenticated_user_id:
        uid = authenticated_user_id
    else:
        uid = "anonymous"

    resolved_id = media_id
    resolved_episode = None
    resume_position_seconds = 0.0
    saved_preferences = None
    media_item = None

    # Determine if media_id is a media_item (series/movie) or an episode
    m_rows = await db.select("media_items", {"id": f"eq.{media_id}", "limit": "1"})
    if m_rows and len(m_rows) > 0:
        media_item = m_rows[0]
        m_type = media_item.get("type", "movie")

        if m_type == "series":
            # Target is a Series UUID: resolve active episode & resume position
            last_ep_id = None
            is_comp = False

            if uid and uid != "anonymous":
                prog = await db.select(
                    "watch_progress",
                    {
                        "user_id": f"eq.{uid}",
                        "media_id": f"eq.{media_id}",
                        "episode_id": "not.is.null",
                        "order": "updated_at.desc",
                        "limit": "1"
                    }
                )
                if prog and len(prog) > 0 and prog[0].get("episode_id"):
                    row = prog[0]
                    last_ep_id = row.get("episode_id")
                    pos = float(row.get("position_seconds") or 0.0)
                    dur = float(row.get("duration_seconds") or 0.0)
                    is_comp = bool(row.get("is_completed", False)) or (dur > 0 and (pos >= dur - 30 or pos / dur >= 0.95))
                    if not is_comp and pos > 5.0:
                        resume_position_seconds = pos

            last_ep = None
            if last_ep_id:
                last_ep_rows = await db.select("episodes", {"id": f"eq.{last_ep_id}", "media_id": f"eq.{media_id}", "is_published": "eq.true", "limit": "1"})
                if last_ep_rows:
                    last_ep = last_ep_rows[0]

            if last_ep and is_comp:
                # Latest episode was completed: advance to next sequential published episode (S1:E2)
                curr_s = last_ep.get("season_number", 1)
                curr_e = last_ep.get("episode_number", 1)
                all_eps = await db.select(
                    "episodes",
                    {
                        "media_id": f"eq.{media_id}",
                        "is_published": "eq.true",
                        "order": "season_number.asc,episode_number.asc"
                    }
                )
                next_ep = None
                for ep in (all_eps or []):
                    s_num = ep.get("season_number", 1)
                    e_num = ep.get("episode_number", 1)
                    if s_num > curr_s or (s_num == curr_s and e_num > curr_e):
                        next_ep = ep
                        break
                if next_ep:
                    resolved_episode = next_ep
                    resolved_id = next_ep["id"]
                    resume_position_seconds = 0.0
                else:
                    resolved_episode = last_ep
                    resolved_id = last_ep["id"]
                    resume_position_seconds = 0.0
            elif last_ep:
                resolved_episode = last_ep
                resolved_id = last_ep["id"]
                if not is_comp and resume_position_seconds > 5.0 and prog and len(prog) > 0:
                    row = prog[0]
                    saved_preferences = {
                        "audio_index": row.get("preferred_audio_index"),
                        "audio_lang": row.get("preferred_audio_lang"),
                        "audio_label": row.get("preferred_audio_label"),
                        "subtitle_index": row.get("preferred_subtitle_index"),
                        "subtitle_lang": row.get("preferred_subtitle_lang"),
                        "subtitle_label": row.get("preferred_subtitle_label"),
                    }
            else:
                # No prior progress: fallback to first published episode (S1:E1)
                first_eps = await db.select(
                    "episodes",
                    {
                        "media_id": f"eq.{media_id}",
                        "is_published": "eq.true",
                        "order": "season_number.asc,episode_number.asc",
                        "limit": "1"
                    }
                )
                if first_eps and len(first_eps) > 0:
                    resolved_episode = first_eps[0]
                    resolved_id = first_eps[0]["id"]
                    resume_position_seconds = 0.0
        else:
            # Movie UUID
            resolved_id = media_id
            resolved_episode = None
            if uid and uid != "anonymous":
                prog = await db.select(
                    "watch_progress",
                    {
                        "user_id": f"eq.{uid}",
                        "media_id": f"eq.{media_id}",
                        "episode_id": "is.null",
                        "limit": "1"
                    }
                )
                if prog and len(prog) > 0:
                    row = prog[0]
                    pos = float(row.get("position_seconds") or 0.0)
                    dur = float(row.get("duration_seconds") or (media_item.get("runtime_minutes", 0) * 60) or 0.0)
                    is_comp = bool(row.get("is_completed", False)) or (dur > 0 and (pos >= dur - 30 or pos / dur >= 0.95))
                    if not is_comp and pos > 5.0:
                        resume_position_seconds = pos
                        saved_preferences = {
                            "audio_index": row.get("preferred_audio_index"),
                            "audio_lang": row.get("preferred_audio_lang"),
                            "audio_label": row.get("preferred_audio_label"),
                            "subtitle_index": row.get("preferred_subtitle_index"),
                            "subtitle_lang": row.get("preferred_subtitle_lang"),
                            "subtitle_label": row.get("preferred_subtitle_label"),
                        }
    else:
        # Check if media_id is an Episode UUID directly
        ep_rows = await db.select("episodes", {"id": f"eq.{media_id}", "is_published": "eq.true", "limit": "1"})
        if ep_rows and len(ep_rows) > 0:
            parent_id = ep_rows[0].get("media_id")
            if parent_id and not await check_media_published(parent_id):
                raise HTTPException(status_code=404, detail="Media not found or unavailable")
            resolved_episode = ep_rows[0]
            resolved_id = media_id
            if uid and uid != "anonymous":
                prog = await db.select(
                    "watch_progress",
                    {
                        "user_id": f"eq.{uid}",
                        "episode_id": f"eq.{media_id}",
                        "limit": "1"
                    }
                )
                if prog and len(prog) > 0:
                    row = prog[0]
                    pos = float(row.get("position_seconds") or 0.0)
                    dur = float(row.get("duration_seconds") or resolved_episode.get("duration_seconds") or 0.0)
                    is_comp = bool(row.get("is_completed", False)) or (dur > 0 and (pos >= dur - 30 or pos / dur >= 0.95))
                    if not is_comp and pos > 5.0:
                        resume_position_seconds = pos
                        saved_preferences = {
                            "audio_index": row.get("preferred_audio_index"),
                            "audio_lang": row.get("preferred_audio_lang"),
                            "audio_label": row.get("preferred_audio_label"),
                            "subtitle_index": row.get("preferred_subtitle_index"),
                            "subtitle_lang": row.get("preferred_subtitle_lang"),
                            "subtitle_label": row.get("preferred_subtitle_label"),
                        }

    library_dir = Path(settings.MEDIA_LIBRARY_DIR) / resolved_id
    token, expires = generate_stream_token(media_id=resolved_id, user_id=uid)

    # Resolution caching for zero-delay segment lookups
    _SERIES_EPISODE_CACHE[f"{media_id}:{uid}"] = resolved_id
    _SERIES_EPISODE_CACHE[f"{resolved_id}:{uid}"] = resolved_id

    duration_sec = get_hls_playlist_duration(library_dir)
    if duration_sec <= 0:
        if resolved_episode and resolved_episode.get("duration_seconds"):
            duration_sec = float(resolved_episode["duration_seconds"])
        elif media_item and media_item.get("runtime_minutes"):
            duration_sec = float(media_item["runtime_minutes"] * 60)
    elif resolved_episode and (not resolved_episode.get("duration_seconds") or resolved_episode["duration_seconds"] <= 0):
        resolved_episode["duration_seconds"] = int(duration_sec)

    # Fetch audio_tracks, subtitle_tracks, available_qualities from media_streams
    stream_param = {"episode_id": f"eq.{resolved_id}", "limit": "1"} if resolved_episode else {"media_id": f"eq.{resolved_id}", "limit": "1"}
    stream_rows = await db.select("media_streams", stream_param)
    audio_tracks = []
    subtitle_tracks = []
    available_qualities = []
    if stream_rows and len(stream_rows) > 0:
        s_row = stream_rows[0]
        audio_tracks = s_row.get("audio_tracks") or []
        subtitle_tracks = s_row.get("subtitle_tracks") or []
        available_qualities = s_row.get("available_qualities") or []

    return {
        "media_id": media_id,
        "resolved_id": resolved_id,
        "token": token,
        "expires": expires,
        "user_id": uid,
        "duration_seconds": round(duration_sec, 1) if duration_sec > 0 else 0,
        "resume_position_seconds": round(resume_position_seconds, 1) if resume_position_seconds > 0 else 0.0,
        "saved_preferences": saved_preferences,
        "resolved_episode": resolved_episode,
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
        "available_qualities": available_qualities
    }

@router.api_route("/{target_id}/master.m3u8", methods=["GET", "HEAD"])
async def get_master_playlist(
    target_id: str,
    token: str = Query(..., description="HMAC-SHA256 signature"),
    expires: int = Query(..., description="Expiration timestamp"),
    uid: str = Query(..., description="User ID"),
    request: Request = None
):
    """
    Validates token and returns the master.m3u8 manifest, rewriting child URLs with auth queries.
    Gracefully resolves series ID to active resume episode or S1:E1.
    Rejects hidden or unpublished media with 404 Not Found.
    """
    try:
        valid_uuid = str(uuid.UUID(target_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid target ID: Must be valid UUID")

    is_pub = await check_media_published(valid_uuid)
    if not is_pub:
        raise HTTPException(status_code=404, detail="Media not found or unavailable")

    resolved_id, library_dir = await resolve_stream_target(valid_uuid, user_id=uid)
    if resolved_id != valid_uuid and not await check_media_published(resolved_id):
        raise HTTPException(status_code=404, detail="Media not found or unavailable")

    if not verify_stream_token(media_id=target_id, token=token, expires=expires, user_id=uid, alt_media_id=resolved_id):
        raise HTTPException(status_code=403, detail="Invalid or expired streaming token")

    master_path = library_dir / "master.m3u8"

    if not master_path.exists():
        raise HTTPException(status_code=404, detail="Stream manifest not found or media is still processing")

    if request and request.method == "HEAD":
        return Response(
            status_code=200,
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Cache-Control": "private, max-age=30",
                "Access-Control-Allow-Origin": "*"
            }
        )

    content = master_path.read_text(encoding="utf-8")

    # Rewrite URI paths in master playlist to carry authentication parameters
    # Subtitle URIs: URI="subs/sub_0_en.vtt" -> URI="subs/sub_0_en.vtt?token=...&expires=...&uid=..."
    # Audio URIs: URI="audio_0/playlist.m3u8" -> URI="audio_0/playlist.m3u8?token=...&expires=...&uid=..."
    # Variant playlist lines: video/playlist.m3u8 -> video/playlist.m3u8?token=...&expires=...&uid=...
    token_query = f"token={token}&expires={expires}&uid={uid}"
    
    rewritten_lines = []
    for line in content.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("#EXT-X-MEDIA") and 'URI="' in line_stripped:
            line_rewritten = re.sub(
                r'URI="([^"]+)"',
                lambda m: f'URI="{m.group(1)}?{token_query}"' if "?" not in m.group(1) else f'URI="{m.group(1)}&{token_query}"',
                line_stripped
            )
            rewritten_lines.append(line_rewritten)
        elif not line_stripped.startswith("#") and line_stripped:
            sep = "&" if "?" in line_stripped else "?"
            rewritten_lines.append(f"{line_stripped}{sep}{token_query}")
        else:
            rewritten_lines.append(line_stripped)

    rewritten_content = "\n".join(rewritten_lines)

    return Response(
        content=rewritten_content,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "private, max-age=30",
            "Access-Control-Allow-Origin": "*"
        }
    )

@router.api_route("/{target_id}/{chunk_path:path}", methods=["GET", "HEAD"])
async def get_stream_chunk(
    target_id: str,
    chunk_path: str,
    token: Optional[str] = Query(None, description="HMAC-SHA256 signature"),
    expires: Optional[int] = Query(None, description="Expiration timestamp"),
    uid: Optional[str] = Query(None, description="User ID"),
    st: Optional[str] = Query(None, description="Segment HMAC signature"),
    e: Optional[int] = Query(None, description="Segment expiration bucket"),
    request: Request = None
):
    """
    Streams sub-playlists, audio renditions, .ts/.aac chunks, and .vtt subtitle files with
    byte-range, CORS, and edge caching headers. Supports Nginx X-Accel-Redirect zero-copy delivery.
    """
    try:
        valid_uuid = str(uuid.UUID(target_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid target ID: Must be valid UUID")

    # Reject any chunk_path containing .., \, null bytes, or starting with /
    clean_subpath = chunk_path.lstrip("/")
    if ".." in clean_subpath or "\\" in clean_subpath or "\x00" in clean_subpath:
        raise HTTPException(status_code=403, detail="Access denied: Path traversal blocked")

    ext = Path(clean_subpath).suffix.lower()
    if ext not in ALLOWED_STREAM_EXTENSIONS:
        raise HTTPException(status_code=403, detail="Access denied: Invalid streaming file extension")

    is_pub = await check_media_published(valid_uuid)
    if not is_pub:
        raise HTTPException(status_code=404, detail="Media not found or unavailable")

    # Fast-path .ts segment resolution: skip DB/watch_progress lookups if target directory exists directly
    library_cand = Path(settings.MEDIA_LIBRARY_DIR) / valid_uuid
    if ext == ".ts" and library_cand.is_dir():
        resolved_id = valid_uuid
        library_dir = library_cand
    else:
        resolved_id, library_dir = await resolve_stream_target(valid_uuid, user_id=uid)
    if resolved_id != valid_uuid and not await check_media_published(resolved_id):
        raise HTTPException(status_code=404, detail="Media not found or unavailable")

    # Segment Authentication vs Strict Manifest/Subtitle Token Verification
    if ext == ".ts":
        if st and e is not None:
            if not verify_segment_token(target_id=valid_uuid, st=st, e=e, alt_target_id=resolved_id):
                raise HTTPException(status_code=403, detail="Invalid or expired segment token")
        elif token and expires is not None:
            # Fallback to user-bound token for legacy / direct clients
            if not verify_stream_token(media_id=valid_uuid, token=token, expires=expires, user_id=uid or "anonymous", alt_media_id=resolved_id):
                raise HTTPException(status_code=403, detail="Invalid or expired streaming token")
        else:
            raise HTTPException(status_code=403, detail="Missing streaming segment token")
    else:
        # Strict user-bound HMAC token validation on manifests, subtitles, and other assets
        if not token or expires is None:
            raise HTTPException(status_code=403, detail="Missing streaming token or expiration")
        if not verify_stream_token(media_id=valid_uuid, token=token, expires=expires, user_id=uid or "anonymous", alt_media_id=resolved_id):
            raise HTTPException(status_code=403, detail="Invalid or expired streaming token")

    base_dir = library_dir.resolve()
    file_path = (base_dir / clean_subpath).resolve()

    # Prevent directory traversal attacks using is_relative_to
    if not file_path.is_relative_to(base_dir):
        raise HTTPException(status_code=403, detail="Access denied: Path traversal blocked")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Requested segment not found")

    # Sub-playlists (e.g. video/playlist.m3u8, audio_0/playlist.m3u8): rewrite segment paths
    if ext == ".m3u8":
        if request and request.method == "HEAD":
            return Response(
                status_code=200,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Cache-Control": "private, max-age=30",
                    "Access-Control-Allow-Origin": "*"
                }
            )

        content = file_path.read_text(encoding="utf-8")
        seg_st, seg_e = generate_segment_token(resolved_id)
        seg_query = f"st={seg_st}&e={seg_e}"
        user_token_query = f"token={token}&expires={expires}&uid={uid or 'anonymous'}"

        rewritten_lines = []
        for line in content.splitlines():
            line_str = line.strip()
            if not line_str.startswith("#") and line_str:
                clean_target = line_str.split("?")[0].lower()
                if clean_target.endswith(".ts"):
                    sep = "&" if "?" in line_str else "?"
                    rewritten_lines.append(f"{line_str}{sep}{seg_query}")
                else:
                    sep = "&" if "?" in line_str else "?"
                    rewritten_lines.append(f"{line_str}{sep}{user_token_query}")
            else:
                rewritten_lines.append(line_str)

        return Response(
            content="\n".join(rewritten_lines),
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Cache-Control": "private, max-age=30",
                "Access-Control-Allow-Origin": "*"
            }
        )

    # Determine accurate MIME type
    if ext == ".vtt":
        mime_type = "text/vtt; charset=utf-8"
    elif ext == ".aac":
        mime_type = "audio/aac"
    elif ext == ".ts":
        mime_type = "video/MP2T"
    elif ext == ".m4s":
        mime_type = "video/iso.segment"
    elif ext == ".mp4":
        mime_type = "video/mp4"
    else:
        mime_type, _ = mimetypes.guess_type(str(file_path))
        if not mime_type:
            mime_type = "application/octet-stream"

    # Static segment caching headers (Cloudflare Edge Caching + Browser)
    if ext == ".ts":
        cache_headers = {
            "Cache-Control": "private, no-transform, max-age=21600",
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*"
        }
    else:
        cache_headers = {
            "Cache-Control": "private, no-transform, max-age=14400",
            "Accept-Ranges": "bytes",
            "Access-Control-Allow-Origin": "*"
        }

    is_behind_nginx = bool(
        request and (
            request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For")
            or request.headers.get("CF-Connecting-IP")
        )
    )

    # High-concurrency zero-copy file delivery via Nginx sendfile
    if is_behind_nginx and ext == ".ts":
        headers = {
            "X-Accel-Redirect": f"/_internal_hls/{resolved_id}/{clean_subpath}",
            **cache_headers
        }
        if request and request.method == "HEAD":
            headers["Content-Length"] = str(file_path.stat().st_size)
        return Response(
            status_code=200,
            media_type=mime_type,
            headers=headers
        )

    if request and request.method == "HEAD":
        return Response(
            status_code=200,
            media_type=mime_type,
            headers={
                **cache_headers,
                "Content-Length": str(file_path.stat().st_size)
            }
        )

    return FileResponse(
        path=str(file_path),
        media_type=mime_type,
        headers=cache_headers
    )
