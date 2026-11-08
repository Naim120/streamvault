import hmac
import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, File, UploadFile, Form
from pydantic import BaseModel, Field, field_validator
from ..config import settings
from ..database import db
from ..metadata.tmdb import tmdb_client
from ..metadata.omdb import omdb_client
from ..metadata.ingester import ingester
from ..transcoder.pipeline_remux import (
    sanitize_track_label,
    detect_stream_language,
    TEXT_SUB_CODECS,
    build_smart_track_label,
    convert_subtitle_bytes_to_vtt
)
from ..transcoder.probe import probe_file
from .stream import get_hls_playlist_duration, check_media_published
import re
from ..security import require_admin_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/metadata", tags=["Metadata & Catalog"])

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}

_catalog_version: int = int(datetime.now(timezone.utc).timestamp() * 1000)

def bump_catalog_version() -> int:
    global _catalog_version
    _catalog_version = int(datetime.now(timezone.utc).timestamp() * 1000)
    invalidate_catalog_cache()
    return _catalog_version

def get_catalog_version() -> int:
    global _catalog_version
    return _catalog_version

class BillboardUpdatePayload(BaseModel):
    is_featured_billboard: bool = False
    exclusive: bool = True
    billboard_priority: Optional[int] = 100

class MediaMetadataUpdatePayload(BaseModel):
    title: Optional[str] = None
    slug: Optional[str] = None
    type: Optional[str] = None
    release_year: Optional[int] = None
    maturity_rating: Optional[str] = None
    runtime_minutes: Optional[int] = None
    synopsis: Optional[str] = None
    poster_url: Optional[str] = None
    backdrop_url: Optional[str] = None
    trailer_youtube_key: Optional[str] = None
    genres: Optional[List[str]] = None
    audio_languages: Optional[List[str]] = None
    badge: Optional[str] = None
    badge_duration_days: Optional[int] = None
    badge_expires_at: Optional[str] = None
    is_published: Optional[bool] = None
    is_featured_billboard: Optional[bool] = None
    billboard_priority: Optional[int] = None

class TrackLabelItem(BaseModel):
    model_config = {"extra": "allow"}

    index: int = Field(default=0, ge=0, le=64)
    lang: str = Field(default="en", max_length=32)
    label: str = Field(default="Track", max_length=96)
    is_default: bool = Field(default=False)
    url: Optional[str] = Field(default=None, max_length=255)

    @field_validator("lang", mode="before")
    @classmethod
    def validate_lang_code(cls, v: Any) -> str:
        clean = str(v or "en").strip().lower()
        match = re.search(r"^[a-z0-9_-]{2,12}", clean)
        if match:
            return match.group(0)
        cleaned_sub = re.sub(r"[^a-z0-9_-]", "", clean)[:12]
        return cleaned_sub if len(cleaned_sub) >= 2 else "und"

    @field_validator("label", mode="before")
    @classmethod
    def sanitize_label(cls, v: Any) -> str:
        return sanitize_track_label(str(v or "Track"), fallback="Track")

class StreamTracksUpdateRequest(BaseModel):
    audio_tracks: list[TrackLabelItem] = []
    subtitle_tracks: list[TrackLabelItem] = []

class ManualIngestPayload(BaseModel):
    filename: str
    tmdb_id: int
    media_type: str = "movie"
    season_number: Optional[int] = None
    episode_number: Optional[int] = None

class UpdateBadgePayload(BaseModel):
    badge: Optional[str] = None
    badge_duration_days: Optional[int] = None
    badge_expires_at: Optional[str] = None
    badge_type: Optional[str] = None # legacy compatibility

