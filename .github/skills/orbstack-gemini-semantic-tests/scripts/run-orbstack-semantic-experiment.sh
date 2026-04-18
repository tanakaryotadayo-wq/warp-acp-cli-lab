#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="${REPO_ROOT:-$(cd "$SKILL_DIR/../../.." && pwd)}"
LAB_ROOT="${LAB_ROOT:-/Users/ryyota/scratch/warp-acp-cli-lab}"
OUTPUT_FILE="${1:-$SKILL_DIR/LATEST_REPORT.md}"
HYPOTHESIS="${EXPERIMENT_HYPOTHESIS:-Validate the latest multi-layer semantic workflow across OrbStack runtime, VORTEX control-plane state, and commit-driven pipeline follow-up.}"
GEMINI_BIN="${GEMINI_BIN:-$(command -v gemini || true)}"
TIMESTAMP_UTC="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

mkdir -p "$(dirname "$OUTPUT_FILE")"

TMP_DIR="$(mktemp -d)"
EVIDENCE_FILE="$TMP_DIR/evidence.txt"
GEMINI_STDOUT="$TMP_DIR/gemini.stdout"
GEMINI_STDERR="$TMP_DIR/gemini.stderr"

cleanup() {
	rm -rf "$TMP_DIR"
}
trap cleanup EXIT

{
	echo "timestamp_utc: $TIMESTAMP_UTC"
	echo "repo_root: $REPO_ROOT"
	echo "lab_root: $LAB_ROOT"
	echo "repo_head: $(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unavailable)"
	echo "repo_branch: $(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || echo unavailable)"
	echo
	echo "[docker context]"
	if command -v docker >/dev/null 2>&1; then
		docker context ls 2>/dev/null || true
	else
		echo "docker missing"
	fi
	echo
	echo "[orb list]"
	if command -v orb >/dev/null 2>&1; then
		orb list 2>/dev/null || true
	else
		echo "orb missing"
	fi
	echo
	echo "[relevant containers]"
	if command -v docker >/dev/null 2>&1; then
		docker ps --format '{{.Names}}\t{{.Status}}' 2>/dev/null | grep -E 'warp-acp-cli-lab|pipeline-01-n8n|temporal|qdrant|redis' || true
	else
		echo "docker missing"
	fi
	echo
	echo "[pipeline status]"
	if [ -f "$REPO_ROOT/.build/ryota/pipeline_01/status.json" ]; then
		cat "$REPO_ROOT/.build/ryota/pipeline_01/status.json"
	else
		echo "missing: $REPO_ROOT/.build/ryota/pipeline_01/status.json"
	fi
	echo
	echo "[pipeline queue status]"
	if [ -f "$REPO_ROOT/extensions/ryota-core.vortex-critic/assets/pipeline/data/pipeline_01/commit_queue_status.json" ]; then
		cat "$REPO_ROOT/extensions/ryota-core.vortex-critic/assets/pipeline/data/pipeline_01/commit_queue_status.json"
	else
		echo "missing: queue status"
	fi
	echo
	echo "[warp lab files]"
	if [ -f "$LAB_ROOT/compose.yaml" ]; then
		sed -n '1,120p' "$LAB_ROOT/compose.yaml"
	else
		echo "missing: $LAB_ROOT/compose.yaml"
	fi
	echo
	echo "[gemini cli]"
	if [ -n "$GEMINI_BIN" ]; then
		echo "gemini_bin: $GEMINI_BIN"
		"$GEMINI_BIN" --version 2>/dev/null || true
	else
		echo "gemini missing"
	fi
} > "$EVIDENCE_FILE"

PROMPT=$(cat <<'EOF'
You are evaluating a meaningful multi-layer semantic test for a local AI workflow.

Use the supplied evidence first. If useful, inspect files in the included workspace directories. Stay read-only.

Return concise markdown with these sections exactly:
- Verdict
- Runtime Layer
- Semantic Layer
- Commit/Pipeline Layer
- Best Next Test

Rules:
- Prefer evidence over guesses.
- Focus on whether this is a meaningful operator-facing test, not just a smoke test.
- Do not mention secrets or raw environment variables.
- Keep it concise.
EOF
)

GEMINI_STATUS=0
if [ -n "$GEMINI_BIN" ]; then
	if ! (
		cd "$REPO_ROOT"
		cat "$EVIDENCE_FILE" | "$GEMINI_BIN" \
			--approval-mode plan \
			--include-directories "$LAB_ROOT" \
			--output-format text \
			-p "$PROMPT"
	) >"$GEMINI_STDOUT" 2>"$GEMINI_STDERR"; then
		GEMINI_STATUS=$?
	fi
else
	GEMINI_STATUS=127
	printf 'Gemini CLI is not installed on this host.\n' >"$GEMINI_STDERR"
fi

{
	echo "# OrbStack Gemini Semantic Experiment"
	echo
	echo "- timestamp_utc: \`$TIMESTAMP_UTC\`"
	echo "- hypothesis: $HYPOTHESIS"
	echo "- repo_root: \`$REPO_ROOT\`"
	echo "- lab_root: \`$LAB_ROOT\`"
	echo
	echo "## Runtime evidence"
	echo
	echo '```text'
	sed -n '1,220p' "$EVIDENCE_FILE"
	echo '```'
	echo
	echo "## Gemini read-only result"
	echo
	if [ "$GEMINI_STATUS" -eq 0 ]; then
		cat "$GEMINI_STDOUT"
	else
		echo "- Gemini CLI failed with exit code \`$GEMINI_STATUS\`."
		echo
		echo '```text'
		sed -n '1,160p' "$GEMINI_STDERR"
		echo '```'
	fi
	echo
	echo "## Commit note"
	echo
	echo "- This report is intended to be committed so the commit-driven pipeline can pick up the latest OrbStack experiment."
} > "$OUTPUT_FILE"

echo "$OUTPUT_FILE"
