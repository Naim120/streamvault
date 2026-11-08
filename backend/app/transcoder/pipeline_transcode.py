import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from .probe import MediaProbeResult
from .pipeline_remux import run_lossless_remux

logger = logging.getLogger(__name__)

async def run_multi_bitrate_transcode(
    input_file: str,
    output_dir: Path,
    probe: MediaProbeResult,
    custom_audio_tracks: Optional[List[Any]] = None,
    custom_subtitle_tracks: Optional[List[Any]] = None,
    force_video_reencode: bool = False,
    cpu_thread_limit: str = "auto",
    hw_accel_type: str = "auto",
    max_threads: int = 2,
    allocated_cpu_cores: Optional[int] = None,
    allocated_ram_mb: Optional[int] = None,
    gpu_enabled: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None
) -> Dict[str, Any]:
    """
    Mode A: Multi-Bitrate ABR Transcoding (1080p, 720p, 480p)
    Delegates to the unified, CPU-governed production pipeline with smart 1080p stream reuse.
    """
    logger.info(f"Mode A: Multi-bitrate ABR pipeline invoked for {input_file}")
    return await run_lossless_remux(
        input_file=input_file,
        output_dir=output_dir,
        probe=probe,
        custom_audio_tracks=custom_audio_tracks,
        custom_subtitle_tracks=custom_subtitle_tracks,
        force_video_reencode=force_video_reencode,
        cpu_thread_limit=cpu_thread_limit,
        hw_accel_type=hw_accel_type,
        transcoding_enabled=True,
        allocated_cpu_cores=allocated_cpu_cores,
        allocated_ram_mb=allocated_ram_mb,
        gpu_enabled=gpu_enabled,
        progress_callback=progress_callback
    )

