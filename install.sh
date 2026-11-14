#!/usr/bin/env bash
# ==============================================================================
# STREAMVAULT — PRODUCTION MASTER INSTALLER & CLOUDFLARE TUNNEL PROVISIONER v2.6
# Supports: Ubuntu/Debian (apt), RHEL/Fedora/Rocky (dnf/yum), Arch (pacman), macOS (brew)
# Architectures: x86_64 (amd64), aarch64 / arm64
# Features: Hardware Telemetry, Named Cloudflare Tunnels, Interrupt Recovery,
#           Zero-Downtime Updates, Reboot Auto-Start & State Persistence
# ==============================================================================

set -e

# --- Colors & UI Formatting ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
TARGET_DIR="$SCRIPT_DIR"

# State & Checkpoint Files
CURRENT_STEP=1
RESUME_FROM_STEP=1
CHECKPOINT_FILE="${SCRIPT_DIR}/.install_checkpoint"
ANSWERS_FILE="${SCRIPT_DIR}/.install_answers.env"
COMPLETE_FILE="${SCRIPT_DIR}/.install_complete"
MODE_RECONFIGURE="false"

banner() {
  clear
  echo -e "${RED}${BOLD}"
  echo "  ____  _                            __     __          _ _   "
  echo " / ___|| |_ _ __ ___  __ _ _ __ ___  \ \   / /__ _ _   _| | |_ "
  echo " \___ \| __| '__/ _ \/ _\` | '_ \` _ \  \ \ / / _\` | | | | | __|"
  echo "  ___) | |_| | |  __/ (_| | | | | | |  \ V / (_| | |_| | | |_ "
  echo " |____/ \__|_|  \___|\__,_|_| |_| |_|   \_/ \__,_|\__,_|_|\__|"
  echo -e "${NC}${CYAN}  Production Media Streaming Engine — Universal Server Installer v2.6${NC}"
  echo "=============================================================================="
  echo ""
}

info()    { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[ ✓ ]${NC} $1"; }
warn()    { echo -e "${YELLOW}[ ! ]${NC} $1"; }
error()   { echo -e "${RED}[ ✗ ]${NC} $1"; }

record_checkpoint() {
  CURRENT_STEP="$1"
  echo "$CURRENT_STEP" > "$CHECKPOINT_FILE"
}

save_answer() {
  local key="$1"
  local val="$2"
  mkdir -p "$(dirname "$ANSWERS_FILE")"
  if [ -f "$ANSWERS_FILE" ]; then
    grep -v "^${key}=" "$ANSWERS_FILE" > "${ANSWERS_FILE}.tmp" || true
    mv "${ANSWERS_FILE}.tmp" "$ANSWERS_FILE"
  fi
  echo "${key}=\"${val}\"" >> "$ANSWERS_FILE"
}

handle_interrupt() {
  local exit_code=$?
  # If installation is already marked complete, exit cleanly without error
  if [ -f "$COMPLETE_FILE" ]; then
    exit 0
  fi
  echo ""
  warn "Signal or error encountered during installation."
  # Terminate active background child jobs
  pkill -P $$ 2>/dev/null || true
  # Clean temporary artifacts
  rm -rf /tmp/cf_deb_* /tmp/cloudflared.deb "${TARGET_DIR}/.env.tmp" "/tmp/streamvault_dist_staging" 2>/dev/null || true
  rm -rf "${TARGET_DIR}/frontend/dist_staging" 2>/dev/null || true
  echo -e "${YELLOW}${BOLD}[!] Installation paused/interrupted at Step ${CURRENT_STEP}.${NC}"
  echo -e "${CYAN}Run './install.sh' anytime to resume where you left off.${NC}"
  exit "$exit_code"
}

trap handle_interrupt SIGINT SIGTERM ERR

# Privilege helper
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  else
    error "This script requires root privileges or 'sudo' to install system packages."
    exit 1
  fi
fi

banner

# ==============================================================================
# EXISTING APP DETECTION & MAINTENANCE MENU (SCENARIO 2)
# ==============================================================================
check_existing_app() {
  local has_env="false"
  local has_venv="false"
  [ -f "${SCRIPT_DIR}/.env" ] && has_env="true"
  ([ -d "${SCRIPT_DIR}/.venv" ] || [ -d "${SCRIPT_DIR}/backend/venv" ]) && has_venv="true"

  if [ -f "$COMPLETE_FILE" ] || ([ "$has_env" = "true" ] && [ "$has_venv" = "true" ]); then
    echo -e "${CYAN}${BOLD}=== Existing StreamVault Installation Detected ===${NC}"
    
    local api_status="○ STOPPED"
    local admin_status="○ STOPPED"
    local nginx_status="○ STOPPED"
    local cf_status="○ STOPPED"

    lsof -ti :8000 >/dev/null 2>&1 && api_status="● RUNNING"
    lsof -ti :4000 >/dev/null 2>&1 && admin_status="● RUNNING"
    lsof -ti :8080 >/dev/null 2>&1 && nginx_status="● RUNNING"
    pgrep -f "cloudflared.*tunnel" >/dev/null 2>&1 && cf_status="● RUNNING"

    echo -e "  • FastAPI Media Server [8000] : $([ "$api_status" = "● RUNNING" ] && echo -e "${GREEN}${api_status}${NC}" || echo -e "${RED}${api_status}${NC}")"
    echo -e "  • Local Admin Panel    [4000] : $([ "$admin_status" = "● RUNNING" ] && echo -e "${GREEN}${admin_status}${NC}" || echo -e "${RED}${admin_status}${NC}")"
    echo -e "  • Nginx Single-Origin  [8080] : $([ "$nginx_status" = "● RUNNING" ] && echo -e "${GREEN}${nginx_status}${NC}" || echo -e "${RED}${nginx_status}${NC}")"
    echo -e "  • Cloudflare Tunnel           : $([ "$cf_status" = "● RUNNING" ] && echo -e "${GREEN}${cf_status}${NC}" || echo -e "${RED}${cf_status}${NC}")"
    echo ""
    echo -e "${BOLD}Installation & Maintenance Menu:${NC}"
    echo "  1) Update / Upgrade Existing App (Recommended — Zero Data Loss)"
    echo "  2) Reconfigure Server Settings Only (Domain, Cloudflare Tunnel, Hardware Governor, Boot Auto-Start)"
    echo "  3) Full Clean Reinstall (Rebuild .venv & Frontend from scratch, keep media/ and .env)"
    echo "  4) Stop All StreamVault Services & Exit"
    read -r -p "Select Option [1-4, Default: 1]: " MAINT_CHOICE
    MAINT_CHOICE="${MAINT_CHOICE:-1}"

    case "$MAINT_CHOICE" in
      1)
        info "Running Zero-Downtime Safe Upgrade..."
        local active_venv="${SCRIPT_DIR}/.venv"
        [ ! -d "$active_venv" ] && [ -d "${SCRIPT_DIR}/backend/venv" ] && active_venv="${SCRIPT_DIR}/backend/venv"

        "${active_venv}/bin/pip" install --upgrade pip -q
        if [ -f "${SCRIPT_DIR}/backend/requirements.txt" ]; then
          "${active_venv}/bin/pip" install -r "${SCRIPT_DIR}/backend/requirements.txt" setproctitle -q
        fi

        info "Building frontend into staged directory (Atomic Swap)..."
        cd "${SCRIPT_DIR}/frontend"
        npm install --no-audit --no-fund
        rm -rf dist_staging
        npx vite build --outDir dist_staging
        rm -rf dist.bak
        [ -d dist ] && mv dist dist.bak
        mv dist_staging dist
        rm -rf dist.bak
        cd "${SCRIPT_DIR}"
        success "Frontend updated cleanly at frontend/dist"

        info "Restarting active StreamVault services..."
        ./start.sh restart
        touch "$COMPLETE_FILE"
        trap - SIGINT SIGTERM ERR EXIT
        disown -a 2>/dev/null || true
        echo ""
        success "StreamVault successfully updated and running in background!"
        exit 0
        ;;
      2)
        info "Entering Interactive Reconfiguration Mode..."
        MODE_RECONFIGURE="true"
        ;;
      3)
        warn "Stopping active services and cleaning virtual environment..."
        ./start.sh stop || true
        rm -rf "${SCRIPT_DIR}/.venv" "${SCRIPT_DIR}/frontend/dist" "$CHECKPOINT_FILE" "$COMPLETE_FILE"
        info "Ready for clean setup."
        ;;
      4)
        info "Stopping all StreamVault services..."
        ./start.sh stop
        exit 0
        ;;
      *)
        error "Invalid option selected."
        exit 1
        ;;
    esac
  fi
}

