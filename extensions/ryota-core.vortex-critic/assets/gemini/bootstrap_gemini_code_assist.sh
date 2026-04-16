#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_ROOT=""
STATUS_FILE=""
HOST="${GEMINI_A2A_HOST:-127.0.0.1}"
PORT="${GEMINI_A2A_PORT:-8765}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-root)
      WORKSPACE_ROOT="$2"
      shift 2
      ;;
    --status-file)
      STATUS_FILE="$2"
      shift 2
      ;;
    --host)
      HOST="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [ -z "$WORKSPACE_ROOT" ]; then
  WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
fi

if [ -z "$STATUS_FILE" ]; then
  STATUS_FILE="$WORKSPACE_ROOT/.build/ryota/gemini-code-assist/status.json"
fi

STATE_DIR="$(dirname "$STATUS_FILE")"
LOG_FILE="$STATE_DIR/bridge.log"
PID_FILE="$STATE_DIR/bridge.pid"
BRIDGE_SCRIPT="$SCRIPT_DIR/gemini_a2a_bridge.py"
BRIDGE_URL="http://${HOST}:${PORT}"

mkdir -p "$STATE_DIR"

check_bridge() {
  curl -sf "${BRIDGE_URL}/newgate/profile" >/dev/null 2>&1
}

write_status() {
  local running_flag="$1"
  local note="$2"
  local running_py="False"
  if [ "$running_flag" = true ]; then
    running_py="True"
  fi
  python3 <<PYEOF
import json
from pathlib import Path

status = {
    "service": "gemini-code-assist-bridge",
    "running": ${running_py},
    "bridgeUrl": "${BRIDGE_URL}",
    "workspaceRoot": "${WORKSPACE_ROOT}",
    "bridgeScript": "${BRIDGE_SCRIPT}",
    "statusFile": "${STATUS_FILE}",
    "logFile": "${LOG_FILE}",
    "pidFile": "${PID_FILE}",
    "geminiSetting": "geminicodeassist.a2a.address",
    "host": "${HOST}",
    "port": int("${PORT}"),
    "note": "${note}",
}

path = Path("${STATUS_FILE}")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
print(json.dumps(status, ensure_ascii=False))
PYEOF
}

note="bridge already running"
running=false
if check_bridge; then
  running=true
else
  : "${GEMINI_A2A_USE_FUSION_GATE:=true}"
  : "${GEMINI_A2A_FUSION_GATE_ALLOW_FAILOVER:=false}"
  : "${GEMINI_A2A_FUSION_GATE_FALLBACK:=false}"

  nohup env \
    GEMINI_A2A_USE_FUSION_GATE="$GEMINI_A2A_USE_FUSION_GATE" \
    GEMINI_A2A_FUSION_GATE_ALLOW_FAILOVER="$GEMINI_A2A_FUSION_GATE_ALLOW_FAILOVER" \
    GEMINI_A2A_FUSION_GATE_FALLBACK="$GEMINI_A2A_FUSION_GATE_FALLBACK" \
    python3 "$BRIDGE_SCRIPT" --host "$HOST" --port "$PORT" \
    >"$LOG_FILE" 2>&1 &
  echo "$!" > "$PID_FILE"
  note="bridge started"

  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if check_bridge; then
      running=true
      break
    fi
    sleep 1
  done
fi

write_status "$running" "$note"

if [ "$running" != true ]; then
  echo "Gemini Code Assist bridge failed to start: ${BRIDGE_URL}" >&2
  exit 1
fi
