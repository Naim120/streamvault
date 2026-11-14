import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from .probe import probe_file, probe_file_async
from .pipeline_remux import run_lossless_remux
from .pipeline_transcode import run_multi_bitrate_transcode
from ..config import settings
from ..database import db

logger = logging.getLogger(__name__)

@dataclass
class TranscodeJob:
    job_id: str
    media_id: Optional[str]
    episode_id: Optional[str]
    input_file_path: str
    title: str
    custom_audio_tracks: Optional[List[Dict[str, Any]]] = None
    custom_subtitle_tracks: Optional[List[Dict[str, Any]]] = None
    force_video_reencode: bool = False
    parent_media_id: Optional[str] = None
    media_type: str = "movie"

    @property
    def filename(self) -> str:
        return Path(self.input_file_path).name if self.input_file_path else ""

import os
import signal
import shutil

class TranscodeQueueManager:
    def __init__(self):
        self.queue: asyncio.Queue[TranscodeJob] = asyncio.Queue(maxsize=100)
        self.worker_task: Optional[asyncio.Task] = None
        self.active_job: Optional[TranscodeJob] = None
        self.active_process: Optional[asyncio.subprocess.Process] = None
        self.cancel_requested: bool = False
        self.active_output_dir: Optional[Path] = None
        self.current_progress_pct: float = 0.0
        self.current_stage: str = "Idle"

    def start_worker(self):
        if self.worker_task is None or self.worker_task.done():
            self.worker_task = asyncio.create_task(self._process_queue_loop())
            logger.info("Transcode sequential worker started (concurrency = 1)")

    def set_active_process(self, proc: asyncio.subprocess.Process, output_dir: Optional[Path] = None):
        self.active_process = proc
        if output_dir:
            self.active_output_dir = output_dir

    def clear_active_process(self):
        self.active_process = None

    def _cleanup_incomplete_renditions(self, output_dir: Path):
        """
        Removes partial rendition directories and .ts/.m3u8 files that do not contain #EXT-X-ENDLIST.
        Ensures broken partial streams are never left behind.
        """
        if not output_dir or not output_dir.exists():
            return
        logger.info(f"Checking for incomplete streams to clean in {output_dir}...")
        for r_name in ["video", "video_720p", "video_480p", "audio_0", "audio_1", "audio_2", "audio_3"]:
            r_dir = output_dir / r_name
            if r_dir.exists() and r_dir.is_dir():
                pl = r_dir / "playlist.m3u8"
                is_complete = False
                if pl.exists():
                    try:
                        content = pl.read_text(encoding="utf-8", errors="ignore")
                        if "#EXT-X-ENDLIST" in content:
                            is_complete = True
                    except Exception:
                        pass
                if not is_complete:
                    try:
                        shutil.rmtree(r_dir)
                        logger.info(f"Cleaned up incomplete rendition directory: {r_dir}")
                    except Exception as err:
                        logger.warning(f"Could not remove partial rendition {r_dir}: {err}")

        # Check master playlist
        master_pl = output_dir / "master.m3u8"
        if master_pl.exists():
            try:
                # If master.m3u8 points to non-existent video playlists, remove it
                master_content = master_pl.read_text(encoding="utf-8", errors="ignore")
                if "video/playlist.m3u8" in master_content and not (output_dir / "video" / "playlist.m3u8").exists():
                    master_pl.unlink()
            except Exception:
                pass

    async def cancel_active_job(self) -> Dict[str, Any]:
        """
        Terminates running FFmpeg process group immediately, cleans up partial files,
        and resets telemetry to Idle.
        """
        if not self.active_job:
            return {"status": "idle", "message": "No active transcode job to cancel."}

        self.cancel_requested = True
        job = self.active_job
        target_id = job.episode_id or job.media_id or job.job_id
        output_dir = self.active_output_dir or (Path(settings.MEDIA_LIBRARY_DIR) / str(target_id))

        logger.warning(f"🛑 Cancel requested for job {job.job_id} ({job.title}). Killing process group...")

        proc = self.active_process
        if proc and proc.returncode is None:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                # Wait up to 1.5s for graceful exit
                for _ in range(15):
                    await asyncio.sleep(0.1)
                    if proc.returncode is not None:
                        break
                if proc.returncode is None:
                    os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.warning(f"Error terminating process group for job {job.job_id}: {e}")

        # Clean up partial files
        self._cleanup_incomplete_renditions(output_dir)

        # Reset telemetry
        self.current_progress_pct = 0.0
        self.current_stage = "Cancelled by Admin"
        self.active_job = None
        self.active_process = None

        await self._update_stream_status(job, "cancelled")

        return {
            "status": "cancelled",
            "message": "Transcoding job cancelled and partial segments cleaned."
        }

    @property
    def pending_jobs(self) -> List[TranscodeJob]:
        try:
            return list(self.queue._queue)
        except Exception:
            return []

    async def enqueue(self, job: TranscodeJob) -> bool:
        try:
            self.queue.put_nowait(job)
            logger.info(f"Enqueued transcode job {job.job_id} for '{job.title}'. Queue size: {self.queue.qsize()}")
            return True
        except asyncio.QueueFull:
            logger.error("Transcode queue is full! Job rejected.")
            return False

    def get_status(self) -> Dict[str, Any]:
        queued_jobs = []
        try:
            for idx, j in enumerate(list(self.queue._queue)):
                queued_jobs.append({
                    "job_id": j.job_id,
                    "title": j.title,
                    "filename": Path(j.input_file_path).name if j.input_file_path else "",
                    "input_file_path": j.input_file_path,
                    "media_id": j.media_id,
                    "episode_id": j.episode_id,
                    "media_type": getattr(j, "media_type", "series" if j.episode_id else "movie"),
                    "position": idx + 1,
                    "audio_count": len(j.custom_audio_tracks) if j.custom_audio_tracks else 0,
                    "sub_count": len(j.custom_subtitle_tracks) if j.custom_subtitle_tracks else 0
                })
        except Exception as e:
            logger.warning(f"Error inspecting queued jobs: {e}")

        return {
            "queue_length": self.queue.qsize(),
            "is_busy": self.active_job is not None,
            "active_job": {
                "job_id": self.active_job.job_id,
                "title": self.active_job.title,
                "filename": Path(self.active_job.input_file_path).name if self.active_job.input_file_path else "",
                "input_file_path": self.active_job.input_file_path,
                "media_id": self.active_job.media_id,
                "episode_id": self.active_job.episode_id,
                "media_type": getattr(self.active_job, "media_type", "series" if self.active_job.episode_id else "movie"),
                "progress_pct": round(self.current_progress_pct, 1),
                "stage": self.current_stage,
                "audio_count": len(self.active_job.custom_audio_tracks) if self.active_job.custom_audio_tracks else 0,
                "sub_count": len(self.active_job.custom_subtitle_tracks) if self.active_job.custom_subtitle_tracks else 0
            } if self.active_job else None,
            "queued_jobs": queued_jobs
        }

    async def cancel_queued_job(self, identifier: str) -> Dict[str, Any]:
        """
        Removes a queued job before it starts executing and cleans up placeholder DB records.
        Matches by job_id, filename, full path, media_id, or episode_id.
        """
        target_job: Optional[TranscodeJob] = None
        new_queue = []
        for j in list(self.queue._queue):
            fname = Path(j.input_file_path).name if j.input_file_path else ""
            if (target_job is None and 
                (j.job_id == identifier or 
                 fname == identifier or 
                 j.input_file_path == identifier or
                 j.media_id == identifier or 
                 j.episode_id == identifier)):
                target_job = j
            else:
                new_queue.append(j)

        if not target_job:
            return {"status": "not_found", "message": f"Job '{identifier}' not found in waiting queue."}

        # Rebuild queue deque
        self.queue._queue.clear()
        for item in new_queue:
            self.queue._queue.append(item)
        if hasattr(self.queue, "_unfinished_tasks"):
            self.queue._unfinished_tasks = max(0, self.queue._unfinished_tasks - 1)

        logger.warning(f"Cancelled waiting queued job {target_job.job_id} ({target_job.title}, {target_job.filename})")
        await self._cleanup_cancelled_queued_job(target_job)

        return {
            "status": "cancelled",
            "message": f"Queued job for '{target_job.title}' ({target_job.filename}) was removed from queue.",
            "job_id": target_job.job_id,
            "filename": target_job.filename,
            "remaining_queue_length": self.queue.qsize()
        }

    async def cancel_all_queued_jobs(self) -> Dict[str, Any]:
        """
        Clears all waiting jobs from the queue and cleans up placeholder DB records.
        """
        cancelled_jobs = list(self.queue._queue)
        self.queue._queue.clear()
        if hasattr(self.queue, "_unfinished_tasks"):
            self.queue._unfinished_tasks = 0

        for j in cancelled_jobs:
            try:
                await self._cleanup_cancelled_queued_job(j)
            except Exception as e:
                logger.warning(f"Error cleaning up queued job {j.job_id}: {e}")

        logger.warning(f"Cancelled all {len(cancelled_jobs)} waiting jobs in transcode queue.")
        return {
            "status": "cleared",
            "message": f"Cleared {len(cancelled_jobs)} waiting job(s) from queue.",
            "cancelled_count": len(cancelled_jobs)
        }

    async def _cleanup_cancelled_queued_job(self, job: TranscodeJob):
        """
        Safely restores or purges DB records for a cancelled queued job:
        - If re-ingest (master.m3u8 exists in library): restore processing_status = 'ready'
        - If fresh ingest (master.m3u8 does not exist): delete placeholder media_streams and unpublished shells
        """
        target_id = job.episode_id or job.media_id or job.job_id
        output_dir = Path(settings.MEDIA_LIBRARY_DIR) / str(target_id)
        master_pl = output_dir / "master.m3u8"
        is_reingest = master_pl.exists()

        match_col = "episode_id" if job.episode_id else "media_id"
        match_val = job.episode_id or job.media_id

        if is_reingest:
            if match_val:
                await db.update("media_streams", match_col, match_val, {"processing_status": "ready"})
            logger.info(f"Restored re-ingest job {job.job_id} ({job.title}) to 'ready' status as master.m3u8 exists.")
        else:
            if match_val:
                try:
                    await db.delete("media_streams", match_col, match_val)
                except Exception as e:
                    logger.warning(f"Could not delete media_streams for cancelled job {job.job_id}: {e}")

                if job.episode_id:
                    try:
                        ep_rows = await db.select("episodes", {"id": f"eq.{job.episode_id}", "is_published": "eq.false"})
                        if ep_rows:
                            await db.delete("episodes", "id", job.episode_id)
                    except Exception as e:
                        logger.warning(f"Could not clean up episode {job.episode_id}: {e}")
                elif job.media_id:
                    try:
                        m_rows = await db.select("media_items", {"id": f"eq.{job.media_id}", "is_published": "eq.false"})
                        if m_rows:
                            await db.delete("media_items", "id", job.media_id)
                    except Exception as e:
                        logger.warning(f"Could not clean up media_item {job.media_id}: {e}")
            logger.info(f"Cleaned up placeholder records for unstarted job {job.job_id} ({job.title}).")

    async def _process_queue_loop(self):
        while True:
            try:
                job = await self.queue.get()
                self.active_job = job
                self.current_progress_pct = 0.0
                self.current_stage = "Probing media streams..."
                
                logger.info(f"Starting processing job {job.job_id} for: {job.title}")
                await self._execute_job(job)
                
            except asyncio.CancelledError:
                logger.info("Transcode worker loop cancelled.")
                break
            except Exception as e:
                logger.exception(f"Unexpected error in transcode worker loop: {e}")
            finally:
                self.active_job = None
                self.current_progress_pct = 0.0
                self.current_stage = "Idle"
                self.queue.task_done()

    async def _execute_job(self, job: TranscodeJob):
        input_path = Path(job.input_file_path)
        if not input_path.exists():
            logger.error(f"Input file for job {job.job_id} not found: {input_path}")
            await self._mark_failed(job, "Input file not found")
            return

        # Target library directory: /media/library/{media_id} or /media/library/{episode_id}
        target_id = job.episode_id or job.media_id or job.job_id
        output_dir = Path(settings.MEDIA_LIBRARY_DIR) / str(target_id)
        self.active_output_dir = output_dir
        self.cancel_requested = False

        try:
            # 1. Update status to processing in DB
            await self._update_stream_status(job, "processing")

            # 2. Probe file
            probe = await probe_file_async(str(input_path))
            if not probe:
                raise ValueError("Could not inspect media streams using ffprobe")

            # 3. Read current system settings from Supabase
            sys_settings = await db.select("system_settings", {"id": "eq.1"})
            transcoding_enabled = False
            max_threads = settings.MAX_TRANSCODE_THREADS
            cpu_thread_limit = "auto"
            hw_accel_type = "auto"
            allocated_cpu_cores = max(1, (os.cpu_count() or 2) // 2)
            allocated_ram_mb = 2048
            gpu_enabled = False

            if sys_settings and len(sys_settings) > 0:
                transcoding_enabled = sys_settings[0].get("transcoding_enabled", False)
                max_threads = sys_settings[0].get("max_transcode_threads", settings.MAX_TRANSCODE_THREADS)
                cpu_thread_limit = sys_settings[0].get("cpu_thread_limit", "auto")
                hw_accel_type = sys_settings[0].get("hw_accel_type", "auto")
                if sys_settings[0].get("allocated_cpu_cores"):
                    allocated_cpu_cores = int(sys_settings[0]["allocated_cpu_cores"])
                if sys_settings[0].get("allocated_ram_mb"):
                    allocated_ram_mb = int(sys_settings[0]["allocated_ram_mb"])
                gpu_enabled = bool(sys_settings[0].get("gpu_enabled", False))

            def update_progress(pct: float, stage: str):
                self.current_progress_pct = pct
                self.current_stage = stage

            # 4. Execute Mode A or Mode B
            if transcoding_enabled:
                logger.info(f"Mode A: Multi-bitrate ABR transcoding enabled for {job.title} (Allocated: {allocated_cpu_cores} cores, {allocated_ram_mb}MB RAM, GPU: {gpu_enabled})")
            else:
                logger.info(f"Mode B: Hardware-agnostic lossless remuxing for {job.title} (Allocated: {allocated_cpu_cores} cores, {allocated_ram_mb}MB RAM, GPU: {gpu_enabled})")

            result = await run_lossless_remux(
                input_file=str(input_path),
                output_dir=output_dir,
                probe=probe,
                custom_audio_tracks=job.custom_audio_tracks,
                custom_subtitle_tracks=job.custom_subtitle_tracks,
                force_video_reencode=getattr(job, "force_video_reencode", False),
                cpu_thread_limit=cpu_thread_limit,
                hw_accel_type=hw_accel_type,
                allocated_cpu_cores=allocated_cpu_cores,
                allocated_ram_mb=allocated_ram_mb,
                gpu_enabled=gpu_enabled,
                transcoding_enabled=transcoding_enabled,
                progress_callback=update_progress
            )

            if self.cancel_requested:
                logger.warning(f"Job {job.job_id} ({job.title}) was cancelled. Skipping ready publish.")
                self._cleanup_incomplete_renditions(output_dir)
                return

            # 5. Save stream record and publish item
            if probe and probe.duration_seconds > 0:
                result["duration_seconds"] = probe.duration_seconds
            await self._mark_ready(job, result)
            logger.info(f"Job {job.job_id} successfully completed. Title '{job.title}' is now PUBLISHED.")

        except Exception as e:
            if self.cancel_requested or "cancelled" in str(e).lower():
                logger.warning(f"Job {job.job_id} cancel confirmed. Cleaning partial segments...")
                self._cleanup_incomplete_renditions(output_dir)
                self.current_stage = "Cancelled by Admin"
                self.current_progress_pct = 0.0
                return

            logger.exception(f"Job {job.job_id} failed: {e}")
            self._cleanup_incomplete_renditions(output_dir)
            master_file = output_dir / "master.m3u8"
            if master_file.exists():
                try:
                    master_file.unlink()
                except Exception:
                    pass
            await self._mark_failed(job, str(e))

    async def _update_stream_status(self, job: TranscodeJob, status: str):
        match_col = "episode_id" if job.episode_id else "media_id"
        match_val = job.episode_id or job.media_id
        if match_val:
            res = await db.update("media_streams", match_col, match_val, {"processing_status": status})
            if not res:
                existing = await db.select("media_streams", {match_col: f"eq.{match_val}"})
                if not existing:
                    await db.insert("media_streams", {match_col: match_val, "processing_status": status})

    async def _mark_ready(self, job: TranscodeJob, result: Dict[str, Any]):
        match_col = "episode_id" if job.episode_id else "media_id"
        match_val = job.episode_id or job.media_id
        
        audio_tracks = result["audio_tracks"] if "audio_tracks" in result else (job.custom_audio_tracks or [])
        subtitle_tracks = result["subtitle_tracks"] if "subtitle_tracks" in result else (job.custom_subtitle_tracks or [])

        # Upsert media stream record
        stream_payload = {
            "master_playlist_path": result["master_playlist_path"],
            "is_transcoded": result["is_transcoded"],
            "available_qualities": result["available_qualities"],
            "audio_tracks": audio_tracks,
            "subtitle_tracks": subtitle_tracks,
            "file_size_bytes": result["file_size_bytes"],
            "processing_status": "ready"
        }
        exact_duration_sec = result.get("duration_seconds")
        if not exact_duration_sec:
            try:
                from ..api.stream import get_hls_playlist_duration
                target_dir = Path(settings.MEDIA_LIBRARY_DIR) / str(job.episode_id or job.media_id)
                dur = get_hls_playlist_duration(target_dir)
                if dur > 0:
                    exact_duration_sec = dur
            except Exception:
                pass

        if job.episode_id:
            stream_payload["episode_id"] = job.episode_id
            res = await db.update("media_streams", "episode_id", job.episode_id, stream_payload)
            if not res:
                existing = await db.select("media_streams", {"episode_id": f"eq.{job.episode_id}"})
                if not existing:
                    await db.insert("media_streams", stream_payload)
            # Publish episode and ensure exact duration_seconds is saved
            ep_update = {"is_published": True}
            if exact_duration_sec and exact_duration_sec > 0:
                ep_update["duration_seconds"] = int(round(exact_duration_sec))
            await db.update("episodes", "id", job.episode_id, ep_update)

            # Automatically publish parent series media_item so the series is visible in the UI
            parent_id = getattr(job, "parent_media_id", None) or job.media_id
            if not parent_id:
                ep_rows = await db.select("episodes", {"id": f"eq.{job.episode_id}"})
                if ep_rows and ep_rows[0].get("media_id"):
                    parent_id = ep_rows[0]["media_id"]
            if parent_id:
                await db.update("media_items", "id", parent_id, {"is_published": True})
                logger.info(f"Published parent series media_item {parent_id} for ready episode {job.episode_id}")
        elif job.media_id:
            stream_payload["media_id"] = job.media_id
            res = await db.update("media_streams", "media_id", job.media_id, stream_payload)
            if not res:
                existing = await db.select("media_streams", {"media_id": f"eq.{job.media_id}"})
                if not existing:
                    await db.insert("media_streams", stream_payload)
            # Publish media item and ensure exact runtime_minutes is saved
            media_update = {"is_published": True}
            if exact_duration_sec and exact_duration_sec > 0:
                media_update["runtime_minutes"] = max(1, int(round(exact_duration_sec / 60.0)))
            await db.update("media_items", "id", job.media_id, media_update)

        try:
            from ..api.metadata import invalidate_catalog_cache
            invalidate_catalog_cache()
        except Exception:
            pass

    async def _mark_failed(self, job: TranscodeJob, reason: str):
        match_col = "episode_id" if job.episode_id else "media_id"
        match_val = job.episode_id or job.media_id
        if match_val:
            res = await db.update("media_streams", match_col, match_val, {"processing_status": "failed"})
            if not res:
                existing = await db.select("media_streams", {match_col: f"eq.{match_val}"})
                if not existing:
                    await db.insert("media_streams", {match_col: match_val, "processing_status": "failed"})

        if job.episode_id:
            await db.update("episodes", "id", job.episode_id, {"is_published": False})
            parent_id = getattr(job, "parent_media_id", None) or job.media_id
            if not parent_id:
                ep_rows = await db.select("episodes", {"id": f"eq.{job.episode_id}"})
                if ep_rows and ep_rows[0].get("media_id"):
                    parent_id = ep_rows[0]["media_id"]
            if parent_id:
                other_eps = await db.select("episodes", {"media_id": f"eq.{parent_id}", "is_published": "eq.true"})
                if not other_eps:
                    await db.update("media_items", "id", parent_id, {"is_published": False})
        elif job.media_id:
            await db.update("media_items", "id", job.media_id, {"is_published": False})

        try:
            from ..api.metadata import invalidate_catalog_cache
            invalidate_catalog_cache()
        except Exception:
            pass

transcode_queue = TranscodeQueueManager()