check_existing_app

# ==============================================================================
# RESUME CHECKPOINT DETECTION (SCENARIO 1)
# ==============================================================================
if [ -f "$CHECKPOINT_FILE" ] && [ "$MODE_RECONFIGURE" != "true" ]; then
  LAST_STEP="$(cat "$CHECKPOINT_FILE" 2>/dev/null || echo 1)"
  if [ "$LAST_STEP" -ge 1 ] && [ "$LAST_STEP" -le 8 ]; then
    warn "An interrupted setup was detected at Step ${LAST_STEP}."
    if [ -f "$ANSWERS_FILE" ]; then
      # shellcheck disable=SC1090
      . "$ANSWERS_FILE" 2>/dev/null || true
    fi
    echo "  1) Resume interrupted setup from Step ${LAST_STEP} (uses saved settings)"
    echo "  2) Discard progress and start fresh from Step 1"
    read -r -p "Select option [1 or 2, Default: 1]: " RESUME_CHOICE
    RESUME_CHOICE="${RESUME_CHOICE:-1}"
    if [ "$RESUME_CHOICE" = "1" ]; then
      RESUME_FROM_STEP="$LAST_STEP"
      info "Resuming setup from Step ${RESUME_FROM_STEP}..."
    else
      rm -f "$CHECKPOINT_FILE" "$ANSWERS_FILE"
      info "Starting fresh installation from Step 1..."
    fi
  fi
fi

# ==============================================================================
# OS & ARCHITECTURE DETECTION
# ==============================================================================
info "Detecting Operating System and CPU Architecture..."

OS_TYPE="$(uname -s)"
ARCH_RAW="$(uname -m)"
PKG_MGR=""
DISTRO="Unknown"

case "$ARCH_RAW" in
  x86_64|amd64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) ARCH="$ARCH_RAW" ;;
esac

if [ "$OS_TYPE" = "Linux" ]; then
  if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO="$ID"
  fi
  if command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
  elif command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
  elif command -v yum >/dev/null 2>&1; then
    PKG_MGR="yum"
  elif command -v pacman >/dev/null 2>&1; then
    PKG_MGR="pacman"
  elif command -v apk >/dev/null 2>&1; then
    PKG_MGR="apk"
  fi
elif [ "$OS_TYPE" = "Darwin" ]; then
  DISTRO="macos"
  if command -v brew >/dev/null 2>&1; then
    PKG_MGR="brew"
  else
    error "Homebrew is required on macOS. Install it from https://brew.sh and re-run."
    exit 1
  fi
fi

success "Detected System: ${BOLD}${OS_TYPE} (${DISTRO})${NC} | Architecture: ${BOLD}${ARCH}${NC} | Package Manager: ${BOLD}${PKG_MGR}${NC}"
echo ""

