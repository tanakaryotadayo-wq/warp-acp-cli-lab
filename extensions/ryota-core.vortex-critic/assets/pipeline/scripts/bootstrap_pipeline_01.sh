#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="${PIPELINE_01_STATE_DIR:-$ROOT/data/pipeline_01}"
STATUS_FILE="${PIPELINE_01_STATUS_FILE:-$STATE_DIR/status.json}"
MOUNT_PATH="${PIPELINE_01_MOUNT_PATH:-$HOME/GoogleDriveCache/oss}"
RCLONE_REMOTE="${PIPELINE_01_RCLONE_REMOTE:-gdrive}"
RCLONE_SUBPATH="${PIPELINE_01_RCLONE_SUBPATH:-}"
DEFAULT_RCLONE_BIN="${HOME}/.local/bin/rclone"
if [ -n "${PIPELINE_01_RCLONE_BIN:-}" ]; then
  RCLONE_BIN="${PIPELINE_01_RCLONE_BIN}"
elif [ -x "$DEFAULT_RCLONE_BIN" ]; then
  RCLONE_BIN="$DEFAULT_RCLONE_BIN"
else
  RCLONE_BIN="$(command -v rclone || true)"
fi
N8N_COMPOSE="${PIPELINE_01_N8N_COMPOSE:-$ROOT/integration/n8n-compose.pipeline_01.yml}"
WORKFLOW_JSON="${PIPELINE_01_WORKFLOW_JSON:-$ROOT/integration/n8n-workflow-pipeline-01.json}"
RCLONE_MOUNT_LOG="${STATE_DIR}/rclone_mount.log"
RCLONE_NFS_LOG="${STATE_DIR}/rclone_nfs.log"
RCLONE_NFS_PID_FILE="${STATE_DIR}/rclone_nfs.pid"
RCLONE_NFS_ADDR="${PIPELINE_01_RCLONE_NFS_ADDR:-127.0.0.1}"
RCLONE_NFS_PORT="${PIPELINE_01_RCLONE_NFS_PORT:-39091}"
CBF_LAUNCHER="${PIPELINE_01_CBF_LAUNCHER:-tmux}"
CBF_TMUX_SESSION="${PIPELINE_01_CBF_TMUX_SESSION:-vortex-pipeline-cbf}"
TMUX_BIN="$(command -v tmux || true)"
DOCKER_BIN="${PIPELINE_01_DOCKER_BIN:-$(command -v docker || true)}"
MOUNT_NFS_BIN="$(command -v mount_nfs || true)"
CONTAINER_RUNTIME="${PIPELINE_01_CONTAINER_RUNTIME:-auto}"
PLATFORM_NAME="$(uname -s)"

mkdir -p "$STATE_DIR" "$MOUNT_PATH"

mount_visible() {
  mount | grep -F "on $MOUNT_PATH " >/dev/null 2>&1
}

detect_mount_mode() {
  local mount_line
  mount_line="$(mount | grep -F "on $MOUNT_PATH " | tail -n 1 || true)"
  if [ -z "$mount_line" ]; then
    printf 'unmounted'
  elif printf '%s' "$mount_line" | grep -q '(nfs'; then
    printf 'nfs'
  else
    printf 'fuse'
  fi
}

has_local_fuse() {
  [ -d /Library/Filesystems/macfuse.fs ] || [ -d /Library/Filesystems/osxfuse.fs ] || [ -d /Library/Filesystems/fuse-t.fs ]
}

kill_nfs_server() {
  if [ -f "$RCLONE_NFS_PID_FILE" ]; then
    local existing_pid
    existing_pid="$(cat "$RCLONE_NFS_PID_FILE" 2>/dev/null || true)"
    if [ -n "$existing_pid" ] && ps -p "$existing_pid" >/dev/null 2>&1; then
      kill "$existing_pid" >/dev/null 2>&1 || true
      sleep 1
    fi
    rm -f "$RCLONE_NFS_PID_FILE"
  fi
}

