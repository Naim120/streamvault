import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# In-memory probe cache keyed by resolved file path -> (mtime, size_bytes, MediaProbeResult)
_PROBE_CACHE: Dict[str, Tuple[float, int, 'MediaProbeResult']] = {}

class MediaProbeResult:
    def __init__(self, raw: Dict[str, Any]):
        self.raw = raw
        self.format_info = raw.get("format", {})
        self.streams = raw.get("streams", [])
        
        # Duration & Size
        self.duration_seconds = float(self.format_info.get("duration", 0.0) or 0.0)
        self.size_bytes = int(self.format_info.get("size", 0) or 0)
        self.bitrate = int(self.format_info.get("bit_rate", 0) or 0)
        
        # Categorized Streams
        self.video_stream: Optional[Dict[str, Any]] = None
        self.audio_streams: List[Dict[str, Any]] = []
        self.subtitle_streams: List[Dict[str, Any]] = []
        
        for s in self.streams:
            codec_type = s.get("codec_type")
            is_attached_pic = bool((s.get("disposition") or {}).get("attached_pic"))
            if codec_type == "video" and not is_attached_pic and not self.video_stream:
                self.video_stream = s
            elif codec_type == "audio":
                self.audio_streams.append(s)
            elif codec_type == "subtitle":
                self.subtitle_streams.append(s)

    @property
    def width(self) -> int:
        return int(self.video_stream.get("width", 1920)) if self.video_stream else 1920

    @property
    def height(self) -> int:
        return int(self.video_stream.get("height", 1080)) if self.video_stream else 1080

    @property
    def video_codec(self) -> str:
        return self.video_stream.get("codec_name", "h264") if self.video_stream else "unknown"

    def format_audio_tracks(self) -> List[Dict[str, Any]]:
        tracks = []
        for idx, a in enumerate(self.audio_streams):
            tags = a.get("tags", {})
            lang = tags.get("language") or tags.get("lang") or f"track_{idx + 1}"
            title = tags.get("title") or tags.get("handler_name") or f"Audio Track {idx + 1} ({lang})"
            tracks.append({
                "index": a.get("index", idx),
                "stream_idx": idx,
                "lang": lang,
                "label": title,
                "codec": a.get("codec_name", "aac"),
                "channels": int(a.get("channels", 2))
            })
        return tracks

    def format_subtitle_tracks(self, vtt_dir_name: str = "") -> List[Dict[str, Any]]:
        tracks = []
        for idx, sub in enumerate(self.subtitle_streams):
            tags = sub.get("tags", {})
            lang = tags.get("language") or tags.get("lang") or f"sub_{idx + 1}"
            title = tags.get("title") or f"Subtitle {idx + 1} ({lang})"
            tracks.append({
                "index": sub.get("index", idx),
                "stream_idx": idx,
                "lang": lang,
                "label": title,
                "vtt_filename": f"sub_{idx}_{lang}.vtt",
                "codec": sub.get("codec_name", "subrip")
            })
        return tracks

def probe_file(file_path: str) -> Optional[MediaProbeResult]:
    """
    Executes ffprobe on input video file and returns a structured MediaProbeResult.
    Caches results by (path, mtime, size) to eliminate redundant disk I/O and process spawns.
    """
    path = Path(file_path).resolve()
    if not path.is_file():
        logger.error(f"Cannot probe non-existent file: {file_path}")
        return None

    try:
        st = path.stat()
        cache_key = str(path)
        if cache_key in _PROBE_CACHE:
            cached_mtime, cached_size, cached_probe = _PROBE_CACHE[cache_key]
            if cached_mtime == st.st_mtime and cached_size == st.st_size:
                return cached_probe
    except Exception:
        st = None

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path)
    ]

    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        if res.returncode != 0:
            logger.error(f"ffprobe failed for {file_path}: {res.stderr}")
            return None
        data = json.loads(res.stdout)
        result = MediaProbeResult(data)
        if st is not None:
            try:
                _PROBE_CACHE[str(path)] = (st.st_mtime, st.st_size, result)
            except Exception:
                pass
        return result
    except Exception as e:
        logger.exception(f"Exception while running ffprobe on {file_path}: {e}")
        return None


async def probe_file_async(file_path: str) -> Optional[MediaProbeResult]:
    """
    Asynchronously executes probe_file off the main asyncio event loop thread via asyncio.to_thread.
    Prevents event-loop starvation when probing video files.
    """
    return await asyncio.to_thread(probe_file, file_path)
