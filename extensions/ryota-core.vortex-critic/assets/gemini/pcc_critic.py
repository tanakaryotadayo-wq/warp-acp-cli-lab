#!/usr/bin/env python3
"""
pcc-critic — PCC 制約注入 × Gemini CLI で AI の本気を引き出す critic パイプライン

Usage:
  pcc-critic "semantic_delta の弱点を指摘しろ"
  pcc-critic --preset 極 "このコードをレビューしろ"
  pcc-critic --model gemini-3.1-pro-preview --preset 探 "設計の穴を見つけろ"
  cat code.py | pcc-critic --preset 極 "このコードの問題点は？"

PCC Presets:
  探 (#探) — 批判的探索。多角的視点、前提への挑戦、弱点指摘（デフォルト）
  極 (#極) — 極限精度。無駄ゼロ、聞かれたことだけ答える
  均 (#均) — バランス型。批判と提案を均等に
  監 (#監) — 監査特化。diff/test/evidence ベースの判定
  刃 (#刃) — 実装設計レビュー。3案比較+構造化出力
"""
import argparse
import json
import os
import subprocess
import sys
import time

# ─── PCC Presets ─────────────────────────────────────────────────────────────

PCC_PRESETS = {
    "探": {
        "label": "#探 (Critical Explorer)",
        "constraints": [
            "Explore multiple perspectives and challenge all assumptions",
            "Identify specific weaknesses with concrete reasoning",
            "Do NOT be agreeable — be a constructive critic",
            "Support each point with examples or scenarios",
            "If there are no weaknesses, say so explicitly — do not fabricate",
        ],
    },
    "極": {
        "label": "#極 (Maximum Precision)",
        "constraints": [
            "Be extremely concise — no filler, no pleasantries",
            "Answer only what is asked, nothing more",
            "Use structured output (numbered lists, tables)",
            "If you cannot answer precisely, state why",
        ],
    },
    "均": {
        "label": "#均 (Balanced Review)",
        "constraints": [
            "Provide equal weight to strengths and weaknesses",
            "For each weakness, suggest a concrete improvement",
            "Be honest but constructive",
            "Prioritize actionable feedback over theoretical concerns",
        ],
    },
    "監": {
        "label": "#監 (Audit Mode)",
        "constraints": [
            "Evaluate based on evidence only: diff, tests, logs, exit codes",
            "Ignore natural language self-reports of success",
            "Classify result as: PASS / NEEDS_EVIDENCE / NO_OP / FAIL",
            "If no evidence is provided, verdict is NEEDS_EVIDENCE",
            "State exactly what evidence is missing",
        ],
    },
    "刃": {
        "label": "#刃 (Blade — Implementation Reviewer)",
        "constraints": [
            "You are a strict implementation designer and code reviewer",
            "First examine related files and existing implementation — judge based on facts only",
            "Never assert unverified claims. Mark guesses explicitly as 'Assumption'",
            "Do NOT stop at one option — compare at least 3 alternatives",
            "For each alternative, output: changes / blast radius / risks / test focus / likely failure points",
            "Recommend the safest option with clear justification",
            "Before saying 'done', present the actual evidence you verified",
            "If information is missing, state a reasonable assumption and proceed",
            "No verbose preamble. Output at maximum density",
        ],
        "output_format": """Output format (MANDATORY):
1. 結論 (Conclusion)
2. 現状理解 (Current Understanding)
3. 代替案 A / B / C (Alternatives with tradeoffs)
4. 推奨案 (Recommendation + reasoning)
5. 実装手順 (Implementation steps)
6. テスト観点 (Test perspectives)
7. 残る不確実性 (Remaining uncertainties)""",
    },
}


# ─── Routing Table ───────────────────────────────────────────────────────────

MODEL_ROUTING = {
    "fast":   "gemini-2.5-flash",
    "standard": "gemini-2.5-pro",
    "deep":   "gemini-3.1-pro-preview",
}

# ─── Core ────────────────────────────────────────────────────────────────────

def inject_pcc(prompt: str, preset: str) -> str:
    """PCC 制約プロトコルを prompt に注入する"""
    config = PCC_PRESETS.get(preset)
    if not config:
        print(f"[PCC] Unknown preset: {preset}, falling back to #探", file=sys.stderr)
        config = PCC_PRESETS["探"]

    constraints = "\n".join(f"  - {c}" for c in config["constraints"])
    output_fmt = config.get("output_format", "")
    fmt_block = f"\n\n{output_fmt}" if output_fmt else ""

    return f"""[PCC Protocol: {config['label']}]
Constraints:
{constraints}
{fmt_block}
---
{prompt}"""


def run_gemini(enriched_prompt: str, model: str, timeout: int = 120) -> dict:
    """Gemini CLI headless で実行し結果を返す"""
    env = os.environ.copy()

    # Mac Studio: Homebrew Node
    homebrew_node = "/opt/homebrew/Cellar/node/25.3.0/bin"
    if os.path.exists(homebrew_node):
        env['PATH'] = f"{homebrew_node}:/opt/homebrew/bin:{env.get('PATH', '')}"
    else:
        # MBA: nvm 経由
        nvm_dir = os.path.expanduser("~/.nvm")
        node_path = os.popen(
            f'bash -c "source {nvm_dir}/nvm.sh && nvm which node 2>/dev/null"'
        ).read().strip()
        if node_path:
            env['PATH'] = f"{os.path.dirname(node_path)}:{env.get('PATH', '')}"

    gemini_bin = "/opt/homebrew/bin/gemini"
    if not os.path.exists(gemini_bin):
        gemini_bin = "gemini"

    t0 = time.monotonic()
    try:
        result = subprocess.run(
            [gemini_bin, '--approval-mode', 'plan', '-p', enriched_prompt, '-m', model],
            capture_output=True, text=True, env=env, timeout=timeout,
            cwd=os.path.expanduser("~"),
        )
        elapsed = time.monotonic() - t0
        return {
            "text": result.stdout.strip() or result.stderr.strip(),
            "exit_code": result.returncode,
            "elapsed": round(elapsed, 1),
            "model": model,
        }
    except subprocess.TimeoutExpired:
        return {
            "text": "",
            "exit_code": -1,
            "elapsed": timeout,
            "model": model,
            "error": "TIMEOUT",
        }


