import os
import shutil
import subprocess
import psutil
from typing import Dict, Any, List

import platform
import functools

def get_os_metadata() -> Dict[str, str]:
    """
    Returns normalized operating system metadata (Linux, Windows, macOS).
    """
    sys_name = platform.system()
    machine = platform.machine()
    release = platform.release()

    if sys_name == "Linux":
        distro = "Linux"
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release") as f:
                    for line in f:
                        if line.startswith("PRETTY_NAME="):
                            distro = line.split("=", 1)[1].strip().strip('"')
                            break
        except Exception:
            pass
        return {
            "platform": "linux",
            "display_name": f"{distro} ({machine})",
            "arch": machine,
            "release": release,
            "distro": distro,
        }
    elif sys_name == "Windows":
        return {
            "platform": "windows",
            "display_name": f"Windows ({release} {machine})",
            "arch": machine,
            "release": release,
            "distro": "Windows",
        }
    elif sys_name == "Darwin":
        return {
            "platform": "macos",
            "display_name": f"macOS ({release} {machine})",
            "arch": machine,
            "release": release,
            "distro": "macOS",
        }
    return {
        "platform": sys_name.lower(),
        "display_name": f"{sys_name} ({machine})",
        "arch": machine,
        "release": release,
        "distro": sys_name,
    }

@functools.lru_cache(maxsize=1)
def detect_ffmpeg_encoders() -> List[str]:
    """
    Runs ffmpeg -encoders to find available h264 encoders (cached once at startup).
    """
    try:
        res = subprocess.run(
            ["ffmpeg", "-encoders"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        encoders = []
        for line in res.stdout.splitlines():
            line_clean = line.strip()
            if any(k in line_clean.lower() for k in ["h264", "nvenc", "vaapi", "qsv", "videotoolbox", "amf"]):
                parts = line_clean.split()
                if len(parts) >= 2:
                    encoders.append(parts[1])
        return encoders
    except Exception:
        return ["libx264"]

@functools.lru_cache(maxsize=1)
def detect_ffmpeg_hwaccels() -> List[str]:
    """
    Runs ffmpeg -hwaccels to find hardware acceleration methods (cached once at startup).
    """
    try:
        res = subprocess.run(
            ["ffmpeg", "-hwaccels"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        lines = [line.strip() for line in res.stdout.splitlines() if line.strip() and not line.startswith("Hardware acceleration") and not line.startswith("ffmpeg")]
        return lines
    except Exception:
        return []

@functools.lru_cache(maxsize=1)
def check_nvenc_functional() -> bool:
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-vf", "format=yuv420p", "-c:v", "h264_nvenc", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3
        )
        return res.returncode == 0
    except Exception:
        return False

@functools.lru_cache(maxsize=1)
def check_vaapi_functional() -> bool:
    if not os.path.exists("/dev/dri/renderD128"):
        return False
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-vaapi_device", "/dev/dri/renderD128", "-vf", "format=nv12,hwupload", "-c:v", "h264_vaapi", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3
        )
        return res.returncode == 0
    except Exception:
        return False

@functools.lru_cache(maxsize=1)
def check_qsv_functional() -> bool:
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_qsv", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3
        )
        return res.returncode == 0
    except Exception:
        return False

@functools.lru_cache(maxsize=1)
def check_videotoolbox_functional() -> bool:
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_videotoolbox", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3
        )
        return res.returncode == 0
    except Exception:
        return False

@functools.lru_cache(maxsize=1)
def check_amf_functional() -> bool:
    try:
        res = subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1", "-c:v", "h264_amf", "-f", "null", "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3
        )
        return res.returncode == 0
    except Exception:
        return False