# ==============================================================================
# STEP 1: HARDWARE AUDIT & RESOURCE RECOMMENDATIONS
# ==============================================================================
if [ "$RESUME_FROM_STEP" -le 1 ]; then
  record_checkpoint 1
  echo -e "${BOLD}--- Step 1: Hardware Audit & Resource Recommendations ---${NC}"

  # 1. CPU Cores
  if command -v nproc >/dev/null 2>&1; then
    CPU_CORES="$(nproc)"
  elif [ "$OS_TYPE" = "Darwin" ]; then
    CPU_CORES="$(sysctl -n hw.ncpu)"
  else
    CPU_CORES=2
  fi

  if [ "$CPU_CORES" -le 2 ]; then
    REC_CPU_THREADS=1
    REC_ABR="false"
    CPU_TIER="Minimal / Low-Spec Server (${CPU_CORES} Cores)"
  elif [ "$CPU_CORES" -le 4 ]; then
    REC_CPU_THREADS=2
    REC_ABR="false"
    CPU_TIER="Standard Server (${CPU_CORES} Cores)"
  else
    REC_CPU_THREADS=$(( CPU_CORES / 2 ))
    [ "$REC_CPU_THREADS" -gt 4 ] && REC_CPU_THREADS=4
    REC_ABR="true"
    CPU_TIER="High-Performance Server (${CPU_CORES} Cores)"
  fi

  # 2. RAM & Swap Audit
  if [ "$OS_TYPE" = "Linux" ]; then
    TOTAL_RAM_MB="$(free -m | awk '/^Mem:/{print $2}')"
    AVAIL_RAM_MB="$(free -m | awk '/^Mem:/{print $7}')"
    SWAP_TOTAL_MB="$(free -m | awk '/^Swap:/{print $2}')"
  elif [ "$OS_TYPE" = "Darwin" ]; then
    TOTAL_RAM_BYTES="$(sysctl -n hw.memsize)"
    TOTAL_RAM_MB=$(( TOTAL_RAM_BYTES / 1024 / 1024 ))
    AVAIL_RAM_MB=$(( TOTAL_RAM_MB / 2 ))
    SWAP_TOTAL_MB=2048
  fi

  if [ "$TOTAL_RAM_MB" -lt 2048 ]; then
    REC_RAM_MB=512
  elif [ "$TOTAL_RAM_MB" -lt 4096 ]; then
    REC_RAM_MB=1024
  else
    REC_RAM_MB=2048
  fi

  # 3. Disk Space Audit
  DISK_FREE_GB="$(df -Pk . | awk 'NR==2 {print int($4 / 1024 / 1024)}')"

  # 4. GPU & Driver Audit
  GPU_VENDOR="None"
  GPU_DRIVER_STATUS="None"
  GPU_ACCEL_AVAILABLE="false"
  GPU_ENCODER="none"

  if command -v lspci >/dev/null 2>&1; then
    VGA_INFO="$(lspci | grep -iE 'vga|3d|display' || true)"
  else
    VGA_INFO=""
  fi

  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)"
    GPU_DRIVER="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)"
    GPU_VENDOR="NVIDIA (${GPU_NAME})"
    GPU_DRIVER_STATUS="Installed (v${GPU_DRIVER})"
    GPU_ACCEL_AVAILABLE="true"
    GPU_ENCODER="h264_nvenc"
  elif echo "$VGA_INFO" | grep -qi "nvidia"; then
    GPU_VENDOR="NVIDIA GPU Detected (Hardware Present)"
    GPU_DRIVER_STATUS="MISSING NVIDIA DRIVER (nvidia-smi not found)"
    GPU_ACCEL_AVAILABLE="false"
  elif [ -e /dev/dri/renderD128 ]; then
    GPU_VENDOR="Intel / AMD Integrated or Discrete GPU (/dev/dri/renderD128)"
    GPU_DRIVER_STATUS="Linux DRM/VAAPI Render Node Active"
    GPU_ACCEL_AVAILABLE="true"
    GPU_ENCODER="h264_vaapi"
  elif [ "$OS_TYPE" = "Darwin" ]; then
    GPU_VENDOR="Apple Media Engine (VideoToolbox)"
    GPU_DRIVER_STATUS="Native macOS Acceleration"
    GPU_ACCEL_AVAILABLE="true"
    GPU_ENCODER="h264_videotoolbox"
  fi

  echo "------------------------------------------------------------------------------"
  echo -e "  • ${BOLD}CPU:${NC}         ${CPU_TIER} -> Recommended Worker Threads: ${GREEN}${REC_CPU_THREADS}${NC}"
  echo -e "  • ${BOLD}Memory:${NC}      ${TOTAL_RAM_MB} MB Total (${AVAIL_RAM_MB} MB Free | Swap: ${SWAP_TOTAL_MB} MB)"
  echo -e "  • ${BOLD}Disk Space:${NC}  ${DISK_FREE_GB} GB Free on current filesystem"
  echo -e "  • ${BOLD}GPU:${NC}         ${GPU_VENDOR}"
  echo -e "  • ${BOLD}GPU Driver:${NC}  ${GPU_DRIVER_STATUS}"
  echo "------------------------------------------------------------------------------"

  if [ "$DISK_FREE_GB" -lt 15 ]; then
    warn "Free disk space is low (${DISK_FREE_GB} GB). At least 25 GB+ is recommended for HLS segments and downloads."
    read -r -p "Continue installation anyway? [y/N]: " CONT_DISK
    [[ ! "$CONT_DISK" =~ ^[Yy]$ ]] && exit 1
  fi

  # Low RAM Swap Offer (Linux)
  if [ "$OS_TYPE" = "Linux" ] && [ "$TOTAL_RAM_MB" -lt 2500 ] && [ "$SWAP_TOTAL_MB" -lt 1024 ]; then
    warn "System has less than 2.5 GB RAM and minimal Swap. Building Frontend or running FFmpeg may hit OOM."
    read -r -p "Would you like to automatically create a 2 GB Linux Swapfile (/swapfile) for stability? [Y/n]: " MAKE_SWAP
    MAKE_SWAP="${MAKE_SWAP:-Y}"
    if [[ "$MAKE_SWAP" =~ ^[Yy]$ ]]; then
      info "Creating 2 GB /swapfile..."
      $SUDO fallocate -l 2G /swapfile || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=2048
      $SUDO chmod 600 /swapfile
      $SUDO mkswap /swapfile
      $SUDO swapon /swapfile
      if ! grep -q "/swapfile" /etc/fstab; then
        echo "/swapfile none swap sw 0 0" | $SUDO tee -a /etc/fstab >/dev/null
      fi
      success "2 GB Swapfile created and activated."
    fi
  fi

  # Missing NVIDIA Driver Offer
  if [ "$GPU_DRIVER_STATUS" = "MISSING NVIDIA DRIVER (nvidia-smi not found)" ] && [ "$PKG_MGR" = "apt" ]; then
    warn "An NVIDIA GPU was detected on the PCIe bus, but proprietary NVIDIA drivers are not installed."
    read -r -p "Would you like to install Ubuntu/Debian NVIDIA drivers (ubuntu-drivers / nvidia-driver) now? [y/N]: " INST_NV
    if [[ "$INST_NV" =~ ^[Yy]$ ]]; then
      $SUDO apt-get update -y
      $SUDO apt-get install -y ubuntu-drivers-common || true
      if command -v ubuntu-drivers >/dev/null 2>&1; then
        $SUDO ubuntu-drivers autoinstall
      else
        $SUDO apt-get install -y nvidia-driver-535 libnvidia-encode-535 || true
      fi
      warn "NVIDIA drivers installed. A system reboot will be required after installation for NVENC to activate."
    fi
  fi

  # Interactive Resource Allocation Prompts
  echo ""
  echo -e "${CYAN}${BOLD}Configure StreamVault Transcoder Resource Governor:${NC}"
  read -r -p "1. Allocate CPU Worker Threads [1-${CPU_CORES}] (Recommended: ${REC_CPU_THREADS}): " USER_CPU_THREADS
  USER_CPU_THREADS="${USER_CPU_THREADS:-$REC_CPU_THREADS}"
  save_answer "USER_CPU_THREADS" "$USER_CPU_THREADS"

  read -r -p "2. Allocate Max Transcoder RAM Limit in MB [512/1024/2048/4096] (Recommended: ${REC_RAM_MB}): " USER_RAM_MB
  USER_RAM_MB="${USER_RAM_MB:-$REC_RAM_MB}"
  save_answer "USER_RAM_MB" "$USER_RAM_MB"

  if [ "$GPU_ACCEL_AVAILABLE" = "true" ]; then
    read -r -p "3. Enable Hardware GPU Acceleration (${GPU_ENCODER})? [Y/n]: " USER_GPU_CHOICE
    USER_GPU_CHOICE="${USER_GPU_CHOICE:-Y}"
    [[ "$USER_GPU_CHOICE" =~ ^[Yy]$ ]] && USER_GPU_ENABLED="true" || USER_GPU_ENABLED="false"
  else
    info "No active GPU hardware encoder detected. Locking Transcoder to CPU-Safe Governor."
    USER_GPU_ENABLED="false"
  fi
  save_answer "USER_GPU_ENABLED" "$USER_GPU_ENABLED"

  if [ "$REC_ABR" = "true" ] || [ "$USER_GPU_ENABLED" = "true" ]; then
    DEF_ABR_PROMPT="y/N"
  else
    DEF_ABR_PROMPT="y/N (Recommended: N for Lossless Remux on low-spec CPU)"
  fi
  read -r -p "4. Enable Multi-Quality ABR Transcoding (1080p/720p/480p) by default? [${DEF_ABR_PROMPT}]: " USER_ABR_CHOICE
  [[ "$USER_ABR_CHOICE" =~ ^[Yy]$ ]] && USER_ABR_ENABLED="true" || USER_ABR_ENABLED="false"
  save_answer "USER_ABR_ENABLED" "$USER_ABR_ENABLED"

  echo ""
fi

