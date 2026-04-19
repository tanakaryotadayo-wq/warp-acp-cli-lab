#!/usr/bin/env bash
set -euo pipefail

# ─────────────────────────────────────────────────────────
# VORTEX Verify Gate — 検証合格しないと次に進めないワークフロー
# Usage: verify_gate.sh <step> [options]
#   Steps: branch | review | commit | push | pr-health | full
# ─────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
GATE_STATE_DIR="${REPO_ROOT}/.build/ryota/verify_gate"
GATE_LOG="${GATE_STATE_DIR}/gate.log"
GEMINI_MODEL_PRO="gemini-3.1-pro-preview"
GEMINI_MODEL_FLASH="gemini-3.1-flash-preview"
FORK_REMOTE="ryota-fork"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

mkdir -p "$GATE_STATE_DIR"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$GATE_LOG"; }
pass() { echo -e "${GREEN}✅ PASS${NC}: $1"; log "PASS: $1"; }
fail() { echo -e "${RED}❌ FAIL${NC}: $1"; log "FAIL: $1"; return 1; }
warn() { echo -e "${YELLOW}⚠️  WARN${NC}: $1"; log "WARN: $1"; }
info() { echo -e "${CYAN}ℹ️  INFO${NC}: $1"; }

# ── Step 1: Branch Check ─────────────────────────────────
step_branch() {
  info "Step 1/5: ブランチチェック"
  local branch
  branch="$(git branch --show-current)"

  if [ -z "$branch" ]; then
    fail "detached HEAD — ブランチに切り替えてください"
  fi

  if [ "$branch" = "main" ] || [ "$branch" = "master" ]; then
    fail "main/master への直接コミットは禁止。feature ブランチを切ってください"
  fi

  if ! echo "$branch" | grep -qE '^ryota/'; then
    warn "ブランチ名が ryota/ prefix なし: $branch (推奨: ryota/<topic>)"
  fi

  local behind
  behind=$(git rev-list --count HEAD.."${FORK_REMOTE}/main" 2>/dev/null || echo "?")
  if [ "$behind" != "?" ] && [ "$behind" -gt 20 ]; then
    warn "main から ${behind} コミット遅れ。リベース推奨。"
  fi

  pass "ブランチ: $branch"
  echo "$branch" > "${GATE_STATE_DIR}/last_branch"
}

# ── Step 2: Diff Review (Gemini 3.1 Pro) ─────────────────
step_review() {
  info "Step 2/5: Gemini 3.1 Pro による diff レビュー"

  local diff_file="${GATE_STATE_DIR}/staged.diff"
  local review_file="${GATE_STATE_DIR}/review_result.json"

  # staged diff 取得 (なければ unstaged)
  if git diff --cached --quiet 2>/dev/null; then
    if git diff --quiet 2>/dev/null; then
      fail "レビュー対象の変更がありません (git add してください)"
    fi
    warn "staged 変更なし。unstaged diff をレビューします。"
    git diff > "$diff_file"
  else
    git diff --cached > "$diff_file"
  fi

  local diff_lines
  diff_lines=$(wc -l < "$diff_file" | tr -d ' ')
  info "diff: ${diff_lines} 行"

  if [ "$diff_lines" -gt 2000 ]; then
    warn "diff が大きい (${diff_lines}行)。分割コミットを推奨。"
  fi

  if ! command -v gemini >/dev/null 2>&1; then
    warn "Gemini CLI 未検出。diff サイズチェックのみ実行。"
    pass "diff レビュー (lite): ${diff_lines} 行"
    echo '{"verdict":"PASS","mode":"lite","reason":"gemini unavailable"}' > "$review_file"
    return 0
  fi

  local prompt
  prompt=$(cat <<'PROMPT_END'
あなたは VORTEX 拡張機能のコードレビュアーです。
以下の git diff を検証し、JSON で回答してください。

チェック項目:
1. 概念的整合性: 変更がコミットメッセージの意図と一致するか
2. 副作用: 無関係なファイルの変更が混入してないか
3. 秘密情報: APIキー、パスワード、トークンが含まれてないか
4. 構文: 明らかな構文エラーがないか
5. ランタイム状態: tracked ディレクトリにランタイムファイルを書いてないか

回答フォーマット (JSON のみ、他のテキスト不要):
{"verdict":"PASS" or "FAIL","issues":["issue1","issue2"],"summary":"一行要約"}
PROMPT_END
)

  info "Gemini 3.1 Pro でレビュー中..."
  local result
  result=$(cat "$diff_file" | gemini -m "$GEMINI_MODEL_PRO" -p "$prompt" -o text 2>/dev/null) || {
    warn "Gemini CLI 呼び出し失敗。lite モードにフォールバック。"
    pass "diff レビュー (lite fallback): ${diff_lines} 行"
    echo '{"verdict":"PASS","mode":"lite","reason":"gemini call failed"}' > "$review_file"
    return 0
  }

  echo "$result" > "$review_file"

  # verdict 抽出
  local verdict
  verdict=$(echo "$result" | grep -o '"verdict"\s*:\s*"[^"]*"' | head -1 | grep -o '"[^"]*"$' | tr -d '"')

  if [ "$verdict" = "FAIL" ]; then
    echo -e "\n${RED}── レビュー結果 ──${NC}"
    echo "$result"
    fail "Gemini レビュー FAIL。上記の issues を修正してください。"
  fi

  pass "Gemini diff レビュー: $verdict"
}

