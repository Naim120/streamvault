import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from .probe import MediaProbeResult
from ..config import settings

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
BRANDED_BIN = BASE_DIR / ".bin" / "streamvault-transcoder"
FFMPEG_BIN = str(BRANDED_BIN) if BRANDED_BIN.exists() else "ffmpeg"

# ISO-639 / Common Language Normalization
LANGUAGE_MAP: Dict[str, tuple[str, str]] = {
    "hin": ("Hindi", "hi"),
    "hi": ("Hindi", "hi"),
    "hindi": ("Hindi", "hi"),
    "eng": ("English", "en"),
    "en": ("English", "en"),
    "english": ("English", "en"),
    "spa": ("Spanish", "es"),
    "es": ("Spanish", "es"),
    "spanish": ("Spanish", "es"),
    "fre": ("French", "fr"),
    "fra": ("French", "fr"),
    "fr": ("French", "fr"),
    "french": ("French", "fr"),
    "ger": ("German", "de"),
    "deu": ("German", "de"),
    "de": ("German", "de"),
    "german": ("German", "de"),
    "jpn": ("Japanese", "ja"),
    "ja": ("Japanese", "ja"),
    "japanese": ("Japanese", "ja"),
    "tam": ("Tamil", "ta"),
    "ta": ("Tamil", "ta"),
    "tamil": ("Tamil", "ta"),
    "tel": ("Telugu", "te"),
    "te": ("Telugu", "te"),
    "telugu": ("Telugu", "te"),
    "kor": ("Korean", "ko"),
    "ko": ("Korean", "ko"),
    "korean": ("Korean", "ko"),
    "ita": ("Italian", "it"),
    "it": ("Italian", "it"),
    "italian": ("Italian", "it"),
    "rus": ("Russian", "ru"),
    "ru": ("Russian", "ru"),
    "russian": ("Russian", "ru"),
    "por": ("Portuguese", "pt"),
    "pt": ("Portuguese", "pt"),
    "portuguese": ("Portuguese", "pt"),
    "chi": ("Chinese", "zh"),
    "zho": ("Chinese", "zh"),
    "zh": ("Chinese", "zh"),
    "chinese": ("Chinese", "zh"),
    "mandarin": ("Chinese", "zh"),
    "cmn": ("Chinese", "zh"),
    "ind": ("Indonesian", "id"),
    "id": ("Indonesian", "id"),
    "indonesian": ("Indonesian", "id"),
    "rum": ("Romanian", "ro"),
    "ron": ("Romanian", "ro"),
    "ro": ("Romanian", "ro"),
    "romanian": ("Romanian", "ro"),
    "ara": ("Arabic", "ar"),
    "ar": ("Arabic", "ar"),
    "arabic": ("Arabic", "ar"),
    "ben": ("Bengali", "bn"),
    "bn": ("Bengali", "bn"),
    "bengali": ("Bengali", "bn"),
    "pan": ("Punjabi", "pa"),
    "pa": ("Punjabi", "pa"),
    "punjabi": ("Punjabi", "pa"),
    "mal": ("Malayalam", "ml"),
    "ml": ("Malayalam", "ml"),
    "malayalam": ("Malayalam", "ml"),
    "kan": ("Kannada", "kn"),
    "kn": ("Kannada", "kn"),
    "kannada": ("Kannada", "kn"),
    "mar": ("Marathi", "mr"),
    "mr": ("Marathi", "mr"),
    "marathi": ("Marathi", "mr"),
    "guj": ("Gujarati", "gu"),
    "gu": ("Gujarati", "gu"),
    "gujarati": ("Gujarati", "gu"),
}

TEXT_SUB_CODECS = {"subrip", "ass", "ssa", "mov_text", "webvtt", "srt"}

def sanitize_track_label(raw_label: str | None, fallback: str = "Audio") -> str:
    if not raw_label:
        return fallback
    cleaned = re.sub(r'<[^>]*>', '', str(raw_label))
    cleaned = re.sub(r'["\'<>\\\r\n\t]', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:64] if cleaned else fallback

def normalize_language(raw_lang: Optional[str], default_label: str = "Audio", default_code: str = "und") -> tuple[str, str]:
    if not raw_lang:
        return default_label, default_code
    clean = raw_lang.strip().lower()
    if clean in ("und", "undefined", "unknown", "none"):
        return default_label, default_code
    if clean in LANGUAGE_MAP:
        return LANGUAGE_MAP[clean]
    # Check prefixes or capitalization
    for k, v in LANGUAGE_MAP.items():
        if clean.startswith(k) or k.startswith(clean):
            return v
    return raw_lang.capitalize(), clean[:2]

def detect_stream_language(stream_dict: Dict[str, Any], fallback_label: str = "Audio", default_code: str = "und") -> tuple[str, str]:
    """
    Detects language label and ISO 639-1 code from stream tags (language tag or title/handler_name).
    """
    tags = stream_dict.get("tags") or {}
    raw_lang = tags.get("language") or tags.get("lang") or ""
    raw_title = tags.get("title") or tags.get("handler_name") or ""

    # 1. Try language tag first
    norm_label, norm_code = normalize_language(raw_lang, default_label="", default_code="")
    if norm_code and norm_code != "und":
        return norm_label, norm_code

    # 2. Try raw_title or handler_name
    clean_title = sanitize_track_label(raw_title, fallback="").lower()
    clean_raw_lang = raw_lang.lower()
    for k, (lbl, code) in LANGUAGE_MAP.items():
        if len(k) >= 3 and (k in clean_title or k in clean_raw_lang):
            return lbl, code

    return fallback_label, (norm_code or default_code)

LEGITIMATE_QUALIFIERS_PATTERN = re.compile(
    r'\b(sdh|forced|cc|closed\s+captions?|signs?(?:\s*&\s*songs?|\s+and\s+songs?)?|songs?|commentary|original|director(?:\'?s)?(?:\s+cut)?)\b',
    re.IGNORECASE
)

def build_smart_track_label(raw_title: Optional[str], norm_label: str) -> str:
    """
    Builds clean default track label for container subtitle/audio tracks.
    Defaults to normalized language name (e.g. 'English'), only appending a qualifier
    if raw_title contains a legitimate token (SDH, Forced, CC, etc.), ignoring release slogans.
    """
    if not norm_label:
        norm_label = "English"
    if not raw_title:
        return norm_label

    cleaned = sanitize_track_label(raw_title, fallback="")
    if not cleaned:
        return norm_label

    match = LEGITIMATE_QUALIFIERS_PATTERN.search(cleaned)
    if match:
        qualifier = match.group(0).strip()
        q_upper = qualifier.upper()
        if q_upper in ("SDH", "CC"):
            qual_display = q_upper
        elif "sign" in qualifier.lower():
            qual_display = "Signs & Songs"
        elif "force" in qualifier.lower():
            qual_display = "Forced"
        elif "comment" in qualifier.lower():
            qual_display = "Commentary"
        elif "director" in qualifier.lower():
            qual_display = "Director's Cut"
        else:
            qual_display = qualifier.title()

        if qual_display.lower() in norm_label.lower():
            return norm_label
        return f"{norm_label} [{qual_display}]"

    return norm_label

def convert_subtitle_bytes_to_vtt(raw_bytes: bytes) -> str:
    """
    Deterministic pure-Python SRT/VTT converter that handles various encodings
    and transforms SRT timestamps to WebVTT format.
    """
    text = ""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except Exception:
            continue
    if not text:
        text = raw_bytes.decode("utf-8", errors="replace")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if text.startswith("WEBVTT"):
        return text + "\n"
    # Convert SRT timestamps (00:01:20,500 --> 00:01:23,200) to WebVTT (00:01:20.500 --> 00:01:23.200)
    text = re.sub(
        r"(\d{1,2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}),(\d{3})",
        r"\1.\2 --> \3.\4",
        text
    )
    return "WEBVTT\n\n" + text + "\n"

