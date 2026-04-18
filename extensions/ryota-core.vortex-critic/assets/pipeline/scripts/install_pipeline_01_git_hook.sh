#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="${1:-$(git rev-parse --show-toplevel)}"
HOOK_DIR="$REPO_ROOT/.git/hooks"
HOOK_FILE="$HOOK_DIR/post-commit"
HOOK_LOCAL="$HOOK_DIR/post-commit.local"
ENQUEUE_SCRIPT="$REPO_ROOT/extensions/ryota-core.vortex-critic/assets/pipeline/scripts/pipeline_01_enqueue_commit.py"

mkdir -p "$HOOK_DIR"

if [ -f "$HOOK_FILE" ] && ! grep -q "VORTEX Pipeline① post-commit hook" "$HOOK_FILE"; then
  mv "$HOOK_FILE" "$HOOK_LOCAL"
fi

python3 - "$HOOK_FILE" "$HOOK_LOCAL" "$ENQUEUE_SCRIPT" <<'PYEOF'
import os
import sys
from pathlib import Path

hook_file = Path(sys.argv[1])
hook_local = Path(sys.argv[2])
enqueue_script = Path(sys.argv[3])

content = f"""#!/usr/bin/env bash
set -euo pipefail

# VORTEX Pipeline① post-commit hook
REPO_ROOT="$(git rev-parse --show-toplevel)"
ENQUEUE_SCRIPT="{enqueue_script}"
HOOK_LOCAL="{hook_local}"

if command -v python3 >/dev/null 2>&1 && [ -f "$ENQUEUE_SCRIPT" ]; then
  nohup python3 "$ENQUEUE_SCRIPT" --repo-root "$REPO_ROOT" >/dev/null 2>&1 &
fi

if [ -x "$HOOK_LOCAL" ]; then
  "$HOOK_LOCAL" "$@"
fi
"""
hook_file.write_text(content, encoding="utf-8")
os.chmod(hook_file, 0o755)
PYEOF

printf 'Installed post-commit hook: %s\n' "$HOOK_FILE"
if [ -f "$HOOK_LOCAL" ]; then
  printf 'Chained local hook: %s\n' "$HOOK_LOCAL"
fi
