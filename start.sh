#!/usr/bin/env bash
# ==============================================================================
# STREAMVAULT — PRODUCTION RUNTIME CONTROLLER & SERVICE SUPERVISOR v2.6
# Lifecycle, Reboot Auto-Start, State Persistence & Cloudflare Named Tunnel
# Usage:
#   ./start.sh              (Defaults to dev or last active mode)
#   ./start.sh prod         (Production mode: Nginx Gateway :8080 + API + Admin + Named Tunnel)
#   ./start.sh dev          (Development mode: Vite :5173 + API :8000 + Admin :4000)
#   ./start.sh tunnel       (Production Gateway + Named or Quick Cloudflare Tunnel)
#   ./start.sh start        (Resumes last active state/mode)
#   ./start.sh stop         (Gracefully stops services & sets STOPPED_BY_ADMIN)
#   ./start.sh stop-from-systemd (Gracefully stops services on system reboot without state change)
#   ./start.sh boot         (Called by systemd on reboot; respects STOPPED_BY_ADMIN)
#   ./start.sh restart      (Restarts active services in current/last mode)
#   ./start.sh status       (Displays live PID, port status, and boot auto-start state)
#   ./start.sh autostart on|off (Enables or disables systemd boot auto-start)
# ==============================================================================

set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
export PATH="$HOME/.local/bin:$PATH"

# Formatting Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# Persistent State File
STATE_FILE="$DIR/.streamvault_state"
DESIRED_STATE="ACTIVE"
LAST_MODE="prod"

if [ -f "$STATE_FILE" ]; then
  # shellcheck disable=SC1090
  . "$STATE_FILE" 2>/dev/null || true
fi

# Load .env variables
if [ -f "$DIR/.env" ]; then
  chmod 600 "$DIR/.env" 2>/dev/null || true
  set -a
  # shellcheck disable=SC1090
  . "$DIR/.env"
  set +a
fi

# Privilege Helper for systemd
SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo -n"
fi

# Detect Python Uvicorn runner
detect_uvicorn() {
  if [ -f "$DIR/.venv/bin/uvicorn" ]; then
    echo "$DIR/.venv/bin/uvicorn"
  elif [ -f "$DIR/backend/venv/bin/uvicorn" ]; then
    echo "$DIR/backend/venv/bin/uvicorn"
  elif command -v uvicorn >/dev/null 2>&1; then
    command -v uvicorn
  else
    echo "python3 -m uvicorn"
  fi
}
UVICORN="$(detect_uvicorn)"

save_state() {
  cat > "$STATE_FILE" <<EOF
DESIRED_STATE="${DESIRED_STATE}"
LAST_MODE="${LAST_MODE}"
EOF
}

# Ensure runtime temp directories exist and dynamically generate portable Nginx configuration
prepare_nginx_conf() {
  mkdir -p "$DIR/.nginx/client_body" "$DIR/.nginx/proxy" "$DIR/.nginx/fastcgi" "$DIR/.nginx/uwsgi" "$DIR/.nginx/scgi" "$DIR/logs"
  chmod 700 "$DIR/.nginx" "$DIR/logs" 2>/dev/null || true

  local nginx_template="$DIR/nginx.conf"
  local nginx_runtime="$DIR/.nginx/nginx.conf"
  local api_port="${MEDIA_SERVER_PORT:-8000}"

  if [ -f "$nginx_template" ]; then
    # Dynamically rewrite template directory paths and internal media server port ($DIR, $api_port)
    sed \
      -e "s|/var/www/streamvault|$DIR|g" \
      -e "s|/home/naim/netflix|$DIR|g" \
      -e "s|{{STREAMVAULT_DIR}}|$DIR|g" \
      -e "s|http://127.0.0.1:[0-9]\+/|http://127.0.0.1:${api_port}/|g" \
      "$nginx_template" > "$nginx_runtime"
  fi

  if [ -f /etc/nginx/conf.d/streamvault.conf ]; then
    $SUDO sed -i "s|http://127.0.0.1:[0-9]\+/|http://127.0.0.1:${api_port}/|g" /etc/nginx/conf.d/streamvault.conf 2>/dev/null || true
  fi
}

get_nginx_conf() {
  if [ -f "$DIR/.nginx/nginx.conf" ]; then
    echo "$DIR/.nginx/nginx.conf"
  else
    echo "$DIR/nginx.conf"
  fi
}

