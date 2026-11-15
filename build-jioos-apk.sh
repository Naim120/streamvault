#!/usr/bin/env bash
# ==============================================================================
# StreamVault - JioOS / Android TV APK Builder (Crash-Proof & Memory-Governed)
# Packages StreamVault into a Leanback-compatible, server-agnostic Android APK
# Optimized for Jio AirFiber Set-Top Box & Android TV devices.
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="${SCRIPT_DIR}/frontend"
ANDROID_DIR="${FRONTEND_DIR}/android"
ASSETS_PUBLIC_DIR="${ANDROID_DIR}/app/src/main/assets/public"
OUTPUT_APK="${SCRIPT_DIR}/streamvault-jioos-tv.apk"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Lower CPU and Disk I/O priority so the build never freezes the host OS
CPU_THROTTLE=""
if command -v nice >/dev/null 2>&1; then
    CPU_THROTTLE="nice -n 15"
fi
if command -v ionice >/dev/null 2>&1; then
    CPU_THROTTLE="${CPU_THROTTLE} ionice -c2 -n7"
fi

# Cap Node.js V8 heap to 768 MB so Vite and Capacitor never spike RAM
export NODE_OPTIONS="--max-old-space-size=768"

echo -e "${CYAN}${BOLD}"
echo "================================================================="
echo "   StreamVault JioOS / Android TV Production APK Builder        "
echo "   (Resource-Governed: Max 2 CPU Workers | 768 MB Heap Cap)     "
echo "================================================================="
echo -e "${NC}"

# 1. Environment & Path Resolution
echo -e "${YELLOW}[1/5] Detecting Node, Java, and Android SDK environment...${NC}"

for p in \
    "/home/naim/.nvm/versions/node/v26.0.0/bin" \
    "${HOME}/.nvm/versions/node/$(ls "${HOME}/.nvm/versions/node" 2>/dev/null | tail -n1)/bin" \
    "${HOME}/.fnm/current/bin" \
    "/usr/local/bin" \
    "/usr/bin"
do
    if [[ -d "$p" && ":$PATH:" != *":$p:"* ]]; then
        export PATH="$p:$PATH"
    fi
done

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    echo -e "${RED}[ERROR] Node.js or npm not found in PATH.${NC}" >&2
    exit 1
fi
echo -e "  ✔ Node.js: $(node -v) | npm: $(npm -v)"

if ! command -v java >/dev/null 2>&1; then
    echo -e "${RED}[ERROR] Java (JDK) not found. Please install default-jdk.${NC}" >&2
    exit 1
fi
JAVA_VER=$(java -version 2>&1 | head -n1)
echo -e "  ✔ Java: ${JAVA_VER}"

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

# Verify available system memory before starting heavy compilation
if command -v free >/dev/null 2>&1; then
    AVAIL_MEM_MB=$(free -m | awk '/^Mem:/{print $7}')
    SWAP_FREE_MB=$(free -m | awk '/^Swap:/{print $4}')
    echo -e "  ✔ System Memory: ${AVAIL_MEM_MB} MB available RAM | ${SWAP_FREE_MB} MB free Swap"
fi

# 2. Validate Capacitor webDir & Compile Clean Web Frontend
echo -e "\n${YELLOW}[2/5] Validating Capacitor config & compiling production frontend...${NC}"
cd "${FRONTEND_DIR}"

# Guard: Ensure capacitor.config points strictly to 'dist' (never '.' or 'node_modules')
if [[ -f "${FRONTEND_DIR}/capacitor.config.ts" ]]; then
    if ! grep -qE "webDir:\s*['\"]dist['\"]" "${FRONTEND_DIR}/capacitor.config.ts"; then
        echo -e "${RED}[ERROR] capacitor.config.ts must have webDir: 'dist' to avoid bundling node_modules!${NC}" >&2
        exit 1
    fi
fi

# Clean previous build artifacts and any accidental symlinks/node_modules
rm -rf "${FRONTEND_DIR}/dist" "${ASSETS_PUBLIC_DIR}"