# ==============================================================================
# STEP 2: PROJECT DIRECTORY & MEDIA FOLDER SELECTION
# ==============================================================================
if [ "$RESUME_FROM_STEP" -le 2 ]; then
  record_checkpoint 2
  echo -e "${BOLD}--- Step 2: Project Directory & Storage Paths ---${NC}"
  read -r -p "Enter target Project Directory [Default: ${TARGET_DIR}]: " USER_TARGET_DIR
  TARGET_DIR="${USER_TARGET_DIR:-$TARGET_DIR}"
  TARGET_DIR="$(eval echo "$TARGET_DIR")"
  save_answer "TARGET_DIR" "$TARGET_DIR"

  if [ "$TARGET_DIR" != "$SCRIPT_DIR" ]; then
    mkdir -p "$TARGET_DIR"
    if [ -d "${SCRIPT_DIR}/backend" ] && [ -d "${SCRIPT_DIR}/frontend" ]; then
      info "Copying StreamVault project files to ${TARGET_DIR}..."
      rsync -a --exclude 'node_modules' --exclude '.venv' --exclude '__pycache__' "${SCRIPT_DIR}/" "${TARGET_DIR}/"
    fi
  fi

  cd "$TARGET_DIR"
  DEFAULT_MEDIA_DIR="${TARGET_DIR}/media"
  read -r -p "Enter Media Storage Directory for downloads & HLS library [Default: ${DEFAULT_MEDIA_DIR}]: " USER_MEDIA_DIR
  MEDIA_DIR="${USER_MEDIA_DIR:-$DEFAULT_MEDIA_DIR}"
  MEDIA_DIR="$(eval echo "$MEDIA_DIR")"
  save_answer "MEDIA_DIR" "$MEDIA_DIR"

  mkdir -p "${MEDIA_DIR}/incoming" "${MEDIA_DIR}/library" "${MEDIA_DIR}/downloads"
  success "Project Path: ${BOLD}${TARGET_DIR}${NC}"
  success "Media Library Path: ${BOLD}${MEDIA_DIR}${NC}"
  echo ""
fi

# ==============================================================================
# STEP 3: CHECK & INSTALL REQUIRED SYSTEM PACKAGES
# ==============================================================================
if [ "$RESUME_FROM_STEP" -le 3 ] && [ "$MODE_RECONFIGURE" != "true" ]; then
  record_checkpoint 3
  echo -e "${BOLD}--- Step 3: Checking & Installing Required System Dependencies ---${NC}"

  install_base_packages() {
    case "$PKG_MGR" in
      apt)
        $SUDO apt-get update -y
        $SUDO apt-get install -y curl wget git jq openssl build-essential python3 python3-venv python3-pip python3-dev ffmpeg aria2 nginx psmisc lsof ca-certificates gnupg
        if [ -e /dev/dri/renderD128 ]; then
          $SUDO apt-get install -y vainfo intel-media-va-driver mesa-va-drivers || true
        fi
        ;;
      dnf|yum)
        $SUDO $PKG_MGR install -y epel-release || true
        $SUDO $PKG_MGR install -y curl wget git jq openssl gcc python3 python3-pip python3-devel ffmpeg aria2 nginx psmisc lsof ca-certificates
        ;;
      pacman)
        $SUDO pacman -Sy --noconfirm curl wget git jq openssl base-devel python python-pip ffmpeg aria2 nginx psmisc lsof ca-certificates
        ;;
      brew)
        brew install curl wget git jq openssl python@3.12 node ffmpeg aria2 nginx
        ;;
      *)
        warn "Unsupported automatic package manager. Ensure python3, node, ffmpeg, aria2c, and nginx are installed."
        ;;
    esac
  }

  install_base_packages

  # Check & Install Node.js >= 20 LTS
  NEED_NODE="false"
  if ! command -v node >/dev/null 2>&1; then
    NEED_NODE="true"
  else
    NODE_MAJOR="$(node -v | cut -d. -f1 | tr -d 'v')"
    if [ "$NODE_MAJOR" -lt 18 ]; then
      NEED_NODE="true"
    fi
  fi

  if [ "$NEED_NODE" = "true" ]; then
    info "Installing Node.js 20 LTS..."
    if [ "$PKG_MGR" = "apt" ]; then
      curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO -E bash -
      $SUDO apt-get install -y nodejs
    elif [ "$PKG_MGR" = "dnf" ] || [ "$PKG_MGR" = "yum" ]; then
      curl -fsSL https://rpm.nodesource.com/setup_20.x | $SUDO bash -
      $SUDO $PKG_MGR install -y nodejs
    fi
  fi

  success "System dependencies verified."
  echo ""
fi

