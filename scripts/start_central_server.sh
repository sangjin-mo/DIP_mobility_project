#!/usr/bin/env bash
# Starts this Mac as the FarmRover central server: ai_report ingest,
# the vision team's pc_server, and web_dashboard together. Opens the
# macOS Application Firewall for the duration and closes it again on exit.
# Windows: use start_central_server.ps1 instead (same behavior, PowerShell).
#
# Usage: ./scripts/start_central_server.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -x .venv/bin/python3 ]; then
  echo "No .venv found at repo root. Create one first:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

VENV_PYTHON="$REPO_ROOT/.venv/bin/python3"
LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

# Read individual keys out of .env without sourcing it -- some values
# (e.g. DASHBOARD_WEATHER_LOCATION_LABEL) contain unquoted spaces and would
# not survive `source .env` as a shell script.
env_value() {
  local key="$1"
  [ -f .env ] || return 0
  grep -E "^${key}=" .env | tail -n1 | cut -d'=' -f2-
}

DASHBOARD_ROVER_CONTROL_URL="$(env_value DASHBOARD_ROVER_CONTROL_URL)"
DASHBOARD_ROVER_CONTROL_TOKEN="$(env_value DASHBOARD_ROVER_CONTROL_TOKEN)"
DASHBOARD_ROVER_STATUS_URL="$(env_value DASHBOARD_ROVER_STATUS_URL)"
DRIVE_PI_SSH_HOST="$(env_value DRIVE_PI_SSH_HOST)"
DRIVE_PI_SSH_USER="$(env_value DRIVE_PI_SSH_USER)"
[ -n "$DRIVE_PI_SSH_USER" ] || DRIVE_PI_SSH_USER="pi"

PIDS=()
FIREWALL_OPENED=0
SOCKETFILTERFW=/usr/libexec/ApplicationFirewall/socketfilterfw

open_firewall() {
  local state
  state="$("$SOCKETFILTERFW" --getglobalstate 2>/dev/null || true)"
  if [[ "$state" == *"disabled"* ]]; then
    echo "macOS Application Firewall is off -- nothing to open (inbound already allowed)."
    return
  fi
  echo "Opening firewall for $VENV_PYTHON ..."
  sudo "$SOCKETFILTERFW" --add "$VENV_PYTHON"
  sudo "$SOCKETFILTERFW" --unblockapp "$VENV_PYTHON"
  FIREWALL_OPENED=1
}

close_firewall() {
  if [ "$FIREWALL_OPENED" = "1" ]; then
    echo "Closing firewall for $VENV_PYTHON ..."
    sudo "$SOCKETFILTERFW" --remove "$VENV_PYTHON" >/dev/null 2>&1 || true
  fi
}

cleanup() {
  echo
  echo "Stopping central server..."
  for pid in "${PIDS[@]:-}"; do
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
  done
  close_firewall
  echo "Stopped."
}
trap cleanup EXIT INT TERM

ensure_pc_server_deps() {
  local req="vision/image_transfer/system/pc_server/requirements.txt"
  if ! "$VENV_PYTHON" -c "import fastapi, uvicorn, jinja2, requests, multipart" >/dev/null 2>&1; then
    echo "Installing vision pc_server dependencies into .venv ..."
    "$VENV_PYTHON" -m pip install -q -r "$req"
  fi
}

check_drive_pi() {
  local status_url="$DASHBOARD_ROVER_STATUS_URL"
  if [ -z "$status_url" ] && [ -n "$DASHBOARD_ROVER_CONTROL_URL" ]; then
    status_url="${DASHBOARD_ROVER_CONTROL_URL%/api/control}/api/status"
  fi

  if [ -n "$DRIVE_PI_SSH_HOST" ]; then
    echo "Attempting best-effort remote start of drive control on $DRIVE_PI_SSH_HOST ..."
    ssh -o ConnectTimeout=5 -o BatchMode=yes "${DRIVE_PI_SSH_USER}@${DRIVE_PI_SSH_HOST}" \
      "cd ~/mycar && DASHBOARD_CONTROL_TOKEN='${DASHBOARD_ROVER_CONTROL_TOKEN}' nohup python manage.py drive --model=models/mypilot.h5 >drive.log 2>&1 & disown" \
      || echo "  (SSH remote start failed, or drive control was already running -- continuing)"
    sleep 2
  fi

  if [ -z "$status_url" ]; then
    echo "DASHBOARD_ROVER_CONTROL_URL not set -- skipping drive Pi reachability check."
    return
  fi

  echo "Checking drive Pi at $status_url ..."
  if curl -fsS --max-time 3 "$status_url" >/dev/null 2>&1; then
    echo "  Drive Pi control server is reachable."
  else
    echo "  WARNING: drive Pi control server did NOT respond at $status_url." >&2
    echo "  Drive controls will show as unreachable in the dashboard until it is started." >&2
  fi
}

open_firewall
ensure_pc_server_deps

echo "Starting ai_report ingest (UDP 9100 / HTTP 9101) ..."
"$VENV_PYTHON" -m ai_report.cli serve >"$LOG_DIR/ai_report.log" 2>&1 &
PIDS+=("$!")

echo "Starting vision pc_server (port 8000) ..."
(cd "$REPO_ROOT/vision/image_transfer/system/pc_server" && "$VENV_PYTHON" main.py) >"$LOG_DIR/pc_server.log" 2>&1 &
PIDS+=("$!")

sleep 1
check_drive_pi

LOCAL_HOSTNAME="$(scutil --get LocalHostName 2>/dev/null || hostname)"
echo
echo "Central server starting. Dashboard will be reachable at:"
echo "  http://${LOCAL_HOSTNAME}.local:8080"
echo "Logs: $LOG_DIR/ai_report.log, $LOG_DIR/pc_server.log"
echo "Press Ctrl+C to stop everything and close the firewall."
echo

"$VENV_PYTHON" -m web_dashboard