def extract_one_embedded_subtitle(input_file: str, raw_stream_idx: int, output_vtt: Path) -> bool:
    """
    Directly extracts one embedded container subtitle to WebVTT using FFmpeg.
    Returns True if output_vtt was created with size > 0.
    """
    try:
        output_vtt.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run([
            FFMPEG_BIN, "-y",
            "-i", str(input_file),
            "-map", f"0:{raw_stream_idx}",
            "-c:s", "webvtt",
            str(output_vtt)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        if res.returncode == 0 and output_vtt.is_file() and output_vtt.stat().st_size > 0:
            return True
        return False
    except Exception as e:
        logger.warning(f"Failed to extract embedded subtitle stream {raw_stream_idx}: {e}")
        return False

async def check_nvenc_available() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
            "-vf", "format=yuv420p", "-c:v", "h264_nvenc", "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        return await asyncio.wait_for(proc.wait(), timeout=5.0) == 0
    except Exception:
        return False

async def check_vaapi_available() -> bool:
    if not os.path.exists("/dev/dri/renderD128"):
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
            "-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload",
            "-c:v", "h264_vaapi", "-f", "null", "-",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        return await asyncio.wait_for(proc.wait(), timeout=5.0) == 0
    except Exception:
        return False

def get_low_priority_prefix() -> List[str]:
    prefix = []
    if shutil.which("nice"):
        prefix.extend(["nice", "-n", "19"])
    if shutil.which("ionice"):
        prefix.extend(["ionice", "-c", "3"])
    return prefix

async def check_rendition_ready(rendition_dir: Path) -> bool:
    """
    Checks if a video rendition directory (e.g. video/, video_720p/, video_480p/)
    contains a valid playlist.m3u8 with #EXT-X-ENDLIST, seg_0000.ts > 0 bytes,
    and is 8-bit h264 (yuv420p).
    """
    playlist_path = rendition_dir / "playlist.m3u8"
    first_seg_path = rendition_dir / "seg_0000.ts"

    if not playlist_path.is_file() or not first_seg_path.is_file():
        return False

    if first_seg_path.stat().st_size == 0:
        return False

    try:
        content = playlist_path.read_text(encoding="utf-8", errors="ignore")
        if "#EXT-X-ENDLIST" not in content:
            return False
    except Exception:
        return False

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt",
            "-of", "json", str(first_seg_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            proc.kill()
            return False
        if proc.returncode == 0 and stdout:
            data = json.loads(stdout.decode("utf-8", errors="ignore"))
            streams = data.get("streams", [])
            if streams:
                codec = streams[0].get("codec_name", "").lower()
                pix_fmt = streams[0].get("pix_fmt", "").lower()
                if codec == "h264" and pix_fmt == "yuv420p":
                    return True
    except Exception as e:
        logger.warning(f"Error checking rendition {rendition_dir} with ffprobe: {e}")
        return False

    return False


async def is_existing_video_ready(
    target_dir: Path,
    transcoding_enabled: bool = False,
    source_height: int = 1080,
    source_width: int = 1920
) -> tuple[bool, Dict[str, bool]]:
    """
    Checks video readiness based on mode:
    - When transcoding_enabled == False (Mode B — Lossless Remux):
      Requires video/playlist.m3u8 with #EXT-X-ENDLIST and 8-bit h264 yuv420p.
    - When transcoding_enabled == True (Mode A — Multi-Quality ABR):
      If 1080p class (height >= 1080 or width >= 1920): requires video/ (1080p), video_720p/ (720p), video_480p/ (480p)
      If 720p class (height >= 720 or width >= 1280): requires video/ (720p), video_480p/ (480p)
      If < 720p: requires video/ (480p)
    Returns: (is_all_ready, ready_dict)
    """
    output_dir = target_dir.parent if target_dir.name in ("video", "video_720p", "video_480p") else target_dir

    video_dir = output_dir / "video"
    video_720p_dir = output_dir / "video_720p"
    video_480p_dir = output_dir / "video_480p"

    is_1080p_ready = await check_rendition_ready(video_dir)

    if not transcoding_enabled:
        return is_1080p_ready, {"1080p": is_1080p_ready, "720p": False, "480p": False}

    is_720p_ready = await check_rendition_ready(video_720p_dir)
    is_480p_ready = await check_rendition_ready(video_480p_dir)

    ready_dict = {
        "1080p": is_1080p_ready,
        "720p": is_720p_ready,
        "480p": is_480p_ready
    }

    is_1080p_class = (source_height >= 1080 or source_width >= 1920)
    is_720p_class = (source_height >= 720 or source_width >= 1280)

    if is_1080p_class:
        all_ready = is_1080p_ready and is_720p_ready and is_480p_ready
    elif is_720p_class:
        all_ready = is_1080p_ready and is_480p_ready
    else:
        all_ready = is_1080p_ready

    return all_ready, ready_dict


def save_stream_map(output_dir: Path, formatted_audio: List[Dict[str, Any]], formatted_subs: List[Dict[str, Any]]):
    stream_map = {
        "audio_map": [
            {
                "id": a["id"],
                "raw_stream_idx": a["raw_stream_idx"],
                "is_default": bool(a.get("is_default", False)),
                "label": a.get("label", "")
            }
            for a in formatted_audio
        ],
        "sub_map": [{"id": s["id"], "raw_stream_idx": s["raw_stream_idx"], "vtt": s["vtt_filename"]} for s in formatted_subs]
    }
    map_file = output_dir / ".stream_map.json"
    try:
        map_file.write_text(json.dumps(stream_map, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Could not write .stream_map.json: {e}")


def check_audio_ready(output_dir: Path, formatted_audio: List[Dict[str, Any]]) -> bool:
    if not formatted_audio:
        return False

    map_file = output_dir / ".stream_map.json"
    if not map_file.is_file():
        return False
    try:
        data = json.loads(map_file.read_text(encoding="utf-8"))
        saved_audio = data.get("audio_map", [])
        expected_audio = [
            {
                "id": a["id"],
                "raw_stream_idx": a["raw_stream_idx"],
                "is_default": bool(a.get("is_default", False)),
                "label": a.get("label", "")
            }
            for a in formatted_audio
        ]
        if saved_audio != expected_audio:
            return False
    except Exception:
        return False

    for a in formatted_audio:
        a_dir = output_dir / a["id"]
        pl = a_dir / "playlist.m3u8"
        seg = a_dir / "seg_0000.ts"
        if not pl.is_file() or not seg.is_file() or seg.stat().st_size == 0:
            return False
        try:
            content = pl.read_text(encoding="utf-8", errors="ignore")
            if "#EXT-X-ENDLIST" not in content:
                return False
        except Exception:
            return False
    return True


def check_subs_ready(output_dir: Path, formatted_subs: List[Dict[str, Any]]) -> bool:
    subs_dir = output_dir / "subs"
    if not formatted_subs:
        existing_vtts = list(subs_dir.glob("*.vtt"))
        return len(existing_vtts) == 0

    map_file = output_dir / ".stream_map.json"
    if not map_file.is_file():
        return False
    try:
        data = json.loads(map_file.read_text(encoding="utf-8"))
        saved_subs = data.get("sub_map", [])
        expected_subs = [{"id": s["id"], "raw_stream_idx": s["raw_stream_idx"], "vtt": s["vtt_filename"]} for s in formatted_subs]
        if saved_subs != expected_subs:
            return False
    except Exception:
        return False

    for s in formatted_subs:
        vtt = subs_dir / s["vtt_filename"]
        if not vtt.is_file() or vtt.stat().st_size == 0:
            return False
    return True


def cleanup_rendition(rendition_dir: Path):
    if not rendition_dir.exists():
        return
    for ts in rendition_dir.glob("seg_*.ts"):
        try:
            ts.unlink()
        except Exception:
            pass
    pl = rendition_dir / "playlist.m3u8"
    if pl.exists():
        try:
            pl.unlink()
        except Exception:
            pass


async def extract_subtitles_async(
    input_file: str,
    output_dir: Path,
    subtitle_tracks: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Extracts text subtitles to WebVTT format into output_dir / 'subs'.
    Ignores bitmap subtitle codecs (hdmv_pgs_subtitle, dvd_subtitle).
    """
    subs_dir = output_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for s in subtitle_tracks:
        codec = s.get("codec", "").lower()
        if codec and codec not in TEXT_SUB_CODECS:
            continue
        stream_idx = s.get("index", s.get("stream_idx", 0))
        lang = s.get("lang", "und")
        clean_lang, lang_code = normalize_language(lang, "Subtitle", "und")
        vtt_filename = f"sub_{len(results)}_{lang_code}.vtt"
        vtt_path = subs_dir / vtt_filename

        cmd = [
            "ffmpeg", "-y", "-i", str(input_file),
            "-map", f"0:{stream_idx}",
            "-vn", "-an",
            "-c:s", "webvtt",
            str(vtt_path)
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            if proc.returncode == 0 and vtt_path.exists() and vtt_path.stat().st_size > 0:
                results.append({
                    "id": f"sub_{len(results)}",
                    "lang": lang_code,
                    "label": sanitize_track_label(s.get("label"), fallback=clean_lang),
                    "vtt_path": f"subs/{vtt_filename}",
                    "url": f"subs/{vtt_filename}"
                })
        except Exception as e:
            logger.warning(f"Failed to extract subtitle track {stream_idx}: {e}")

    return results


def build_consolidated_ffmpeg_cmd(
    input_file: str,
    output_dir: Path,
    v_stream_idx: int,
    tier: str,
    gpu_encoder: Optional[str],
    cpu_threads: int,
    formatted_audio: List[Dict[str, Any]],
    formatted_subs: List[Dict[str, Any]],
    need_1080p: bool = False,
    need_720p: bool = False,
    need_480p: bool = False,
    need_audio: bool = False,
    need_subs: bool = False,
    allocated_ram_mb: int = 2048,
    is_native_8bit_h264: bool = False,
    force_video_reencode: bool = False,
    native_maxrate: str = "4500k",
    native_bufsize: str = "9000k"
) -> List[str]:
    """
    Builds the consolidated, low-priority FFmpeg command for HLS packaging.
    Enforces strict decoder, filter, and encoder thread limits on CPU-safe paths.
    Always stream-copies native 8-bit H.264 for primary native rung (0% Video CPU).
    Selectively outputs only the streams/renditions that are not already ready on disk.
    """
    cmd_prefix = get_low_priority_prefix()
    cmd: List[str] = [*cmd_prefix, FFMPEG_BIN, "-y"]

    if gpu_encoder == "h264_vaapi":
        cmd.extend(["-vaapi_device", "/dev/dri/renderD128"])

    primary_reencodes_on_cpu = bool(need_1080p and not (is_native_8bit_h264 and not force_video_reencode) and tier == "tier_b")
    any_video_reencode_on_cpu = (
        primary_reencodes_on_cpu or
        (need_720p and tier != "tier_c") or
        (need_480p and tier != "tier_c")
    )

    num_cpu_rungs = sum([
        primary_reencodes_on_cpu,
        (need_720p and tier != "tier_c"),
        (need_480p and tier != "tier_c")
    ])
    per_rung_threads = max(1, cpu_threads // max(1, num_cpu_rungs))

    if any_video_reencode_on_cpu:
        cmd.extend(["-threads", str(cpu_threads)])

    cmd.extend(["-i", str(input_file)])
    cmd.extend(["-map_metadata", "-1"])

    # Scale max muxing queue size proportionally with allocated RAM
    queue_size = 2048 if allocated_ram_mb >= 2048 else (1024 if allocated_ram_mb >= 1024 else 512)
    cmd.extend(["-max_muxing_queue_size", str(queue_size)])

    if any_video_reencode_on_cpu:
        cmd.extend(["-filter_threads", "1", "-filter_complex_threads", "1"])

    # 1. Output Primary Native Rung (video/playlist.m3u8)
    if need_1080p:
        v_primary_dir = output_dir / "video"
        v_primary_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["-map", f"0:{v_stream_idx}"])
        if is_native_8bit_h264 and not force_video_reencode:
            # ALWAYS stream copy 8-bit H.264 for native rung (0% CPU)
            cmd.extend(["-c:v", "copy", "-bsf:v", "h264_mp4toannexb"])
        elif tier == "tier_c":
            if gpu_encoder == "h264_nvenc":
                cmd.extend([
                    "-vf", "format=yuv420p",
                    "-c:v", "h264_nvenc",
                    "-preset", "p1",
                    "-tune", "ll",
                    "-rc", "vbr",
                    "-cq", "23",
                    "-maxrate", native_maxrate,
                    "-bufsize", native_bufsize,
                    "-g", "96",
                    "-keyint_min", "96"
                ])
            elif gpu_encoder == "h264_vaapi":
                cmd.extend([
                    "-vf", "format=nv12,hwupload",
                    "-c:v", "h264_vaapi",
                    "-maxrate", native_maxrate,
                    "-bufsize", native_bufsize,
                    "-g", "96",
                    "-keyint_min", "96"
                ])
        elif tier == "tier_b":
            cmd.extend([
                "-vf", "format=yuv420p",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "fastdecode",
                "-x264opts", "rc-lookahead=0:bframes=0:ref=1:scenecut=0:me=dia:subme=0:trellis=0",
                "-crf", "23",
                "-maxrate", native_maxrate,
                "-bufsize", native_bufsize,
                "-threads", str(per_rung_threads),
                "-g", "96",
                "-keyint_min", "96",
                "-sc_threshold", "0"
            ])

        cmd.extend([
            "-metadata", "encoded_by=StreamVault Media Engine v2.5",
            "-an", "-sn",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_playlist_type", "vod",
            "-hls_segment_type", "mpegts",
            "-hls_flags", "independent_segments",
            "-hls_segment_filename", str(v_primary_dir / "seg_%04d.ts"),
            str(v_primary_dir / "playlist.m3u8")
        ])

    # 2. Output 720p (video_720p/playlist.m3u8)
    if need_720p:
        v720_dir = output_dir / "video_720p"
        v720_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["-map", f"0:{v_stream_idx}"])
        buf_720 = f"{min(allocated_ram_mb * 2, 5600)}k"
        if tier == "tier_c" and gpu_encoder == "h264_nvenc":
            cmd.extend([
                "-vf", "scale=-2:720,format=yuv420p",
                "-c:v", "h264_nvenc",
                "-preset", "p1",
                "-tune", "ll",
                "-rc", "vbr",
                "-cq", "24",
                "-maxrate", "2800k",
                "-bufsize", buf_720,
                "-g", "96",
                "-keyint_min", "96"
            ])
        elif tier == "tier_c" and gpu_encoder == "h264_vaapi":
            cmd.extend([
                "-vf", "scale=-2:720,format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-maxrate", "2800k",
                "-bufsize", buf_720,
                "-g", "96",
                "-keyint_min", "96"
            ])
        else:
            cmd.extend([
                "-vf", "scale=-2:720:flags=fast_bilinear,format=yuv420p",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "fastdecode",
                "-x264opts", "rc-lookahead=0:bframes=0:ref=1:scenecut=0:me=dia:subme=0:trellis=0",
                "-crf", "24",
                "-maxrate", "2800k",
                "-bufsize", buf_720,
                "-threads", str(per_rung_threads),
                "-g", "96",
                "-keyint_min", "96",
                "-sc_threshold", "0"
            ])

        cmd.extend([
            "-metadata", "encoded_by=StreamVault Media Engine v2.5",
            "-an", "-sn",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_playlist_type", "vod",
            "-hls_segment_type", "mpegts",
            "-hls_flags", "independent_segments",
            "-hls_segment_filename", str(v720_dir / "seg_%04d.ts"),
            str(v720_dir / "playlist.m3u8")
        ])

    # 3. Output 480p (video_480p/playlist.m3u8)
    if need_480p:
        v480_dir = output_dir / "video_480p"
        v480_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend(["-map", f"0:{v_stream_idx}"])
        buf_480 = f"{min(allocated_ram_mb, 2800)}k"
        if tier == "tier_c" and gpu_encoder == "h264_nvenc":
            cmd.extend([
                "-vf", "scale=-2:480,format=yuv420p",
                "-c:v", "h264_nvenc",
                "-preset", "p1",
                "-tune", "ll",
                "-rc", "vbr",
                "-cq", "25",
                "-maxrate", "1400k",
                "-bufsize", buf_480,
                "-g", "96",
                "-keyint_min", "96"
            ])
        elif tier == "tier_c" and gpu_encoder == "h264_vaapi":
            cmd.extend([
                "-vf", "scale=-2:480,format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-maxrate", "1400k",
                "-bufsize", buf_480,
                "-g", "96",
                "-keyint_min", "96"
            ])
        else:
            cmd.extend([
                "-vf", "scale=-2:480:flags=fast_bilinear,format=yuv420p",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "fastdecode",
                "-x264opts", "rc-lookahead=0:bframes=0:ref=1:scenecut=0:me=dia:subme=0:trellis=0",
                "-crf", "25",
                "-maxrate", "1400k",
                "-bufsize", buf_480,
                "-threads", str(per_rung_threads),
                "-g", "96",
                "-keyint_min", "96",
                "-sc_threshold", "0"
            ])

        cmd.extend([
            "-metadata", "encoded_by=StreamVault Media Engine v2.5",
            "-an", "-sn",
            "-f", "hls",
            "-hls_time", "4",
            "-hls_list_size", "0",
            "-hls_playlist_type", "vod",
            "-hls_segment_type", "mpegts",
            "-hls_flags", "independent_segments",
            "-hls_segment_filename", str(v480_dir / "seg_%04d.ts"),
            str(v480_dir / "playlist.m3u8")
        ])

    # 4. Audio Outputs (if needed)
    if need_audio:
        for i, a in enumerate(formatted_audio):
            audio_dir = output_dir / f"audio_{i}"
            audio_dir.mkdir(parents=True, exist_ok=True)
            clean_audio_label = sanitize_track_label(a.get("label"), fallback=f"Audio Track {i + 1}")
            cmd.extend([
                "-map", f"0:{a['raw_stream_idx']}",
                "-vn", "-sn",
                "-c:a", "aac",
                "-threads", "1",
                "-ac", "2",
                "-b:a", "192k",
                "-ar", "48000",
                "-metadata", "encoded_by=StreamVault Media Engine v2.5",
                "-metadata:s:a:0", f"title={clean_audio_label}",
                "-f", "hls",
                "-hls_time", "4",
                "-hls_list_size", "0",
                "-hls_playlist_type", "vod",
                "-hls_segment_type", "mpegts",
                "-hls_flags", "independent_segments",
                "-hls_segment_filename", str(audio_dir / "seg_%04d.ts"),
                str(audio_dir / "playlist.m3u8")
            ])

    # 5. Subtitle Outputs (if needed)
    if need_subs:
        subs_dir = output_dir / "subs"
        subs_dir.mkdir(parents=True, exist_ok=True)
        for s in formatted_subs:
            if s.get("raw_stream_idx", -1) >= 0:
                cmd.extend([
                    "-map", f"0:{s['raw_stream_idx']}",
                    "-vn", "-an",
                    "-c:s", "webvtt",
                    str(subs_dir / s["vtt_filename"])
                ])

    return cmd


async def run_lossless_remux(
    input_file: str,
    output_dir: Path,
    probe: MediaProbeResult,
    custom_audio_tracks: Optional[List[Any]] = None,
    custom_subtitle_tracks: Optional[List[Any]] = None,
    force_video_reencode: bool = False,
    cpu_thread_limit: str = "auto",
    hw_accel_type: str = "auto",
    transcoding_enabled: bool = False,
    allocated_cpu_cores: Optional[int] = None,
    allocated_ram_mb: Optional[int] = None,
    gpu_enabled: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict[str, Any]:
    """
    Hardware-Agnostic, CPU-Safe Production Pipeline
    - Mode B (transcoding_enabled=False): Single high-quality 8-bit H.264 stream.
    - Mode A (transcoding_enabled=True): Multi-quality ABR (1080p, 720p, 480p) with 1080p stream reuse.
    - CPU Thread Governance (capped to allocated_cpu_cores or <= 2 threads, nice -n 19 ionice -c 3, filter_threads 1).
    - Child Process RAM limit enforced via RLIMIT_AS based on allocated_ram_mb.
    - Smart incremental re-ingest (skips already ready video rungs, audio tracks, and subs).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    video_dir = output_dir / "video"
    video_720p_dir = output_dir / "video_720p"
    video_480p_dir = output_dir / "video_480p"
    subs_dir = output_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)

    # 1. Calculate Governed CPU Threads & RAM Limit
    total_logical = os.cpu_count() or 2
    if allocated_cpu_cores and allocated_cpu_cores > 0:
        cpu_threads = max(1, min(allocated_cpu_cores, total_logical))
    else:
        limit_str = str(cpu_thread_limit).strip().lower()
        if limit_str == "1":
            cpu_threads = 1
        elif limit_str == "2":
            cpu_threads = 2
        else:  # "auto"
            cpu_threads = 1 if total_logical <= 2 else 2

    ram_mb = allocated_ram_mb if (allocated_ram_mb and allocated_ram_mb > 0) else 2048

    # 2. Parse Admin Manual Language Tags & Custom Configurations
    admin_audio_configs: List[Dict[str, Any]] = []
    if custom_audio_tracks:
        for t in custom_audio_tracks:
            if isinstance(t, dict):
                if t.get("included", True) is False:
                    continue
                lbl = t.get("label") or t.get("clean_label") or t.get("lang") or ""
                lng = t.get("lang") or ""
                is_def = bool(t.get("is_default", False))
                raw_idx = t.get("raw_stream_idx")
            else:
                lbl = str(t)
                lng = ""
                is_def = False
                raw_idx = None
            lbl_clean = sanitize_track_label(lbl, fallback="") if lbl else ""
            if lbl_clean and lbl_clean.lower() not in ("none", "und"):
                admin_audio_configs.append({
                    "label": lbl_clean,
                    "lang": lng.strip(),
                    "is_default": is_def,
                    "raw_stream_idx": raw_idx
                })

        # Single-Default Guarantee on admin_audio_configs
        if admin_audio_configs:
            first_def_idx = next((idx for idx, c in enumerate(admin_audio_configs) if c.get("is_default")), -1)
            if first_def_idx == -1:
                first_def_idx = 0
            for idx, c in enumerate(admin_audio_configs):
                c["is_default"] = (idx == first_def_idx)

    # 3. Reconcile Audio Streams (Language-Aware & Stream-Index Accurate Matching)
    raw_audio_streams = [s for s in probe.streams if s.get("codec_type") == "audio"]
    formatted_audio: List[Dict[str, Any]] = []

    detected_audio_streams = []
    for a_idx, a_stream in enumerate(raw_audio_streams):
        norm_label, norm_code = detect_stream_language(
            a_stream,
            fallback_label=f"Audio Track {a_idx + 1}",
            default_code="und"
        )
        detected_audio_streams.append({
            "stream": a_stream,
            "raw_stream_idx": a_stream.get("index", a_idx + 1),
            "norm_label": norm_label,
            "norm_code": norm_code,
            "used": False
        })

    if admin_audio_configs:
        for cfg in admin_audio_configs:
            cfg_code = cfg["lang"].strip().lower() if cfg.get("lang") else ""
            cfg_label = cfg["label"].strip().lower()
            matched = None

            # 0. Match by exact raw_stream_idx if provided
            if cfg.get("raw_stream_idx") is not None:
                for cand in detected_audio_streams:
                    if not cand["used"] and cand["raw_stream_idx"] == cfg["raw_stream_idx"]:
                        matched = cand
                        break

            # 1. Match by language code
            if not matched and cfg_code:
                for cand in detected_audio_streams:
                    if not cand["used"] and cand["norm_code"].lower() == cfg_code:
                        matched = cand
                        break

            # 2. Match by normalized label or title
            if not matched and cfg_label:
                for cand in detected_audio_streams:
                    if not cand["used"] and (cfg_label in cand["norm_label"].lower() or cand["norm_label"].lower() in cfg_label):
                        matched = cand
                        break

            # 3. Fallback to next unused raw audio stream
            if not matched:
                for cand in detected_audio_streams:
                    if not cand["used"]:
                        matched = cand
                        break

            if matched:
                matched["used"] = True
                out_idx = len(formatted_audio)
                clean_lbl = sanitize_track_label(cfg["label"], fallback=matched["norm_label"])
                clean_lang = cfg["lang"] if cfg.get("lang") else (matched["norm_code"] if matched["norm_code"] != "und" else clean_lbl[:2].lower())
                formatted_audio.append({
                    "order": out_idx,
                    "raw_stream_idx": matched["raw_stream_idx"],
                    "id": f"audio_{out_idx}",
                    "index": out_idx,
                    "lang": clean_lang,
                    "label": clean_lbl,
                    "playlist": f"audio_{out_idx}/playlist.m3u8",
                    "url": f"audio_{out_idx}/playlist.m3u8",
                    "codec": "aac",
                    "channels": int(matched["stream"].get("channels", 2)),
                    "is_default": bool(cfg.get("is_default", False))
                })
    else:
        for i, cand in enumerate(detected_audio_streams):
            out_idx = len(formatted_audio)
            a_stream = cand["stream"]
            tags = a_stream.get("tags") or {}
            raw_title = tags.get("title") or tags.get("handler_name") or ""
            clean_title = sanitize_track_label(raw_title, fallback="")
            if clean_title and clean_title.lower() != cand["norm_label"].lower() and not clean_title.lower().startswith("audio"):
                lbl = sanitize_track_label(f"{cand['norm_label']} ({clean_title})", fallback=cand["norm_label"])
            else:
                lbl = cand["norm_label"]

            formatted_audio.append({
                "order": out_idx,
                "raw_stream_idx": cand["raw_stream_idx"],
                "id": f"audio_{out_idx}",
                "index": out_idx,
                "lang": cand["norm_code"],
                "label": lbl,
                "playlist": f"audio_{out_idx}/playlist.m3u8",
                "url": f"audio_{out_idx}/playlist.m3u8",
                "codec": "aac",
                "channels": int(a_stream.get("channels", 2)),
                "is_default": (i == 0)
            })

    if not formatted_audio:
        default_label = sanitize_track_label(admin_audio_configs[0]["label"], fallback="Original Audio") if admin_audio_configs else "Original Audio"
        default_lang = admin_audio_configs[0]["lang"] if admin_audio_configs and admin_audio_configs[0]["lang"] else "en"
        formatted_audio.append({
            "order": 0,
            "raw_stream_idx": 1,
            "id": "audio_0",
            "index": 0,
            "lang": default_lang,
            "label": default_label,
            "playlist": "audio_0/playlist.m3u8",
            "url": "audio_0/playlist.m3u8",
            "codec": "aac",
            "channels": 2,
            "is_default": True
        })

    # Single-Default Guarantee on formatted_audio
    first_def_idx = next((idx for idx, a in enumerate(formatted_audio) if a.get("is_default")), -1)
    if first_def_idx == -1:
        first_def_idx = 0
    for idx, a in enumerate(formatted_audio):
        a["is_default"] = (idx == first_def_idx)

    # 4. Filter and Reconcile Text Subtitles (Embedded + Attached External)
    raw_sub_streams = [
        s for s in probe.streams 
        if s.get("codec_type") == "subtitle" and s.get("codec_name", "").lower() in TEXT_SUB_CODECS
    ]
    formatted_subs: List[Dict[str, Any]] = []
    subtitles_explicitly_disabled = False

    if custom_subtitle_tracks is not None:
        # Admin explicitly configured subtitle tracks via Modal / API
        subs_dir.mkdir(parents=True, exist_ok=True)
        # In-memory cache of all existing .vtt files before cleaning
        existing_vtt_cache: Dict[str, str] = {}
        for f in subs_dir.glob("*.vtt"):
            if f.is_file():
                try:
                    existing_vtt_cache[f.name] = f.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    pass

        # Clean existing .vtt files now that they are cached in-memory
        for f in subs_dir.glob("*.vtt"):
            try:
                f.unlink()
            except Exception:
                pass

        included_subs: List[Dict[str, Any]] = []
        for t in custom_subtitle_tracks:
            if isinstance(t, dict):
                if t.get("included", True) is False:
                    continue
                lbl = t.get("label") or t.get("clean_label") or t.get("lang") or ""
                if str(lbl).strip().lower() in ("none", "off"):
                    continue
                included_subs.append(t)
            else:
                lbl = str(t)
                if lbl.strip().lower() not in ("none", "off", "und"):
                    included_subs.append({"label": lbl, "lang": "", "included": True})

        if not included_subs:
            formatted_subs = []
            need_subs = False
            subtitles_explicitly_disabled = True
        else:
            detected_sub_streams = []
            for s_idx, s_stream in enumerate(raw_sub_streams):
                norm_label, norm_code = detect_stream_language(
                    s_stream,
                    fallback_label=f"Subtitle {s_idx + 1}",
                    default_code="und"
                )
                detected_sub_streams.append({
                    "stream": s_stream,
                    "raw_stream_idx": s_stream.get("index", s_idx),
                    "norm_label": norm_label,
                    "norm_code": norm_code,
                    "used": False
                })

            for cfg in included_subs:
                raw_idx = cfg.get("raw_stream_idx")
                is_ext = bool(cfg.get("is_external") or cfg.get("ext_id") or (raw_idx is not None and raw_idx < 0) or (cfg.get("path") and not str(cfg.get("path")).startswith("subs/")))
                clean_lbl = sanitize_track_label(cfg.get("label") or cfg.get("clean_label"), fallback="")
                clean_lang = (cfg.get("lang") or "en").strip().lower()[:10]

                incoming_root = Path(settings.MEDIA_INCOMING_DIR).resolve()
                subs_root = subs_dir.resolve()

                safe_path: Optional[Path] = None
                raw_path_str = cfg.get("path")
                if raw_path_str:
                    cand_p = Path(str(raw_path_str))
                    resolved_cand = cand_p.resolve()
                    if not cand_p.is_symlink() and resolved_cand.is_file() and (
                        resolved_cand.is_relative_to(incoming_root) or resolved_cand.is_relative_to(subs_root)
                    ):
                        safe_path = resolved_cand
                    else:
                        logger.warning(f"Blocked out-of-bounds subtitle path: {raw_path_str}")

                # Check if this is an already-ingested library subtitle (e.g. English (web) or from existing_vtt_cache)
                candidate_vtt_names = []
                for k in ("vtt_path", "url", "vtt_filename", "path"):
                    val = cfg.get(k)
                    if val:
                        candidate_vtt_names.append(Path(str(val)).name)

                cached_key = next((name for name in candidate_vtt_names if name in existing_vtt_cache), None)
                is_saved_library = (
                    bool(cached_key) or 
                    cfg.get("source") == "library" or 
                    cfg.get("is_library") or 
                    (safe_path is not None and safe_path.is_relative_to(subs_root))
                )

                if is_saved_library and (cached_key or (safe_path is not None and safe_path.is_relative_to(subs_root))):
                    # Case B: Already-Ingested Library Subtitle
                    out_idx = len(formatted_subs)
                    vtt_filename = f"sub_{out_idx}_{clean_lang}.vtt"
                    dest_vtt = subs_dir / vtt_filename
                    try:
                        if cached_key:
                            dest_vtt.write_text(existing_vtt_cache[cached_key], encoding="utf-8")
                        elif safe_path is not None and safe_path.is_relative_to(subs_root):
                            raw_b = safe_path.read_bytes()
                            dest_vtt.write_text(convert_subtitle_bytes_to_vtt(raw_b), encoding="utf-8")
                    except Exception as e:
                        logger.warning(f"Error restoring library subtitle {cached_key}: {e}")

                    if dest_vtt.exists() and dest_vtt.stat().st_size > 0:
                        formatted_subs.append({
                            "order": out_idx,
                            "raw_stream_idx": int(cfg.get("raw_stream_idx", -1)),
                            "id": f"sub_{out_idx}",
                            "index": out_idx,
                            "lang": clean_lang,
                            "label": clean_lbl or f"Subtitle {out_idx + 1}",
                            "vtt_filename": vtt_filename,
                            "url": f"subs/{vtt_filename}",
                            "vtt_path": f"subs/{vtt_filename}",
                            "is_library": True,
                            "source_type": cfg.get("source_type") or "saved",
                            "original_filename": cfg.get("original_filename") or cfg.get("filename") or vtt_filename
                        })
                elif not is_ext and raw_idx is not None and raw_idx >= 0:
                    # Case A: Embedded Container Stream
                    matched = next((c for c in detected_sub_streams if not c["used"] and c["raw_stream_idx"] == raw_idx), None)
                    if not matched:
                        matched = next((c for c in detected_sub_streams if not c["used"]), None)
                    if matched:
                        matched["used"] = True
                        out_idx = len(formatted_subs)
                        vtt_filename = f"sub_{out_idx}_{clean_lang}.vtt"
                        dest_vtt = subs_dir / vtt_filename
                        # Extract directly via pure extract_one_embedded_subtitle
                        extracted = extract_one_embedded_subtitle(input_file, matched["raw_stream_idx"], dest_vtt)
                        # Build smart label
                        default_lbl = build_smart_track_label(
                            matched["stream"].get("tags", {}).get("title"),
                            matched["norm_label"]
                        )
                        formatted_subs.append({
                            "order": out_idx,
                            "raw_stream_idx": matched["raw_stream_idx"],
                            "id": f"sub_{out_idx}",
                            "index": out_idx,
                            "lang": clean_lang,
                            "label": clean_lbl or default_lbl,
                            "vtt_filename": vtt_filename,
                            "url": f"subs/{vtt_filename}",
                            "vtt_path": f"subs/{vtt_filename}",
                            "extracted": extracted,
                            "source_type": "container",
                            "original_filename": vtt_filename
                        })
                elif is_ext:
                    # Case C: Attached External .srt/.vtt file
                    ext_file: Optional[Path] = safe_path if (safe_path is not None and safe_path.is_relative_to(incoming_root)) else None
                    raw_ext_id = cfg.get("ext_id")
                    if ext_file is None and raw_ext_id and input_file:
                        if not re.fullmatch(r"[\w\-]+", str(raw_ext_id)):
                            logger.warning(f"Blocked invalid subtitle ext_id: {raw_ext_id}")
                            continue
                        safe_stem = re.sub(r"[^\w\-.]", "_", Path(input_file).stem)
                        for stem_cand in (safe_stem, Path(input_file).stem):
                            custom_dir = (incoming_root / ".custom_subs" / stem_cand).resolve()
                            if custom_dir.is_relative_to(incoming_root) and custom_dir.is_dir():
                                for f in custom_dir.glob(f"{raw_ext_id}.*"):
                                    rf = f.resolve()
                                    if not f.is_symlink() and rf.is_relative_to(incoming_root) and rf.is_file() and rf.suffix.lower() in (".vtt", ".srt", ".ass"):
                                        ext_file = rf
                                        break
                            if ext_file is not None:
                                break
                    if ext_file is not None:
                        out_idx = len(formatted_subs)
                        vtt_filename = f"sub_{out_idx}_{clean_lang}.vtt"
                        dest_vtt = subs_dir / vtt_filename
                        try:
                            raw_b = ext_file.read_bytes()
                            vtt_text = convert_subtitle_bytes_to_vtt(raw_b)
                            dest_vtt.write_text(vtt_text, encoding="utf-8")
                        except Exception as e:
                            logger.warning(f"Error copying external subtitle {ext_file}: {e}")
                        except Exception as e:
                            logger.warning(f"Error copying external subtitle {ext_path_str}: {e}")

                        if dest_vtt.exists() and dest_vtt.stat().st_size > 0:
                            formatted_subs.append({
                                "order": out_idx,
                                "raw_stream_idx": -1,
                                "id": f"sub_{out_idx}",
                                "index": out_idx,
                                "lang": clean_lang,
                                "label": clean_lbl or f"External ({clean_lang.upper()})",
                                "vtt_filename": vtt_filename,
                                "url": f"subs/{vtt_filename}",
                                "vtt_path": f"subs/{vtt_filename}",
                                "is_external": True,
                                "source_type": "external",
                                "original_filename": cfg.get("original_filename") or cfg.get("filename") or ext_file.name
                            })
                else:
                    # Generic language match (bulk mode without raw indices)
                    cfg_code = clean_lang
                    cfg_label = clean_lbl.lower()
                    matched = None
                    if cfg_code:
                        matched = next((c for c in detected_sub_streams if not c["used"] and c["norm_code"].lower() == cfg_code), None)
                    if not matched and cfg_label:
                        matched = next((c for c in detected_sub_streams if not c["used"] and (cfg_label in c["norm_label"].lower() or c["norm_label"].lower() in cfg_label)), None)
                    if not matched:
                        matched = next((c for c in detected_sub_streams if not c["used"]), None)
                    if matched:
                        matched["used"] = True
                        out_idx = len(formatted_subs)
                        vtt_filename = f"sub_{out_idx}_{clean_lang}.vtt"
                        dest_vtt = subs_dir / vtt_filename
                        extracted = extract_one_embedded_subtitle(input_file, matched["raw_stream_idx"], dest_vtt)
                        formatted_subs.append({
                            "order": out_idx,
                            "raw_stream_idx": matched["raw_stream_idx"],
                            "id": f"sub_{out_idx}",
                            "index": out_idx,
                            "lang": clean_lang,
                            "label": clean_lbl or matched["norm_label"],
                            "vtt_filename": vtt_filename,
                            "url": f"subs/{vtt_filename}",
                            "vtt_path": f"subs/{vtt_filename}",
                            "extracted": extracted,
                            "source_type": "container",
                            "original_filename": vtt_filename
                        })
            need_subs = any(
                s.get("raw_stream_idx", -1) >= 0 and not (subs_dir / s["vtt_filename"]).is_file()
                for s in formatted_subs
            )
    else:
        for k, s_stream in enumerate(raw_sub_streams):
            norm_label, norm_code = detect_stream_language(
                s_stream,
                fallback_label=f"Subtitle {k + 1}",
                default_code="und"
            )
            vtt_filename = f"sub_{k}_{norm_code}.vtt"
            dest_vtt = subs_dir / vtt_filename
            smart_lbl = build_smart_track_label(
                s_stream.get("tags", {}).get("title"),
                norm_label
            )
            extracted = extract_one_embedded_subtitle(input_file, s_stream.get("index", k), dest_vtt)
            formatted_subs.append({
                "order": k,
                "raw_stream_idx": s_stream.get("index", k),
                "id": f"sub_{k}",
                "index": k,
                "lang": norm_code,
                "label": smart_lbl,
                "vtt_filename": vtt_filename,
                "url": f"subs/{vtt_filename}",
                "vtt_path": f"subs/{vtt_filename}",
                "extracted": extracted
            })

        # Discover companion external subtitles next to input_file (.srt, .vtt, .ass)
        if input_file:
            try:
                in_p = Path(input_file)
                in_dir = in_p.parent
                in_stem = in_p.stem
                incoming_root = Path(settings.MEDIA_INCOMING_DIR).resolve()
                for ext in (".srt", ".vtt", ".ass"):
                    for sub_f in in_dir.glob(f"*{ext}"):
                        if sub_f.is_symlink() or not sub_f.resolve().is_relative_to(incoming_root) or not sub_f.is_file():
                            continue
                        if sub_f.name.startswith(in_stem):
                            sub_out_idx = len(formatted_subs)
                            sub_suffix = sub_f.stem.replace(in_stem, "").strip("._- ")
                            clean_code = sub_suffix.lower()[:3] if sub_suffix else "en"
                            clean_lbl = f"External ({clean_code.upper()})"
                            dest_vtt = subs_dir / f"sub_{sub_out_idx}_{clean_code}.vtt"

                            if not dest_vtt.exists() or dest_vtt.stat().st_size == 0:
                                try:
                                    raw_b = sub_f.read_bytes()
                                    dest_vtt.write_text(convert_subtitle_bytes_to_vtt(raw_b), encoding="utf-8")
                                except Exception as e:
                                    logger.warning(f"Error copying companion subtitle {sub_f}: {e}")

                            if dest_vtt.exists() and dest_vtt.stat().st_size > 0:
                                formatted_subs.append({
                                    "order": sub_out_idx,
                                    "raw_stream_idx": -1,
                                    "id": f"sub_{sub_out_idx}",
                                    "index": sub_out_idx,
                                    "lang": clean_code,
                                    "label": clean_lbl,
                                    "vtt_filename": dest_vtt.name,
                                    "url": f"subs/{dest_vtt.name}",
                                    "vtt_path": f"subs/{dest_vtt.name}",
                                    "is_external": True
                                })
            except Exception as e:
                logger.warning(f"Error checking companion subtitles: {e}")

        can_reuse_subs = check_subs_ready(output_dir, formatted_subs)
        need_subs = not can_reuse_subs

    # Auto-clean staged .custom_subs/{video_stem} upon ingest completion
    if input_file:
        try:
            incoming_subs = (Path(settings.MEDIA_INCOMING_DIR) / ".custom_subs").resolve()
            safe_stem = re.sub(r"[^\w\-.]", "_", Path(input_file).stem)
            staged_dir = (incoming_subs / safe_stem).resolve()
            if staged_dir.is_relative_to(incoming_subs) and staged_dir.exists() and staged_dir.is_dir():
                shutil.rmtree(staged_dir, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Error cleaning staged custom subs for {input_file}: {e}")

    # 5. Check Readiness of Audio
    can_reuse_audio = check_audio_ready(output_dir, formatted_audio)
    need_audio = not can_reuse_audio

    # 6. Check Readiness of Video Rungs (Strict Source Resolution Ceiling — Never Upscale)
    source_h = probe.height or 1080
    source_w = probe.width or 1920
    duration_sec = probe.duration_seconds or 1.0

    is_1080p_class = (source_h >= 1080 or source_w >= 1920)
    is_720p_class = not is_1080p_class and (source_h >= 720 or source_w >= 1280)

    if is_1080p_class:
        native_rung_label = "1080p"
        native_maxrate = "4500k"
        native_bufsize = "9000k"
    elif is_720p_class:
        native_rung_label = "720p"
        native_maxrate = "2800k"
        native_bufsize = "5600k"
    else:
        native_rung_label = "480p"
        native_maxrate = "1400k"
        native_bufsize = "2800k"

    if force_video_reencode:
        need_1080p = True  # Primary native stream in video/
        if not transcoding_enabled:
            need_720p = False
            need_480p = False
        else:
            if is_1080p_class:
                need_720p = True
                need_480p = True
            elif is_720p_class:
                need_720p = False  # Primary video/ IS the 720p stream
                need_480p = True
            else:
                need_720p = False
                need_480p = False
    else:
        all_ready, ready_dict = await is_existing_video_ready(
            output_dir,
            transcoding_enabled=transcoding_enabled,
            source_height=source_h,
            source_width=source_w
        )
        need_1080p = not ready_dict.get("1080p", False)
        if not transcoding_enabled:
            need_720p = False
            need_480p = False
        else:
            if is_1080p_class:
                need_720p = not ready_dict.get("720p", False)
                need_480p = not ready_dict.get("480p", False)
            elif is_720p_class:
                need_720p = False  # Primary video/ IS the 720p stream!
                need_480p = not ready_dict.get("480p", False)
            else:
                need_720p = False
                need_480p = False

    any_work_needed = need_1080p or need_720p or need_480p or need_audio or need_subs

    # 7. Helper to Execute FFmpeg Subprocess with Real-Time Progress Parsing & Cancellation
    async def execute_ffmpeg_process(cmd_to_run: List[str], stage_desc: str):
        from app.transcoder.worker import transcode_queue
        if transcode_queue.cancel_requested:
            raise RuntimeError("Transcoding job cancelled by admin")

        def _pin_cpu_affinity():
            if hasattr(os, "sched_setaffinity"):
                try:
                    num_cores = max(1, min(cpu_threads, os.cpu_count() or cpu_threads))
                    os.sched_setaffinity(0, set(range(num_cores)))
                except Exception:
                    pass

        proc = await asyncio.create_subprocess_exec(
            *cmd_to_run,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_pin_cpu_affinity
        )
        transcode_queue.set_active_process(proc, output_dir)
        time_regex = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        stderr_tail: List[str] = []

        try:
            while True:
                if transcode_queue.cancel_requested:
                    raise RuntimeError("Transcoding job cancelled by admin")
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                if decoded.strip():
                    stderr_tail.append(decoded.strip())
                    if len(stderr_tail) > 20:
                        stderr_tail.pop(0)
                match = time_regex.search(decoded)
                if match and progress_callback:
                    hours, minutes, seconds = match.groups()
                    current_sec = float(hours) * 3600 + float(minutes) * 60 + float(seconds)
                    pct = min(98.0, max(15.0, (current_sec / duration_sec) * 100.0))
                    progress_callback(round(pct, 1), f"{stage_desc}: {pct:.0f}%")

            await proc.wait()
            if proc.returncode != 0:
                if transcode_queue.cancel_requested:
                    raise RuntimeError("Transcoding job cancelled by admin")
                last_err = "\n".join(stderr_tail[-5:]) if stderr_tail else f"Exit code {proc.returncode}"
                raise RuntimeError(f"FFmpeg failed with code {proc.returncode}: {last_err}")
        finally:
            transcode_queue.clear_active_process()

    # 8. Execute FFmpeg if any work needed
    video_streams = [
        s for s in probe.streams 
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    v_stream = video_streams[0] if video_streams else (probe.video_stream or {})
    v_stream_idx = v_stream.get("index", 0)
    pix_fmt = v_stream.get("pix_fmt")
    v_codec = v_stream.get("codec_name", "h264")
    is_native_8bit_h264 = (v_codec == "h264") and (pix_fmt in ("yuv420p", None))

    if not any_work_needed:
        logger.info("⚡ Fast Re-Ingest: All video rungs, audio tracks, and subtitles already ready on disk. Reusing everything!")
        if progress_callback:
            progress_callback(95.0, "⚡ Fast Re-Ingest: All streams ready, finalizing master playlist...")
    else:
        # Clean up only incomplete or targeted renditions
        if need_1080p: cleanup_rendition(video_dir)
        if need_720p: cleanup_rendition(video_720p_dir)
        if need_480p: cleanup_rendition(video_480p_dir)
        if need_audio:
            for d in output_dir.glob("audio_*"):
                if d.is_dir():
                    shutil.rmtree(d, ignore_errors=True)
        if custom_subtitle_tracks is None and (need_subs or subtitles_explicitly_disabled):
            for f in subs_dir.glob("*.vtt"):
                try: f.unlink()
                except Exception: pass

        # Determine candidate tier for video processing
        chosen_tier = "tier_b"
        gpu_encoder = None

        if need_1080p and is_native_8bit_h264 and not need_720p and not need_480p:
            chosen_tier = "tier_a"
            logger.info(f"⚡ Tier A: Using lossless zero-CPU stream copy for 8-bit H.264 video stream {v_stream_idx}")
        elif hw_accel_type != "none" and gpu_enabled:
            if await check_nvenc_available():
                chosen_tier = "tier_c"
                gpu_encoder = "h264_nvenc"
                logger.info("🚀 Tier C: Using hardware-accelerated h264_nvenc encoder for video")
            elif await check_vaapi_available():
                chosen_tier = "tier_c"
                gpu_encoder = "h264_vaapi"
                logger.info("🚀 Tier C: Using hardware-accelerated h264_vaapi encoder for video")

        if chosen_tier == "tier_b":
            logger.info(
                f"🛡️ Tier B: Using CPU-Safe Ultra-Light Software Conversion "
                f"(capped to {cpu_threads} threads, {ram_mb}MB RAM limit, zero-overhead buffers, idle priority)"
            )

        stage_desc_parts = []
        if need_1080p: stage_desc_parts.append(native_rung_label)
        if need_720p: stage_desc_parts.append("720p")
        if need_480p: stage_desc_parts.append("480p")
        if need_audio: stage_desc_parts.append(f"{len(formatted_audio)} audio")
        if need_subs: stage_desc_parts.append(f"{len(formatted_subs)} subs")
        stage_desc = f"Packaging ({', '.join(stage_desc_parts)})"

        # Execute with Automatic Fallback Guarantee to Tier B
        if chosen_tier in ("tier_c", "tier_a"):
            try:
                cmd = build_consolidated_ffmpeg_cmd(
                    input_file=input_file,
                    output_dir=output_dir,
                    v_stream_idx=v_stream_idx,
                    tier=chosen_tier,
                    gpu_encoder=gpu_encoder,
                    cpu_threads=cpu_threads,
                    formatted_audio=formatted_audio,
                    formatted_subs=formatted_subs,
                    need_1080p=need_1080p,
                    need_720p=need_720p,
                    need_480p=need_480p,
                    need_audio=need_audio,
                    need_subs=need_subs,
                    allocated_ram_mb=ram_mb,
                    is_native_8bit_h264=is_native_8bit_h264,
                    force_video_reencode=force_video_reencode,
                    native_maxrate=native_maxrate,
                    native_bufsize=native_bufsize
                )
                await execute_ffmpeg_process(cmd, stage_desc)
            except Exception as tier_err:
                from app.transcoder.worker import transcode_queue
                if transcode_queue.cancel_requested or "cancelled" in str(tier_err).lower():
                    raise
                logger.warning(
                    f"⚠️ {chosen_tier.upper()} failed ({tier_err}). "
                    f"Engaging Automatic Fallback Guarantee -> Tier B: CPU-Safe Ultra-Light Software Conversion..."
                )
                if need_1080p: cleanup_rendition(video_dir)
                if need_720p: cleanup_rendition(video_720p_dir)
                if need_480p: cleanup_rendition(video_480p_dir)

                cmd_fallback = build_consolidated_ffmpeg_cmd(
                    input_file=input_file,
                    output_dir=output_dir,
                    v_stream_idx=v_stream_idx,
                    tier="tier_b",
                    gpu_encoder=None,
                    cpu_threads=cpu_threads,
                    formatted_audio=formatted_audio,
                    formatted_subs=formatted_subs,
                    need_1080p=need_1080p,
                    need_720p=need_720p,
                    need_480p=need_480p,
                    need_audio=need_audio,
                    need_subs=need_subs,
                    allocated_ram_mb=ram_mb,
                    is_native_8bit_h264=is_native_8bit_h264,
                    force_video_reencode=force_video_reencode,
                    native_maxrate=native_maxrate,
                    native_bufsize=native_bufsize
                )
                await execute_ffmpeg_process(cmd_fallback, f"{stage_desc} (Tier B Fallback)")
        else:
            cmd = build_consolidated_ffmpeg_cmd(
                input_file=input_file,
                output_dir=output_dir,
                v_stream_idx=v_stream_idx,
                tier="tier_b",
                gpu_encoder=None,
                cpu_threads=cpu_threads,
                formatted_audio=formatted_audio,
                formatted_subs=formatted_subs,
                need_1080p=need_1080p,
                need_720p=need_720p,
                need_480p=need_480p,
                need_audio=need_audio,
                need_subs=need_subs,
                allocated_ram_mb=ram_mb,
                is_native_8bit_h264=is_native_8bit_h264,
                force_video_reencode=force_video_reencode,
                native_maxrate=native_maxrate,
                native_bufsize=native_bufsize
            )
            await execute_ffmpeg_process(cmd, stage_desc)

    if progress_callback:
        progress_callback(99.0, "Generating master playlist manifest...")

    # 9. Programmatically Write master.m3u8 Linking Video and Audio Playlists
    master_lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        '#EXT-X-SESSION-DATA:DATA-ID="com.streamvault.engine",VALUE="StreamVault-ABR-v2.5"',
        ""
    ]

    # Keep audio renditions in natural audio_0, audio_1, ... order
    for a in formatted_audio:
        is_default = bool(a.get("is_default", False))
        is_def_str = "YES" if is_default else "NO"
        auto_sel_str = "YES" if is_default else "NO"
        clean_lbl = sanitize_track_label(a["label"], fallback=f"Audio Track {a['index'] + 1}")
        clean_lng = a["lang"] or "und"
        line = f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="{clean_lbl}",LANGUAGE="{clean_lng}",DEFAULT={is_def_str},AUTOSELECT={auto_sel_str},URI="{a["playlist"]}"'
        master_lines.append(line)

    master_lines.append("")

    if transcoding_enabled:
        if is_1080p_class:
            master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",NAME="1080p",AUDIO="audio"')
            master_lines.append("video/playlist.m3u8")
            master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2",NAME="720p",AUDIO="audio"')
            master_lines.append("video_720p/playlist.m3u8")
            master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480,CODECS="avc1.64001e,mp4a.40.2",NAME="480p",AUDIO="audio"')
            master_lines.append("video_480p/playlist.m3u8")
            available_qualities = ["Auto", "1080p", "720p", "480p"]
        elif is_720p_class:
            master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,CODECS="avc1.64001f,mp4a.40.2",NAME="720p",AUDIO="audio"')
            master_lines.append("video/playlist.m3u8")
            master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480,CODECS="avc1.64001e,mp4a.40.2",NAME="480p",AUDIO="audio"')
            master_lines.append("video_480p/playlist.m3u8")
            available_qualities = ["Auto", "720p", "480p"]
        else:
            master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480,CODECS="avc1.64001e,mp4a.40.2",NAME="480p",AUDIO="audio"')
            master_lines.append("video/playlist.m3u8")
            available_qualities = ["Auto", "480p"]
    else:
        master_lines.append('#EXT-X-STREAM-INF:BANDWIDTH=5000000,CODECS="avc1.640028,mp4a.40.2",NAME="Original",AUDIO="audio"')
        master_lines.append("video/playlist.m3u8")
        available_qualities = ["Original"]

    master_playlist_path = output_dir / "master.m3u8"
    master_playlist_path.write_text("\n".join(master_lines) + "\n", encoding="utf-8")

    # Persist stream mapping metadata for incremental validation and stale track rejection
    save_stream_map(output_dir, formatted_audio, formatted_subs)

    if progress_callback:
        progress_callback(100.0, "Ingestion and packaging completed successfully.")

    total_size = sum(f.stat().st_size for f in output_dir.rglob("*") if f.is_file())

    clean_audio_tracks = [
        {
            "id": a["id"],
            "index": a["index"],
            "raw_stream_idx": int(a.get("raw_stream_idx", a["index"] + 1)),
            "lang": a["lang"],
            "label": sanitize_track_label(a["label"], fallback=f"Audio Track {a['index'] + 1}"),
            "playlist": a["playlist"],
            "url": a.get("url") or a["playlist"],
            "codec": a["codec"],
            "channels": a["channels"],
            "is_default": bool(a.get("is_default", False))
        }
        for a in formatted_audio
    ]

    clean_sub_tracks = []
    for s in formatted_subs:
        vtt_file = output_dir / s["vtt_path"]
        if vtt_file.is_file() and vtt_file.stat().st_size > 0:
            raw_idx = int(s.get("raw_stream_idx", -1))
            clean_sub_tracks.append({
                "id": s["id"],
                "index": len(clean_sub_tracks),
                "lang": s["lang"],
                "label": sanitize_track_label(s["label"], fallback=f"Subtitle {len(clean_sub_tracks) + 1}"),
                "url": s["url"],
                "vtt_path": s["vtt_path"],
                "vtt_filename": s.get("vtt_filename") or Path(s["vtt_path"]).name,
                "raw_stream_idx": raw_idx,
                "source_type": "container" if raw_idx >= 0 else "external",
                "original_filename": s.get("original_filename") or s.get("vtt_filename") or Path(s["vtt_path"]).name
            })

    return {
        "master_playlist_path": f"{output_dir.name}/master.m3u8",
        "is_transcoded": bool(transcoding_enabled),
        "available_qualities": available_qualities,
        "audio_tracks": clean_audio_tracks,
        "subtitle_tracks": clean_sub_tracks,
        "file_size_bytes": total_size
    }