# ==============================================================================
# STEP 4: DEPLOYMENT MODE SELECTION & CLOUDFLARE NAMED TUNNEL
# ==============================================================================
if [ "$RESUME_FROM_STEP" -le 4 ]; then
  record_checkpoint 4
  echo -e "${BOLD}--- Step 4: Select Server Deployment Mode ---${NC}"
  echo "  1) Localhost / Home LAN Mode       (Access via http://localhost:8080 or LAN IP)"
  echo "  2) Production / Self-Hosted Domain (Authenticated Cloudflare Tunnel + Auto DNS Records)"
  read -r -p "Select Deployment Mode [1 or 2, Default: ${DEPLOY_MODE:-1}]: " USER_DEPLOY_MODE
  DEPLOY_MODE="${USER_DEPLOY_MODE:-${DEPLOY_MODE:-1}}"
  save_answer "DEPLOY_MODE" "$DEPLOY_MODE"

  NGINX_PORT=8080
  PUBLIC_DOMAIN="${PUBLIC_DOMAIN:-localhost}"
  FRONTEND_ORIGIN="${FRONTEND_ORIGIN:-http://localhost:${NGINX_PORT}}"

  install_cloudflared() {
    if ! command -v cloudflared >/dev/null 2>&1; then
      info "Installing official Cloudflare Tunnel daemon (cloudflared) for ${OS_TYPE}/${ARCH}..."
      if [ "$OS_TYPE" = "Darwin" ]; then
        brew install cloudflare/cloudflare/cloudflared
      elif [ "$PKG_MGR" = "apt" ]; then
        local cf_deb_url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb"
        local cf_tmp_dir
        cf_tmp_dir=$(mktemp -d /tmp/cf_deb_XXXXXX)
        chmod 700 "$cf_tmp_dir"
        local cf_deb_path="${cf_tmp_dir}/cloudflared.deb"
        curl -fsSL "$cf_deb_url" -o "$cf_deb_path"
        $SUDO dpkg -i "$cf_deb_path"
        rm -rf "$cf_tmp_dir"
      else
        local cf_bin_url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}"
        $SUDO curl -fsSL "$cf_bin_url" -o /usr/local/bin/cloudflared
        $SUDO chmod +x /usr/local/bin/cloudflared
      fi
    fi
    success "cloudflared ready: $(cloudflared --version 2>/dev/null || echo 'installed')"
  }

  if [ "$DEPLOY_MODE" = "1" ]; then
    echo ""
    echo -e "${CYAN}${BOLD}Free Cloudflare Public Access (No Custom Domain Required):${NC}"
    echo "  Would you like to bind this localhost server to the internet using a free Cloudflare domain/tunnel?"
    echo "  This allows anyone on the internet to stream securely from your server via HTTPS without owning a domain."
    read -r -p "Enable Free Cloudflare Public Access? [y/N]: " ENABLE_FREE_CF
    ENABLE_FREE_CF="${ENABLE_FREE_CF:-N}"

    if [[ "$ENABLE_FREE_CF" =~ ^[Yy]$ ]]; then
      ENABLE_TUNNEL="true"
      save_answer "ENABLE_TUNNEL" "true"

      echo ""
      echo -e "${BOLD}Select Free Cloudflare Domain Method:${NC}"
      echo "  1) Cloudflare Quick Tunnel (*.trycloudflare.com) [Automatic, Zero Setup, Instant HTTPS URL]"
      echo "  2) Cloudflare Pages (*.pages.dev)               [Permanent Custom Subdomain e.g. https://myvault.pages.dev]"
      read -r -p "Select Free Method [1 or 2, Default: 1]: " FREE_CF_METHOD
      FREE_CF_METHOD="${FREE_CF_METHOD:-1}"
      save_answer "FREE_CF_METHOD" "$FREE_CF_METHOD"

      install_cloudflared

      if [ "$FREE_CF_METHOD" = "2" ]; then
        TUNNEL_TYPE="pages"
        echo ""
        echo -e "${CYAN}Cloudflare Pages (.pages.dev) Configuration:${NC}"
        echo "Enter your desired or existing Cloudflare Pages project name."
        echo "(For example, entering 'streamvault' maps to https://streamvault.pages.dev)"
        read -r -p "Pages Project Name [Default: streamvault]: " CF_PAGES_NAME
        CF_PAGES_NAME="$(echo "${CF_PAGES_NAME:-streamvault}" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9-')"
        PUBLIC_DOMAIN="${CF_PAGES_NAME}.pages.dev"
        FRONTEND_ORIGIN="https://${PUBLIC_DOMAIN}"
        CF_PAGES_PROJECT="$CF_PAGES_NAME"
        save_answer "PUBLIC_DOMAIN" "$PUBLIC_DOMAIN"
        save_answer "FRONTEND_ORIGIN" "$FRONTEND_ORIGIN"
        save_answer "TUNNEL_TYPE" "pages"
        save_answer "CF_PAGES_PROJECT" "$CF_PAGES_PROJECT"

        echo ""
        echo -e "${YELLOW}Optional Automated Cloudflare Pages Sync:${NC}"
        echo "If you provide your Cloudflare Account ID and API Token, StreamVault can automatically"
        echo "update your Cloudflare Pages target tunnel whenever the server starts."
        read -r -p "Cloudflare Account ID (press Enter to skip): " CF_ACCOUNT_ID
        if [ -n "$CF_ACCOUNT_ID" ]; then
          read -r -p "Cloudflare API Token (with Pages edit permissions): " CF_API_TOKEN
          save_answer "CLOUDFLARE_ACCOUNT_ID" "$CF_ACCOUNT_ID"
          save_answer "CLOUDFLARE_API_TOKEN" "$CF_API_TOKEN"
        fi
        success "Cloudflare Pages configured for https://${PUBLIC_DOMAIN}."
        echo -e "Deployment worker template is available at ${CYAN}${TARGET_DIR}/cloudflare-pages/_worker.js${NC}"
      else
        TUNNEL_TYPE="quick"
        PUBLIC_DOMAIN="trycloudflare.com"
        FRONTEND_ORIGIN=""
        save_answer "PUBLIC_DOMAIN" "trycloudflare.com"
        save_answer "FRONTEND_ORIGIN" ""
        save_answer "TUNNEL_TYPE" "quick"
        success "Cloudflare Quick Tunnel enabled (automatic *.trycloudflare.com URL)."
      fi
    else
      ENABLE_TUNNEL="false"
      TUNNEL_TYPE="none"
      save_answer "ENABLE_TUNNEL" "false"
      save_answer "TUNNEL_TYPE" "none"
    fi
  elif [ "$DEPLOY_MODE" = "2" ]; then
    echo ""
    echo -e "${CYAN}${BOLD}=== Cloudflare Authenticated Named Tunnel Provisioning ===${NC}"
    install_cloudflared

    CF_CERT_PATH="${HOME}/.cloudflared/cert.pem"
    if [ -f "$CF_CERT_PATH" ]; then
      success "Existing Cloudflare authentication certificate found at ${CF_CERT_PATH}"
      read -r -p "Do you want to re-authenticate with a different Cloudflare account/zone? [y/N]: " REAUTH_CF
      if [[ "$REAUTH_CF" =~ ^[Yy]$ ]]; then
        rm -f "$CF_CERT_PATH"
        cloudflared tunnel login
      fi
    else
      info "Launching 'cloudflared tunnel login'..."
      echo "  -> Copy/open the URL printed below in your browser, log in to Cloudflare, and select your domain zone."
      cloudflared tunnel login
    fi

    if [ ! -f "$CF_CERT_PATH" ]; then
      error "Cloudflare certificate (${CF_CERT_PATH}) was not created. Please complete browser login and re-run."
      exit 1
    fi
    success "Cloudflare Account Authenticated!"

    echo ""
    read -r -p "Enter a name for your Cloudflare Tunnel [Default: ${CF_TUNNEL_NAME:-streamvault-prod}]: " USER_CF_NAME
    CF_TUNNEL_NAME="${USER_CF_NAME:-${CF_TUNNEL_NAME:-streamvault-prod}}"
    save_answer "CF_TUNNEL_NAME" "$CF_TUNNEL_NAME"

    EXISTING_UUID="$(cloudflared tunnel list | awk -v name="$CF_TUNNEL_NAME" '$2 == name {print $1}' | head -n1)"
    if [ -n "$EXISTING_UUID" ]; then
      CF_TUNNEL_UUID="$EXISTING_UUID"
      success "Reusing existing Cloudflare Tunnel '${CF_TUNNEL_NAME}' (UUID: ${CF_TUNNEL_UUID})"
    else
      info "Creating new Cloudflare Tunnel '${CF_TUNNEL_NAME}'..."
      cloudflared tunnel create "$CF_TUNNEL_NAME"
      CF_TUNNEL_UUID="$(cloudflared tunnel list | awk -v name="$CF_TUNNEL_NAME" '$2 == name {print $1}' | head -n1)"
      success "Created Tunnel '${CF_TUNNEL_NAME}' (UUID: ${CF_TUNNEL_UUID})"
    fi

    CF_CRED_FILE="${HOME}/.cloudflared/${CF_TUNNEL_UUID}.json"
    if [ ! -f "$CF_CRED_FILE" ]; then
      error "Tunnel credentials file not found at ${CF_CRED_FILE}."
      exit 1
    fi

    echo ""
    echo -e "${CYAN}Enter the Domain or Subdomain where StreamVault will be hosted${NC}"
    echo "  Examples: stream.yourdomain.com  OR  watch.yourdomain.com  OR  yourdomain.com"
    while true; do
      read -r -p "Public Domain / Hostname: " USER_PUB_DOM
      USER_PUB_DOM="${USER_PUB_DOM:-$PUBLIC_DOMAIN}"
      USER_PUB_DOM="$(echo "$USER_PUB_DOM" | tr -d ' ' | sed -e 's|^https\?://||' -e 's|/.*$||')"
      if [[ "$USER_PUB_DOM" =~ ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
        PUBLIC_DOMAIN="$USER_PUB_DOM"
        break
      else
        warn "Please enter a valid domain or subdomain (e.g., stream.example.com)."
      fi
    done
    save_answer "PUBLIC_DOMAIN" "$PUBLIC_DOMAIN"
    FRONTEND_ORIGIN="https://${PUBLIC_DOMAIN}"
    save_answer "FRONTEND_ORIGIN" "$FRONTEND_ORIGIN"

    info "Routing Cloudflare DNS CNAME record for ${BOLD}${PUBLIC_DOMAIN}${NC} -> Tunnel ${BOLD}${CF_TUNNEL_UUID}${NC}..."
    cloudflared tunnel route dns --overwrite-dns "$CF_TUNNEL_NAME" "$PUBLIC_DOMAIN"
    success "Cloudflare DNS CNAME record updated for https://${PUBLIC_DOMAIN}!"

    mkdir -p "${HOME}/.cloudflared"
    cat > "${HOME}/.cloudflared/config.yml" <<EOF
tunnel: ${CF_TUNNEL_UUID}
credentials-file: ${CF_CRED_FILE}
protocol: http2

ingress:
  - hostname: ${PUBLIC_DOMAIN}
    service: http://127.0.0.1:${NGINX_PORT}
    originRequest:
      noTLSVerify: true
      connectTimeout: 30s
      keepAliveTimeout: 90s
      httpHostHeader: ${PUBLIC_DOMAIN}
  - service: http_status:404
EOF
    success "Generated Cloudflare Tunnel config at ${HOME}/.cloudflared/config.yml"
  fi
  echo ""
fi

# ==============================================================================
# STEP 5: ENVIRONMENT & API CONFIGURATION (.env)
# ==============================================================================
if [ "$RESUME_FROM_STEP" -le 5 ]; then
  record_checkpoint 5
  echo -e "${BOLD}--- Step 5: Environment & API Configuration (.env) ---${NC}"

  ENV_FILE="${TARGET_DIR}/.env"
  if [ -f "$ENV_FILE" ]; then
    info "Existing .env file detected."
    read -r -p "Keep existing API keys in .env and only update server/resource variables? [Y/n]: " KEEP_ENV
    KEEP_ENV="${KEEP_ENV:-Y}"
  else
    KEEP_ENV="N"
  fi

  if [[ ! "$KEEP_ENV" =~ ^[Yy]$ ]]; then
    read -r -p "Supabase Project URL (e.g. https://xyz.supabase.co): " SUPABASE_URL
    read -r -p "Supabase Anon Public Key: " SUPABASE_ANON_KEY
    read -r -p "Supabase Service Role Secret Key: " SUPABASE_SERVICE_ROLE_KEY
    read -r -p "TMDB API v3 Key / Read Token: " TMDB_API_KEY
    read -r -p "OMDb API Key (Optional, press Enter to skip): " OMDB_API_KEY

    ADMIN_SECRET="$(openssl rand -hex 32)"
    STREAM_TOKEN_SECRET="$(openssl rand -hex 32)"
    ARIA2_RPC_SECRET="$(openssl rand -hex 16)"

    ( umask 077 && touch "$ENV_FILE" && chmod 600 "$ENV_FILE" )
    cat > "$ENV_FILE" <<EOF
# --- Frontend (Vite) ---
VITE_SUPABASE_URL=${SUPABASE_URL}
VITE_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}
VITE_MEDIA_SERVER_URL=

# --- Backend & Supabase ---
SUPABASE_URL=${SUPABASE_URL}
SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}
SUPABASE_SERVICE_ROLE_KEY=${SUPABASE_SERVICE_ROLE_KEY}