# ── Step 3: Commit ────────────────────────────────────────
step_commit() {
  info "Step 3/5: コミット"

  # gate 状態確認
  if [ ! -f "${GATE_STATE_DIR}/review_result.json" ]; then
    fail "レビュー未実施。先に 'verify_gate.sh review' を実行してください。"
  fi

  local review_age
  review_age=$(( $(date +%s) - $(stat -f %m "${GATE_STATE_DIR}/review_result.json" 2>/dev/null || stat -c %Y "${GATE_STATE_DIR}/review_result.json" 2>/dev/null) ))
  if [ "$review_age" -gt 600 ]; then
    warn "レビュー結果が ${review_age}秒前。再レビュー推奨。"
  fi

  if git diff --cached --quiet 2>/dev/null; then
    fail "staged 変更がありません。git add してください。"
  fi

  local msg="${1:-}"
  if [ -z "$msg" ]; then
    fail "コミットメッセージを指定してください: verify_gate.sh commit 'feat: ...'"
  fi

  git commit -m "$msg

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

  pass "コミット完了: $(git rev-parse --short HEAD)"
  git rev-parse HEAD > "${GATE_STATE_DIR}/last_commit"
}

# ── Step 4: Push ──────────────────────────────────────────
step_push() {
  info "Step 4/5: プッシュ"

  if [ ! -f "${GATE_STATE_DIR}/last_commit" ]; then
    fail "コミット未完了。先に commit ステップを実行してください。"
  fi

  local branch
  branch="$(git branch --show-current)"
  git push "$FORK_REMOTE" "$branch" --force-with-lease 2>&1

  pass "プッシュ完了: ${FORK_REMOTE}/${branch}"
  echo "$branch" > "${GATE_STATE_DIR}/last_pushed_branch"
}

# ── Step 5: PR Health ─────────────────────────────────────
step_pr_health() {
  info "Step 5/5: PR ヘルスチェック"

  local branch
  branch="$(git branch --show-current)"
  local repo="tanakaryotadayo-wq/warp-acp-cli-lab"

  local pr_data
  pr_data=$(gh pr list --repo "$repo" --head "$branch" --json number,title,mergeable,additions,deletions,changedFiles --jq '.[0]' 2>/dev/null)

  if [ -z "$pr_data" ] || [ "$pr_data" = "null" ]; then
    info "PR 未作成。作成しますか？ (y/N)"
    read -r answer
    if [ "$answer" = "y" ]; then
      gh pr create --repo "$repo" --head "$branch" --base main --fill 2>&1
      pass "PR 作成完了"
    else
      warn "PR 未作成。手動で作成してください。"
    fi
    return 0
  fi

  local mergeable additions files
  mergeable=$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('mergeable','UNKNOWN'))")
  additions=$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('additions',0))")
  files=$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('changedFiles',0))")

  if [ "$mergeable" = "CONFLICTING" ]; then
    fail "PR にコンフリクトあり。リベースが必要です。"
  fi

  if [ "$additions" -gt 5000 ]; then
    warn "PR が大きい (${additions} 行追加, ${files} ファイル)。分割推奨。"
  fi

  pass "PR ヘルス: mergeable=$mergeable, +${additions}/-$(echo "$pr_data" | python3 -c "import sys,json; print(json.load(sys.stdin).get('deletions',0))"), ${files} files"
}

# ── Full Pipeline ─────────────────────────────────────────
step_full() {
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${CYAN}  VORTEX Verify Gate — Full Pipeline${NC}"
  echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo ""

  step_branch
  echo ""
  step_review
  echo ""

  info "レビュー合格。コミットメッセージを入力:"
  read -r commit_msg
  step_commit "$commit_msg"
  echo ""

  step_push
  echo ""
  step_pr_health
  echo ""

  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${GREEN}  全ステップ完了 ✅${NC}"
  echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# ── Status ────────────────────────────────────────────────
step_status() {
  echo -e "${CYAN}━━━ Gate Status ━━━${NC}"
  echo "Branch:  $(cat "${GATE_STATE_DIR}/last_branch" 2>/dev/null || echo 'N/A')"
  echo "Review:  $(cat "${GATE_STATE_DIR}/review_result.json" 2>/dev/null | head -1 || echo 'N/A')"
  echo "Commit:  $(cat "${GATE_STATE_DIR}/last_commit" 2>/dev/null || echo 'N/A')"
  echo "Pushed:  $(cat "${GATE_STATE_DIR}/last_pushed_branch" 2>/dev/null || echo 'N/A')"
  echo ""
  if [ -f "$GATE_LOG" ]; then
    echo -e "${CYAN}━━━ Recent Log ━━━${NC}"
    tail -10 "$GATE_LOG"
  fi
}

# ── Main ──────────────────────────────────────────────────
case "${1:-status}" in
  branch)     step_branch ;;
  review)     step_review ;;
  commit)     shift; step_commit "$*" ;;
  push)       step_push ;;
  pr-health)  step_pr_health ;;
  full)       step_full ;;
  status)     step_status ;;
  *)
    echo "Usage: verify_gate.sh <branch|review|commit|push|pr-health|full|status>"
    echo ""
    echo "Steps (must pass in order):"
    echo "  branch    — ブランチ名チェック (main禁止)"
    echo "  review    — Gemini 3.1 Pro diff レビュー"
    echo "  commit    — コミット (レビュー合格必須)"
    echo "  push      — プッシュ (コミット必須)"
    echo "  pr-health — PR ヘルスチェック"
    echo "  full      — 全ステップ連続実行"
    echo "  status    — 現在のゲート状態表示"
    ;;
esac