# ==============================================================================
# SUB-COMMAND: STATUS
# ==============================================================================
show_status() {
  echo -e "${BOLD}===========================================================${NC}"
  echo -e "${BOLD}  STREAMVAULT SYSTEM & SERVICE STATUS                      ${NC}"
  echo -e "${BOLD}===========================================================${NC}"
  echo -e "  • Project Directory:    ${CYAN}${DIR}${NC}"
  echo -e "  • Desired State:        $([ "$DESIRED_STATE" = "ACTIVE" ] && echo -e "${GREEN}${BOLD}ACTIVE${NC}" || echo -e "${RED}${BOLD}STOPPED_BY_ADMIN${NC}")"
  echo -e "  • Last Configured Mode: ${BOLD}${LAST_MODE}${NC}"

  # Boot auto-start check
  local autostart_status="Disabled"
  if command -v systemctl >/dev/null 2>&1 && [ -f /etc/systemd/system/streamvault.service ]; then
    if systemctl is-enabled streamvault.service >/dev/null 2>&1; then
      autostart_status="Enabled (systemd: streamvault.service)"
    else
      autostart_status="Disabled (systemd: installed but not enabled)"
    fi
  fi
  echo -e "  • Boot Auto-Start:      ${BOLD}${autostart_status}${NC}"
  echo ""
  echo -e "${BOLD}Live Component Breakdown:${NC}"

  # 1. FastAPI Media Server
  local api_port="${MEDIA_SERVER_PORT:-8000}"
  local api_pid
  api_pid="$(pgrep -f "uvicorn app.main:app" | head -n1 || true)"
  if [ -z "$api_pid" ]; then
    api_pid="$(pgrep -f "streamvault-api" | head -n1 || true)"
  fi
  if [ -z "$api_pid" ] && (echo > /dev/tcp/127.0.0.1/"$api_port") 2>/dev/null; then
    if curl -sI "http://127.0.0.1:${api_port}/" 2>/dev/null | grep -iq "StreamVault"; then
      api_pid="Active (Port ${api_port})"
    fi
  fi
  if [ -n "$api_pid" ]; then
    echo -e "  • FastAPI Media Server   [Port ${api_port}] : ${GREEN}● RUNNING${NC} (PID: ${api_pid})"
  else
    echo -e "  • FastAPI Media Server   [Port ${api_port}] : ${RED}○ STOPPED${NC}"
  fi

  # 2. Local Admin Panel (:4000)
  local admin_pid
  admin_pid="$(lsof -ti :4000 2>/dev/null | head -n1 || pgrep -f "admin-local.server:app" | head -n1 || true)"
  if [ -n "$admin_pid" ]; then
    echo -e "  • Local Admin Panel      [Port 4000] : ${GREEN}● RUNNING${NC} (PID: ${admin_pid})"
  else
    echo -e "  • Local Admin Panel      [Port 4000] : ${RED}○ STOPPED${NC}"
  fi

  # 3. Nginx Single-Origin Gateway (:8080)
  local nginx_pid
  nginx_pid="$(lsof -ti :8080 2>/dev/null | head -n1 || true)"
  [ -z "$nginx_pid" ] && [ -f "$DIR/.nginx/nginx.pid" ] && nginx_pid="$(cat "$DIR/.nginx/nginx.pid" 2>/dev/null || true)"
  [ -z "$nginx_pid" ] && command -v pgrep >/dev/null 2>&1 && nginx_pid="$(pgrep -f "nginx: master" | head -n1 || pgrep -x nginx | head -n1 || true)"
  if [ -z "$nginx_pid" ] && (echo > /dev/tcp/127.0.0.1/8080) 2>/dev/null; then
    nginx_pid="Active (Port 8080)"
  fi
  if [ -n "$nginx_pid" ]; then
    echo -e "  • Nginx Single-Origin    [Port 8080] : ${GREEN}● RUNNING${NC} (PID: ${nginx_pid})"
  else
    echo -e "  • Nginx Single-Origin    [Port 8080] : ${RED}○ STOPPED${NC}"
  fi

  # 4. Aria2c RPC Daemon (:6800)
  local aria2_pid
  aria2_pid="$(lsof -ti :6800 2>/dev/null | head -n1 || pgrep -x "aria2c" | head -n1 || true)"
  if [ -n "$aria2_pid" ]; then
    echo -e "  • Aria2c Download Daemon [Port 6800] : ${GREEN}● RUNNING${NC} (PID: ${aria2_pid})"
  else
    echo -e "  • Aria2c Download Daemon [Port 6800] : ${RED}○ STOPPED${NC}"
  fi

  # 5. Cloudflare Tunnel
  local cf_pid=""
  if [ -f "$DIR/.nginx/cloudflared.pid" ] && kill -0 "$(cat "$DIR/.nginx/cloudflared.pid" 2>/dev/null)" 2>/dev/null; then
    cf_pid="$(cat "$DIR/.nginx/cloudflared.pid")"
  elif pgrep -f "cloudflared.*8080" >/dev/null 2>&1; then
    cf_pid="$(pgrep -f "cloudflared.*8080" | head -n1)"
  fi

  if [ -n "$cf_pid" ]; then
    if [ -f "$HOME/.cloudflared/config.yml" ] && [ -n "$PUBLIC_DOMAIN" ] && [ "$PUBLIC_DOMAIN" != "localhost" ] && [ "$PUBLIC_DOMAIN" != "trycloudflare.com" ] && [[ ! "$PUBLIC_DOMAIN" =~ \.pages\.dev$ ]]; then
      echo -e "  • Cloudflare Named Tunnel            : ${GREEN}● RUNNING${NC} (PID: ${cf_pid} -> https://${PUBLIC_DOMAIN})"
    elif [[ "$PUBLIC_DOMAIN" =~ \.pages\.dev$ ]]; then
      local quick_origin
      quick_origin="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$DIR/logs/cloudflared.log" 2>/dev/null | tail -n1 || true)"
      if [ -n "$quick_origin" ]; then
        echo -e "  • Cloudflare Pages Backend Tunnel    : ${GREEN}● RUNNING${NC} (PID: ${cf_pid} -> ${quick_origin})"
        echo -e "    └─ Configured Pages URL            : https://${PUBLIC_DOMAIN}"
      else
        echo -e "  • Cloudflare Pages Backend Tunnel    : ${GREEN}● RUNNING${NC} (PID: ${cf_pid})"
      fi
    else
      local quick_url
      quick_url="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$DIR/logs/cloudflared.log" 2>/dev/null | tail -n1 || true)"
      if [ -n "$quick_url" ]; then
        echo -e "  • Cloudflare Quick Tunnel            : ${GREEN}● RUNNING${NC} (PID: ${cf_pid} -> ${quick_url})"
      else
        echo -e "  • Cloudflare Quick Tunnel            : ${GREEN}● RUNNING${NC} (PID: ${cf_pid})"
      fi
    fi
  else
    echo -e "  • Cloudflare Tunnel                  : ${RED}○ STOPPED${NC}"
  fi

  # 6. Vite Dev Server (:5173)
  local vite_pid
  vite_pid="$(lsof -ti :5173 2>/dev/null | head -n1 || true)"
  if [ -n "$vite_pid" ]; then
    echo -e "  • Vite Dev Server        [Port 5173] : ${GREEN}● RUNNING${NC} (PID: ${vite_pid})"
  fi

  echo -e "${BOLD}===========================================================${NC}"
  exit 0
}