# --- Metadata Providers ---
TMDB_API_KEY=${TMDB_API_KEY}
OMDB_API_KEY=${OMDB_API_KEY}

# --- Security & Streaming Secrets ---
ADMIN_API_SECRET=${ADMIN_SECRET}
ADMIN_SECRET=${ADMIN_SECRET}
HMAC_STREAM_SECRET=${STREAM_TOKEN_SECRET}
STREAM_TOKEN_SECRET=${STREAM_TOKEN_SECRET}
ARIA2_RPC_SECRET=${ARIA2_RPC_SECRET}

# --- Deployment & Storage ---
FRONTEND_ORIGIN=${FRONTEND_ORIGIN}
PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
MEDIA_DIR=${MEDIA_DIR}
ENABLE_TUNNEL=${ENABLE_TUNNEL:-false}
TUNNEL_TYPE=${TUNNEL_TYPE:-none}
CF_PAGES_PROJECT=${CF_PAGES_PROJECT:-}
CLOUDFLARE_ACCOUNT_ID=${CLOUDFLARE_ACCOUNT_ID:-}
CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN:-}

# --- Hardware Resource Governor Defaults ---
DEFAULT_ALLOCATED_CPU_CORES=${USER_CPU_THREADS}
DEFAULT_ALLOCATED_RAM_MB=${USER_RAM_MB}
DEFAULT_GPU_ENABLED=${USER_GPU_ENABLED}
DEFAULT_TRANSCODING_ENABLED=${USER_ABR_ENABLED}
EOF
    chmod 600 "$ENV_FILE"
    success "Created secured .env configuration."
  else
    ( umask 077 && touch "${ENV_FILE}.tmp" && chmod 600 "${ENV_FILE}.tmp" )
    grep -vE '^(FRONTEND_ORIGIN|PUBLIC_DOMAIN|MEDIA_DIR|VITE_MEDIA_SERVER_URL|ENABLE_TUNNEL|TUNNEL_TYPE|CF_PAGES_PROJECT|CLOUDFLARE_ACCOUNT_ID|CLOUDFLARE_API_TOKEN|DEFAULT_ALLOCATED_CPU_CORES|DEFAULT_ALLOCATED_RAM_MB|DEFAULT_GPU_ENABLED|DEFAULT_TRANSCODING_ENABLED)=' "$ENV_FILE" > "${ENV_FILE}.tmp" || true
    cat >> "${ENV_FILE}.tmp" <<EOF
VITE_MEDIA_SERVER_URL=
FRONTEND_ORIGIN=${FRONTEND_ORIGIN}
PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
MEDIA_DIR=${MEDIA_DIR}
ENABLE_TUNNEL=${ENABLE_TUNNEL:-false}
TUNNEL_TYPE=${TUNNEL_TYPE:-none}
CF_PAGES_PROJECT=${CF_PAGES_PROJECT:-}
CLOUDFLARE_ACCOUNT_ID=${CLOUDFLARE_ACCOUNT_ID:-}
CLOUDFLARE_API_TOKEN=${CLOUDFLARE_API_TOKEN:-}
DEFAULT_ALLOCATED_CPU_CORES=${USER_CPU_THREADS}
DEFAULT_ALLOCATED_RAM_MB=${USER_RAM_MB}
DEFAULT_GPU_ENABLED=${USER_GPU_ENABLED}
DEFAULT_TRANSCODING_ENABLED=${USER_ABR_ENABLED}
EOF
    mv "${ENV_FILE}.tmp" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    success "Updated existing .env configuration."
  fi

  if [ -d "${TARGET_DIR}/frontend" ]; then
    ( umask 077 && touch "${TARGET_DIR}/frontend/.env" && chmod 600 "${TARGET_DIR}/frontend/.env" )
    grep -E '^VITE_' "$ENV_FILE" > "${TARGET_DIR}/frontend/.env" || true
  fi
  echo ""
fi