$CPU_THROTTLE npm run build -- --sourcemap false

# Explicitly purge any accidental node_modules, source maps, or media files from dist/
rm -rf "${FRONTEND_DIR}/dist/node_modules" "${FRONTEND_DIR}/dist/.git"
find "${FRONTEND_DIR}/dist" -type f \( -name "*.map" -o -name "*.mp4" -o -name "*.mkv" -o -name "*.ts" \) -delete 2>/dev/null || true

DIST_SIZE_MB=$(du -sm "${FRONTEND_DIR}/dist" | awk '{print $1}')
DIST_FILE_COUNT=$(find "${FRONTEND_DIR}/dist" -type f | wc -l)
echo -e "  ✔ Frontend dist size: ${DIST_SIZE_MB} MB (${DIST_FILE_COUNT} files)"

if [[ "$DIST_SIZE_MB" -gt 25 || "$DIST_FILE_COUNT" -gt 500 ]]; then
    echo -e "${RED}[ERROR] frontend/dist is unexpectedly large (${DIST_SIZE_MB} MB, ${DIST_FILE_COUNT} files). Aborting to prevent APK asset bloat!${NC}" >&2
    exit 1
fi
echo -e "${GREEN}  ✔ Frontend build verified clean (zero node_modules).${NC}"

# 3. Copy Web Assets to Android (Using 'cap copy' to avoid heavy hooks)
echo -e "\n${YELLOW}[3/5] Copying production web assets into Android container...${NC}"
$CPU_THROTTLE npx cap copy android

# Double-check Android assets/public directory before Gradle runs aapt2
if [[ -d "${ASSETS_PUBLIC_DIR}/node_modules" ]]; then
    rm -rf "${ASSETS_PUBLIC_DIR}/node_modules"
fi
ASSETS_SIZE_MB=$(du -sm "${ASSETS_PUBLIC_DIR}" 2>/dev/null | awk '{print $1}' || echo 0)
echo -e "${GREEN}  ✔ Android assets/public verified: ${ASSETS_SIZE_MB} MB.${NC}"

# 4. Configure Memory-Capped Gradle & Compile APK
echo -e "\n${YELLOW}[4/5] Compiling Android TV APK with memory-capped Gradle...${NC}"
mkdir -p "${ANDROID_DIR}"
echo "sdk.dir=${ANDROID_HOME}" > "${ANDROID_DIR}/local.properties"

# Enforce strict memory & worker limits in gradle.properties so Gradle + Kotlin never crash the host
cat > "${ANDROID_DIR}/gradle.properties" << 'EOF'
org.gradle.jvmargs=-Xmx768m -XX:MaxMetaspaceSize=384m -XX:+UseSerialGC -Dfile.encoding=UTF-8
org.gradle.daemon=false
org.gradle.parallel=false
org.gradle.workers.max=2
org.gradle.IntensiveTask.maxWorkers=1
org.gradle.caching=true
kotlin.daemon.jvmargs=-Xmx512m
kotlin.compiler.execution.strategy=in-process
android.useAndroidX=true
android.enableJetifier=true
EOF

cd "${ANDROID_DIR}"
chmod +x ./gradlew

# Stop any orphaned Gradle daemons from earlier runs that may be holding RAM
./gradlew --stop >/dev/null 2>&1 || true

# Run Gradle with capped heap, max 2 workers, and in-process Kotlin compilation
export GRADLE_OPTS="-Dorg.gradle.jvmargs=\"-Xmx768m -XX:MaxMetaspaceSize=384m -XX:+UseSerialGC\" -Dorg.gradle.daemon=false -Dorg.gradle.workers.max=2 -Dkotlin.compiler.execution.strategy=in-process"

$CPU_THROTTLE ./gradlew assembleDebug \
    --no-daemon \
    --max-workers=2 \
    --console=plain \
    -x lint \
    -x test

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
echo -e "  Size        : ${APK_SIZE}"
echo -e "  SHA-256     : ${APK_SHA}"
echo -e "=================================================================\n"