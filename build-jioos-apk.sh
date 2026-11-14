#!/usr/bin/env bash
# ==============================================================================
# StreamVault - JioOS / Android TV APK Builder
# Packages StreamVault into a Leanback-compatible, server-agnostic Android APK
# Optimized for Jio AirFiber Set-Top Box & Android TV devices.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${SCRIPT_DIR}/frontend"
ANDROID_DIR="${FRONTEND_DIR}/android"
OUTPUT_APK="${SCRIPT_DIR}/streamvault-jioos-tv.apk"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}${BOLD}"
echo "================================================================="
echo "   StreamVault JioOS / Android TV Production APK Builder        "
echo "================================================================="
echo -e "${NC}"

# 1. Environment & Path Resolution
echo -e "${YELLOW}[1/5] Detecting Node, Java, and Android SDK environment...${NC}"

# Include standard user paths (nvm, fnm, bun, local sdk)
for p in \
    "/home/naim/.nvm/versions/node/v26.0.0/bin" \
    "${HOME}/.nvm/versions/node/$(ls ${HOME}/.nvm/versions/node 2>/dev/null | tail -n1)/bin" \
    "${HOME}/.fnm/current/bin" \
    "/usr/local/bin" \
    "/usr/bin"
do
    if [[ -d "$p" && ":$PATH:" != *":$p:"* ]]; then
        export PATH="$p:$PATH"
    fi
done

# Verify Node & NPM
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo -e "${RED}[ERROR] Node.js or npm not found in PATH.${NC}" >&2
    exit 1
fi
echo -e "  ✔ Node.js: $(node -v) | npm: $(npm -v)"

# Verify Java (JDK)
if ! command -v java >/dev/null 2>&1; then
    echo -e "${RED}[ERROR] Java (JDK) not found. Please install default-jdk (apt install default-jdk).${NC}" >&2
    exit 1
fi
JAVA_VER=$(java -version 2>&1 | head -n1)
echo -e "  ✔ Java: ${JAVA_VER}"

# Auto-detect Android SDK if not already exported
if [[ -z "${ANDROID_HOME:-}" ]]; then
    for candidate in \
        "${HOME}/android-sdk" \
        "${HOME}/Android/Sdk" \
        "/usr/lib/android-sdk" \
        "/opt/android-sdk"
    do
        if [[ -d "$candidate" && -d "$candidate/platforms" ]]; then
            export ANDROID_HOME="$candidate"
            export ANDROID_SDK_ROOT="$candidate"
            break
        fi
    done
fi

if [[ -z "${ANDROID_HOME:-}" || ! -d "${ANDROID_HOME:-}" ]]; then
    echo -e "${RED}[ERROR] Android SDK directory not found. Set ANDROID_HOME to your Android SDK path.${NC}" >&2
    exit 1
fi
echo -e "  ✔ Android SDK: ${ANDROID_HOME}"

# Ensure local.properties points to current Android SDK
mkdir -p "${ANDROID_DIR}"
echo "sdk.dir=${ANDROID_HOME}" > "${ANDROID_DIR}/local.properties"

# 2. Compile Web Frontend
echo -e "\n${YELLOW}[2/5] Compiling production frontend bundle...${NC}"
cd "${FRONTEND_DIR}"
npm run build
echo -e "${GREEN}  ✔ Frontend build complete.${NC}"

# 3. Synchronize Capacitor Native Android Assets
echo -e "\n${YELLOW}[3/5] Syncing Capacitor Android assets & configurations...${NC}"
npx cap sync android
echo -e "${GREEN}  ✔ Capacitor Android assets synchronized.${NC}"

# 4. Compile Android TV APK via Gradle
echo -e "\n${YELLOW}[4/5] Compiling Android APK with Gradle (assembleDebug)...${NC}"
cd "${ANDROID_DIR}"
chmod +x ./gradlew

# Execute Gradle build
./gradlew assembleDebug --no-daemon

# 5. Extract and Validate Final APK
echo -e "\n${YELLOW}[5/5] Packaging and verifying final APK...${NC}"
BUILT_APK="${ANDROID_DIR}/app/build/outputs/apk/debug/app-debug.apk"

if [[ ! -f "${BUILT_APK}" ]]; then
    echo -e "${RED}[ERROR] Expected APK not found at ${BUILT_APK}.${NC}" >&2
    exit 1
fi

cp -f "${BUILT_APK}" "${OUTPUT_APK}"
APK_SIZE=$(du -h "${OUTPUT_APK}" | cut -f1)
APK_SHA=$(sha256sum "${OUTPUT_APK}" | awk '{print $1}')

echo -e "\n${GREEN}${BOLD}=================================================================${NC}"
echo -e "${GREEN}${BOLD} ✔ StreamVault JioOS / Android TV APK Compiled Successfully!   ${NC}"
echo -e "${GREEN}${BOLD}=================================================================${NC}"
echo -e "  Output Path : ${BOLD}${OUTPUT_APK}${NC}"
echo -e "  Package Name: com.streamvault.app"
echo -e "  Size        : ${APK_SIZE}"
echo -e "  SHA-256     : ${APK_SHA}"
echo -e ""
echo -e "${CYAN}${BOLD}Installation on Jio AirFiber Set-Top Box / Android TV:${NC}"
echo -e "  1. Via ADB (USB / Wi-Fi Debugging):"
echo -e "     ${BOLD}adb connect <STB_IP_ADDRESS>:5555${NC}"
echo -e "     ${BOLD}adb install -r ${OUTPUT_APK}${NC}"
echo -e "  2. Via USB Flash Drive:"
echo -e "     Copy ${OUTPUT_APK} to a FAT32/NTFS USB drive, plug into Jio STB,"
echo -e "     and install via the 'Files' / 'File Commander' app."
echo -e "  3. Dynamic Server Discovery:"
echo -e "     On first launch, StreamVault queries Supabase for the current"
echo -e "     server / Cloudflare tunnel URL automatically. You can also"
echo -e "     click the [ ⚙️ Server ] button on the welcome/profile screen"
echo -e "     to manually bind to any local IP or custom domain anytime."
echo -e "=================================================================\n"