def audit_response(text: str) -> dict:
    """応答の品質を監査する"""
    if not text or len(text.strip()) < 20:
        return {"verdict": "NO_OP", "sycophancy": 0.0, "evidence_count": 0, "words": 0}

    lower = text.lower()
    words = len(text.split())

    # 迎合検知
    syc_markers = [
        "great question", "excellent point", "absolutely right",
        "wonderful", "that's a great idea", "you're absolutely",
        "brilliant", "i love this",
    ]
    syc = sum(1 for m in syc_markers if m in lower) / 3.0

    # evidence 検知（批判的内容の指標）
    ev_markers = [
        "however", "risk", "weakness", "problem", "flaw",
        "limitation", "because", "specifically", "example",
        "breaking", "failure", "impossible", "incorrect",
        "矛盾", "欠陥", "弱点", "問題", "不可能", "破綻", "危険",
    ]
    evidence = sum(1 for m in ev_markers if m in lower)

    if syc > 0.5:
        verdict = "SYCOPHANTIC"
    elif evidence >= 3:
        verdict = "PASS"
    elif evidence >= 1:
        verdict = "REVIEW"
    elif words < 50:
        verdict = "NO_OP"
    else:
        verdict = "NEEDS_EVIDENCE"

    return {
        "verdict": verdict,
        "sycophancy": round(min(syc, 1.0), 2),
        "evidence_count": evidence,
        "words": words,
    }


def main():
    parser = argparse.ArgumentParser(
        description="PCC Critic — PCC 制約注入で AI の本気を引き出す",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Presets:
  探  批判的探索（デフォルト）
  極  極限精度
  均  バランス型
  監  監査特化
  刃  実装設計レビュー（3案比較+構造化出力）

Models:
  fast      gemini-2.5-flash（初期スクリーニング）
  standard  gemini-2.5-pro（標準レビュー）
  deep      gemini-3.1-pro-preview（最深批評）

Examples:
  pcc-critic "この設計の弱点は？"
  pcc-critic --preset 極 --model deep "コードレビューしろ"
  cat diff.txt | pcc-critic --preset 監 "この diff を監査しろ"
        """,
    )
    parser.add_argument("prompt", nargs="?", help="プロンプト（stdinからも読める）")
    parser.add_argument("--preset", "-P", default="探", choices=PCC_PRESETS.keys(),
                        help="PCC プリセット（デフォルト: 探）")
    parser.add_argument("--model", "-m", default="deep",
                        help="モデル名 or ショートカット (fast/standard/deep)")
    parser.add_argument("--timeout", "-t", type=int, default=120, help="タイムアウト秒")
    parser.add_argument("--json", "-j", action="store_true", help="JSON 出力")
    parser.add_argument("--audit-only", action="store_true",
                        help="stdin のテキストを監査するだけ（Gemini 呼び出しなし）")

    args = parser.parse_args()

    # stdin からの入力を取得
    stdin_text = ""
    if not sys.stdin.isatty():
        stdin_text = sys.stdin.read().strip()

    if args.audit_only:
        text = stdin_text or (args.prompt or "")
        audit = audit_response(text)
        if args.json:
            print(json.dumps(audit, ensure_ascii=False, indent=2))
        else:
            for k, v in audit.items():
                print(f"  {k}: {v}")
        sys.exit(0 if audit["verdict"] == "PASS" else 1)

    # プロンプト構築
    prompt = args.prompt or ""
    if stdin_text:
        prompt = f"{stdin_text}\n\n---\n{prompt}" if prompt else stdin_text

    if not prompt:
        parser.error("プロンプトを指定するか、stdin からパイプしてください")

    # モデル解決
    model = MODEL_ROUTING.get(args.model, args.model)

    # PCC 注入
    enriched = inject_pcc(prompt, args.preset)

    if not args.json:
        print(f"[PCC] Preset: #{args.preset} → {PCC_PRESETS[args.preset]['label']}")
        print(f"[Model] {model}")
        print(f"[Prompt] {len(enriched)} chars")
        print("─" * 50)

    # Gemini 実行
    result = run_gemini(enriched, model, args.timeout)

    # Audit
    audit = audit_response(result["text"])

    if args.json:
        output = {
            "pcc_preset": args.preset,
            "model": model,
            "response": result["text"],
            "elapsed": result["elapsed"],
            "exit_code": result["exit_code"],
            "audit": audit,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(result["text"])
        print("─" * 50)
        print(f"[Audit] verdict={audit['verdict']} sycophancy={audit['sycophancy']} "
              f"evidence={audit['evidence_count']} words={audit['words']} "
              f"time={result['elapsed']}s")

    # exit code: 0=PASS, 1=other
    sys.exit(0 if audit["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