class EpisodeMetadataUpdate(BaseModel):
    title: Optional[str] = None
    synopsis: Optional[str] = None
    still_url: Optional[str] = None
    season_number: Optional[int] = Field(default=None, ge=1, le=100)
    episode_number: Optional[int] = Field(default=None, ge=1, le=2000)
    duration_seconds: Optional[int] = Field(default=None, ge=0)
    is_published: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def sanitize_title(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = re.sub(r'<[^>]*>', '', v)
        clean = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', clean).strip()
        if not clean or len(clean) > 200:
            raise ValueError("Episode title must be between 1 and 200 characters.")
        return clean

    @field_validator("synopsis")
    @classmethod
    def sanitize_synopsis(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        clean = re.sub(r'<[^>]*>', '', v)
        clean = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', clean).strip()
        if len(clean) > 2000:
            raise ValueError("Synopsis must not exceed 2000 characters.")
        return clean or None

    @field_validator("still_url")
    @classmethod
    def validate_still_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        url = v.strip()
        url_lower = url.lower()
        if any(bad in url_lower for bad in ["javascript:", "data:text/", "data:image/svg", "vbscript:"]):
            raise ValueError("Invalid image URL protocol.")
        if not (url.startswith("https://") or url.startswith("http://localhost:") or url.startswith("http://127.0.0.1:") or url.startswith("/media/")):
            raise ValueError("Thumbnail URL must begin with https://, http://localhost:, http://127.0.0.1:, or /media/.")
        return url

@router.get("/search")
async def search_tmdb(
    query: str = Query(..., min_length=1),
    type: str = Query("multi", pattern="^(movie|tv|multi)$")
):
    """
    Searches TMDB for movies and TV series with PostgREST/injection sanitization.
    """
    clean_query = re.sub(r'[(),*\\;"\'%_<>]+', ' ', query)[:64].strip()
    if not clean_query:
        return []
    return await tmdb_client.search(query=clean_query, media_type=type)

@router.get("/details/{tmdb_id}")
async def get_tmdb_details(
    tmdb_id: int,
    type: str = Query("movie", pattern="^(movie|series|tv)$")
):
    """
    Returns full TMDB metadata + calculated OMDb critic rating.
    """
    details = await tmdb_client.get_details(tmdb_id=tmdb_id, media_type=type)
    if not details:
        raise HTTPException(status_code=404, detail="TMDB details not found")

    critic_rating = await omdb_client.get_ratings(
        imdb_id=details.get("imdb_id"),
        title=details.get("title"),
        year=details.get("release_year")
    )
    details["critic_rating"] = critic_rating
    return details

import time
from fastapi import APIRouter, Depends, HTTPException, Query, Request, File, UploadFile, Form, UploadFile, File
from ..metadata.parser import parse_video_filename, find_matching_catalog_media

_catalog_cache = {
    "media_items": [],
    "episodes": [],
    "media_streams": [],
    "last_fetched": 0.0
}
CATALOG_CACHE_TTL = 30.0

# In-memory LRU search query cache: (catalog_version, normalized_query, type, genre, limit) -> list of MediaItem dicts
_search_query_cache: dict[tuple[int, str, str, str, int], list] = {}
_MAX_SEARCH_CACHE_SIZE = 1000

SEARCH_STOPWORDS = {
    "all", "the", "and", "for", "with", "from", "this", "that", "have",
    "into", "when", "who", "what", "where", "why", "how", "are", "was",
    "were", "been", "being", "its", "his", "her", "their", "our", "your"
}

def sanitize_search_query(text: str) -> str:
    if not text:
        return ""
    # Cap length at 64 chars
    text = text[:64]
    # Strip control characters
    text = re.sub(r'[\x00-\x1f\x7f]', '', text)
    # Strip HTML tags
    text = re.sub(r'<[^>]*>', ' ', text)
    # Strip SQL/PostgREST/regex operators (; ' " \ % * ( ) [ ] < >)
    text = re.sub(r'[<>[\](),*\\;"\'%_]', ' ', text)
    # Normalize whitespace and lower
    return re.sub(r'\s+', ' ', text).strip().lower()

def _clean_search_tokens(text: str) -> str:
    return sanitize_search_query(text)

def invalidate_catalog_cache():
    global _catalog_cache, _search_query_cache
    _catalog_cache["last_fetched"] = 0.0
    _search_query_cache.clear()

async def get_cached_catalog_data(force: bool = False):
    global _catalog_cache
    now = time.time()
    if not force and (now - _catalog_cache["last_fetched"] < CATALOG_CACHE_TTL):
        return _catalog_cache["media_items"], _catalog_cache["episodes"], _catalog_cache.get("media_streams", [])

    try:
        media_items = await db.select("media_items", {})
        episodes = await db.select("episodes", {})
        media_streams = await db.select("media_streams", {})
        _catalog_cache["media_items"] = media_items or []
        _catalog_cache["episodes"] = episodes or []
        _catalog_cache["media_streams"] = media_streams or []
        _catalog_cache["last_fetched"] = now
    except Exception as e:
        logger.warning(f"Failed to refresh catalog cache from Supabase: {e}")
    return _catalog_cache["media_items"], _catalog_cache["episodes"], _catalog_cache.get("media_streams", [])

@router.get("/search-catalog")
async def search_catalog(
    q: Optional[str] = Query(None, description="Search query string"),
    limit: int = Query(24, ge=1, le=100, description="Max results to return"),
    type: Optional[str] = Query(None, description="Filter by type (movie|series)"),
    genre: Optional[str] = Query(None, description="Filter by genre")
):
    """
    Ultra-fast in-memory scored catalog search with sub-millisecond LRU cache,
    returning published media items sorted by relevance with strict word-boundary matching.
    """
    version = get_catalog_version()
    raw_query = q or ""
    q_norm = sanitize_search_query(raw_query)
    type_norm = (type or "").strip().lower()
    genre_norm = (genre or "").strip().lower()

    cache_key = (version, q_norm, type_norm, genre_norm, limit)
    if cache_key in _search_query_cache:
        results = _search_query_cache[cache_key]
        return Response(
            content=json.dumps(results),
            media_type="application/json",
            headers={
                "Cache-Control": "public, max-age=15, stale-while-revalidate=60",
                "X-Search-Source": "memory-lru"
            }
        )

    media_items, _, _ = await get_cached_catalog_data()
    published_items = [m for m in media_items if m.get("is_published", False)]

    # Apply type or genre filters if requested
    if type_norm:
        published_items = [m for m in published_items if str(m.get("type", "")).lower() == type_norm]
    if genre_norm and genre_norm != "all":
        published_items = [
            m for m in published_items
            if any(genre_norm in str(g).lower() for g in (m.get("genres") or []))
        ]

    if not q_norm:
        # Default top recommended: is_featured_billboard DESC, views_count DESC, created_at DESC
        sorted_items = sorted(
            published_items,
            key=lambda m: (
                1 if m.get("is_featured_billboard") else 0,
                m.get("billboard_priority") or 0,
                m.get("views_count") or 0,
                m.get("created_at") or ""
            ),
            reverse=True
        )[:limit]
        results = sorted_items
    else:
        q_tokens = [tok for tok in q_norm.split() if tok]
        non_stop_tokens = [tok for tok in q_tokens if tok not in SEARCH_STOPWORDS]
        tokens_to_verify = non_stop_tokens if non_stop_tokens else q_tokens

        scored_items = []

        for item in published_items:
            title = (item.get("title") or "").strip()
            title_lower = title.lower()
            genres_lower = [str(g).strip().lower() for g in (item.get("genres") or [])]
            release_year_str = str(item.get("release_year") or "").strip()
            synopsis_lower = (item.get("synopsis") or "").strip().lower()

            score = 0.0

            # 1. Exact title match (title_lower == q): +100
            if title_lower == q_norm:
                score += 100.0
            # 2. Title starts with q (title_lower.startswith(q)): +85
            elif title_lower.startswith(q_norm):
                score += 85.0
            # 3. Word-Prefix in Title: Every token in q_tokens matches start of a word in title_lower: +70
            elif q_tokens and all(bool(re.search(rf"\b{re.escape(tok)}", title_lower)) for tok in q_tokens):
                score += 70.0
            # 4. Substring in Title (only if len(q) >= 3 and q not in STOPWORDS and q in title_lower): +45
            elif len(q_norm) >= 3 and (q_norm not in SEARCH_STOPWORDS) and (q_norm in title_lower):
                score += 45.0

            # Genre, Type & Release Year Matching:
            # Any genre starts with q (or matches word boundary \b) or str(release_year) == q: +35
            genre_matched = any(g.startswith(q_norm) or bool(re.search(rf"\b{re.escape(q_norm)}", g)) for g in genres_lower)
            year_matched = bool(release_year_str and release_year_str == q_norm)
            if genre_matched or year_matched:
                score += 35.0

            # Synopsis Matching (Strict Whole-Word Only for Non-Stopwords >= 4 Chars):
            # Only check synopsis if len(q) >= 4 and q not in STOPWORDS, and require an exact whole-word boundary match: +15
            if len(q_norm) >= 4 and (q_norm not in SEARCH_STOPWORDS) and bool(re.search(rf"\b{re.escape(q_norm)}\b", synopsis_lower)):
                score += 15.0

            # Multi-word requirement:
            # If q has multiple words, require every non-stopword token to match at least Title, Genre, or Year
            if len(q_tokens) > 1 and score > 0:
                all_match = all(
                    bool(re.search(rf"\b{re.escape(tok)}", title_lower)) or
                    any(g.startswith(tok) or bool(re.search(rf"\b{re.escape(tok)}", g)) for g in genres_lower) or
                    (release_year_str and release_year_str == tok)
                    for tok in tokens_to_verify
                )
                if not all_match:
                    score = 0.0

            if score > 0:
                scored_items.append((score, item.get("views_count") or 0, item))

        scored_items.sort(key=lambda x: (x[0], x[1]), reverse=True)
        results = [x[2] for x in scored_items[:limit]]

    # Manage cache size
    if len(_search_query_cache) >= _MAX_SEARCH_CACHE_SIZE:
        oldest_keys = list(_search_query_cache.keys())[:200]
        for k in oldest_keys:
            _search_query_cache.pop(k, None)

    _search_query_cache[cache_key] = results

    return Response(
        content=json.dumps(results),
        media_type="application/json",
        headers={
            "Cache-Control": "public, max-age=15, stale-while-revalidate=60",
            "X-Search-Source": "memory-computed"
        }
    )

def match_catalog_in_memory(parsed: Dict[str, Any], media_items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not media_items:
        return None

    # 1. Match by explicit TMDB ID if present
    tmdb_id = parsed.get("tmdb_id")
    if tmdb_id:
        for item in media_items:
            if item.get("tmdb_id") == tmdb_id:
                return item

    title = (parsed.get("clean_title") or "").strip()
    if not title:
        return None

    title_lower = title.lower()
    parsed_norm = re.sub(r'[^a-z0-9]', '', title_lower)
    parsed_year = parsed.get("year")

    # 2. Match by alphanumeric stripped comparison (ignores hyphens, colons, punctuation, casing, spacing)
    # e.g. 'Spider Man Brand New Day' -> 'spidermanbrandnewday' matches 'Spider-Man: Brand New Day' -> 'spidermanbrandnewday'
    if parsed_norm:
        alpha_matches = []
        for it in media_items:
            it_title = it.get("title") or ""
            it_norm = re.sub(r'[^a-z0-9]', '', it_title.lower())
            it_slug = (it.get("slug") or "").lower().replace("-", "")

            # Exact alphanumeric match
            if it_norm == parsed_norm:
                alpha_matches.append(it)
            # Slug starts with or contains parsed_norm
            elif it_slug and (it_slug == parsed_norm or it_slug.startswith(parsed_norm) or parsed_norm.startswith(it_slug)):
                alpha_matches.append(it)
            # Substring match if both strings are sufficiently long (>= 6 chars)
            elif len(parsed_norm) >= 6 and len(it_norm) >= 6 and (parsed_norm in it_norm or it_norm in parsed_norm):
                alpha_matches.append(it)

        if len(alpha_matches) == 1:
            return alpha_matches[0]
        elif len(alpha_matches) > 1:
            if parsed_year:
                for it in alpha_matches:
                    if it.get("release_year") == parsed_year:
                        return it
            return alpha_matches[0]

    # 3. Exact case-insensitive match fallback
    for it in media_items:
        if (it.get("title") or "").strip().lower() == title_lower:
            return it

    return None

def find_episode_in_memory(media_id: str, s_num: int, ep_num: int, episodes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for ep in episodes:
        try:
            ep_s = int(ep.get("season_number") or 0)
            ep_e = int(ep.get("episode_number") or 0)
            if ep.get("media_id") == media_id and ep_s == int(s_num) and ep_e == int(ep_num):
                return ep
        except (ValueError, TypeError):
            continue
    return None

@router.get("/incoming-files", dependencies=[Depends(require_admin_secret)])
async def list_incoming_files():
    """
    Lists all video files inside /media/incoming with intelligent filename parsing and catalog match status.
    Uses 1 bulk cached Supabase query (30s TTL) and matches filenames in memory to eliminate N+1 database queries.
    """
    incoming = Path(settings.MEDIA_INCOMING_DIR)
    if not incoming.exists():
        return []

    media_items, episodes, media_streams = await get_cached_catalog_data()

    files = []
    covered_target_ids = set()

    for f in sorted(incoming.rglob("*")):
        if f.is_symlink():
            continue
        try:
            rel = f.relative_to(incoming)
            if any(part.startswith(".") for part in rel.parts):
                continue
        except Exception:
            continue
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            parsed = parse_video_filename(f.name)
            matched = match_catalog_in_memory(parsed, media_items)
            
            is_published = False
            is_ready = False
            published_title = None
            existing_audio_tracks = None
            existing_subtitle_tracks = None

            if matched:
                target_stream_id = None
                has_master = False
                if matched.get("type") == "series":
                    s_num = parsed.get("season_number", 1)
                    ep_num = parsed.get("episode_number", 1)
                    ep_row = find_episode_in_memory(matched["id"], s_num, ep_num, episodes)
                    if ep_row:
                        ep_lib = Path(settings.MEDIA_LIBRARY_DIR) / ep_row["id"]
                        has_master = (ep_lib / "master.m3u8").exists()
                        published_title = f"{matched['title']} S{s_num}:E{ep_num} - {ep_row.get('title') or 'Episode ' + str(ep_num)}"
                        target_stream_id = ep_row["id"]
                else:
                    m_lib = Path(settings.MEDIA_LIBRARY_DIR) / matched["id"]
                    has_master = (m_lib / "master.m3u8").exists()
                    published_title = matched.get("title")
                    target_stream_id = matched["id"]

                processing_status = None
                if target_stream_id:
                    covered_target_ids.add(target_stream_id)
                    st = next((s for s in media_streams if s.get("episode_id") == target_stream_id or s.get("media_id") == target_stream_id), None)
                    if st:
                        processing_status = st.get("processing_status")
                        existing_audio_tracks = [
                            {
                                **a,
                                "label": sanitize_track_label(a.get("label"), fallback=f"Audio Track {(a.get('index') or 0) + 1}"),
                                "is_default": bool(a.get("is_default", False))
                            }
                            for a in (st.get("audio_tracks") or [])
                        ]
                        if existing_audio_tracks and not any(a.get("is_default") for a in existing_audio_tracks):
                            existing_audio_tracks[0]["is_default"] = True
                        existing_subtitle_tracks = [
                            {
                                **s,
                                "label": sanitize_track_label(s.get("label"), fallback=f"Subtitle {(s.get('index') or 0) + 1}")
                            }
                            for s in (st.get("subtitle_tracks") or [])
                        ]

                # Failed files or missing master must never be marked is_published = True
                if has_master and processing_status != "failed":
                    is_ready = True
                    is_published = True
                else:
                    is_ready = False
                    is_published = False

            files.append({
                "filename": f.name,
                "relative_path": str(f.relative_to(incoming)),
                "size_bytes": f.stat().st_size,
                "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
                "parsed": parsed,
                "is_published": is_published,
                "is_ready": is_ready,
                "is_raw_source_removed": False,
                "processing_status": processing_status if matched else None,
                "published_title": published_title,
                "existing_audio_tracks": existing_audio_tracks,
                "existing_subtitle_tracks": existing_subtitle_tracks,
                "matched_media": {
                    "id": matched["id"],
                    "title": matched["title"],
                    "type": matched.get("type", "movie"),
                    "tmdb_id": matched.get("tmdb_id")
                } if matched else None
            })

    # Include published streams from /media/library/ when raw incoming files have been deleted
    lib_root = Path(settings.MEDIA_LIBRARY_DIR).resolve()
    for st in (media_streams or []):
        target_id = st.get("episode_id") or st.get("media_id")
        if not target_id or target_id in covered_target_ids:
            continue

        if st.get("processing_status") == "failed":
            continue

        lib_path = (lib_root / str(target_id)).resolve()
        if not lib_path.exists() or not (lib_path / "master.m3u8").exists():
            continue
        covered_target_ids.add(target_id)

        dir_size = 0
        for p in lib_path.rglob("*"):
            if p.is_file():
                try:
                    dir_size += p.stat().st_size
                except Exception:
                    pass

        matched_m = next((m for m in (media_items or []) if m.get("id") == st.get("media_id")), None)
        title = matched_m.get("title", "Library Stream") if matched_m else "Library Stream"
        ep_row = next((e for e in (episodes or []) if e.get("id") == target_id), None) if st.get("episode_id") else None

        if ep_row:
            s_num = ep_row.get("season_number", 1)
            e_num = ep_row.get("episode_number", 1)
            display_title = f"{title} S{s_num}:E{e_num} - {ep_row.get('title') or f'Episode {e_num}'}"
            parsed_info = {
                "clean_title": title,
                "season_number": s_num,
                "episode_number": e_num,
                "year": matched_m.get("release_year") if matched_m else None
            }
        else:
            display_title = title
            parsed_info = {
                "clean_title": title,
                "year": matched_m.get("release_year") if matched_m else None
            }

        clean_audio = [
            {
                **a,
                "label": sanitize_track_label(a.get("label"), fallback=f"Audio Track {(a.get('index') or 0) + 1}"),
                "is_default": bool(a.get("is_default", False))
            }
            for a in (st.get("audio_tracks") or [])
        ]
        if clean_audio and not any(a.get("is_default") for a in clean_audio):
            clean_audio[0]["is_default"] = True
        clean_subs = [
            {
                **s,
                "label": sanitize_track_label(s.get("label"), fallback=f"Subtitle {(s.get('index') or 0) + 1}")
            }
            for s in (st.get("subtitle_tracks") or [])
        ]

        files.append({
            "filename": f"{display_title} (HLS Stream)",
            "relative_path": f"[library]/{target_id}",
            "size_bytes": dir_size,
            "size_mb": round(dir_size / (1024 * 1024), 1),
            "parsed": parsed_info,
            "is_published": True,
            "is_ready": True,
            "is_raw_source_removed": True,
            "target_id": str(target_id),
            "media_id": st.get("media_id"),
            "episode_id": st.get("episode_id"),
            "published_title": display_title,
            "existing_audio_tracks": clean_audio,
            "existing_subtitle_tracks": clean_subs,
            "matched_media": {
                "id": matched_m["id"] if matched_m else str(target_id),
                "title": title,
                "type": matched_m.get("type", "movie") if matched_m else ("series" if st.get("episode_id") else "movie"),
                "tmdb_id": matched_m.get("tmdb_id") if matched_m else None
            } if matched_m else None
        })

    return files


def _convert_srt_to_vtt(text: str) -> str:
    # 1. Normalize line breaks and strip BOM
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    # 2. Fix SRT timestamp commas to WebVTT dots (00:01:20,500 --> 00:01:23,200 to 00:01:20.500 --> 00:01:23.200)
    text = re.sub(r'(\d{2}:\d{2}:\d{2}),(\d{3})', r'\1.\2', text)
    # Fix 2-part timestamps if present (01:20,500 -> 00:01:20.500)
    text = re.sub(r'(?<=\s)(\d{2}:\d{2}),(\d{3})(?=\s)', r'00:\1.\2', text)
    # 3. Strip unsafe HTML tags
    text = re.sub(r'<(?:script|iframe|object|embed)[^>]*>.*?</(?:script|iframe|object|embed)>', '', text, flags=re.IGNORECASE | re.DOTALL)
    # 4. Ensure starts with WEBVTT
    lines = text.strip().split("\n")
    if not (lines and lines[0].strip().startswith("WEBVTT")):
        return "WEBVTT\n\n" + text.strip() + "\n"
    return text.strip() + "\n"


@router.get("/incoming-files/{filename:path}/probe", dependencies=[Depends(require_admin_secret)])
async def probe_incoming_file_stream(filename: str):
    """
    Probes real embedded audio and text subtitle streams of an incoming video file.
    Pre-fills previously saved custom labels if already published in library (re-ingest).
    """
    incoming = Path(settings.MEDIA_INCOMING_DIR)
    target_file = (incoming / filename).resolve()
    if not target_file.is_relative_to(incoming.resolve()) or not target_file.is_file():
        raise HTTPException(status_code=404, detail=f"File '{filename}' not found in incoming directory")

    probe = probe_file(str(target_file))
    if not probe:
        raise HTTPException(status_code=422, detail=f"Could not probe media streams from '{filename}'")

    parsed = parse_video_filename(target_file.name)
    media_items, episodes, media_streams = await get_cached_catalog_data(force=True)

    is_reingest = False
    existing_stream = None
    display_title = parsed.get("clean_title") or target_file.stem
    matched_m = match_catalog_in_memory(parsed, media_items)

    matched_ep = None
    if parsed.get("season_number") is not None and parsed.get("episode_number") is not None:
        matched_ep = find_episode_in_memory(
            matched_m["id"] if matched_m else "",
            parsed["season_number"],
            parsed["episode_number"],
            episodes
        )
        if not matched_ep:
            for ep in episodes:
                if (ep.get("season_number") == parsed.get("season_number") and 
                    ep.get("episode_number") == parsed.get("episode_number")):
                    matched_ep = ep
                    break

        if matched_ep:
            ep_num = matched_ep.get("episode_number")
            season_num = matched_ep.get("season_number", 1)
            ep_title = matched_ep.get("title")
            parent_show = next((m for m in media_items if m["id"] == matched_ep.get("media_id")), None)
            show_name = parent_show["title"] if parent_show else (parsed.get("clean_title") or "Series")
            if ep_title:
                display_title = f"{show_name} — S{season_num}:E{ep_num} \"{ep_title}\""
            else:
                display_title = f"{show_name} — S{season_num}:E{ep_num}"
            for st in media_streams:
                if st.get("episode_id") == matched_ep["id"]:
                    is_reingest = True
                    existing_stream = st
                    break
    elif matched_m:
        display_title = f"{matched_m.get('title')} ({matched_m.get('release_year', '')})".strip()
        for st in media_streams:
            if st.get("media_id") == matched_m["id"]:
                is_reingest = True
                existing_stream = st
                break

    existing_audio = existing_stream.get("audio_tracks") or [] if existing_stream else []
    existing_subs = existing_stream.get("subtitle_tracks") or [] if existing_stream else []

    target_id = matched_ep["id"] if matched_ep else (matched_m["id"] if matched_m else None)

    # 1. Video information
    video_info = {
        "width": probe.width,
        "height": probe.height,
        "codec": probe.video_codec
    }

    # 2. Embedded Audio
    embedded_audio = []
    first_default_assigned = False
    for a_idx, s in enumerate(probe.audio_streams):
        raw_idx = s.get("index", a_idx + 1)
        norm_label, norm_code = detect_stream_language(s, fallback_label=f"Audio Track {a_idx + 1}", default_code="und")
        tags = s.get("tags") or {}
        raw_title = tags.get("title") or tags.get("handler_name") or ""
        clean_lbl = sanitize_track_label(raw_title, fallback=norm_label)

        is_def = False
        if existing_audio:
            match_prev = next((ea for ea in existing_audio if ea.get("raw_stream_idx") == raw_idx or ea.get("index") == a_idx), None)
            if match_prev:
                if match_prev.get("label"):
                    clean_lbl = sanitize_track_label(match_prev["label"], fallback=clean_lbl)
                if match_prev.get("lang"):
                    norm_code = match_prev["lang"]
                is_def = bool(match_prev.get("is_default", False))
        elif a_idx == 0:
            is_def = True

        if is_def and not first_default_assigned:
            first_default_assigned = True
        elif is_def and first_default_assigned:
            is_def = False

        embedded_audio.append({
            "raw_stream_idx": raw_idx,
            "codec": s.get("codec_name", "aac"),
            "channels": int(s.get("channels", 2)),
            "lang": norm_code,
            "raw_title": raw_title,
            "clean_label": clean_lbl,
            "included": True,
            "is_default": is_def
        })

    if embedded_audio and not any(a["is_default"] for a in embedded_audio):
        embedded_audio[0]["is_default"] = True

    # 3. Subtitles (Container Streams + Staged External + Saved Library Subtitles)
    text_subs = [s for s in probe.subtitle_streams if s.get("codec_name", "").lower() in TEXT_SUB_CODECS]

    # Always build embedded_subtitles from ffprobe (for container backup / restore button)
    embedded_subtitles = []
    for s_idx, s in enumerate(text_subs):
        raw_idx = s.get("index", s_idx)
        norm_label, norm_code = detect_stream_language(s, fallback_label=f"Subtitle {s_idx + 1}", default_code="und")
        tags = s.get("tags") or {}
        raw_title = tags.get("title") or tags.get("handler_name") or ""
        smart_lbl = build_smart_track_label(raw_title, norm_label)
        codec_name = (s.get("codec_name") or "subrip").upper()
        embedded_subtitles.append({
            "raw_stream_idx": raw_idx,
            "codec": s.get("codec_name", "subrip"),
            "lang": norm_code if norm_code != "und" else "en",
            "raw_title": raw_title,
            "clean_label": smart_lbl,
            "included": True
        })

    # Staged External Subtitles (.custom_subs and companion files in incoming)
    staged_external_subtitles = []
    safe_video_stem = re.sub(r"[^\w\-.]", "_", target_file.stem)
    custom_subs_dir = incoming / ".custom_subs" / safe_video_stem
    if not custom_subs_dir.exists():
        custom_subs_dir = incoming / ".custom_subs" / target_file.stem

    if custom_subs_dir.exists():
        for meta_file in sorted(custom_subs_dir.glob("*.meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                vtt_file = custom_subs_dir / f"{meta['ext_id']}.vtt"
                if vtt_file.exists() and vtt_file.stat().st_size > 0:
                    staged_external_subtitles.append({
                        "id": meta["ext_id"],
                        "ext_id": meta["ext_id"],
                        "raw_stream_idx": -1,
                        "filename": meta.get("filename") or vtt_file.name,
                        "path": str(vtt_file),
                        "lang": meta.get("lang", "en"),
                        "label": meta.get("label") or f"External ({meta.get('lang', 'EN').upper()})",
                        "source_type": "external",
                        "source_badge": "EXTERNAL .SRT",
                        "included": True
                    })
            except Exception:
                pass

    # Discover companion subtitles next to video in incoming folder
    try:
        in_stem = target_file.stem
        for ext in (".srt", ".vtt", ".ass"):
            for comp_f in target_file.parent.glob(f"*{ext}"):
                if comp_f.name.startswith(in_stem) and not any(es["filename"] == comp_f.name for es in staged_external_subtitles):
                    comp_suffix = comp_f.stem.replace(in_stem, "").strip("._- ")
                    comp_code = comp_suffix.lower()[:3] if comp_suffix else "en"
                    staged_external_subtitles.append({
                        "id": f"comp_{len(staged_external_subtitles)}",
                        "ext_id": f"comp_{len(staged_external_subtitles)}",
                        "raw_stream_idx": -1,
                        "filename": comp_f.name,
                        "path": str(comp_f),
                        "lang": comp_code,
                        "label": f"External ({comp_code.upper()})",
                        "source_type": "external",
                        "source_badge": "EXTERNAL .SRT",
                        "included": True
                    })
    except Exception as e:
        logger.warning(f"Error checking companion subtitles for probe: {e}")

    # Build active subtitles list
    active_subtitles = []

    if is_reingest and existing_stream:
        # Case A: File is already published. media_streams.subtitle_tracks is the single source of truth!
        library_dir = Path(settings.MEDIA_LIBRARY_DIR) / target_id if target_id else None
        subs_dir = library_dir / "subs" if library_dir else None

        for idx, t in enumerate(existing_subs):
            vtt_p = t.get("vtt_path") or t.get("url") or ""
            vtt_fname = Path(vtt_p).name
            vtt_file = subs_dir / vtt_fname if (subs_dir and vtt_fname) else None
            # Only include if file actually exists in library or vtt_path is present
            if vtt_file and not (vtt_file.is_file() and vtt_file.stat().st_size > 0):
                continue

            raw_idx = t.get("raw_stream_idx")
            if raw_idx is not None:
                raw_idx = int(raw_idx)
            else:
                # Legacy fallback
                is_container = t.get("source_type") == "container"
                if not is_container and not t.get("is_external"):
                    match_cs = next((cs for cs in embedded_subtitles if cs["clean_label"].lower() == (t.get("label") or "").strip().lower()), None)
                    if match_cs:
                        raw_idx = match_cs["raw_stream_idx"]
                    else:
                        raw_idx = -1
                else:
                    raw_idx = -1

            source_t = "container" if raw_idx >= 0 else "saved"
            badge = f"STREAM #{raw_idx} (SUBRIP)" if raw_idx >= 0 else "SAVED IN LIBRARY"

            active_subtitles.append({
                "id": t.get("id") or f"sub_{idx}",
                "raw_stream_idx": raw_idx,
                "codec": t.get("codec") or "webvtt",
                "lang": t.get("lang") or "en",
                "label": t.get("label") or f"Subtitle {idx + 1}",
                "vtt_path": t.get("vtt_path") or f"subs/{vtt_fname}",
                "path": str(vtt_file) if vtt_file else "",
                "filename": t.get("original_filename") or t.get("vtt_filename") or vtt_fname,
                "source_type": source_t,
                "source_badge": badge,
                "included": True
            })

        # Append any newly staged external subtitles that haven't been ingested into media_streams yet
        for ext_sub in staged_external_subtitles:
            already_in_library = any(
                asub.get("filename") == ext_sub.get("filename") or
                asub.get("label") == ext_sub.get("label")
                for asub in active_subtitles
            )
            if not already_in_library:
                active_subtitles.append(ext_sub)

    else:
        # Case B: File is NOT published yet. Populate from embedded container streams + staged external
        for s in embedded_subtitles:
            active_subtitles.append({
                "id": f"stream_{s['raw_stream_idx']}",
                "raw_stream_idx": s["raw_stream_idx"],
                "codec": s.get("codec", "subrip"),
                "lang": s.get("lang", "en"),
                "raw_title": s.get("raw_title", ""),
                "label": s.get("clean_label", "English"),
                "source_type": "container",
                "source_badge": f"STREAM #{s['raw_stream_idx']} ({(s.get('codec') or 'SUBRIP').upper()})",
                "included": True
            })
        active_subtitles.extend(staged_external_subtitles)

    return {
        "filename": filename,
        "display_title": display_title,
        "is_reingest": is_reingest,
        "video_info": video_info,
        "embedded_audio": embedded_audio,
        "embedded_subtitles": embedded_subtitles,
        "external_subtitles": staged_external_subtitles,
        "subtitles": active_subtitles
    }


@router.post("/auto-ingest", dependencies=[Depends(require_admin_secret)])
async def auto_ingest_file(payload: Dict[str, Any]):
    """
    Automatically parses filename, matches with catalog or TMDB, and triggers ingestion.
    """
    filename = payload.get("filename")
    if not filename:
        raise HTTPException(status_code=400, detail="Filename required")

    incoming = Path(settings.MEDIA_INCOMING_DIR).resolve()
    target_file = (incoming / filename).resolve()
    if not target_file.is_relative_to(incoming):
        raise HTTPException(status_code=403, detail="Invalid file path")
    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail=f"File {filename} not found in incoming directory")

    parsed = parse_video_filename(filename)
    matched = await find_matching_catalog_media(parsed)

    target_media_id = payload.get("media_id") or (matched["id"] if matched else None)
    target_tmdb_id = payload.get("tmdb_id") or (matched.get("tmdb_id") if matched else parsed.get("tmdb_id"))
    target_type = payload.get("media_type") or (matched.get("type") if matched else parsed.get("media_type", "movie"))

    if not target_media_id and not target_tmdb_id:
        # Search TMDB for best match with multiple fallbacks
        try:
            search_res = await tmdb_client.search(parsed["clean_title"], parsed["media_type"])
            if not search_res:
                search_res = await tmdb_client.search(parsed["clean_title"], "multi")
            if not search_res and ":" in parsed.get("clean_title", ""):
                search_res = await tmdb_client.search(parsed["clean_title"].split(":")[0].strip(), parsed["media_type"])
            if not search_res and "-" in parsed.get("clean_title", ""):
                search_res = await tmdb_client.search(parsed["clean_title"].split("-")[0].strip(), parsed["media_type"])
            if search_res and len(search_res) > 0:
                top_match = search_res[0]
                target_tmdb_id = top_match["tmdb_id"]
                target_type = top_match["type"]
        except Exception as e:
            logger.warning(f"TMDB search failed during auto-ingest: {e}")

    if not target_media_id and not target_tmdb_id:
        raise HTTPException(status_code=422, detail=f"Could not automatically match metadata for '{filename}'. Please specify TMDB ID or catalog title.")

    audio_langs = payload.get("audio_languages")
    subtitle_langs = payload.get("subtitle_languages")
    audio_tracks_config = payload.get("audio_tracks_config")
    subtitle_tracks_config = payload.get("subtitle_tracks_config")
    force_video_reencode = bool(payload.get("force_video_reencode", False))

    res = await ingester.ingest_media(
        file_path=str(target_file),
        tmdb_id=target_tmdb_id,
        media_type=target_type,
        season_number=parsed.get("season_number"),
        episode_number=parsed.get("episode_number"),
        media_id=target_media_id,
        audio_languages=audio_langs,
        subtitle_languages=subtitle_langs,
        audio_tracks_config=audio_tracks_config,
        subtitle_tracks_config=subtitle_tracks_config,
        force_video_reencode=force_video_reencode
    )
    invalidate_catalog_cache()
    return {"status": "success", "result": res, "parsed": parsed}

class BulkIngestItem(BaseModel):
    filename: str
    audio_languages: Optional[List[str]] = None
    subtitle_languages: Optional[List[str]] = None
    audio_tracks_config: Optional[List[Dict[str, Any]]] = None
    subtitle_tracks_config: Optional[List[Dict[str, Any]]] = None

class BulkIngestPayload(BaseModel):
    items: Optional[List[BulkIngestItem]] = None
    filenames: Optional[List[str]] = None
    audio_languages: Optional[List[str]] = None
    subtitle_languages: Optional[List[str]] = None
    audio_tracks_config: Optional[List[Dict[str, Any]]] = None
    subtitle_tracks_config: Optional[List[Dict[str, Any]]] = None

@router.post("/bulk-ingest", dependencies=[Depends(require_admin_secret)])
async def bulk_ingest_files(payload: BulkIngestPayload):
    """
    Ingests and queues multiple incoming video files with batch or per-file audio/subtitle configurations.
    Fault-tolerant: failures in one item do not stop subsequent items in the batch.
    """
    results = []
    if payload.items and len(payload.items) > 0:
        for item in payload.items:
            try:
                res = await auto_ingest_file({
                    "filename": item.filename,
                    "audio_languages": item.audio_languages,
                    "subtitle_languages": item.subtitle_languages,
                    "audio_tracks_config": item.audio_tracks_config,
                    "subtitle_tracks_config": item.subtitle_tracks_config
                })
                results.append({"filename": item.filename, "status": "queued", "result": res})
            except Exception as e:
                logger.warning(f"Error during bulk ingest of '{item.filename}': {e}")
                results.append({"filename": item.filename, "status": "error", "error": str(e)})
        return {"total": len(payload.items), "results": results}
    elif payload.filenames and len(payload.filenames) > 0:
        for fn in payload.filenames:
            try:
                res = await auto_ingest_file({
                    "filename": fn,
                    "audio_languages": payload.audio_languages,
                    "subtitle_languages": payload.subtitle_languages,
                    "audio_tracks_config": payload.audio_tracks_config,
                    "subtitle_tracks_config": payload.subtitle_tracks_config
                })
                results.append({"filename": fn, "status": "queued", "result": res})
            except Exception as e:
                logger.warning(f"Error during bulk ingest of '{fn}': {e}")
                results.append({"filename": fn, "status": "error", "error": str(e)})
        return {"total": len(payload.filenames), "results": results}

    return {"total": 0, "results": []}

class BulkDeleteItem(BaseModel):
    filename: str
    is_raw_source_removed: Optional[bool] = False
    media_id: Optional[str] = None
    episode_id: Optional[str] = None
    target_id: Optional[str] = None

class BulkDeletePayload(BaseModel):
    items: List[BulkDeleteItem]

@router.post("/bulk-delete", dependencies=[Depends(require_admin_secret)])
async def bulk_delete_media_items(payload: BulkDeletePayload):
    """
    Bulk deletes raw incoming files, published streams from /media/library/,
    and associated database records across movies and series episodes.
    """
    incoming = Path(settings.MEDIA_INCOMING_DIR).resolve()
    freed_bytes = 0
    deleted_count = 0

    media_items, episodes_list, _ = await get_cached_catalog_data()

    for item in payload.items:
        # 1. Delete Raw File if present in /media/incoming
        if not item.is_raw_source_removed and item.filename:
            raw_file = (incoming / item.filename).resolve()
            if raw_file.is_relative_to(incoming) and raw_file.exists() and raw_file.is_file():
                try:
                    freed_bytes += raw_file.stat().st_size
                    raw_file.unlink()
                except Exception as e:
                    logger.warning(f"Failed to unlink raw incoming file {raw_file}: {e}")

            # Clean custom subs directory if exists
            safe_stem = re.sub(r"[^\w\-.]", "_", Path(item.filename).stem)
            subs_dir = (incoming / ".custom_subs" / safe_stem).resolve()
            if subs_dir.is_relative_to(incoming) and subs_dir.exists() and subs_dir.is_dir():
                try:
                    shutil.rmtree(subs_dir, ignore_errors=True)
                except Exception:
                    pass

        # 2. Determine if matched in library/database and delete DB records & HLS stream
        ep_id = item.episode_id
        m_id = item.media_id
        target_id = item.target_id

        if not ep_id and not m_id:
            if target_id:
                ep = next((e for e in episodes_list if e.get("id") == target_id), None)
                if ep:
                    ep_id = target_id
                else:
                    m = next((m for m in media_items if m.get("id") == target_id), None)
                    if m:
                        m_id = target_id
            elif item.filename:
                parsed = parse_video_filename(item.filename)
                matched = match_catalog_in_memory(parsed, media_items)
                if matched:
                    if matched.get("type") == "series":
                        s_num = parsed.get("season_number", 1)
                        ep_num = parsed.get("episode_number", 1)
                        ep_row = find_episode_in_memory(matched["id"], s_num, ep_num, episodes_list)
                        if ep_row:
                            ep_id = ep_row["id"]
                    else:
                        m_id = matched["id"]

        if ep_id:
            try:
                res = await delete_episode(ep_id)
                freed_bytes += int((res.get("freed_mb") or 0) * 1048576)
                deleted_count += 1
            except Exception as e:
                logger.warning(f"Error deleting episode {ep_id}: {e}")
        elif m_id:
            try:
                res = await delete_media(m_id)
                freed_bytes += int((res.get("freed_mb") or 0) * 1048576)
                deleted_count += 1
            except Exception as e:
                logger.warning(f"Error deleting media {m_id}: {e}")
        else:
            deleted_count += 1

    bump_catalog_version()
    invalidate_catalog_cache()
    return {
        "status": "deleted",
        "deleted_count": deleted_count,
        "freed_mb": round(freed_bytes / (1024 * 1024), 1)
    }

@router.post("/ingest", dependencies=[Depends(require_admin_secret)])
async def trigger_manual_ingest(payload: ManualIngestPayload):
    """
    Manually binds an incoming video file with TMDB metadata and queues processing.
    """
    incoming = Path(settings.MEDIA_INCOMING_DIR)
    target_file = (incoming / payload.filename).resolve()

    if not target_file.is_relative_to(incoming.resolve()):
        raise HTTPException(status_code=403, detail="Invalid file path")

    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail=f"File {payload.filename} does not exist in incoming directory")

    res = await ingester.ingest_media(
        file_path=str(target_file),
        tmdb_id=payload.tmdb_id,
        media_type=payload.media_type,
        season_number=payload.season_number,
        episode_number=payload.episode_number
    )
    return res

@router.patch("/badge/{media_id}", dependencies=[Depends(require_admin_secret)])
async def update_media_badge(media_id: str, payload: UpdateBadgePayload):
    """
    Updates the promotional badge on a media item ('Recently Added', 'New Season', 'Top 10', etc.) with optional expiration.
    """
    badge = payload.badge if payload.badge is not None else payload.badge_type
    if not badge or badge.strip().lower() in ("none", ""):
        update_data = {
            "badge": None,
            "badge_expires_at": None,
            "badge_type": "none"
        }
    else:
        badge = badge.strip()
        expires_at = None
        if payload.badge_expires_at:
            expires_at = payload.badge_expires_at
        elif payload.badge_duration_days is not None:
            if payload.badge_duration_days > 0:
                expires_at = (datetime.now(timezone.utc) + timedelta(days=payload.badge_duration_days)).isoformat()
            else:
                expires_at = None
        else:
            # Default to 10 days if duration was not provided
            expires_at = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()

        legacy_type = "recently_added" if "recent" in badge.lower() else ("new_season" if "season" in badge.lower() else "none")
        update_data = {
            "badge": badge,
            "badge_expires_at": expires_at,
            "badge_type": legacy_type
        }

    res = await db.update("media_items", "id", media_id, update_data)
    bump_catalog_version()
    return {"status": "updated", "item": res, "catalog_version": get_catalog_version()}

@router.get("/catalog-state")
async def get_catalog_state():
    """
    Returns current catalog_version timestamp and lists of all published media IDs and episode IDs
    for real-time client state synchronization without polling overhead.
    """
    media_items, episodes, _ = await get_cached_catalog_data()
    pub_media_set = {m["id"] for m in (media_items or []) if bool(m.get("is_published", False))}
    pub_media_ids = [m["id"] for m in (media_items or []) if m["id"] in pub_media_set]
    pub_ep_ids = [
        e["id"] for e in (episodes or [])
        if bool(e.get("is_published", False)) and e.get("media_id") in pub_media_set
    ]
    return {
        "catalog_version": get_catalog_version(),
        "published_media_ids": pub_media_ids,
        "published_episode_ids": pub_ep_ids
    }

@router.get("/public-showcase")
async def get_public_showcase():
    """
    Lightweight public teaser list returning up to 6 published titles for the commercial landing page.
    Excludes stream URLs, internal paths, and protected content.
    """
    media_items, _, _ = await get_cached_catalog_data()
    published = [m for m in (media_items or []) if bool(m.get("is_published", False))]

    def sort_score(item: dict) -> tuple:
        is_featured = 1 if item.get("is_featured_billboard") else 0
        priority = item.get("billboard_priority") or 0
        views = item.get("views_count") or 0
        return (is_featured, priority, views)

    sorted_items = sorted(published, key=sort_score, reverse=True)
    showcase = []
    for item in sorted_items[:6]:
        showcase.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "poster_url": item.get("poster_url"),
            "backdrop_url": item.get("backdrop_url"),
            "release_year": item.get("release_year"),
            "maturity_rating": item.get("maturity_rating") or "U/A 13+",
            "type": item.get("type") or "movie",
            "genres": item.get("genres") or [],
        })
    return showcase

@router.put("/{media_id}/billboard", dependencies=[Depends(require_admin_secret)])
async def update_billboard_status(media_id: str, payload: BillboardUpdatePayload):
    """
    Sets or toggles the Homepage Billboard status of a movie or series.
    If exclusive is True and is_featured_billboard is True, clears all other billboards.
    If media_id is 'auto' or 'none', clears all pinned billboards.
    """
    if media_id.strip().lower() in ("auto", "none"):
        try:
            active_billboards = await db.select("media_items", {"is_featured_billboard": "is.true"})
            for it in (active_billboards or []):
                await db.update("media_items", "id", it["id"], {"is_featured_billboard": False, "billboard_priority": 0})
        except Exception as e:
            logger.warning(f"Error resetting billboards: {e}")
        bump_catalog_version()
        return {"status": "updated", "mode": "auto", "catalog_version": get_catalog_version()}

    try:
        val_uuid = str(uuid.UUID(media_id.strip()))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid media_id UUID")

    target = await db.select("media_items", {"id": f"eq.{val_uuid}"})
    if not target:
        raise HTTPException(status_code=404, detail="Media item not found in catalog")

    if payload.exclusive and payload.is_featured_billboard:
        # Clear billboard from all other media items
        try:
            active_billboards = await db.select("media_items", {"is_featured_billboard": "is.true"})
            for it in (active_billboards or []):
                if it.get("id") != val_uuid:
                    await db.update("media_items", "id", it["id"], {"is_featured_billboard": False, "billboard_priority": 0})
        except Exception as e:
            logger.warning(f"Error resetting other billboards: {e}")

        updated = await db.update("media_items", "id", val_uuid, {
            "is_featured_billboard": True,
            "billboard_priority": payload.billboard_priority if payload.billboard_priority is not None else 100
        })
    else:
        new_priority = (payload.billboard_priority if payload.billboard_priority is not None else 100) if payload.is_featured_billboard else 0
        updated = await db.update("media_items", "id", val_uuid, {
            "is_featured_billboard": payload.is_featured_billboard,
            "billboard_priority": new_priority
        })

    bump_catalog_version()
    return {
        "status": "updated",
        "media_id": val_uuid,
        "is_featured_billboard": payload.is_featured_billboard,
        "billboard_priority": payload.billboard_priority or (100 if payload.is_featured_billboard else 0),
        "catalog_version": get_catalog_version(),
        "item": updated
    }

@router.put("/{media_id}", dependencies=[Depends(require_admin_secret)])
async def update_media_metadata(media_id: str, payload: MediaMetadataUpdatePayload):
    """
    Updates metadata for a Movie or Series item in media_items, including promotional badges and billboard flags.
    """
    try:
        val_uuid = str(uuid.UUID(media_id.strip()))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid media_id UUID")

    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields provided for update")

    if not data.get("slug"):
        data.pop("slug", None)

    for url_field in ("poster_url", "backdrop_url", "trailer_youtube_key"):
        if url_field in data:
            v = (data[url_field] or "").strip()
            data[url_field] = v if v else None

    if "audio_languages" in data and not data["audio_languages"]:
        data["audio_languages"] = ["English"]

    # Badge handling
    if "badge" in data or "badge_duration_days" in data:
        badge_val = (data.get("badge") or "").strip()
        badge_duration = data.pop("badge_duration_days", None)
        if not badge_val or badge_val.lower() in ("none", ""):
            data["badge"] = None
            data["badge_expires_at"] = None
            data["badge_type"] = "none"
        else:
            data["badge"] = badge_val
            if badge_duration is not None:
                if badge_duration > 0:
                    data["badge_expires_at"] = (datetime.now(timezone.utc) + timedelta(days=badge_duration)).isoformat()
                else:
                    data["badge_expires_at"] = None
            elif not data.get("badge_expires_at"):
                data["badge_expires_at"] = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
            data["badge_type"] = "recently_added" if "recent" in badge_val.lower() else ("new_season" if "season" in badge_val.lower() else "none")

    updated = await db.update("media_items", "id", val_uuid, data)
    bump_catalog_version()
    return {"status": "updated", "media_id": val_uuid, "item": updated, "catalog_version": get_catalog_version()}

@router.delete("/episodes/{episode_id}", dependencies=[Depends(require_admin_secret)])
async def delete_episode(episode_id: str):
    """
    Permanently deletes a single episode from database and /media/library/ disk.
    If 0 episodes remain in the parent series, deletes or unpublishes the parent series.
    """
    try:
        valid_episode_uuid = str(uuid.UUID(episode_id.strip()))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid episode_id UUID")

    rows = await db.select("episodes", {"id": f"eq.{valid_episode_uuid}"})
    if not rows:
        raise HTTPException(status_code=404, detail="Episode not found in database")
    ep = rows[0]
    parent_media_id = ep.get("media_id")

    # 1. Delete HLS Library Folder from Disk
    lib_root = Path(settings.MEDIA_LIBRARY_DIR).resolve()
    ep_dir = (lib_root / valid_episode_uuid).resolve()
    freed_bytes = 0

    if ep_dir.exists() and ep_dir.is_dir():
        if not ep_dir.is_relative_to(lib_root):
            raise HTTPException(status_code=403, detail="Illegal directory traversal attempted")
        for p in ep_dir.rglob("*"):
            if p.is_file():
                try:
                    freed_bytes += p.stat().st_size
                except Exception:
                    pass
        shutil.rmtree(ep_dir, ignore_errors=True)

    # 2. Delete Database Records
    await db.delete("media_streams", "episode_id", valid_episode_uuid)
    await db.delete("watch_progress", "episode_id", valid_episode_uuid)
    await db.delete("watch_history", "episode_id", valid_episode_uuid)
    await db.delete("episodes", "id", valid_episode_uuid)

    # 3. Check Remaining Episodes in Parent Series
    parent_series_deleted = False
    if parent_media_id:
        remaining = await db.select("episodes", {"media_id": f"eq.{parent_media_id}"})
        if not remaining or len(remaining) == 0:
            # 0 episodes remain in series; delete parent series from media_items so an empty series shell never stays on homepage!
            await db.delete("custom_list_items", "media_id", parent_media_id)
            await db.delete("user_likes", "media_id", parent_media_id)
            await db.delete("watch_progress", "media_id", parent_media_id)
            await db.delete("watch_history", "media_id", parent_media_id)
            await db.delete("media_streams", "media_id", parent_media_id)
            await db.delete("seasons", "media_id", parent_media_id)
            await db.delete("media_items", "id", parent_media_id)
            parent_dir = (lib_root / parent_media_id).resolve()
            if parent_dir.exists() and parent_dir.is_dir() and parent_dir.is_relative_to(lib_root):
                shutil.rmtree(parent_dir, ignore_errors=True)
            parent_series_deleted = True

    bump_catalog_version()
    return {
        "status": "deleted",
        "deleted_type": "episode",
        "episode_id": valid_episode_uuid,
        "parent_media_id": parent_media_id,
        "parent_series_deleted": parent_series_deleted,
        "freed_mb": round(freed_bytes / 1048576, 1),
        "catalog_version": get_catalog_version()
    }

@router.delete("/media/{media_id}", dependencies=[Depends(require_admin_secret)])
async def delete_media(media_id: str):
    """
    Permanently deletes an entire Movie or Series (+ all episodes) from DB and /media/library/ disk.
    """
    try:
        valid_media_uuid = str(uuid.UUID(media_id.strip()))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid media_id UUID")

    items = await db.select("media_items", {"id": f"eq.{valid_media_uuid}"})
    if not items:
        raise HTTPException(status_code=404, detail="Media item not found in database")
    media_item = items[0]
    media_type = media_item.get("type", "movie")

    lib_root = Path(settings.MEDIA_LIBRARY_DIR).resolve()
    freed_bytes = 0

    # If series, query all episodes to collect all episode_id UUIDs
    episode_ids = []
    if media_type == "series":
        eps = await db.select("episodes", {"media_id": f"eq.{valid_media_uuid}"})
        episode_ids = [e["id"] for e in (eps or []) if e.get("id")]

    # Delete All HLS Folders from Disk (/media/library/)
    # 1. Main media folder
    m_dir = (lib_root / valid_media_uuid).resolve()
    if m_dir.exists() and m_dir.is_dir():
        if not m_dir.is_relative_to(lib_root):
            raise HTTPException(status_code=403, detail="Illegal directory traversal attempted")
        for p in m_dir.rglob("*"):
            if p.is_file():
                try:
                    freed_bytes += p.stat().st_size
                except Exception:
                    pass
        shutil.rmtree(m_dir, ignore_errors=True)

    # 2. Episode HLS folders
    for ep_id in episode_ids:
        ep_dir = (lib_root / ep_id).resolve()
        if ep_dir.exists() and ep_dir.is_dir() and ep_dir.is_relative_to(lib_root):
            for p in ep_dir.rglob("*"):
                if p.is_file():
                    try:
                        freed_bytes += p.stat().st_size
                    except Exception:
                        pass
            shutil.rmtree(ep_dir, ignore_errors=True)

    # Delete All Database Records
    # 1. Watch progress & watch history
    await db.delete("watch_progress", "media_id", valid_media_uuid)
    await db.delete("watch_history", "media_id", valid_media_uuid)
    for ep_id in episode_ids:
        await db.delete("watch_progress", "episode_id", ep_id)
        await db.delete("watch_history", "episode_id", ep_id)

    # 2. Custom lists & user likes
    await db.delete("custom_list_items", "media_id", valid_media_uuid)
    await db.delete("user_likes", "media_id", valid_media_uuid)

    # 3. Media streams
    await db.delete("media_streams", "media_id", valid_media_uuid)
    for ep_id in episode_ids:
        await db.delete("media_streams", "episode_id", ep_id)

    # 4. Episodes & seasons
    for ep_id in episode_ids:
        await db.delete("episodes", "id", ep_id)
    await db.delete("seasons", "media_id", valid_media_uuid)

    # 5. Media item
    await db.delete("media_items", "id", valid_media_uuid)

    bump_catalog_version()
    return {
        "status": "deleted",
        "deleted_type": media_type,
        "media_id": valid_media_uuid,
        "episodes_count": len(episode_ids),
        "freed_mb": round(freed_bytes / 1048576, 1),
        "catalog_version": get_catalog_version()
    }

@router.delete("/incoming-file/{filename:path}", dependencies=[Depends(require_admin_secret)])
async def delete_incoming_file(filename: str):
    """
    Safely deletes a raw video file from /media/incoming to reclaim disk space.
    The self-contained HLS streaming files in /media/library are preserved and unaffected.
    """
    incoming = Path(settings.MEDIA_INCOMING_DIR).resolve()
    target_file = (incoming / filename).resolve()

    if not target_file.is_relative_to(incoming):
        raise HTTPException(status_code=403, detail="Invalid file path")

    if not target_file.exists() or not target_file.is_file():
        raise HTTPException(status_code=404, detail="File not found in incoming directory")

    try:
        size_bytes = target_file.stat().st_size
        size_mb = round(size_bytes / (1024 * 1024), 2)
        target_file.unlink()
        return {
            "status": "deleted",
            "filename": filename,
            "reclaimed_mb": size_mb,
            "message": f"Successfully deleted '{filename}' and reclaimed {size_mb} MB disk space."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")

@router.get("/series/{media_id}/episodes", dependencies=[Depends(require_admin_secret)])
async def get_series_episodes(media_id: str):
    """
    Returns uploaded episodes for a series, ordered by season and episode number.
    Only returns episodes present in the database (uploaded/ingested episodes),
    including sanitized audio_tracks and subtitle_tracks for each episode.
    """
    try:
        valid_uuid = str(uuid.UUID(media_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid media_id UUID")

    try:
        rows = await db.select("episodes", {
            "media_id": f"eq.{valid_uuid}",
            "order": "season_number.asc,episode_number.asc"
        })
    except Exception as e:
        logger.error(f"Error querying episodes for series {valid_uuid}: {e}")
        raise HTTPException(status_code=500, detail="Failed to query series episodes")

    ep_ids = [row.get("id") for row in (rows or []) if row.get("id")]
    stream_by_ep: Dict[str, Dict[str, Any]] = {}
    if ep_ids:
        try:
            st_rows = await db.select("media_streams", {})
            for st in (st_rows or []):
                ep_id = st.get("episode_id")
                if ep_id and ep_id in ep_ids:
                    stream_by_ep[ep_id] = st
        except Exception as e:
            logger.warning(f"Could not load media_streams for series episodes: {e}")

    episodes = []
    seasons_set = set()
    for row in (rows or []):
        s_num = int(row.get("season_number") or 1)
        e_num = int(row.get("episode_number") or 1)
        seasons_set.add(s_num)

        st = stream_by_ep.get(row.get("id"))
        clean_audio = [
            {
                **a,
                "label": sanitize_track_label(a.get("label"), fallback=f"Audio Track {(a.get('index') or 0) + 1}"),
                "is_default": bool(a.get("is_default", False))
            }
            for a in (st.get("audio_tracks") or [])
        ] if st else []
        if clean_audio and not any(a.get("is_default") for a in clean_audio):
            clean_audio[0]["is_default"] = True
        clean_subs = [
            {
                **s,
                "label": sanitize_track_label(s.get("label"), fallback=f"Subtitle {(s.get('index') or 0) + 1}")
            }
            for s in (st.get("subtitle_tracks") or [])
        ] if st else []

        episodes.append({
            "id": row.get("id"),
            "season_number": s_num,
            "episode_number": e_num,
            "code": f"S{s_num}:E{e_num}",
            "title": row.get("title") or f"Episode {e_num}",
            "synopsis": row.get("synopsis") or "",
            "still_url": row.get("still_url") or "",
            "duration_seconds": int(row.get("duration_seconds") or 0),
            "is_published": bool(row.get("is_published", True)),
            "audio_tracks": clean_audio,
            "subtitle_tracks": clean_subs
        })

    return {
        "media_id": valid_uuid,
        "seasons": sorted(list(seasons_set)),
        "episodes": episodes
    }

@router.put("/episodes/{episode_id}", dependencies=[Depends(require_admin_secret)])
async def update_episode_metadata(episode_id: str, payload: EpisodeMetadataUpdate):
    """
    Updates metadata for an individual uploaded episode and invalidates catalog cache.
    """
    try:
        valid_uuid = str(uuid.UUID(episode_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid episode_id UUID")

    update_data: Dict[str, Any] = {}
    if payload.title is not None:
        update_data["title"] = payload.title
    if payload.synopsis is not None:
        update_data["synopsis"] = payload.synopsis
    if payload.still_url is not None:
        update_data["still_url"] = payload.still_url
    if payload.season_number is not None:
        update_data["season_number"] = payload.season_number
    if payload.episode_number is not None:
        update_data["episode_number"] = payload.episode_number
    if payload.duration_seconds is not None:
        update_data["duration_seconds"] = payload.duration_seconds
    if payload.is_published is not None:
        update_data["is_published"] = payload.is_published

    if not update_data:
        raise HTTPException(status_code=400, detail="No valid update fields provided.")

    try:
        updated = await db.update("episodes", "id", valid_uuid, update_data)
        if not updated:
            raise HTTPException(status_code=404, detail="Episode not found or update failed.")
        invalidate_catalog_cache()
        return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update episode {valid_uuid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def update_master_m3u8_track_labels(master_path: Path, audio_tracks: List[Dict[str, Any]]) -> bool:
    if not master_path.exists() or not master_path.is_file():
        return False
    try:
        content = master_path.read_text(encoding="utf-8", errors="ignore")
        lines = content.splitlines()

        audio_map = {int(t.get("index", 0)): t for t in audio_tracks}
        audio_seq_idx = 0

        before_audio_lines: List[str] = []
        audio_lines: List[tuple[int, str]] = []
        after_audio_lines: List[str] = []
        seen_audio = False

        for line in lines:
            if line.startswith("#EXT-X-MEDIA:TYPE=AUDIO"):
                seen_audio = True
                uri_match = re.search(r'URI="[^"]*audio_(\d+)[^"]*"', line)
                if uri_match:
                    track_idx = int(uri_match.group(1))
                else:
                    track_idx = audio_seq_idx

                if track_idx in audio_map:
                    track_info = audio_map[track_idx]
                    clean_lbl = sanitize_track_label(track_info.get("label"), fallback=f"Audio Track {track_idx + 1}")
                    clean_lng = track_info.get("lang") or "und"
                    is_def = bool(track_info.get("is_default", False))
                    is_def_str = "YES" if is_def else "NO"
                    auto_sel_str = "YES" if is_def else "NO"

                    new_line = re.sub(r'NAME="[^"]*"', f'NAME="{clean_lbl}"', line)
                    if 'LANGUAGE="' in new_line:
                        new_line = re.sub(r'LANGUAGE="[^"]*"', f'LANGUAGE="{clean_lng}"', new_line)
                    else:
                        new_line = re.sub(r'GROUP-ID="([^"]+)"', f'GROUP-ID="\\1",LANGUAGE="{clean_lng}"', new_line)

                    if 'DEFAULT=' in new_line:
                        new_line = re.sub(r'DEFAULT=(?:YES|NO)', f'DEFAULT={is_def_str}', new_line)
                    else:
                        new_line = new_line.replace('URI=', f'DEFAULT={is_def_str},URI=')

                    if 'AUTOSELECT=' in new_line:
                        new_line = re.sub(r'AUTOSELECT=(?:YES|NO)', f'AUTOSELECT={auto_sel_str}', new_line)
                    else:
                        new_line = new_line.replace('URI=', f'AUTOSELECT={auto_sel_str},URI=')

                    audio_lines.append((track_idx, new_line))
                else:
                    audio_lines.append((track_idx, line))
                audio_seq_idx += 1
            else:
                if not seen_audio:
                    before_audio_lines.append(line)
                else:
                    after_audio_lines.append(line)

        # Keep audio renditions in natural audio_0, audio_1, ... order
        audio_lines.sort(key=lambda x: x[0])
        combined_audio_lines = [item[1] for item in audio_lines]

        new_lines = before_audio_lines + combined_audio_lines + after_audio_lines

        tmp_path = master_path.with_suffix(".tmp")
        tmp_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        os.replace(tmp_path, master_path)
        return True
    except Exception as e:
        logger.error(f"Failed to update master.m3u8 track labels at {master_path}: {e}")
        return False

@router.get("/streams/{target_id}/tracks")
async def get_stream_tracks(target_id: str, request: Request):
    """
    Returns sanitized audio and subtitle tracks for a movie or episode stream.
    Rejects unpublished media for unauthenticated callers.
    """
    try:
        valid_uuid = str(uuid.UUID(target_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid target_id UUID")

    admin_sec = request.headers.get("x-admin-secret", "")
    is_admin = bool(admin_sec and getattr(settings, "ADMIN_API_SECRET", None) and hmac.compare_digest(admin_sec, settings.ADMIN_API_SECRET))
    if not is_admin:
        is_pub = await check_media_published(valid_uuid)
        if not is_pub:
            raise HTTPException(status_code=404, detail="Media stream not found or unavailable")

    rows = await db.select("media_streams", {"episode_id": f"eq.{valid_uuid}"})
    if not rows:
        rows = await db.select("media_streams", {"media_id": f"eq.{valid_uuid}"})
    if not rows:
        # Check if target_id is a series or movie without a direct stream record yet
        media_rows = await db.select("media_items", {"id": f"eq.{valid_uuid}"})
        if media_rows:
            media_item = media_rows[0]
            if media_item.get("type") == "series":
                episodes = await db.select("episodes", {"media_id": f"eq.{valid_uuid}", "order": "season_number.asc,episode_number.asc"})
                for ep in (episodes or []):
                    ep_streams = await db.select("media_streams", {"episode_id": f"eq.{ep.get('id')}"})
                    if ep_streams and (ep_streams[0].get("audio_tracks") or ep_streams[0].get("subtitle_tracks")):
                        row = ep_streams[0]
                        clean_audio = [
                            {
                                **a,
                                "label": sanitize_track_label(a.get("label"), fallback=f"Audio Track {(a.get('index') or 0) + 1}"),
                                "is_default": bool(a.get("is_default", False))
                            }
                            for a in (row.get("audio_tracks") or [])
                        ]
                        if clean_audio and not any(a.get("is_default") for a in clean_audio):
                            clean_audio[0]["is_default"] = True
                        clean_subs = [
                            {
                                **s,
                                "label": sanitize_track_label(s.get("label"), fallback=f"Subtitle {(s.get('index') or 0) + 1}")
                            }
                            for s in (row.get("subtitle_tracks") or [])
                        ]
                        target_dir = Path(settings.MEDIA_LIBRARY_DIR) / str(ep.get('id'))
                        dur_sec = get_hls_playlist_duration(target_dir)
                        return {
                            "target_id": valid_uuid,
                            "audio_tracks": clean_audio,
                            "subtitle_tracks": clean_subs,
                            "duration_seconds": round(dur_sec, 1) if dur_sec > 0 else 0
                        }
            target_dir = Path(settings.MEDIA_LIBRARY_DIR) / valid_uuid
            dur_sec = get_hls_playlist_duration(target_dir)
            return {
                "target_id": valid_uuid,
                "audio_tracks": [],
                "subtitle_tracks": [],
                "duration_seconds": round(dur_sec, 1) if dur_sec > 0 else 0
            }
        raise HTTPException(status_code=404, detail="Media stream not found for target_id")

    row = rows[0]
    clean_audio = [
        {
            **a,
            "label": sanitize_track_label(a.get("label"), fallback=f"Audio Track {(a.get('index') or 0) + 1}"),
            "is_default": bool(a.get("is_default", False))
        }
        for a in (row.get("audio_tracks") or [])
    ]
    if clean_audio and not any(a.get("is_default") for a in clean_audio):
        clean_audio[0]["is_default"] = True

    clean_subs = [
        {
            **s,
            "label": sanitize_track_label(s.get("label"), fallback=f"Subtitle {(s.get('index') or 0) + 1}")
        }
        for s in (row.get("subtitle_tracks") or [])
    ]
    target_dir = Path(settings.MEDIA_LIBRARY_DIR) / valid_uuid
    dur_sec = get_hls_playlist_duration(target_dir)
    return {
        "target_id": valid_uuid,
        "audio_tracks": clean_audio,
        "subtitle_tracks": clean_subs,
        "duration_seconds": round(dur_sec, 1) if dur_sec > 0 else 0
    }

async def backfill_episode_durations():
    """
    One-time startup/lazy backfill that populates episodes.duration_seconds from video/playlist.m3u8
    for any existing episode whose duration_seconds is 0, null, or mismatched.
    """
    try:
        eps = await db.select("episodes", {})
        for ep in (eps or []):
            ep_id = ep.get("id")
            cur_dur = ep.get("duration_seconds")
            if not ep_id:
                continue
            ep_dir = Path(settings.MEDIA_LIBRARY_DIR) / ep_id
            dur = get_hls_playlist_duration(ep_dir)
            if dur > 0 and (not cur_dur or cur_dur <= 0 or abs(cur_dur - dur) > 5):
                await db.update("episodes", "id", ep_id, {"duration_seconds": int(round(dur))})
                logger.info(f"Backfilled exact duration for episode {ep_id} ({ep.get('title')}): {int(round(dur))}s")
        # Also ensure movies have accurate runtime_minutes
        movies = await db.select("media_items", {"type": "eq.movie"})
        for m in (movies or []):
            m_id = m.get("id")
            cur_run = m.get("runtime_minutes")
            if not m_id:
                continue
            m_dir = Path(settings.MEDIA_LIBRARY_DIR) / m_id
            dur = get_hls_playlist_duration(m_dir)
            if dur > 0 and (not cur_run or cur_run <= 0):
                await db.update("media_items", "id", m_id, {"runtime_minutes": max(1, int(round(dur / 60.0)))})
                logger.info(f"Backfilled exact runtime for movie {m_id} ({m.get('title')}): {max(1, int(round(dur / 60.0)))}m")
    except Exception as e:
        logger.warning(f"Error during episode/media duration backfill: {e}")

@router.put("/streams/{target_id}/tracks", dependencies=[Depends(require_admin_secret)])
async def update_stream_tracks(target_id: str, payload: StreamTracksUpdateRequest):
    """
    Instantly renames Audio and Subtitle tracks in Supabase and rewrites master.m3u8 in place without re-transcoding.
    """
    try:
        valid_uuid = str(uuid.UUID(target_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid target_id UUID")

    # Enforce single-default invariant on payload.audio_tracks
    if payload.audio_tracks:
        def_idx = next((i for i, t in enumerate(payload.audio_tracks) if t.is_default), -1)
        if def_idx == -1:
            def_idx = 0
        for i, t in enumerate(payload.audio_tracks):
            t.is_default = (i == def_idx)

    # 1. Fetch existing stream record
    rows = await db.select("media_streams", {"episode_id": f"eq.{valid_uuid}"})
    match_col = "episode_id"
    if not rows:
        rows = await db.select("media_streams", {"media_id": f"eq.{valid_uuid}"})
        match_col = "media_id"
    if not rows:
        raise HTTPException(status_code=404, detail=f"No media stream found for target {valid_uuid}")

    row = rows[0]
    existing_audio = row.get("audio_tracks") or []
    existing_subs = row.get("subtitle_tracks") or []

    # 2. Build updated sanitized audio tracks
    update_audio_map = {item.index: item for item in payload.audio_tracks}
    new_audio_tracks = []
    if existing_audio:
        for idx, a in enumerate(existing_audio):
            a_idx = a.get("index", idx)
            if a_idx in update_audio_map:
                u = update_audio_map[a_idx]
                clean_lbl = sanitize_track_label(u.label, fallback=a.get("label", f"Audio {idx + 1}"))
                new_audio_tracks.append({
                    **a,
                    "label": clean_lbl,
                    "lang": u.lang or a.get("lang", "und"),
                    "is_default": bool(u.is_default)
                })
            else:
                new_audio_tracks.append({
                    **a,
                    "label": sanitize_track_label(a.get("label"), fallback=f"Audio {idx + 1}"),
                    "is_default": bool(a.get("is_default", False))
                })
    else:
        for u in payload.audio_tracks:
            clean_lbl = sanitize_track_label(u.label, fallback=f"Audio {u.index + 1}")
            new_audio_tracks.append({
                "id": f"audio_{u.index}",
                "index": u.index,
                "lang": u.lang,
                "label": clean_lbl,
                "playlist": f"audio_{u.index}/playlist.m3u8",
                "url": f"audio_{u.index}/playlist.m3u8",
                "codec": "aac",
                "is_default": bool(u.is_default)
            })

    if new_audio_tracks:
        def_idx = next((i for i, a in enumerate(new_audio_tracks) if a.get("is_default")), -1)
        if def_idx == -1:
            def_idx = 0
        for i, a in enumerate(new_audio_tracks):
            a["is_default"] = (i == def_idx)

    # 3. Build updated sanitized subtitle tracks
    update_sub_map = {item.index: item for item in payload.subtitle_tracks}
    new_sub_tracks = []
    if existing_subs:
        for idx, s in enumerate(existing_subs):
            s_idx = s.get("index", idx)
            if s_idx in update_sub_map:
                u = update_sub_map[s_idx]
                clean_lbl = sanitize_track_label(u.label, fallback=s.get("label", f"Subtitle {idx + 1}"))
                new_sub_tracks.append({
                    **s,
                    "label": clean_lbl,
                    "lang": u.lang or s.get("lang", "en"),
                    "url": u.url or s.get("url")
                })
            else:
                new_sub_tracks.append({
                    **s,
                    "label": sanitize_track_label(s.get("label"), fallback=f"Subtitle {idx + 1}")
                })
    else:
        for u in payload.subtitle_tracks:
            clean_lbl = sanitize_track_label(u.label, fallback=f"Subtitle {u.index + 1}")
            raw_path = u.url or f"subs/sub_{u.index}_{u.lang}.vtt"
            new_sub_tracks.append({
                "id": f"sub_{u.index}",
                "index": u.index,
                "lang": u.lang,
                "label": clean_lbl,
                "url": raw_path,
                "vtt_path": raw_path
            })

    # 4. Save to Supabase
    updated_stream = await db.update("media_streams", "id", row["id"], {
        "audio_tracks": new_audio_tracks,
        "subtitle_tracks": new_sub_tracks
    })

    # 5. Rewrite master.m3u8 on disk
    master_path = Path(settings.MEDIA_LIBRARY_DIR) / valid_uuid / "master.m3u8"
    if not master_path.exists() and row.get("master_playlist_path"):
        master_path = Path(settings.MEDIA_LIBRARY_DIR) / row["master_playlist_path"]

    m3u8_updated = update_master_m3u8_track_labels(master_path, new_audio_tracks)
    invalidate_catalog_cache()

    return {
        "status": "success",
        "target_id": valid_uuid,
        "master_m3u8_updated": m3u8_updated,
        "audio_tracks": new_audio_tracks,
        "subtitle_tracks": new_sub_tracks,
        "stream": updated_stream
    }


class VisibilityUpdatePayload(BaseModel):
    is_published: bool


@router.put("/media/{media_id}/visibility", dependencies=[Depends(require_admin_secret)])
async def toggle_media_visibility(media_id: str, payload: VisibilityUpdatePayload):
    try:
        valid_uuid = str(uuid.UUID(media_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid media UUID")

    res = await db.update("media_items", "id", valid_uuid, {"is_published": payload.is_published})
    if not res:
        raise HTTPException(status_code=404, detail=f"Media {media_id} not found")

    bump_catalog_version()
    return {
        "status": "success",
        "media_id": valid_uuid,
        "is_published": payload.is_published
    }


@router.put("/episodes/{episode_id}/visibility", dependencies=[Depends(require_admin_secret)])
async def toggle_episode_visibility(episode_id: str, payload: VisibilityUpdatePayload):
    try:
        valid_uuid = str(uuid.UUID(episode_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid episode UUID")

    ep_rows = await db.select("episodes", {"id": f"eq.{valid_uuid}", "limit": "1"})
    if not ep_rows:
        raise HTTPException(status_code=404, detail=f"Episode {episode_id} not found")

    parent_media_id = ep_rows[0].get("media_id")
    await db.update("episodes", "id", valid_uuid, {"is_published": payload.is_published})

    # Auto-sync parent series visibility
    if parent_media_id:
        if payload.is_published:
            try:
                await db.update("media_items", "id", parent_media_id, {"is_published": True})
            except Exception as e:
                logger.warning(f"Could not auto-publish parent series {parent_media_id}: {e}")
        else:
            try:
                remaining_pub = await db.select("episodes", {
                    "media_id": f"eq.{parent_media_id}",
                    "is_published": "eq.true",
                    "limit": "1"
                })
                if not remaining_pub or len(remaining_pub) == 0:
                    await db.update("media_items", "id", parent_media_id, {"is_published": False})
            except Exception as e:
                logger.warning(f"Could not check remaining episodes for series {parent_media_id}: {e}")

    bump_catalog_version()
    return {
        "status": "success",
        "episode_id": valid_uuid,
        "is_published": payload.is_published,
        "parent_media_id": parent_media_id
    }


@router.post("/streams/{target_id}/subtitles/upload", dependencies=[Depends(require_admin_secret)])
async def upload_stream_subtitle(
    target_id: str,
    file: UploadFile = File(...),
    lang: Optional[str] = Form(None),
    label: Optional[str] = Form(None)
):
    try:
        valid_uuid = str(uuid.UUID(target_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid target_id UUID")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".srt", ".vtt", ".ass"):
        raise HTTPException(status_code=400, detail=f"Unsupported subtitle format '{ext}'. Must be .srt, .vtt, or .ass")

    MAX_SUBTITLE_BYTES = 5 * 1024 * 1024  # 5 MB
    raw_content = await file.read(MAX_SUBTITLE_BYTES + 1)
    if len(raw_content) > MAX_SUBTITLE_BYTES:
        raise HTTPException(status_code=413, detail="Subtitle file exceeds maximum limit of 5 MB")
    if not raw_content:
        raise HTTPException(status_code=400, detail="Subtitle file is empty (0 bytes)")

    clean_lang = (lang or "en").strip().lower()[:10]
    clean_label = sanitize_track_label(label or f"Custom ({clean_lang.upper()})", fallback="Custom Subtitle")

    # Locate media stream row
    rows = await db.select("media_streams", {"episode_id": f"eq.{valid_uuid}"})
    if not rows:
        rows = await db.select("media_streams", {"media_id": f"eq.{valid_uuid}"})
    if not rows:
        raise HTTPException(status_code=404, detail="No media stream found for target_id")

    stream_row = rows[0]
    library_dir = Path(settings.MEDIA_LIBRARY_DIR) / valid_uuid
    subs_dir = library_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)

    ts_now = int(datetime.now(timezone.utc).timestamp())
    vtt_name = f"sub_custom_{ts_now}_{clean_lang}.vtt"
    vtt_path = subs_dir / vtt_name

    # Deterministic pure-Python conversion to WebVTT
    vtt_content = convert_subtitle_bytes_to_vtt(raw_content)
    vtt_path.write_text(vtt_content, encoding="utf-8")

    # Append to subtitle_tracks
    existing_subs = stream_row.get("subtitle_tracks") or []
    sub_id = f"sub_custom_{ts_now}"
    new_sub = {
        "id": sub_id,
        "index": len(existing_subs),
        "lang": clean_lang,
        "label": clean_label,
        "url": f"subs/{vtt_name}",
        "vtt_path": f"subs/{vtt_name}",
        "is_custom": True
    }
    updated_subs = [*existing_subs, new_sub]

    await db.update("media_streams", "id", stream_row["id"], {
        "subtitle_tracks": updated_subs
    })

    bump_catalog_version()

    return {
        "status": "success",
        "target_id": valid_uuid,
        "subtitle": new_sub,
        "total_subtitles": len(updated_subs)
    }


@router.delete("/streams/{target_id}/subtitles/{sub_id}", dependencies=[Depends(require_admin_secret)])
async def delete_stream_subtitle(target_id: str, sub_id: str):
    try:
        valid_uuid = str(uuid.UUID(target_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Invalid target_id UUID")

    rows = await db.select("media_streams", {"episode_id": f"eq.{valid_uuid}"})
    if not rows:
        rows = await db.select("media_streams", {"media_id": f"eq.{valid_uuid}"})
    if not rows:
        raise HTTPException(status_code=404, detail="No media stream found for target_id")

    stream_row = rows[0]
    existing_subs = stream_row.get("subtitle_tracks") or []
    library_dir = Path(settings.MEDIA_LIBRARY_DIR) / valid_uuid

    matched_sub = None
    remaining_subs = []
    for s in existing_subs:
        if str(s.get("id")) == str(sub_id) or str(s.get("index")) == str(sub_id):
            matched_sub = s
        else:
            remaining_subs.append(s)

    if not matched_sub:
        raise HTTPException(status_code=404, detail=f"Subtitle track '{sub_id}' not found")

    rel_url = matched_sub.get("url") or matched_sub.get("vtt_path")
    if rel_url:
        safe_rel = rel_url.lstrip("/")
        sub_file = (library_dir / safe_rel).resolve()
        if sub_file.is_relative_to(library_dir.resolve()) and sub_file.exists():
            try:
                sub_file.unlink()
            except Exception as e:
                logger.warning(f"Could not delete physical subtitle file {sub_file}: {e}")

    for idx, s in enumerate(remaining_subs):
        s["index"] = idx

    await db.update("media_streams", "id", stream_row["id"], {
        "subtitle_tracks": remaining_subs
    })

    bump_catalog_version()

    return {
        "status": "success",
        "target_id": valid_uuid,
        "deleted_sub_id": sub_id,
        "remaining_subtitles": len(remaining_subs)
    }


@router.post("/incoming/upload-subtitle", dependencies=[Depends(require_admin_secret)])
async def upload_incoming_subtitle(
    file: UploadFile = File(...),
    target_filename: str = Form(...),
    lang: Optional[str] = Form("en"),
    label: Optional[str] = Form(None)
):
    """
    Saves an external subtitle (.srt, .vtt, .ass) safely into .custom_subs associated with an incoming video.
    Converts .srt to valid WebVTT deterministically in Python with multi-encoding fallback.
    """
    incoming_dir = Path(settings.MEDIA_INCOMING_DIR)
    target_video = (incoming_dir / target_filename).resolve()
    if not target_video.is_relative_to(incoming_dir.resolve()) or not target_video.is_file():
        raise HTTPException(status_code=404, detail="Target video file not found in incoming directory")

    ext = Path(file.filename or "").suffix.lower()
    if ext not in (".srt", ".vtt", ".ass"):
        raise HTTPException(status_code=400, detail="Only .srt, .vtt, or .ass files are supported")

    MAX_SUBTITLE_BYTES = 5 * 1024 * 1024  # 5 MB
    raw_bytes = await file.read(MAX_SUBTITLE_BYTES + 1)
    if len(raw_bytes) > MAX_SUBTITLE_BYTES:
        raise HTTPException(status_code=413, detail="Subtitle file exceeds maximum limit of 5 MB")
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded subtitle file is empty")

    decoded_text = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            decoded_text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if decoded_text is None:
        decoded_text = raw_bytes.decode("utf-8", errors="replace")

    vtt_content = _convert_srt_to_vtt(decoded_text)

    safe_video_stem = re.sub(r"[^\w\-.]", "_", target_video.stem)
    custom_subs_dir = incoming_dir / ".custom_subs" / safe_video_stem
    custom_subs_dir.mkdir(parents=True, exist_ok=True)

    ext_id = f"custom_{int(time.time() * 1000)}"
    vtt_dest = custom_subs_dir / f"{ext_id}.vtt"
    vtt_dest.write_text(vtt_content, encoding="utf-8")

    clean_lang = (lang or "en").strip().lower()[:10]
    clean_lbl = sanitize_track_label(label or f"Custom ({clean_lang.upper()})", fallback="Custom Subtitle")

    meta_dest = custom_subs_dir / f"{ext_id}.meta.json"
    meta_payload = {
        "ext_id": ext_id,
        "filename": file.filename or f"{ext_id}.vtt",
        "lang": clean_lang,
        "label": clean_lbl,
        "uploaded_at": int(time.time())
    }
    meta_dest.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

    # Return updated list of external subtitles
    external_subtitles = []
    for meta_file in sorted(custom_subs_dir.glob("*.meta.json")):
        try:
            m = json.loads(meta_file.read_text(encoding="utf-8"))
            v_file = custom_subs_dir / f"{m['ext_id']}.vtt"
            if v_file.exists():
                external_subtitles.append({
                    "ext_id": m["ext_id"],
                    "filename": m.get("filename") or v_file.name,
                    "path": str(v_file),
                    "lang": m.get("lang", "en"),
                    "label": m.get("label"),
                    "included": True
                })
        except Exception:
            pass

    return {
        "status": "success",
        "target_filename": target_filename,
        "attached_ext_id": ext_id,
        "external_subtitles": external_subtitles
    }


@router.delete("/incoming/external-subtitle", dependencies=[Depends(require_admin_secret)])
async def delete_incoming_external_subtitle(
    target_filename: str = Query(...),
    ext_id: str = Query(...)
):
    """
    Removes an attached external subtitle from the video's .custom_subs directory.
    """
    incoming_dir = Path(settings.MEDIA_INCOMING_DIR)
    target_video = (incoming_dir / target_filename).resolve()
    if not target_video.is_relative_to(incoming_dir.resolve()) or not target_video.is_file():
        raise HTTPException(status_code=404, detail="Target video file not found in incoming directory")

    safe_video_stem = re.sub(r"[^\w\-.]", "_", target_video.stem)
    custom_subs_dir = incoming_dir / ".custom_subs" / safe_video_stem
    if custom_subs_dir.exists():
        safe_ext_id = re.sub(r"[^\w\-]", "", ext_id)
        for p in (custom_subs_dir / f"{safe_ext_id}.vtt", custom_subs_dir / f"{safe_ext_id}.meta.json"):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

    external_subtitles = []
    if custom_subs_dir.exists():
        for meta_file in sorted(custom_subs_dir.glob("*.meta.json")):
            try:
                m = json.loads(meta_file.read_text(encoding="utf-8"))
                v_file = custom_subs_dir / f"{m['ext_id']}.vtt"
                if v_file.exists():
                    external_subtitles.append({
                        "ext_id": m["ext_id"],
                        "filename": m.get("filename") or v_file.name,
                        "path": str(v_file),
                        "lang": m.get("lang", "en"),
                        "label": m.get("label"),
                        "included": True
                    })
            except Exception:
                pass

    return {
        "status": "success",
        "target_filename": target_filename,
        "deleted_ext_id": ext_id,
        "external_subtitles": external_subtitles
    }


