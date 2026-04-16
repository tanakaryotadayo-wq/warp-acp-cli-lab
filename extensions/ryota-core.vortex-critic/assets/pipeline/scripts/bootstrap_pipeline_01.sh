#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${PIPELINE_01_STATE_DIR:-$ROOT/data/pipeline_01}"
STATUS_FILE="${PIPELINE_01_STATUS_FILE:-$STATE_DIR/status.json}"
MOUNT_PATH="${PIPELINE_01_MOUNT_PATH:-$HOME/GoogleDriveCache/oss}"
RCLONE_REMOTE="${PIPELINE_01_RCLONE_REMOTE:-gdrive}"
RCLONE_SUBPATH="${PIPELINE_01_RCLONE_SUBPATH:-}"
N8N_COMPOSE="${PIPELINE_01_N8N_COMPOSE:-$ROOT/integration/n8n-compose.pipeline_01.yml}"
WORKFLOW_JSON="${PIPELINE_01_WORKFLOW_JSON:-$ROOT/integration/n8n-workflow-pipeline-01.json}"

mkdir -p "$STATE_DIR" "$MOUNT_PATH"

mounted=false
if mount | grep -F "on $MOUNT_PATH " >/dev/null 2>&1; then
  mounted=true
else
  remote_spec="${RCLONE_REMOTE}:"
  if [ -n "$RCLONE_SUBPATH" ]; then
    remote_spec="${RCLONE_REMOTE}:${RCLONE_SUBPATH}"
  fi
  if rclone mount "$remote_spec" "$MOUNT_PATH" --daemon --vfs-cache-mode full --dir-cache-time 10m --poll-interval 30s >/dev/null 2>&1; then
    mounted=true
  fi
fi

cbf_healthy=false
if curl -sf http://127.0.0.1:9801/v1/cbf/health >/dev/null 2>&1; then
  cbf_healthy=true
else
  nohup python3 "$ROOT/gate/cbf.py" --serve --host 127.0.0.1 --port 9801 \
    --db "$STATE_DIR/cbf_history.db" --log "$STATE_DIR/cbf_log.json" \
    >"$STATE_DIR/cbf_server.log" 2>&1 &
  sleep 2
  if curl -sf http://127.0.0.1:9801/v1/cbf/health >/dev/null 2>&1; then
    cbf_healthy=true
  fi
fi

n8n_started=false
if [ -f "$N8N_COMPOSE" ] && docker compose -f "$N8N_COMPOSE" up -d >/dev/null 2>&1; then
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf http://127.0.0.1:5678/ >/dev/null 2>&1; then
      n8n_started=true
      break
    fi
    sleep 2
  done
fi

mounted_py=False
cbf_healthy_py=False
n8n_started_py=False
[ "$mounted" = true ] && mounted_py=True
[ "$cbf_healthy" = true ] && cbf_healthy_py=True
[ "$n8n_started" = true ] && n8n_started_py=True

python3 <<PYEOF
import json
from pathlib import Path

status = {
    "pipeline": "pipeline-01",
    "stage": "bootstrapped",
    "mounted": ${mounted_py},
    "cbfHealthy": ${cbf_healthy_py},
    "n8nReady": ${n8n_started_py},
    "mountPath": "${MOUNT_PATH}",
    "driveRemote": "${RCLONE_REMOTE}",
    "driveSubpath": "${RCLONE_SUBPATH}",
    "workflowJson": "${WORKFLOW_JSON}",
    "bootstrapScript": "${ROOT}/scripts/bootstrap_pipeline_01.sh",
}

path = Path("${STATUS_FILE}")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
print(json.dumps(status, ensure_ascii=False))
PYEOF
