import ctypes
import os
import shutil
from pathlib import Path

try:
    import setproctitle
except ImportError:
    setproctitle = None

def set_process_identity(name: str) -> None:
    """
    Sets branded OS process identity:
    1. Full commandline title for htop / ps aux
    2. 15-character Linux kernel comm name for top / System Monitor / Task Manager
    """
    if setproctitle:
        try:
            setproctitle.setproctitle(name)
        except Exception:
            pass

    try:
        libc = ctypes.CDLL("libc.so.6")
        # PR_SET_NAME = 15 in sys/prctl.h
        libc.prctl(15, name[:15].encode("utf-8"), 0, 0, 0)
    except Exception:
        pass

def ensure_branded_symlinks(root_dir: Path) -> dict[str, str]:
    """
    Ensures .bin/ directory in root contains branded symlinks to ffmpeg and aria2c:
    - .bin/streamvault-transcoder -> ffmpeg
    - .bin/streamvault-downloader -> aria2c
    Returns mapping of binary paths.
    """
    bin_dir = root_dir / ".bin"
    bin_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg_path = shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
    aria2c_path = shutil.which("aria2c") or "/home/naim/.local/bin/aria2c"

    transcoder_link = bin_dir / "streamvault-transcoder"
    downloader_link = bin_dir / "streamvault-downloader"

    for link, target in [(transcoder_link, ffmpeg_path), (downloader_link, aria2c_path)]:
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            if target and os.path.exists(target):
                os.symlink(target, link)
        except Exception:
            pass

    return {
        "transcoder": str(transcoder_link) if transcoder_link.exists() else (ffmpeg_path or "ffmpeg"),
        "downloader": str(downloader_link) if downloader_link.exists() else (aria2c_path or "aria2c")
    }