# ==============================================================================
# SUB-COMMAND: STOPPING SERVICES
# ==============================================================================
stop_all_services() {
  echo -e "${YELLOW}Stopping StreamVault services...${NC}"
  pkill -f "uvicorn app.main:app" 2>/dev/null || true
  pkill -f "admin-local.server:app" 2>/dev/null || true
  pkill -f "streamvault-api" 2>/dev/null || true
  pkill -f "streamvault-admin" 2>/dev/null || true
  pkill -f "vite" 2>/dev/null || true

  # Only terminate StreamVault's own cloudflared processes on port 8080 or tracked via PID
  if [ -f "$DIR/.nginx/cloudflared.pid" ]; then
    local tracked_cf_pid
    tracked_cf_pid="$(cat "$DIR/.nginx/cloudflared.pid" 2>/dev/null || true)"
    if [ -n "$tracked_cf_pid" ] && kill -0 "$tracked_cf_pid" 2>/dev/null; then
      kill "$tracked_cf_pid" 2>/dev/null || true
    fi
    rm -f "$DIR/.nginx/cloudflared.pid" 2>/dev/null || true
  fi
  pkill -f "cloudflared.*8080" 2>/dev/null || true
  pkill -x "aria2c" 2>/dev/null || true

  if [ -f "$DIR/.nginx/nginx.pid" ] && kill -0 "$(cat "$DIR/.nginx/nginx.pid")" 2>/dev/null; then
    local conf_file
    conf_file="$(get_nginx_conf)"
    nginx -c "$conf_file" -s quit 2>/dev/null || true
  fi
  rm -f "$DIR/.nginx/nginx.pid" 2>/dev/null || true
}