# ==============================================================================
# STEP 6: BACKEND ENVIRONMENT & STAGED FRONTEND PRODUCTION BUILD
# ==============================================================================
if [ "$RESUME_FROM_STEP" -le 6 ]; then
  record_checkpoint 6
  echo -e "${BOLD}--- Step 6: Building Backend Environment & Production Web Bundle ---${NC}"

  # 1. Branded Process Binary Symlinks (.bin/)
  mkdir -p "${TARGET_DIR}/.bin"
  ln -sf "$(command -v ffmpeg)" "${TARGET_DIR}/.bin/streamvault-transcoder"
  ln -sf "$(command -v aria2c)" "${TARGET_DIR}/.bin/streamvault-downloader"

  # 2. Virtual Environment
  ACTIVE_VENV="${TARGET_DIR}/.venv"
  if [ ! -d "$ACTIVE_VENV" ] && [ -d "${TARGET_DIR}/backend/venv" ]; then
    ACTIVE_VENV="${TARGET_DIR}/backend/venv"
  elif [ ! -d "$ACTIVE_VENV" ]; then
    python3 -m venv "$ACTIVE_VENV"
  fi

  "${ACTIVE_VENV}/bin/pip" install --upgrade pip -q
  if [ -f "${TARGET_DIR}/backend/requirements.txt" ]; then
    "${ACTIVE_VENV}/bin/pip" install -r "${TARGET_DIR}/backend/requirements.txt" setproctitle -q
  fi

  # Symlink synchronization for paths
  if [ -d "${TARGET_DIR}/.venv" ] && [ ! -d "${TARGET_DIR}/backend/venv" ]; then
    ln -sf "${TARGET_DIR}/.venv" "${TARGET_DIR}/backend/venv" 2>/dev/null || true
  elif [ -d "${TARGET_DIR}/backend/venv" ] && [ ! -d "${TARGET_DIR}/.venv" ]; then
    ln -sf "${TARGET_DIR}/backend/venv" "${TARGET_DIR}/.venv" 2>/dev/null || true
  fi
  success "Python virtual environment ready."

  # 3. Staged Frontend Build (Atomic Swap)
  if [ -d "${TARGET_DIR}/frontend" ]; then
    info "Installing frontend dependencies & building optimized production bundle..."
    cd "${TARGET_DIR}/frontend"
    npm install --no-audit --no-fund
    rm -rf dist_staging
    npx vite build --outDir dist_staging
    rm -rf dist.bak
    [ -d dist ] && mv dist dist.bak
    mv dist_staging dist
    rm -rf dist.bak
    cd "${TARGET_DIR}"
    success "Frontend production bundle verified at ${TARGET_DIR}/frontend/dist"
  fi

  # 4. Compile Admin Local Tailwind Stylesheet
  if [ -f "${TARGET_DIR}/frontend/node_modules/.bin/tailwindcss" ] && [ -f "${TARGET_DIR}/admin-local/tailwind.input.css" ]; then
    if [ ! -f "${TARGET_DIR}/admin-local/static/tailwind.min.css" ] || [ "${TARGET_DIR}/admin-local/server.py" -nt "${TARGET_DIR}/admin-local/static/tailwind.min.css" ]; then
      info "Compiling Admin Panel static Tailwind stylesheet..."
      mkdir -p "${TARGET_DIR}/admin-local/static"
      NODE_OPTIONS="--max-old-space-size=256" "${TARGET_DIR}/frontend/node_modules/.bin/tailwindcss" -i "${TARGET_DIR}/admin-local/tailwind.input.css" -o "${TARGET_DIR}/admin-local/static/tailwind.min.css" --minify
      success "Admin Panel Tailwind stylesheet compiled at ${TARGET_DIR}/admin-local/static/tailwind.min.css"
    fi
  fi
  echo ""
fi

# ==============================================================================
# STEP 7: CONFIGURE NGINX SINGLE-ORIGIN GATEWAY
# ==============================================================================
if [ "$RESUME_FROM_STEP" -le 7 ]; then
  record_checkpoint 7
  echo -e "${BOLD}--- Step 7: Configuring Nginx Single-Origin Gateway (Port ${NGINX_PORT:-8080}) ---${NC}"

  mkdir -p "${TARGET_DIR}/nginx"
  cat > "${TARGET_DIR}/nginx/streamvault.conf" <<EOF
# Only trust CF-Connecting-IP when the TCP connection comes from local cloudflared (127.0.0.1/::1) AND includes a CF-Ray header
map "\$remote_addr:\$http_cf_ray" \$trusted_cf_ip {
    "~^(127\.0\.0\.1|::1):.+"  \$http_cf_connecting_ip;
    default                    "";
}

map "\$remote_addr:\$http_cf_ray" \$trusted_cf_country {
    "~^(127\.0\.0\.1|::1):.+"  \$http_cf_ipcountry;
    default                    "";
}

map "\$remote_addr:\$http_cf_ray" \$trusted_cf_city {
    "~^(127\.0\.0\.1|::1):.+"  \$http_cf_ipcity;
    default                    "";
}

map "\$remote_addr:\$http_cf_ray" \$trusted_cf_ipv6 {
    "~^(127\.0\.0\.1|::1):.+"  \$http_cf_connecting_ipv6;
    default                    "";
}

map "\$remote_addr:\$http_cf_ray" \$trusted_cf_pseudo_ipv4 {
    "~^(127\.0\.0\.1|::1):.+"  \$http_cf_pseudo_ipv4;
    default                    "";
}

map "\$remote_addr:\$http_cf_ray:\$http_x_real_ip" \$trusted_client_real_ip {
    "~^(127\.0\.0\.1|::1):[^:]+:.*"  \$http_cf_connecting_ip;
    "~^(127\.0\.0\.1|::1)::.+"       \$http_x_real_ip;
    default                          \$remote_addr;
}

server {
    listen ${NGINX_PORT:-8080} default_server;
    listen [::]:${NGINX_PORT:-8080} default_server;
    server_name ${PUBLIC_DOMAIN:-localhost} localhost 127.0.0.1 _;

    root ${TARGET_DIR}/frontend/dist;
    index index.html;

    server_tokens off;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;

    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types
        text/plain
        text/css
        text/xml
        application/json
        application/javascript
        application/xml
        application/vnd.apple.mpegurl
        text/vtt;

    client_max_body_size 25M;
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    aio threads;

    # Strictly block external/public access to internal Admin, System, and Aria2 endpoints
    location ~ ^/api/(health|system|aria2|admin|docs|redoc|openapi\.json) {
        return 404;
    }

    # Zero-copy kernel sendfile delivery for HLS segments via X-Accel-Redirect
    location /_internal_hls/ {
        internal;
        alias ${TARGET_DIR}/media/library/;
        add_header Cache-Control "private, no-transform, max-age=21600";
        add_header Access-Control-Allow-Origin "*";
    }

    # 1. API & HLS Streaming Reverse Proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000/api/;
        proxy_http_version 1.1;

        # Forward real client headers for security and rate limiting
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$trusted_client_real_ip;
        proxy_set_header X-Forwarded-For \$trusted_client_real_ip;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header CF-Connecting-IP \$trusted_cf_ip;
        proxy_set_header CF-IPCountry \$trusted_cf_country;
        proxy_set_header CF-IPCity \$trusted_cf_city;
        proxy_set_header CF-Connecting-IPv6 \$trusted_cf_ipv6;
        proxy_set_header CF-Pseudo-IPv4 \$trusted_cf_pseudo_ipv4;

        # Low-latency streaming optimizations (disable proxy buffering for HLS chunks)
        proxy_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }

    # 2. Direct Media Ingestion & Static Media Proxy
    location /media/ {
        proxy_pass http://127.0.0.1:8000/media/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$trusted_client_real_ip;
        proxy_set_header X-Forwarded-For \$trusted_client_real_ip;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
    }

    # 3. Immutable Static Assets Caching (Vite hashed bundles: /assets/*)
    location /assets/ {
        expires 30d;
        add_header Cache-Control "public, max-age=2592000, immutable";
        add_header X-Content-Type-Options "nosniff" always;
        try_files \$uri =404;
    }

    # Dynamic XML Sitemap with static fallback
    location = /sitemap.xml {
        proxy_pass http://127.0.0.1:8000/sitemap.xml;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$trusted_client_real_ip;
        proxy_set_header X-Forwarded-For \$trusted_client_real_ip;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_intercept_errors on;
        error_page 502 503 504 = @static_sitemap;
    }

    location @static_sitemap {
        try_files /sitemap.xml =404;
    }

    # Robots.txt
    location = /robots.txt {
        try_files /robots.txt =404;
    }

    # 4. Single-Page Application (SPA) HTML fallback
    location / {
        try_files \$uri \$uri/ /index.html;
        add_header Cache-Control "no-cache, must-revalidate";
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
    }
}
EOF

  # Ensure standalone nginx.conf is also synchronized with the target installation directory
  if [ -f "${TARGET_DIR}/nginx.conf" ]; then
    sed -i "s|/var/www/streamvault|${TARGET_DIR}|g; s|/home/naim/netflix|${TARGET_DIR}|g" "${TARGET_DIR}/nginx.conf" 2>/dev/null || true
  fi

  if [ -d /etc/nginx/conf.d ]; then
    chmod o+x "$HOME" "$TARGET_DIR" "${TARGET_DIR}/frontend" "${TARGET_DIR}/frontend/dist" 2>/dev/null || true
    $SUDO cp "${TARGET_DIR}/nginx/streamvault.conf" /etc/nginx/conf.d/streamvault.conf
    $SUDO nginx -t && $SUDO systemctl restart nginx || true
    success "System Nginx configured on port ${NGINX_PORT:-8080}."
  fi
  echo ""