set_mount_error_from_log() {
  local log_file="$1"
  mount_error="$(tail -n 10 "$log_file" 2>/dev/null | tr '\n' ' ' | sed 's/\"/\x27/g' | sed 's/  */ /g' | cut -c1-400)"
}

remote_spec="${RCLONE_REMOTE}:"
if [ -n "$RCLONE_SUBPATH" ]; then
  remote_spec="${RCLONE_REMOTE}:${RCLONE_SUBPATH}"
fi

mounted=false
mount_mode="unmounted"
mount_error=""
active_rclone_log="$RCLONE_MOUNT_LOG"

start_nfs_mount() {
  : > "$RCLONE_NFS_LOG"
  kill_nfs_server
  "$RCLONE_BIN" serve nfs "$remote_spec" \
    --addr "${RCLONE_NFS_ADDR}:${RCLONE_NFS_PORT}" \
    --vfs-cache-mode full \
    --dir-cache-time 10m \
    --poll-interval 30s \
    --log-file "$RCLONE_NFS_LOG" \
    -vv >/dev/null 2>&1 &
  local nfs_pid=$!
  printf '%s\n' "$nfs_pid" > "$RCLONE_NFS_PID_FILE"
  sleep 3

  local mount_nfs_output=""
  if mount_nfs_output="$("$MOUNT_NFS_BIN" -o "port=${RCLONE_NFS_PORT},mountport=${RCLONE_NFS_PORT},tcp" "${RCLONE_NFS_ADDR}:/" "$MOUNT_PATH" 2>&1)"; then
    for _ in 1 2 3 4 5; do
      if mount_visible; then
        mounted=true
        mount_mode="nfs"
        mount_error=""
        active_rclone_log="$RCLONE_NFS_LOG"
        return 0
      fi
      sleep 1
    done
    mount_error="nfs_mount_not_visible"
  else
    mount_error="$(printf '%s' "$mount_nfs_output" | tr '\n' ' ' | sed 's/\"/\x27/g' | sed 's/  */ /g' | cut -c1-400)"
  fi

  kill_nfs_server
  return 1
}

docker_context=""
effective_container_runtime="$CONTAINER_RUNTIME"
container_runtime_note=""
if [ -n "$DOCKER_BIN" ]; then
  docker_context="$("$DOCKER_BIN" context show 2>/dev/null || true)"
fi
if [ "$effective_container_runtime" = "auto" ]; then
  if [ "$docker_context" = "orbstack" ]; then
    effective_container_runtime="orbstack"
  elif [ -n "$DOCKER_BIN" ]; then
    effective_container_runtime="docker"
  else
    effective_container_runtime="unavailable"
  fi
fi
if [ "$CONTAINER_RUNTIME" = "orbstack" ] && [ "$docker_context" != "orbstack" ]; then
  container_runtime_note="requested_orbstack_but_context_${docker_context:-unknown}"
fi

if mount_visible; then
  mounted=true
  mount_mode="$(detect_mount_mode)"
else
  if [ -z "$RCLONE_BIN" ]; then
    mount_error="rclone_not_found"
  elif [ "$PLATFORM_NAME" = "Darwin" ] && [ -n "$MOUNT_NFS_BIN" ] && ! has_local_fuse; then
    start_nfs_mount || true
  elif "$RCLONE_BIN" mount "$remote_spec" "$MOUNT_PATH" --daemon --daemon-timeout 15s --vfs-cache-mode full --dir-cache-time 10m --poll-interval 30s --log-file "$RCLONE_MOUNT_LOG" -vv >/dev/null 2>&1; then
    for _ in 1 2 3 4 5; do
      if mount_visible; then
        mounted=true
        mount_mode="$(detect_mount_mode)"
        active_rclone_log="$RCLONE_MOUNT_LOG"
        break
      fi
      sleep 1
    done
    if [ "$mounted" != true ]; then
      if [ "$PLATFORM_NAME" = "Darwin" ] && [ -n "$MOUNT_NFS_BIN" ] && grep -Eqi 'cannot find FUSE|failed to mount FUSE fs' "$RCLONE_MOUNT_LOG"; then
        start_nfs_mount || true
      else
        mount_error="mount_not_visible"
        set_mount_error_from_log "$RCLONE_MOUNT_LOG"
      fi
    fi
  else
    if [ "$PLATFORM_NAME" = "Darwin" ] && [ -n "$MOUNT_NFS_BIN" ] && grep -Eqi 'cannot find FUSE|failed to mount FUSE fs' "$RCLONE_MOUNT_LOG"; then
      start_nfs_mount || true
    else
      set_mount_error_from_log "$RCLONE_MOUNT_LOG"
    fi
  fi