# ==============================================================================
# SUB-COMMAND: AUTOSTART (systemd on/off)
# ==============================================================================
configure_autostart() {
  local action="$1"
  if ! command -v systemctl >/dev/null 2>&1; then
    echo -e "${RED}[✗] systemd (systemctl) is not available on this operating system.${NC}"
    exit 1
  fi

  if [ "$action" = "on" ]; then
    echo -e "${CYAN}Configuring systemd auto-start service (/etc/systemd/system/streamvault.service)...${NC}"
    local current_user
    current_user="$(whoami)"

    cat <<EOF | $SUDO tee /etc/systemd/system/streamvault.service >/dev/null
[Unit]
Description=StreamVault Production Media Streaming Platform
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=forking
User=${current_user}
WorkingDirectory=${DIR}
ExecStart=${DIR}/start.sh boot
ExecStop=${DIR}/start.sh stop-from-systemd
RemainAfterExit=yes
TimeoutStartSec=120

[Install]
WantedBy=multi-user.target
EOF
    $SUDO systemctl daemon-reload
    $SUDO systemctl enable streamvault.service
    echo -e "${GREEN}[ ✓ ] StreamVault boot auto-start ENABLED via systemd.${NC}"
  elif [ "$action" = "off" ]; then
    echo -e "${YELLOW}Disabling systemd auto-start for StreamVault...${NC}"
    if [ -f /etc/systemd/system/streamvault.service ]; then
      $SUDO systemctl disable streamvault.service 2>/dev/null || true
      $SUDO rm -f /etc/systemd/system/streamvault.service
      $SUDO systemctl daemon-reload
    fi
    echo -e "${GREEN}[ ✓ ] StreamVault boot auto-start DISABLED.${NC}"
  else
    echo "Usage: ./start.sh autostart [on|off]"
    exit 1
  fi
  exit 0
}

# Parse Command Mode & Execution Options
MODE="${1:-}"
DAEMON_MODE="true"

# Check for explicit foreground / background flags
for arg in "$@"; do
  if [ "$arg" = "-f" ] || [ "$arg" = "--foreground" ]; then
    DAEMON_MODE="false"
  elif [ "$arg" = "-d" ] || [ "$arg" = "--daemon" ]; then
    DAEMON_MODE="true"
  fi
done

case "$MODE" in
  status)
    show_status
    ;;
  logs)
    echo -e "${CYAN}Streaming live StreamVault logs (Ctrl+C to exit)...${NC}"
    tail -n 50 -f "$DIR/logs/"*.log 2>/dev/null || tail -n 50 -f "$DIR/logs/media_server.log" "$DIR/logs/admin.log" 2>/dev/null
    exit 0
    ;;
  stop)
    stop_all_services
    DESIRED_STATE="STOPPED_BY_ADMIN"
    save_state
    echo -e "${GREEN}[ ✓ ] StreamVault stopped successfully.${NC}"
    echo -e "${YELLOW}Note: Because you stopped the app manually, it will remain stopped across server reboots until you run './start.sh start' or './start.sh prod'.${NC}"
    exit 0
    ;;
  stop-from-systemd)
    stop_all_services
    echo -e "${GREEN}[ ✓ ] StreamVault cleanly stopped by systemd shutdown.${NC}"
    exit 0
    ;;
  autostart)
    configure_autostart "$2"
    ;;
  restart)
    stop_all_services
    sleep 1
    MODE="${LAST_MODE:-prod}"
    DESIRED_STATE="ACTIVE"
    save_state
    ;;
  start)
    MODE="${LAST_MODE:-prod}"
    DESIRED_STATE="ACTIVE"
    save_state
    ;;
  boot)
    if [ "$DESIRED_STATE" = "STOPPED_BY_ADMIN" ]; then
      echo "[INFO] StreamVault was manually stopped by admin prior to reboot. Respecting stopped state and skipping auto-start."
      exit 0
    fi
    MODE="${LAST_MODE:-prod}"
    ;;
  dev)
    MODE="dev"
    DESIRED_STATE="ACTIVE"
    LAST_MODE="dev"
    save_state
    if [ "$2" != "-d" ] && [ "$2" != "--daemon" ]; then
      DAEMON_MODE="false"
    fi
    ;;
  prod|tunnel|"")
    MODE="${MODE:-${LAST_MODE:-prod}}"
    DESIRED_STATE="ACTIVE"
    LAST_MODE="$MODE"
    save_state
    ;;
  *)
    echo "Usage: ./start.sh [prod|dev|tunnel|start|stop|restart|status|logs|autostart on/off]"
    exit 1
    ;;
esac

echo "==========================================================="
echo "  Starting StreamVault (Mode: ${MODE^^})                   "
echo "==========================================================="