fi

# ==============================================================================
# STEP 8: BOOT AUTO-START & LAUNCH SERVICES (SCENARIO 3)
# ==============================================================================
record_checkpoint 8
echo -e "${BOLD}--- Step 8: Boot Auto-Start & Launching StreamVault ---${NC}"

# Boot Auto-Start Prompt
if command -v systemctl >/dev/null 2>&1; then
  read -r -p "Do you want StreamVault to start automatically when the server reboots? [Y/n]: " AUTO_BOOT_PROMPT
  AUTO_BOOT_PROMPT="${AUTO_BOOT_PROMPT:-Y}"
  if [[ "$AUTO_BOOT_PROMPT" =~ ^[Yy]$ ]]; then
    "${TARGET_DIR}/start.sh" autostart on || true
  else
    "${TARGET_DIR}/start.sh" autostart off 2>/dev/null || true
  fi
fi

# Launch Services
if [ -f "${TARGET_DIR}/start.sh" ]; then
  chmod +x "${TARGET_DIR}/start.sh"
  info "Launching StreamVault services..."
  if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled streamvault.service >/dev/null 2>&1; then
    $SUDO systemctl restart streamvault.service || "${TARGET_DIR}/start.sh" start
  else
    "${TARGET_DIR}/start.sh" start
  fi
fi

# Clean up checkpoints on successful completion
rm -f "$CHECKPOINT_FILE" "$ANSWERS_FILE"
touch "$COMPLETE_FILE"

# Disarm trap handler so viewing links or pressing Ctrl+C never prints interrupted
trap - SIGINT SIGTERM ERR EXIT

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || ipconfig getifaddr en0 2>/dev/null || echo '127.0.0.1')"

echo ""
echo "=============================================================================="
echo -e "${GREEN}${BOLD}  ✓ STREAMVAULT INSTALLATION & SETUP COMPLETE!${NC}"
echo "=============================================================================="
echo ""
echo -e "${BOLD}Hardware Governor Profile Applied:${NC}"
echo "  • CPU Worker Threads : ${USER_CPU_THREADS:-2} / ${CPU_CORES:-4} Cores"
echo "  • RAM Buffer Limit   : ${USER_RAM_MB:-2048} MB"
echo "  • GPU Offload        : ${USER_GPU_ENABLED:-false}"
echo "  • Multi-Bitrate ABR  : ${USER_ABR_ENABLED:-false}"
echo ""

if [ "${DEPLOY_MODE:-1}" = "1" ]; then
  if [ "${ENABLE_TUNNEL:-false}" = "true" ]; then
    echo -e "${CYAN}${BOLD}Free Cloudflare Public Access Links:${NC}"
    if [ "${TUNNEL_TYPE:-}" = "pages" ] || [[ "${PUBLIC_DOMAIN:-}" =~ \.pages\.dev$ ]]; then
      echo -e "  • ${BOLD}Free Public Web App URL:${NC}      ${GREEN}https://${PUBLIC_DOMAIN}${NC}"
      echo -e "  • ${BOLD}Cloudflare Pages Worker:${NC}      ${TARGET_DIR}/cloudflare-pages/_worker.js"
      echo -e "  • ${BOLD}Setup & Deploy Guide:${NC}         ${TARGET_DIR}/cloudflare-pages/README.md"
    else
      echo -e "  • ${BOLD}Cloudflare Quick Tunnel:${NC}    ${GREEN}Active (Check terminal logs or run './start.sh status')${NC}"
    fi
    echo -e "  • ${BOLD}Local Web App (LAN):${NC}          ${GREEN}http://${LAN_IP}:${NGINX_PORT:-8080}${NC}"
    echo -e "  • ${BOLD}Local Admin Operations Panel:${NC} ${YELLOW}http://127.0.0.1:4000${NC} (Strictly Local)"
    echo ""
    echo -e "${BOLD}Supabase Redirect URLs to Whitelist (Authentication -> URL Configuration):${NC}"
    echo "  • http://localhost:${NGINX_PORT:-8080}/**"
    if [ -n "$PUBLIC_DOMAIN" ] && [ "$PUBLIC_DOMAIN" != "trycloudflare.com" ] && [ "$PUBLIC_DOMAIN" != "localhost" ]; then
      echo -e "  • ${GREEN}https://${PUBLIC_DOMAIN}/**${NC}"
    fi
  else
    echo -e "${CYAN}${BOLD}Local / LAN Access Links:${NC}"
    echo -e "  • ${BOLD}StreamVault Web App (Local):${NC}   ${GREEN}http://localhost:${NGINX_PORT:-8080}${NC}"
    echo -e "  • ${BOLD}StreamVault Web App (LAN):${NC}     ${GREEN}http://${LAN_IP}:${NGINX_PORT:-8080}${NC}"
    echo -e "  • ${BOLD}Local Admin Operations Panel:${NC}  ${YELLOW}http://127.0.0.1:4000${NC} (Strictly Local)"
    echo ""
    echo -e "${BOLD}Supabase Redirect URL to Whitelist:${NC}"
    echo "  • http://localhost:${NGINX_PORT:-8080}/**"
  fi
else
  echo -e "${CYAN}${BOLD}Production / Self-Hosted Access Links:${NC}"
  echo -e "  • ${BOLD}Public Web App URL:${NC}            ${GREEN}https://${PUBLIC_DOMAIN}${NC}"
  echo -e "  • ${BOLD}Local Gateway (Internal):${NC}      http://127.0.0.1:${NGINX_PORT:-8080}"
  echo -e "  • ${BOLD}Local Admin Operations Panel:${NC}  ${YELLOW}http://127.0.0.1:4000${NC} (Strictly Local)"
  echo ""
  echo -e "${BOLD}Important — Add This Redirect URL in Supabase Dashboard -> Authentication -> URL Configuration:${NC}"
  echo -e "  • ${GREEN}https://${PUBLIC_DOMAIN}/**${NC}"
fi
echo "=============================================================================="
echo ""
echo -e "${GREEN}${BOLD}✓ StreamVault is detached and running in the background.${NC}"
echo -e "You can manage services anytime using:"
echo -e "  • Check Status : ${BOLD}./start.sh status${NC}"
echo -e "  • View Logs    : ${BOLD}./start.sh logs${NC}"
echo -e "  • Stop App     : ${BOLD}./start.sh stop${NC}"
echo -e "  • Restart App  : ${BOLD}./start.sh restart${NC}"
echo ""

disown -a 2>/dev/null || true
exit 0