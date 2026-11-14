import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Set
from .client import aria2_client
from ..config import settings
from ..metadata.ingester import ingester

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".m4v", ".webm"}

class Aria2DownloadMonitor:
    def __init__(self):
        # GID -> {tmdb_id, media_type, season_number, episode_number, added_at}
        self.metadata_bindings: Dict[str, Dict[str, Any]] = {}
        self.processed_gids: Set[str] = set()
        self.monitor_task: Optional[asyncio.Task] = None

    def bind_metadata(self, gid: str, tmdb_id: int, media_type: str = "movie", season_number: Optional[int] = None, episode_number: Optional[int] = None):
        self.metadata_bindings[gid] = {
            "tmdb_id": tmdb_id,
            "media_type": media_type,
            "season_number": season_number,
            "episode_number": episode_number
        }
        logger.info(f"Bound metadata for GID {gid}: TMDB {tmdb_id} ({media_type})")

    def start(self):
        if self.monitor_task is None or self.monitor_task.done():
            self.monitor_task = asyncio.create_task(self._monitor_loop())
            logger.info("Aria2 download monitor background task started.")

    async def _monitor_loop(self):
        while True:
            try:
                # Check stopped/completed downloads from aria2
                stopped = await aria2_client.tell_stopped(0, 50)
                for item in stopped:
                    gid = item.get("gid")
                    status = item.get("status")
                    if status == "complete" and gid not in self.processed_gids:
                        self.processed_gids.add(gid)
                        await self._handle_completed_download(gid, item)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error in Aria2 download monitor loop: {e}")
            
            await asyncio.sleep(4)

    async def _handle_completed_download(self, gid: str, item: Dict[str, Any]):
        files = item.get("files", [])
        if not files:
            logger.warning(f"No files found for completed GID {gid}")
            return

        # Find the largest video file
        largest_video_path = None
        largest_size = 0

        for f in files:
            path_str = f.get("path")
            if not path_str:
                continue
            path = Path(path_str)
            if path.suffix.lower() in VIDEO_EXTENSIONS and path.exists():
                size = path.stat().st_size
                if size > largest_size:
                    largest_size = size
                    largest_video_path = path

        if not largest_video_path:
            logger.warning(f"No valid video file found for completed GID {gid}")
            return

        incoming_root = Path(settings.MEDIA_INCOMING_DIR).resolve()
        resolved_video_path = largest_video_path.resolve()
        if not resolved_video_path.is_relative_to(incoming_root) or not resolved_video_path.is_file():
            logger.warning(f"Security: Completed file {resolved_video_path} is outside incoming directory {incoming_root}")
            return

        logger.info(f"Download complete for GID {gid}. Largest video file: {resolved_video_path} ({round(largest_size / (1024*1024), 2)} MB)")

        # Check if metadata binding exists
        binding = self.metadata_bindings.get(gid)
        if not binding:
            logger.info(f"No manual metadata binding for GID {gid}. File remains in {settings.MEDIA_INCOMING_DIR} for manual ingest.")
            return

        try:
            tmdb_id = binding["tmdb_id"]
            media_type = binding.get("media_type", "movie")
            season_num = binding.get("season_number")
            ep_num = binding.get("episode_number")

            logger.info(f"Auto-triggering media ingestion for TMDB ID {tmdb_id} on {resolved_video_path.name}")
            await ingester.ingest_media(
                file_path=str(resolved_video_path),
                tmdb_id=tmdb_id,
                media_type=media_type,
                season_number=season_num,
                episode_number=ep_num
            )
            logger.info(f"Auto-ingest successfully queued for GID {gid}")
        except Exception as e:
            logger.exception(f"Failed to auto-ingest file for GID {gid}: {e}")

aria2_monitor = Aria2DownloadMonitor()