mkdir -p "$DIR/media/incoming" "$DIR/media/library" "$DIR/media/temp"
mkdir -p "$DIR/.nginx" "$DIR/logs" "$HOME/.aria2"
chmod 700 "$DIR/.nginx" "$DIR/logs" "$HOME/.aria2" 2>/dev/null || true

# 1. Aria2c Daemon
if ! pgrep -x "aria2c" > /dev/null; then
  echo "[1/4] Starting aria2c background daemon on port 6800..."
  if [ -z "$ARIA2_RPC_SECRET" ] || [ "$ARIA2_RPC_SECRET" = "aria2_rpc_supersecret_token_123456" ]; then
    ARIA2_SECRET=$(openssl rand -hex 24 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(24))")
  else
    ARIA2_SECRET="$ARIA2_RPC_SECRET"
  fi
  ( umask 077 && touch "$HOME/.aria2/aria2.conf" && chmod 600 "$HOME/.aria2/aria2.conf" )
  cat << EOF > "$HOME/.aria2/aria2.conf"
dir=${DIR}/media/incoming
enable-rpc=true
rpc-listen-all=false
rpc-listen-port=6800
rpc-secret=${ARIA2_SECRET}
rpc-allow-origin-all=false
max-connection-per-server=16
min-split-size=10M
split=16
max-concurrent-downloads=3
seed-time=0
bt-save-metadata=true
EOF
  chmod 600 "$HOME/.aria2/aria2.conf"
  aria2c --conf-path="$HOME/.aria2/aria2.conf" -D || echo "aria2 already active..."
else
  echo "[1/4] aria2c daemon is already active."
fi

# 2. FastAPI Media Server
API_HOST="127.0.0.1"
API_PORT="${MEDIA_SERVER_PORT:-8000}"

# Auto-detect if target API_PORT is occupied by an external foreign process
if (echo > /dev/tcp/127.0.0.1/"$API_PORT") 2>/dev/null; then
  if ! pgrep -f "uvicorn app.main:app" >/dev/null 2>&1; then
    echo -e "${YELLOW}[ ! ] Port ${API_PORT} is in use by an external application. Auto-allocating StreamVault Media Server to port 8005.${NC}"
    API_PORT="8005"
    export MEDIA_SERVER_PORT="8005"
    export MEDIA_SERVER_INTERNAL_URL="http://127.0.0.1:8005"
    if [ -f "$DIR/.env" ]; then
      if grep -q "^MEDIA_SERVER_PORT=" "$DIR/.env"; then
        sed -i 's/^MEDIA_SERVER_PORT=.*/MEDIA_SERVER_PORT=8005/' "$DIR/.env"
      else
        echo "MEDIA_SERVER_PORT=8005" >> "$DIR/.env"
      fi
      if grep -q "^MEDIA_SERVER_INTERNAL_URL=" "$DIR/.env"; then
        sed -i 's|^MEDIA_SERVER_INTERNAL_URL=.*|MEDIA_SERVER_INTERNAL_URL=http://127.0.0.1:8005|' "$DIR/.env"
      else
        echo "MEDIA_SERVER_INTERNAL_URL=http://127.0.0.1:8005" >> "$DIR/.env"
      fi
    fi
  fi
fi

echo "[2/4] Starting FastAPI Media Server on port ${API_PORT}..."
cd "$DIR/backend"
if [ "$DAEMON_MODE" = "true" ]; then
  setsid nohup "$UVICORN" app.main:app --host "$API_HOST" --port "$API_PORT" < /dev/null >> "$DIR/logs/media_server.log" 2>&1 &
else
  "$UVICORN" app.main:app --host "$API_HOST" --port "$API_PORT" >> "$DIR/logs/media_server.log" 2>&1 &
fi
MEDIA_PID=$!

# 3. Local Admin Panel (Port 4000)
echo "[3/4] Starting Local Admin Panel on 127.0.0.1:4000..."
cd "$DIR"

# Ensure Admin Panel static Tailwind stylesheet is compiled
if [ -f "$DIR/frontend/node_modules/.bin/tailwindcss" ] && [ -f "$DIR/admin-local/tailwind.input.css" ]; then
  mkdir -p "$DIR/admin-local/static"
  if [ ! -f "$DIR/admin-local/static/tailwind.min.css" ] || [ "$DIR/admin-local/server.py" -nt "$DIR/admin-local/static/tailwind.min.css" ]; then
    echo "Compiling Admin Panel static Tailwind stylesheet..."
    NODE_OPTIONS="--max-old-space-size=256" "$DIR/frontend/node_modules/.bin/tailwindcss" -i "$DIR/admin-local/tailwind.input.css" -o "$DIR/admin-local/static/tailwind.min.css" --minify > /dev/null 2>&1 || true
  fi
