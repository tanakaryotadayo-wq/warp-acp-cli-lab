#!/usr/bin/env python3
"""
vortex-critic.py

VORTEX Protocol enhanced DeepSeek critic hook.
Cloned from deepseek-critic.py with evidence-based verification layer.

Principle: "worker の自己申告は信用しない。
git diff、tests、lint、exit code、changed files で判定する。
スコープ外変更は落とす。"

Usage:
  1. As a VS Code workspace hook (.copilot/hooks/)
  2. As a standalone audit tool: echo '{"prompt":"...","workspaceRoot":"/path"}' | python3 vortex-critic.py
  3. As an MCP umpire (called from fusion-orchestrator-mcp)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


# ── PCC Presets (調教済み) ───────────────────────────────────────────────────

PCC_PRESETS = {
    "探": {
        "label": "#探",
        "constraints": [
            "Challenge assumptions and look for specific weaknesses.",
            "Do not be agreeable for its own sake.",
            "Prefer concrete risks over vague criticism.",
            "If something is uncertain, mark it as an assumption.",
        ],
    },
    "極": {
        "label": "#極",
        "constraints": [
            "Be concise and structured.",
            "Answer only what is useful for moving the task forward.",
            "Avoid filler and social niceties.",
        ],
    },
    "均": {
        "label": "#均",
        "constraints": [
            "Balance strengths and weaknesses.",
            "Pair each weakness with one practical improvement.",
        ],
    },
    "監": {
        "label": "#監",
        "constraints": [
            "Judge based on evidence and likely verification needs.",
            "Ignore self-reports of success if they lack support.",
            "Call out missing evidence explicitly.",
            "If git diff is provided, verify claims against actual changes.",
            "If test exit code is non-zero, mark as UNVERIFIED regardless of prose.",
        ],
    },
    "刃": {
        "label": "#刃",
        "constraints": [
            "Behave like a strict implementation reviewer.",
            "Prefer concrete change plans over abstract commentary.",
            "Recommend the safest next step with reasons.",
        ],
    },
    # VORTEX Protocol 専用プリセット
    "渦": {
        "label": "#渦 (VORTEX)",
        "constraints": [
            "You are a VORTEX Protocol auditor. Your sole purpose is to detect the Completion Illusion.",
            "NEVER trust self-reports of success. Only trust objective evidence.",
            "If git diff is provided, verify that every claimed change exists in the diff.",
            "If test results are provided, verify exit code === 0. Non-zero = UNVERIFIED.",
            "If no verification evidence (test output, lint results, exit codes) is attached, mark as UNVERIFIED_MUTATION.",
            "Flag any changed files that were NOT mentioned in the original task scope as SCOPE_VIOLATION.",
            "Output a binary verdict: VERIFIED or UNVERIFIED, followed by evidence list.",
            "Be relentless. The AI you are auditing has a known tendency to claim completion one step early.",
        ],
    },
}


DEFAULT_CONFIG = {
    "enabled": True,
    "provider": "deepseek",
    "model": "deepseek-chat",
    "preset": "渦",  # VORTEX preset by default
    "baseUrl": "https://api.deepseek.com/chat/completions",
    "keychainService": "deepseek-api",
    "keychainAccount": "default",
    "fallbackKeychainServices": ["deepseek-api", "deepseek", "DeepSeek API"],
    "maxTokens": 1200,
    "temperature": 0.1,  # Lower temp for stricter judgment
    "promptContextChars": 16000,
    "responseChars": 4000,
    "styleNote": "Return critique in Japanese. Be dense, concrete, and merciless about missing evidence.",
    "focusAreas": [
        "completion illusion detection",
        "missing verification evidence",
        "git diff vs claimed changes mismatch",
        "test/lint exit code verification",
        "scope violation detection",
    ],
}


# ── Evidence Collection (VORTEX Core) ────────────────────────────────────────

def collect_git_evidence(workspace_root: str) -> dict[str, Any]:
    """Collect objective evidence from git state."""
    evidence: dict[str, Any] = {}

    try:
        # git diff --stat for changed files
        result = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=workspace_root, capture_output=True, text=True, timeout=5, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            evidence["diff_stat"] = result.stdout.strip()
    except Exception:
        pass

    try:
        # git diff --name-only for file list
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=workspace_root, capture_output=True, text=True, timeout=5, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            evidence["changed_files"] = result.stdout.strip().split("\n")
    except Exception:
        pass

    try:
        # staged changes too
        result = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=workspace_root, capture_output=True, text=True, timeout=5, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            evidence["staged_files"] = result.stdout.strip().split("\n")
    except Exception:
        pass

    try:
        # Last commit message (for reference)
        result = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=workspace_root, capture_output=True, text=True, timeout=5, check=False
        )
        if result.returncode == 0 and result.stdout.strip():
            evidence["last_commit"] = result.stdout.strip()
    except Exception:
        pass

    return evidence


def collect_test_evidence(workspace_root: str) -> dict[str, Any]:
    """Check for recent test results or common test artifacts."""
    evidence: dict[str, Any] = {}
    ws = Path(workspace_root)

    # Check for common test result files
    test_artifacts = [
        "test-results.xml", "junit.xml", ".pytest_cache/lastfailed",
        "coverage/lcov.info", "coverage/coverage-summary.json",
    ]
    found = []
    for artifact in test_artifacts:
        path = ws / artifact
        if path.exists():
            found.append(str(artifact))
    if found:
        evidence["test_artifacts_found"] = found

    return evidence


# ── Core Functions ───────────────────────────────────────────────────────────

def load_config() -> dict[str, Any]:
    config_path = Path(__file__).with_name("vortex-critic.config.json")
    if not config_path.exists():
        # Fallback to deepseek config
        config_path = Path(__file__).with_name("deepseek-critic.config.json")
    if not config_path.exists():
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        merged = dict(DEFAULT_CONFIG)
        merged.update(data)
        return merged
    except Exception:
        print("VORTEX critic config JSON is invalid.", file=sys.stderr)
        return dict(DEFAULT_CONFIG)


def read_stdin_json() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {"prompt": raw}


def read_keychain_secret(service: str, account: str | None) -> str | None:
    base = ["security", "find-generic-password", "-w", "-s", service]
    variants = []
    if account:
        variants.append(base + ["-a", account])
    variants.append(base)

    for cmd in variants:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
        except Exception:
            continue
    return None


def resolve_api_key(config: dict[str, Any]) -> str | None:
    env_key = os.environ.get("DEEPSEEK_API_KEY")
    if env_key:
        return env_key

    service = config.get("keychainService")
    account = config.get("keychainAccount")
    if isinstance(service, str) and service:
        secret = read_keychain_secret(service, account if isinstance(account, str) else None)
        if secret:
            return secret

    for fallback_service in config.get("fallbackKeychainServices", []):
        if not isinstance(fallback_service, str):
            continue
        secret = read_keychain_secret(fallback_service, account if isinstance(account, str) else None)
        if secret:
            return secret

    return None


def build_messages(data: dict[str, Any], config: dict[str, Any], evidence: dict[str, Any]) -> list[dict[str, str]]:
    preset_name = config.get("preset", "渦")
    preset = PCC_PRESETS.get(preset_name, PCC_PRESETS["渦"])
    constraints = "\n".join(f"- {item}" for item in preset["constraints"])
    style_note = config.get("styleNote", DEFAULT_CONFIG["styleNote"])
    focus_areas = "\n".join(f"- {item}" for item in config.get("focusAreas", []))
    prompt = str(data.get("prompt", ""))
    active_file = data.get("activeFile")
    workspace_root = data.get("workspaceRoot")

    focus_block = ""
    if focus_areas:
        focus_block = "- Extra focus areas:\n" + focus_areas + "\n"

    system_prompt = (
        f"[PCC Protocol: {preset['label']}]\n"
        "[VORTEX Evidence Verification Layer]\n"
        "You are a read-only critic and evidence auditor for another coding agent.\n"
        "You do not implement the task. You verify claims against evidence.\n\n"
        "Core Principle:\n"
        "- workerの自己申告は信用しない\n"
        "- git diff、tests、lint、exit code、changed files で判定する\n"
        "- スコープ外変更は落とす\n\n"
        "Constraints:\n"
        f"{constraints}\n"
        f"- {style_note}\n"
        f"{focus_block}"
        "\nOutput format:\n"
        "1. VERDICT: VERIFIED or UNVERIFIED\n"
        "2. Evidence found (list what objective evidence exists)\n"
        "3. Evidence missing (what should exist but doesn't)\n"
        "4. Scope check (any files changed outside of intended scope?)\n"
        "5. Next required action (concrete verification command to run)\n"
    )

    context_lines = []
    if isinstance(active_file, str) and active_file:
        context_lines.append(f"Active file: {active_file}")
    if isinstance(workspace_root, str) and workspace_root:
        context_lines.append(f"Workspace root: {workspace_root}")

    # Inject objective evidence
    if evidence:
        context_lines.append("\n--- Objective Evidence (collected by VORTEX, not self-reported) ---")
        if "diff_stat" in evidence:
            context_lines.append(f"Git diff stat:\n{evidence['diff_stat']}")
        if "changed_files" in evidence:
            context_lines.append(f"Changed files: {', '.join(evidence['changed_files'])}")
        if "staged_files" in evidence:
            context_lines.append(f"Staged files: {', '.join(evidence['staged_files'])}")
        if "last_commit" in evidence:
            context_lines.append(f"Last commit: {evidence['last_commit']}")
        if "test_artifacts_found" in evidence:
            context_lines.append(f"Test artifacts found: {', '.join(evidence['test_artifacts_found'])}")
        if not evidence:
            context_lines.append("⚠️ NO OBJECTIVE EVIDENCE FOUND. Worker has not run any verification.")

        # Check for test exit code passed via stdin
        if "test_exit_code" in data:
            context_lines.append(f"Test exit code: {data['test_exit_code']}")
        if "lint_exit_code" in data:
            context_lines.append(f"Lint exit code: {data['lint_exit_code']}")
        if "scope_files" in data:
            context_lines.append(f"Intended scope: {', '.join(data['scope_files'])}")

    context_block = "\n".join(context_lines)
    if context_block:
        context_block = f"Known context:\n{context_block}\n\n"

    user_prompt = (
        "Review the following AI agent output as a VORTEX auditor.\n"
        "Do not answer the original request. Only verify whether the agent's claims are backed by evidence.\n"
        "If the agent claims 'done' or 'completed' without test/lint evidence, mark UNVERIFIED.\n\n"
        f"{context_block}"
        f"{prompt[: int(config.get('promptContextChars', DEFAULT_CONFIG['promptContextChars']))]}"
    )

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def call_deepseek(messages: list[dict[str, str]], config: dict[str, Any], api_key: str) -> str | None:
    payload = {
        "model": config.get("model", DEFAULT_CONFIG["model"]),
        "messages": messages,
        "temperature": config.get("temperature", DEFAULT_CONFIG["temperature"]),
        "max_tokens": config.get("maxTokens", DEFAULT_CONFIG["maxTokens"]),
        "stream": False,
    }
    request = urllib.request.Request(
        config.get("baseUrl", DEFAULT_CONFIG["baseUrl"]),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return f"VORTEX critic failed with HTTP {exc.code}."
    except Exception:
        return None

    try:
        text = body["choices"][0]["message"]["content"]
    except Exception:
        return None

    if not isinstance(text, str):
        return None
    return text[: int(config.get("responseChars", DEFAULT_CONFIG["responseChars"]))]


def emit(additional_context: str | None) -> int:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
        }
    }
    if additional_context:
        payload["hookSpecificOutput"]["additionalContext"] = additional_context
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main() -> int:
    config = load_config()
    if not config.get("enabled", True):
        return emit(None)

    data = read_stdin_json()
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return emit(None)

    api_key = resolve_api_key(config)
    if not api_key:
        print("DeepSeek API key missing.", file=sys.stderr)
        return emit(None)

    # Collect objective evidence from workspace (VORTEX Core)
    workspace_root = data.get("workspaceRoot", "")
    evidence: dict[str, Any] = {}
    if workspace_root and Path(workspace_root).is_dir():
        evidence.update(collect_git_evidence(workspace_root))
        evidence.update(collect_test_evidence(workspace_root))

    # Pass through any test/lint exit codes from caller
    if "test_exit_code" in data:
        evidence["test_exit_code"] = data["test_exit_code"]
    if "lint_exit_code" in data:
        evidence["lint_exit_code"] = data["lint_exit_code"]

    messages = build_messages(data, config, evidence)
    response = call_deepseek(messages, config, api_key)
    if not response:
        print("VORTEX critic returned no response.", file=sys.stderr)
        return emit(None)

    additional_context = f"[VORTEX Critic]\n{response.strip()}"
    return emit(additional_context)


if __name__ == "__main__":
    raise SystemExit(main())
