import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from .tmdb import tmdb_client, TMDB_IMAGE_BASE
from .omdb import omdb_client
from ..database import db
from ..config import settings
from ..transcoder.worker import transcode_queue, TranscodeJob
from ..transcoder.pipeline_remux import sanitize_track_label
from ..transcoder.probe import probe_file, probe_file_async

logger = logging.getLogger(__name__)

def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

class MediaIngester:
    async def ingest_media(
        self,
        file_path: str,
        tmdb_id: Optional[int] = None,
        media_type: str = "movie",
        season_number: Optional[int] = None,
        episode_number: Optional[int] = None,
        media_id: Optional[str] = None,
        audio_languages: Optional[List[str]] = None,
        subtitle_languages: Optional[List[str]] = None,
        audio_tracks_config: Optional[List[Dict[str, Any]]] = None,
        subtitle_tracks_config: Optional[List[Dict[str, Any]]] = None,
        force_video_reencode: bool = False
    ) -> Dict[str, Any]:
        """
        Fetches full TMDB + OMDb metadata or uses existing catalog item, writes to Supabase, and enqueues FFmpeg processing.
        """
        media_item = None
        details = None

        # Probe incoming file to extract exact duration_seconds and runtime_minutes immediately
        probe = None
        try:
            probe = await probe_file_async(file_path)
        except Exception as e:
            logger.warning(f"Could not probe file {file_path}: {e}")
        probe_seconds = int(round(probe.duration_seconds)) if probe and probe.duration_seconds > 0 else 0
        probe_minutes = max(1, int(round(probe_seconds / 60.0))) if probe_seconds > 0 else 0

        # A. If explicit media_id provided, fetch existing item directly
        if media_id:
            items = await db.select("media_items", {"id": f"eq.{media_id}"})
            if items and len(items) > 0:
                media_item = items[0]
                media_type = media_item.get("type", media_type)
                title = media_item.get("title")
                if probe_minutes > 0 and (not media_item.get("runtime_minutes") or media_item.get("runtime_minutes") <= 0):
                    await db.update("media_items", "id", media_id, {"runtime_minutes": probe_minutes})

        # B. Check if existing item matches tmdb_id
        if not media_item and tmdb_id:
            existing_items = await db.select("media_items", {"tmdb_id": f"eq.{tmdb_id}"})
            if existing_items and len(existing_items) > 0:
                media_item = existing_items[0]
                media_id = media_item["id"]
                media_type = media_item.get("type", media_type)
                title = media_item.get("title")
                logger.info(f"Found existing catalog media_item {media_id} for TMDB ID {tmdb_id}")
                if probe_minutes > 0 and (not media_item.get("runtime_minutes") or media_item.get("runtime_minutes") <= 0):
                    await db.update("media_items", "id", media_id, {"runtime_minutes": probe_minutes})

        # C. Fetch TMDB details if we don't have an existing item (or to enrich)
        if not media_item and tmdb_id:
            try:
                details = await tmdb_client.get_details(tmdb_id, media_type)
            except Exception as e:
                logger.warning(f"Could not reach TMDB: {e}")

            if not details and not media_item:
                raise ValueError(f"Could not retrieve TMDB metadata or existing catalog item for ID {tmdb_id}")

        if details and not media_item:
            # 2. Fetch OMDb critic rating
            critic_score = None
            try:
                critic_score = await omdb_client.get_ratings(
                    imdb_id=details.get("imdb_id"),
                    title=details.get("title"),
                    year=details.get("release_year")
                )
            except Exception:
                pass

            title = details.get("title")
            slug = f"{slugify(title)}-{details.get('release_year', '2026')}"

            insert_payload = {
                "tmdb_id": tmdb_id,
                "imdb_id": details.get("imdb_id"),
                "type": details.get("type"),
                "title": title,
                "slug": slug,
                "synopsis": details.get("synopsis"),
                "release_year": details.get("release_year"),
                "maturity_rating": details.get("maturity_rating"),
                "runtime_minutes": probe_minutes if probe_minutes > 0 else details.get("runtime_minutes"),
                "poster_url": details.get("poster_url"),
                "backdrop_url": details.get("backdrop_url"),
                "trailer_youtube_key": details.get("trailer_youtube_key"),
                "genres": details.get("genres", []),
                "cast_members": details.get("cast_members", []),
                "creators_directors": details.get("creators_directors", []),
                "critic_rating": critic_score,
                "badge_type": "recently_added",
                "is_published": False
            }
            media_item = await db.insert("media_items", insert_payload)
            if not media_item:
                raise RuntimeError("Failed to insert media_item into Supabase")
            media_id = media_item["id"]
            logger.info(f"Created new media_item {media_id} for '{title}'")
        elif media_item:
            media_id = media_item["id"]
            title = media_item.get("title")

        episode_id = None

        # 4. If series, ensure Season and Episode records exist
        is_series = media_type == "series" or (media_item and media_item.get("type") == "series")
        if is_series:
            s_num = season_number or 1
            ep_num = episode_number or 1

            # Fetch or create Season
            existing_seasons = await db.select("seasons", {
                "media_id": f"eq.{media_id}",
                "season_number": f"eq.{s_num}"
            })
            if existing_seasons and len(existing_seasons) > 0:
                season = existing_seasons[0]
                season_id = season["id"]
            else:
                season_data = await db.insert("seasons", {
                    "media_id": media_id,
                    "season_number": s_num,
                    "title": f"Season {s_num}",
                    "episode_count": 0
                })
                season_id = season_data["id"]

            # Fetch season details from TMDB to get accurate episode name & still if available
            season_info = None
            if tmdb_id:
                try:
                    season_info = await tmdb_client.get_tv_season(tmdb_id, s_num)
                except Exception:
                    pass
            ep_title = f"Episode {ep_num}"
            ep_synopsis = ""
            ep_still = None
            ep_duration = probe_seconds if probe_seconds > 0 else 2700

            if season_info and "episodes" in season_info:
                for ep in season_info["episodes"]:
                    if ep.get("episode_number") == ep_num:
                        ep_title = ep.get("name") or ep_title
                        ep_synopsis = ep.get("overview") or ""
                        still_path = ep.get("still_path")
                        if still_path:
                            ep_still = f"{TMDB_IMAGE_BASE}/w500{still_path}"
                        if ep.get("runtime") and probe_seconds <= 0:
                            ep_duration = ep.get("runtime") * 60
                        break

            # Fetch or create Episode
            existing_eps = await db.select("episodes", {
                "season_id": f"eq.{season_id}",
                "episode_number": f"eq.{ep_num}"
            })
            if existing_eps and len(existing_eps) > 0:
                episode = existing_eps[0]
                episode_id = episode["id"]
                if probe_seconds > 0 and (not episode.get("duration_seconds") or episode.get("duration_seconds") <= 0):
                    await db.update("episodes", "id", episode_id, {"duration_seconds": probe_seconds})
            else:
                ep_data = await db.insert("episodes", {
                    "media_id": media_id,
                    "season_id": season_id,
                    "season_number": s_num,
                    "episode_number": ep_num,
                    "title": ep_title,
                    "synopsis": ep_synopsis,
                    "duration_seconds": ep_duration,
                    "still_url": ep_still,
                    "is_published": False
                })
                episode_id = ep_data["id"]
                try:
                    all_eps = await db.select("episodes", {"season_id": f"eq.{season_id}"})
                    if all_eps:
                        await db.update("seasons", "id", season_id, {"episode_count": len(all_eps)})
                except Exception:
                    pass

        # If series already has published episodes, ensure parent media_item is marked published
        if is_series and media_id:
            try:
                pub_eps = await db.select("episodes", {"media_id": f"eq.{media_id}", "is_published": "eq.true"})
                if pub_eps and len(pub_eps) > 0:
                    await db.update("media_items", "id", media_id, {"is_published": True})
            except Exception:
                pass

        # 5. Format initial audio and subtitle tracks if specified
        formatted_audio = None
        if audio_tracks_config:
            formatted_audio = [
                {
                    "index": idx,
                    "raw_stream_idx": cfg.get("raw_stream_idx"),
                    "lang": (cfg.get("lang") or "en")[:12],
                    "label": sanitize_track_label(cfg.get("label"), fallback=f"Audio Track {idx + 1}"),
                    "codec": cfg.get("codec", "aac"),
                    "is_default": bool(cfg.get("is_default", False))
                }
                for idx, cfg in enumerate(audio_tracks_config)
                if cfg.get("label") or cfg.get("lang") or cfg.get("raw_stream_idx") is not None
            ]
            if formatted_audio:
                def_idx = next((i for i, a in enumerate(formatted_audio) if a.get("is_default")), -1)
                if def_idx == -1:
                    def_idx = 0
                for i, a in enumerate(formatted_audio):
                    a["is_default"] = (i == def_idx)
        elif audio_languages:
            formatted_audio = [
                {
                    "index": idx,
                    "lang": lang.strip().lower()[:2],
                    "label": sanitize_track_label(lang.strip(), fallback=f"Audio Track {idx + 1}"),
                    "codec": "aac",
                    "is_default": (idx == 0)
                }
                for idx, lang in enumerate(audio_languages) if lang.strip()
            ]

        formatted_subs = None
        if subtitle_tracks_config is not None:
            incoming_root = Path(settings.MEDIA_INCOMING_DIR).resolve()
            target_id = episode_id or media_id
            subs_root = (Path(settings.MEDIA_LIBRARY_DIR).resolve() / str(target_id) / "subs").resolve() if target_id else None
            allowed_roots = [incoming_root, *([subs_root] if subs_root else [])]

            formatted_subs = []
            for idx, cfg in enumerate(subtitle_tracks_config):
                if not ((cfg.get("label") or cfg.get("lang") or cfg.get("raw_stream_idx") is not None) and str(cfg.get("label", "")).lower() != "none"):
                    continue
                raw_ext_id = cfg.get("ext_id")
                if raw_ext_id is not None and not re.fullmatch(r"[\w\-]+", str(raw_ext_id)):
                    raise HTTPException(status_code=400, detail="Invalid subtitle ext_id")
                raw_path = cfg.get("path")
                canonical_path = None
                if raw_path is not None and str(raw_path).strip():
                    p = Path(str(raw_path))
                    resolved_p = p.resolve()
                    if p.is_symlink() or not any(resolved_p.is_relative_to(r) for r in allowed_roots) or not resolved_p.is_file():
                        raise HTTPException(status_code=403, detail="Subtitle path outside allowed directories")
                    canonical_path = str(resolved_p)
                formatted_subs.append({
                    "index": idx,
                    "raw_stream_idx": cfg.get("raw_stream_idx"),
                    "lang": (cfg.get("lang") or "en")[:12],
                    "label": sanitize_track_label(cfg.get("label"), fallback=f"Subtitle {idx + 1}"),
                    "format": "vtt",
                    "is_external": bool(cfg.get("is_external", False)),
                    "path": canonical_path,
                    "ext_id": str(raw_ext_id) if raw_ext_id is not None else None,
                })
        elif subtitle_languages:
            formatted_subs = [
                {
                    "index": idx,
                    "lang": lang.strip().lower()[:2],
                    "label": sanitize_track_label(lang.strip(), fallback=f"Subtitle {idx + 1}"),
                    "format": "vtt"
                }
                for idx, lang in enumerate(subtitle_languages) if lang.strip() and lang.strip().lower() != "none"
            ]

        stream_payload = {
            "master_playlist_path": f"{episode_id or media_id}/master.m3u8",
            "is_transcoded": False,
            "available_qualities": ["Original"],
            "audio_tracks": formatted_audio if formatted_audio is not None else [],
            "subtitle_tracks": formatted_subs if formatted_subs is not None else [],
            "file_size_bytes": 0,
            "processing_status": "queued"
        }
        if episode_id:
            stream_payload["episode_id"] = episode_id
        else:
            stream_payload["media_id"] = media_id

        match_query = {"episode_id": f"eq.{episode_id}"} if episode_id else {"media_id": f"eq.{media_id}"}
        existing_streams = await db.select("media_streams", match_query)
        if existing_streams and len(existing_streams) > 0:
            await db.update("media_streams", "id", existing_streams[0]["id"], stream_payload)
        else:
            await db.insert("media_streams", stream_payload)

        # 6. Enqueue transcode/remux job
        job = TranscodeJob(
            job_id=str(uuid.uuid4()),
            media_id=media_id if not episode_id else None,
            episode_id=episode_id,
            parent_media_id=media_id if episode_id else None,
            input_file_path=file_path,
            title=f"{title}" + (f" S{season_number}E{episode_number}" if episode_id else ""),
            custom_audio_tracks=formatted_audio,
            custom_subtitle_tracks=formatted_subs,
            force_video_reencode=force_video_reencode,
            media_type="series" if episode_id else "movie"
        )
        await transcode_queue.enqueue(job)
        try:
            from ..api.system import invalidate_system_caches
            invalidate_system_caches()
        except Exception:
            pass

        return {
            "media_id": media_id,
            "episode_id": episode_id,
            "title": title,
            "status": "queued"
        }

ingester = MediaIngester()