fi

if [ "$DAEMON_MODE" = "true" ]; then
  setsid nohup "$UVICORN" admin-local.server:app --host 127.0.0.1 --port 4000 < /dev/null >> "$DIR/logs/admin.log" 2>&1 &
else
  "$UVICORN" admin-local.server:app --host 127.0.0.1 --port 4000 >> "$DIR/logs/admin.log" 2>&1 &
fi
ADMIN_PID=$!

# 4. Web Tier (Prod Nginx vs Dev Vite)
if [ "$MODE" = "prod" ] || [ "$MODE" = "tunnel" ]; then
  echo "[4/4] Ensuring production frontend is built..."
  if [ ! -d "$DIR/frontend/dist" ] || [ ! -f "$DIR/frontend/dist/index.html" ]; then
    cd "$DIR/frontend"
    npm run build
  fi

  echo "Starting Nginx single-origin gateway on port 8080..."
  prepare_nginx_conf
  conf_file="$(get_nginx_conf)"

  # Detect if port 8080 is already active
  port_8080_in_use="false"
  if (echo > /dev/tcp/127.0.0.1/8080) 2>/dev/null; then
    port_8080_in_use="true"
  elif command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :8080 )" 2>/dev/null | grep -q 8080; then
    port_8080_in_use="true"
  elif command -v lsof >/dev/null 2>&1 && lsof -i :8080 -sTCP:LISTEN >/dev/null 2>&1; then
    port_8080_in_use="true"
  elif command -v fuser >/dev/null 2>&1 && fuser 8080/tcp >/dev/null 2>&1; then
    port_8080_in_use="true"
  fi

  if [ "$port_8080_in_use" = "true" ]; then
    # Check if the process listening on port 8080 is Nginx
    is_nginx="false"
    if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
      is_nginx="true"
    elif [ -f "$DIR/.nginx/nginx.pid" ] && kill -0 "$(cat "$DIR/.nginx/nginx.pid")" 2>/dev/null; then
      is_nginx="true"
    elif curl -sI http://127.0.0.1:8080 2>/dev/null | grep -iq "server: nginx"; then
      is_nginx="true"
    elif pgrep -x nginx >/dev/null 2>&1; then
      is_nginx="true"
    fi

    if [ "$is_nginx" = "true" ]; then
      echo -e "${GREEN}[ ✓ ] Nginx single-origin gateway is already listening on port 8080.${NC}"
      if [ -f "$DIR/.nginx/nginx.pid" ] && kill -0 "$(cat "$DIR/.nginx/nginx.pid")" 2>/dev/null; then
        nginx -c "$conf_file" -s reload 2>/dev/null || true
      elif command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
        $SUDO systemctl reload nginx 2>/dev/null || nginx -s reload 2>/dev/null || true
      else
        $SUDO nginx -s reload 2>/dev/null || nginx -s reload 2>/dev/null || true
      fi
    else
      echo -e "${RED}[ ✗ ] Port 8080 is already occupied by a non-Nginx process.${NC}"
      echo -e "${YELLOW}Cannot start Nginx gateway on port 8080. Please stop the conflicting process.${NC}"
      exit 1
    fi
  else
    # Port 8080 is free, launch standalone Nginx or start system Nginx
    if [ -f "$DIR/.nginx/nginx.pid" ] && kill -0 "$(cat "$DIR/.nginx/nginx.pid")" 2>/dev/null; then
      nginx -c "$conf_file" -s reload
    elif command -v systemctl >/dev/null 2>&1 && [ -f /etc/nginx/conf.d/streamvault.conf ]; then
      $SUDO systemctl restart nginx 2>/dev/null || nginx -c "$conf_file"
    else
      nginx -c "$conf_file"
    fi
  fi

  # Check for authenticated Named Tunnel from install.sh
  HAS_NAMED_TUNNEL="false"
  if [ -f "$HOME/.cloudflared/config.yml" ] && [ -n "$PUBLIC_DOMAIN" ] && [ "$PUBLIC_DOMAIN" != "localhost" ] && [ "$PUBLIC_DOMAIN" != "trycloudflare.com" ] && [[ ! "$PUBLIC_DOMAIN" =~ \.pages\.dev$ ]]; then
    HAS_NAMED_TUNNEL="true"
  fi

  USE_TUNNEL="false"
  if [ "$MODE" = "tunnel" ] || [ "$HAS_NAMED_TUNNEL" = "true" ] || [ "${ENABLE_TUNNEL:-false}" = "true" ] || [ "${TUNNEL_TYPE:-none}" = "pages" ] || [ "${TUNNEL_TYPE:-none}" = "quick" ]; then
    USE_TUNNEL="true"
  fi

  TUNNEL_URL=""
  if [ "$USE_TUNNEL" = "true" ]; then
    if [ "$HAS_NAMED_TUNNEL" = "true" ]; then
      echo "Starting Authenticated Cloudflare Named Tunnel (https://${PUBLIC_DOMAIN})..."
      pkill -f "cloudflared.*tunnel.*run" 2>/dev/null || true
      if [ "$DAEMON_MODE" = "true" ]; then
        setsid nohup cloudflared --config "$HOME/.cloudflared/config.yml" tunnel run < /dev/null >> "$DIR/logs/cloudflared.log" 2>&1 &
      else
        cloudflared --config "$HOME/.cloudflared/config.yml" tunnel run >> "$DIR/logs/cloudflared.log" 2>&1 &
      fi
      TUNNEL_PID=$!
    else
      echo "Starting Free Cloudflare Tunnel to Nginx Gateway (http://127.0.0.1:8080)..."
      if [ -f "$DIR/.nginx/cloudflared.pid" ]; then
        kill "$(cat "$DIR/.nginx/cloudflared.pid" 2>/dev/null)" 2>/dev/null || true
        rm -f "$DIR/.nginx/cloudflared.pid"
      fi
      pkill -f "cloudflared.*8080" 2>/dev/null || true
      local cf_bin="cloudflared"
      if ! command -v cloudflared >/dev/null 2>&1 && [ -f "$DIR/cloudflared" ]; then
        cf_bin="$DIR/cloudflared"
      fi

      if command -v cloudflared >/dev/null 2>&1 || [ -f "$DIR/cloudflared" ]; then
        if [ "$DAEMON_MODE" = "true" ]; then
          setsid nohup "$cf_bin" tunnel --url http://127.0.0.1:8080 < /dev/null >> "$DIR/logs/cloudflared.log" 2>&1 &
        else
          "$cf_bin" tunnel --url http://127.0.0.1:8080 >> "$DIR/logs/cloudflared.log" 2>&1 &
        fi
        TUNNEL_PID=$!
        echo "$TUNNEL_PID" > "$DIR/.nginx/cloudflared.pid"
      fi

      # Wait up to 6 seconds for tunnel origin URL to appear in logs
      for i in $(seq 1 6); do
        if [ -f "$DIR/logs/cloudflared.log" ]; then
          TUNNEL_URL="$(grep -oE 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$DIR/logs/cloudflared.log" | tail -n1 || true)"
          [ -n "$TUNNEL_URL" ] && break
        fi
        sleep 1
      done

      # If Cloudflare Pages project is configured, update backend tunnel origin
      if [ -n "$TUNNEL_URL" ]; then
        if [ -f "$DIR/cloudflare-pages/_worker.js" ]; then
          sed -i "s|__TUNNEL_ORIGIN_PLACEHOLDER__|${TUNNEL_URL}|g" "$DIR/cloudflare-pages/_worker.js" 2>/dev/null || true
        fi
        if [ -n "${CLOUDFLARE_ACCOUNT_ID:-}" ] && [ -n "${CLOUDFLARE_API_TOKEN:-}" ] && [ -n "${CF_PAGES_PROJECT:-}" ]; then
          echo "Syncing active tunnel to Cloudflare Pages (${CF_PAGES_PROJECT})..."
          curl -s -X PATCH "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/pages/projects/${CF_PAGES_PROJECT}" \
            -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{\"deployment_configs\":{\"production\":{\"env_vars\":{\"BACKEND_TUNNEL_URL\":{\"value\":\"${TUNNEL_URL}\"}}}}}" >/dev/null 2>&1 || true
        fi
      fi
    fi
  fi

  LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || ipconfig getifaddr en0 2>/dev/null || echo '127.0.0.1')"

  # Automatically synchronize Supabase Auth site_url and uri_allow_list to reachable origin
  TARGET_ORIGIN="http://${LAN_IP}:8080"
  if [ "$HAS_NAMED_TUNNEL" = "true" ] && [ -n "$PUBLIC_DOMAIN" ]; then
    TARGET_ORIGIN="https://${PUBLIC_DOMAIN}"
  elif [ "${TUNNEL_TYPE:-}" = "pages" ] || [[ "$PUBLIC_DOMAIN" =~ \.pages\.dev$ ]]; then
    TARGET_ORIGIN="https://${PUBLIC_DOMAIN}"
  elif [ -n "$TUNNEL_URL" ]; then
    TARGET_ORIGIN="${TUNNEL_URL}"
  fi

  SB_TOKEN="${SUPABASE_MANAGEMENT_TOKEN:-}"
  SB_REF="${SUPABASE_PROJECT_REF:-}"
  if [ -n "$SB_TOKEN" ] && [ -n "$SB_REF" ]; then
    ALLOW_LIST="http://localhost:8080/**,http://localhost:5173/**,http://127.0.0.1:8080/**,http://${LAN_IP}:8080/**,http://${LAN_IP}/**"
    if [ -n "$PUBLIC_DOMAIN" ] && [ "$PUBLIC_DOMAIN" != "localhost" ]; then
      ALLOW_LIST="${ALLOW_LIST},https://${PUBLIC_DOMAIN}/**"
    fi
    if [ -n "$TARGET_ORIGIN" ]; then
      ALLOW_LIST="${ALLOW_LIST},${TARGET_ORIGIN}/**"
    fi
    if [ -n "$TUNNEL_URL" ]; then
      ALLOW_LIST="${ALLOW_LIST},${TUNNEL_URL}/**"
    fi
    curl -s -X PATCH "https://api.supabase.com/v1/projects/${SB_REF}/config/auth" \
      -H "Authorization: Bearer ${SB_TOKEN}" \
      -H "Content-Type: application/json" \
      -d "{\"site_url\":\"${TARGET_ORIGIN}\",\"uri_allow_list\":\"${ALLOW_LIST}\"}" >/dev/null 2>&1 || true
  fi

  echo ""
  echo "==========================================================="
  echo "  StreamVault PRODUCTION GATEWAY is LIVE!                 "
  if [ "$HAS_NAMED_TUNNEL" = "true" ]; then
    echo "  • Public Custom Domain:      https://${PUBLIC_DOMAIN}        "
  elif [ "${TUNNEL_TYPE:-}" = "pages" ] || [[ "$PUBLIC_DOMAIN" =~ \.pages\.dev$ ]]; then
    echo "  • Free Cloudflare Pages URL: https://${PUBLIC_DOMAIN}        "
    if [ -n "$TUNNEL_URL" ]; then
      echo "  • Underlying Tunnel Origin:  ${TUNNEL_URL}                  "
    fi
  elif [ -n "$TUNNEL_URL" ]; then
    echo "  • Free Quick Tunnel URL:     ${TUNNEL_URL}                  "
  fi
  echo "  • Nginx Gateway (Local):     http://localhost:8080           "
  echo "  • Nginx Gateway (LAN):       http://${LAN_IP}:8080           "
  echo "  • Local Admin Panel:     http://127.0.0.1:4000           "
  echo "==========================================================="