@functools.lru_cache(maxsize=1)
def detect_physical_gpu() -> Dict[str, Any]:
    """
    Checks if a physical discrete or integrated GPU is present on Linux, Windows, or macOS,
    determines whether hardware encoding drivers are functional, and generates tailored
    multi-platform installation guides.
    """
    os_meta = get_os_metadata()
    current_os = os_meta["platform"]

    gpu_info: Dict[str, Any] = {
        "detected": False,
        "name": None,
        "vendor": None,
        "driver_in_use": None,
        "is_proprietary_driver_loaded": False,
        "nvenc_ready": False,
        "missing_reason": None,
        "recommended_driver": None,
        "install_command": None,
        "os_platform": current_os,
        "os_display_name": os_meta["display_name"],
        "os_arch": os_meta["arch"],
        "guides": {
            "linux": {
                "os_name": "Linux",
                "badge": "Linux (Ubuntu / Debian / Arch / Fedora)",
                "summary": "Linux requires proprietary vendor drivers (e.g. nvidia-driver-580) to enable NVENC/CUDA hardware offloading. Open-source nouveau lacks NVENC support.",
                "distros": [
                    {
                        "id": "ubuntu",
                        "name": "Ubuntu / Debian / Mint",
                        "cmd": "sudo apt update && sudo apt install -y nvidia-driver-580 && sudo reboot",
                        "note": "Installs the official NVIDIA proprietary driver and libcuda.so.1."
                    },
                    {
                        "id": "arch",
                        "name": "Arch Linux / Manjaro",
                        "cmd": "sudo pacman -Sy --noconfirm nvidia nvidia-utils && sudo reboot",
                        "note": "Installs latest NVIDIA kernel modules and userspace libraries."
                    },
                    {
                        "id": "fedora",
                        "name": "Fedora / RHEL",
                        "cmd": "sudo dnf install -y akmod-nvidia xorg-x11-drv-nvidia-cuda && sudo reboot",
                        "note": "Installs RPM Fusion packaged NVIDIA kernel driver and CUDA runtime."
                    }
                ],
                "intel_cmd": "sudo apt install -y intel-media-va-driver-non-free vainfo",
                "amd_cmd": "sudo apt install -y mesa-va-drivers vainfo"
            },
            "windows": {
                "os_name": "Windows",
                "badge": "Windows 10 / 11 (64-bit)",
                "summary": "Windows leverages GeForce Game Ready / Studio drivers. Installing the driver immediately places nvcuda.dll in System32 for FFmpeg NVENC acceleration.",
                "cli_command": "winget install Nvidia.Driver.Display",
                "cli_note": "Or install GeForce Experience: winget install Nvidia.GeForceExperience",
                "ffmpeg_setup": "winget install Gyan.FFmpeg",
                "download_url": "https://www.nvidia.com/Download/index.aspx",
                "intel_info": "Intel QuickSync: winget install Intel.GraphicsDriver",
                "amd_info": "AMD AMF: winget install AdvancedMicroDevices.AMDSoftwareAdrenalinEdition"
            },
            "macos": {
                "os_name": "macOS",
                "badge": "macOS (Apple Silicon M1-M4 & Intel)",
                "summary": "Apple Silicon (M1/M2/M3/M4) and Intel Macs utilize Apple's native VideoToolbox framework (h264_videotoolbox). No external GPU drivers are needed—the Media Engine is built directly into macOS!",
                "cli_command": "brew install ffmpeg",
                "cli_note": "Homebrew's FFmpeg binary comes pre-compiled with native Apple VideoToolbox hardware acceleration out-of-the-box.",
                "framework": "Apple VideoToolbox (h264_videotoolbox)",
                "note": "Apple deprecated NVIDIA in macOS 10.14+. Apple Silicon Unified Memory Media Engines achieve zero-CPU transcoding natively!"
            }
        }
    }

    try:
        # 1. Linux Detection
        if current_os == "linux":
            if shutil.which("lspci"):
                res = subprocess.run(["lspci", "-k"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=5)
                lines = res.stdout.splitlines()

                current_block = []
                blocks = []
                for line in lines:
                    if line and not line.startswith("\t") and not line.startswith(" "):
                        if current_block:
                            blocks.append("\n".join(current_block))
                        current_block = [line]
                    else:
                        current_block.append(line)
                if current_block:
                    blocks.append("\n".join(current_block))

                for block in blocks:
                    lower_block = block.lower()
                    if "vga compatible controller" in lower_block or "3d controller" in lower_block or "display controller" in lower_block:
                        if "nvidia" in lower_block:
                            gpu_info["detected"] = True
                            gpu_info["vendor"] = "NVIDIA"
                            for line in block.splitlines():
                                if "vga compatible controller:" in line.lower() or "3d controller:" in line.lower():
                                    parts = line.split(":", 2)
                                    if len(parts) >= 3:
                                        gpu_info["name"] = parts[2].strip()
                                    else:
                                        gpu_info["name"] = "NVIDIA GPU"
                                if "kernel driver in use:" in line.lower():
                                    gpu_info["driver_in_use"] = line.split(":", 1)[1].strip()

                            raw_name = gpu_info["name"] or ""
                            if "[" in raw_name and "]" in raw_name:
                                bracket_content = raw_name.split("[")[1].split("]")[0]
                                gpu_info["name"] = f"NVIDIA {bracket_content}"

                            driver = gpu_info.get("driver_in_use", "")
                            gpu_info["is_proprietary_driver_loaded"] = (driver == "nvidia")

                            if driver != "nvidia":
                                gpu_info["missing_reason"] = (
                                    f"Host GPU '{gpu_info['name']}' is running the open-source '{driver or 'unknown'}' driver. "
                                    "NVENC hardware encoding requires the proprietary NVIDIA driver (providing libcuda.so.1)."
                                )
                                gpu_info["recommended_driver"] = "nvidia-driver-580"
                                gpu_info["install_command"] = "sudo apt update && sudo apt install -y nvidia-driver-580"
                            break
                        elif "intel" in lower_block:
                            gpu_info["detected"] = True
                            gpu_info["vendor"] = "Intel"
                            gpu_info["name"] = "Intel Integrated Graphics"
                            for line in block.splitlines():
                                if "kernel driver in use:" in line.lower():
                                    gpu_info["driver_in_use"] = line.split(":", 1)[1].strip()
                        elif "advanced micro devices" in lower_block or "amd" in lower_block or "radeon" in lower_block:
                            gpu_info["detected"] = True
                            gpu_info["vendor"] = "AMD"
                            gpu_info["name"] = "AMD Radeon GPU"
                            for line in block.splitlines():
                                if "kernel driver in use:" in line.lower():
                                    gpu_info["driver_in_use"] = line.split(":", 1)[1].strip()

        # 2. Windows Detection
        elif current_os == "windows":
            try:
                ps_res = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Caption"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=6
                )
                if ps_res.returncode == 0 and ps_res.stdout.strip():
                    captions = [c.strip() for c in ps_res.stdout.splitlines() if c.strip()]
                    for cap in captions:
                        lower_cap = cap.lower()
                        if "nvidia" in lower_cap:
                            gpu_info["detected"] = True
                            gpu_info["vendor"] = "NVIDIA"
                            gpu_info["name"] = cap
                            gpu_info["driver_in_use"] = "Windows WDDM (NVIDIA)"
                            gpu_info["install_command"] = "winget install Nvidia.Driver.Display"
                            gpu_info["recommended_driver"] = "GeForce Game Ready / Studio Driver"
                            break
                        elif "intel" in lower_cap:
                            gpu_info["detected"] = True
                            gpu_info["vendor"] = "Intel"
                            gpu_info["name"] = cap
                            gpu_info["install_command"] = "winget install Intel.GraphicsDriver"
                        elif "amd" in lower_cap or "radeon" in lower_cap:
                            gpu_info["detected"] = True
                            gpu_info["vendor"] = "AMD"
                            gpu_info["name"] = cap
                            gpu_info["install_command"] = "winget install AdvancedMicroDevices.AMDSoftwareAdrenalinEdition"
            except Exception:
                pass

        # 3. macOS Detection
        elif current_os == "macos":
            try:
                sp_res = subprocess.run(
                    ["system_profiler", "SPDisplaysDataType"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=6
                )
                if sp_res.returncode == 0:
                    gpu_info["detected"] = True
                    for line in sp_res.stdout.splitlines():
                        if "chipset model:" in line.lower():
                            gpu_info["name"] = line.split(":", 1)[1].strip()
                            if "apple" in gpu_info["name"].lower():
                                gpu_info["vendor"] = "Apple"
                            elif "intel" in gpu_info["name"].lower():
                                gpu_info["vendor"] = "Intel"
                            elif "amd" in gpu_info["name"].lower() or "radeon" in gpu_info["name"].lower():
                                gpu_info["vendor"] = "AMD"
                            break
                    if not gpu_info["name"]:
                        gpu_info["name"] = "Apple Silicon GPU / Media Engine"
                        gpu_info["vendor"] = "Apple"
                    gpu_info["driver_in_use"] = "Apple Metal / VideoToolbox"
                    gpu_info["install_command"] = "brew install ffmpeg"
            except Exception:
                gpu_info["detected"] = True
                gpu_info["name"] = "Apple Silicon / VideoToolbox"
                gpu_info["vendor"] = "Apple"
                gpu_info["install_command"] = "brew install ffmpeg"

    except Exception as e:
        gpu_info["error"] = str(e)

    return gpu_info

@functools.lru_cache(maxsize=1)
def get_static_hardware_info() -> Dict[str, Any]:
    """
    Gathers static hardware info that never changes at runtime.
    """
    os_meta = get_os_metadata()
    cpu_cores = psutil.cpu_count(logical=True) or 1
    vm = psutil.virtual_memory()
    total_ram_gb = round(vm.total / (1024 ** 3), 2)
    disk_info = psutil.disk_usage("/")
    total_disk_gb = round(disk_info.total / (1024 ** 3), 2)

    encoders = detect_ffmpeg_encoders()
    hwaccels = detect_ffmpeg_hwaccels()

    has_nvenc = check_nvenc_functional()
    has_vaapi = check_vaapi_functional()
    has_qsv = check_qsv_functional()
    has_videotoolbox = check_videotoolbox_functional()
    has_amf = check_amf_functional()

    gpu_hw = detect_physical_gpu()
    gpu_hw["nvenc_ready"] = has_nvenc

    preferred_encoder = "libx264"
    if has_nvenc:
        preferred_encoder = "h264_nvenc"
    elif has_videotoolbox:
        preferred_encoder = "h264_videotoolbox"
    elif has_qsv:
        preferred_encoder = "h264_qsv"
    elif has_vaapi:
        preferred_encoder = "h264_vaapi"
    elif has_amf:
        preferred_encoder = "h264_amf"

    return {
        "os_platform": os_meta["platform"],
        "os_display_name": os_meta["display_name"],
        "os_arch": os_meta["arch"],
        "cpu_cores": cpu_cores,
        "total_ram_gb": total_ram_gb,
        "total_disk_gb": total_disk_gb,
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "ffprobe_available": shutil.which("ffprobe") is not None,
        "encoders": encoders,
        "hwaccels": hwaccels,
        "has_nvenc": has_nvenc,
        "has_vaapi": has_vaapi,
        "has_qsv": has_qsv,
        "has_videotoolbox": has_videotoolbox,
        "has_amf": has_amf,
        "preferred_encoder": preferred_encoder,
        "gpu_hardware": gpu_hw,
    }

def get_system_capabilities() -> Dict[str, Any]:
    """
    Combines cached static hardware info with instantaneous dynamic metrics (CPU%, Free RAM, Free Disk).
    """
    static = get_static_hardware_info()
    cpu_percent = psutil.cpu_percent(interval=None) # Non-blocking instantaneous read
    
    vm = psutil.virtual_memory()
    available_ram_gb = round(vm.available / (1024 ** 3), 2)
    ram_percent = vm.percent

    disk_info = psutil.disk_usage("/")
    free_disk_gb = round(disk_info.free / (1024 ** 3), 2)
    disk_percent = disk_info.percent

    cpu_cores = static["cpu_cores"]
    is_low_spec = cpu_cores < 2 or available_ram_gb < 2.0
    warning_message = None
    if is_low_spec:
        warning_message = f"Host has low resources ({cpu_cores} CPU cores, {available_ram_gb} GB RAM available). Multi-bitrate transcoding is discouraged; lossless remuxing mode is recommended."

    return {
        **static,
        "cpu_percent": cpu_percent,
        "available_ram_gb": available_ram_gb,
        "ram_percent": ram_percent,
        "free_disk_gb": free_disk_gb,
        "disk_percent": disk_percent,
        "is_low_spec": is_low_spec,
        "warning_message": warning_message
    }