fi

cbf_healthy=false
cbf_launcher_used="$CBF_LAUNCHER"
if curl -sf http://127.0.0.1:9801/v1/cbf/health >/dev/null 2>&1; then
  cbf_healthy=true
else
  if [ "$CBF_LAUNCHER" = "tmux" ] && [ -n "$TMUX_BIN" ]; then
    : > "$STATE_DIR/cbf_server.log"
    if "$TMUX_BIN" has-session -t "$CBF_TMUX_SESSION" >/dev/null 2>&1; then
      "$TMUX_BIN" kill-session -t "$CBF_TMUX_SESSION" >/dev/null 2>&1 || true
    fi
    quoted_root=$(printf '%q' "$ROOT")
    quoted_cbf=$(printf '%q' "$ROOT/gate/cbf.py")
    quoted_db=$(printf '%q' "$STATE_DIR/cbf_history.db")
    quoted_log_json=$(printf '%q' "$STATE_DIR/cbf_log.json")
    quoted_log_file=$(printf '%q' "$STATE_DIR/cbf_server.log")
    tmux_command="cd ${quoted_root} && exec python3 ${quoted_cbf} --serve --host 127.0.0.1 --port 9801 --db ${quoted_db} --log ${quoted_log_json} >>${quoted_log_file} 2>&1"
    "$TMUX_BIN" new-session -d -s "$CBF_TMUX_SESSION" "$tmux_command"
    cbf_launcher_used="tmux"
  else
    nohup python3 "$ROOT/gate/cbf.py" --serve --host 127.0.0.1 --port 9801 \
      --db "$STATE_DIR/cbf_history.db" --log "$STATE_DIR/cbf_log.json" \
      >"$STATE_DIR/cbf_server.log" 2>&1 &
    cbf_launcher_used="subprocess"
  fi
  sleep 2
  if curl -sf http://127.0.0.1:9801/v1/cbf/health >/dev/null 2>&1; then
    cbf_healthy=true
  fi
fi

n8n_started=false
if [ -n "$DOCKER_BIN" ] && [ -f "$N8N_COMPOSE" ] && "$DOCKER_BIN" compose -f "$N8N_COMPOSE" up -d >/dev/null 2>&1; then
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
    "containerRuntime": "${effective_container_runtime}",
    "dockerContext": "${docker_context}",
    "containerRuntimeNote": "${container_runtime_note}",
    "cbfLauncher": "${cbf_launcher_used}",
    "cbfTmuxSession": "${CBF_TMUX_SESSION}",
    "mountMode": "${mount_mode}",
    "mountPath": "${MOUNT_PATH}",
    "driveRemote": "${RCLONE_REMOTE}",
    "driveSubpath": "${RCLONE_SUBPATH}",
    "rclonePath": "${RCLONE_BIN}",
    "mountError": "${mount_error}",
    "rcloneLog": "${active_rclone_log}",
    "rcloneMountLog": "${RCLONE_MOUNT_LOG}",
    "rcloneNfsLog": "${RCLONE_NFS_LOG}",
    "rcloneNfsPidFile": "${RCLONE_NFS_PID_FILE}",
    "rcloneNfsAddr": "${RCLONE_NFS_ADDR}",
    "rcloneNfsPort": "${RCLONE_NFS_PORT}",
    "n8nCompose": "${N8N_COMPOSE}",
    "workflowJson": "${WORKFLOW_JSON}",
    "bootstrapScript": "${ROOT}/scripts/bootstrap_pipeline_01.sh",
}

path = Path("${STATUS_FILE}")
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
print(json.dumps(status, ensure_ascii=False))
PYEOF