else
  echo "[4/4] Starting Vite React Frontend with /api proxy on http://localhost:5173..."
  cd "$DIR/frontend"
  npm run dev &
  FRONTEND_PID=$!

  echo ""
  echo "==========================================================="
  echo "  StreamVault DEV ENVIRONMENT is LIVE!                    "
  echo "  • Public Web App:        http://localhost:5173           "
  echo "  • Media Server API:      http://localhost:8000/docs      "
  echo "  • Local Admin Panel:     http://127.0.0.1:4000           "
  echo "==========================================================="
fi

# If running in daemon mode (prod, start, restart, boot, or backgrounded), disown and exit 0 cleanly
if [ "$DAEMON_MODE" = "true" ]; then
  disown -a 2>/dev/null || true
  echo ""
  echo -e "${GREEN}[ ✓ ] StreamVault background startup sequence complete.${NC}"
  echo -e "Services are running detached in background."
  echo -e "Run ${BOLD}./start.sh status${NC} to view health, or ${BOLD}./start.sh stop${NC} to shutdown."
  echo ""
  exit 0
fi

echo "Press Ctrl+C to terminate services (or run './start.sh stop' from another terminal)."

cleanup() {
  echo "Terminating services..."
  kill $MEDIA_PID $ADMIN_PID ${FRONTEND_PID:-} ${TUNNEL_PID:-} 2>/dev/null || true
  pkill -x "aria2c" 2>/dev/null || true
  if [ -f "$DIR/.nginx/nginx.pid" ]; then
    local conf_file
    conf_file="$(get_nginx_conf)"
    nginx -c "$conf_file" -s quit 2>/dev/null || true
  fi
  exit 0
}

trap cleanup INT TERM
wait
