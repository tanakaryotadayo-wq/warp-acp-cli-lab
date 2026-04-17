#!/usr/bin/env python3
"""
fleet-bridge — Copilot CLI Fleet用MCP stdio bridge (Mac Studio ローカル版)

titan_mcp_bridge.py からクローン。
Mac Studio 上で直接動作するため、自身の fusion-gate HTTP API (localhost:9000) に接続。

用途:
  - GPT-5 mini Fleet のサブエージェントがこの MCP を使う
  - ログ記録、KI更新、パターン抽出を無料AIで回す
  - 監査は DeepSeek VORTEX Critic (別パイプライン) が担当

起動:
  copilot --model gpt-5-mini --additional-mcp-config '{"fleet-bridge":{"command":"python3","args":["/Users/ryyota/vscode-oss/extensions/ryota-core.vortex-critic/assets/gemini/fleet_bridge.py"]}}'
"""
import json
import sys
import urllib.request
import urllib.error
import hashlib
import re

# Mac Studio のローカル fusion-gate API
FUSION_GATE_URL = "http://localhost:9000"

# Fleet向けツール定義（GPT-5 mini が使うもののみ）
TOOLS = [
    # === ログ記録系 ===
    {
        "name": "fleet_log",
        "description": "セッションログを構造化記録（成功/失敗/失敗→成功パターン）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_type": {"type": "string", "enum": ["success", "failure", "recovery"], "description": "成功/失敗/リカバリ"},
                "task": {"type": "string", "description": "何をしようとしたか"},
                "result": {"type": "string", "description": "結果の詳細"},
                "cause": {"type": "string", "description": "失敗原因（failure/recovery時）"},
                "fix": {"type": "string", "description": "修正方法（recovery時）"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "タグ"},
            },
            "required": ["event_type", "task", "result"],
        },
    },

    # === KI系 ===
    {
        "name": "eck_read",
        "description": "ECK/KI履歴読み込み",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "eck_write",
        "description": "ECKに追記（KI更新）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "append": {"type": "boolean", "default": True},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "ki_queue_list",
        "description": "KI昇格キューを一覧する",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "promoted", "all"], "default": "pending"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "ki_queue_promote",
        "description": "KI昇格キューの項目を knowledge/ に昇格する",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry_id": {"type": "string", "description": "キュー項目ID"},
                "ki_name": {"type": "string", "description": "昇格先のKIディレクトリ名"},
                "title": {"type": "string", "description": "KIタイトル"},
                "summary": {"type": "string", "description": "KI要約"},
                "artifact_name": {"type": "string", "description": "artifact markdown file name"},
                "content": {"type": "string", "description": "artifact markdown本文"},
            },
            "required": ["entry_id"],
        },
    },

    # === 成功事例系 ===
    {
        "name": "success_register",
        "description": "成功事例を登録する",
        "inputSchema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "カテゴリ (pcc/security/architecture/tool/config/debug/pattern)"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "solution": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["category", "title", "description", "solution"],
        },
    },
    {
        "name": "success_search",
        "description": "成功事例を検索する。キーワード、カテゴリ、タグで検索可能",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
        },
    },

    # === メモリ系 ===
    {
        "name": "memory_search",
        "description": "過去会話のセマンティック検索",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_feedback",
        "description": "検索結果の採用/不採用フィードバック",
        "inputSchema": {
            "type": "object",
            "properties": {
                "chunk_id": {"type": "string"},
                "source_type": {"type": "string", "enum": ["ki", "chat"]},
                "adopted": {"type": "boolean", "default": True},
                "feedback": {"type": "string"},
            },
            "required": ["chunk_id", "source_type"],
        },
    },

    # === 統計系 ===
    {
        "name": "get_stats",
        "description": "FusionGateシステム統計",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

# ─── Memory pipeline (extracted module) ──────────────────────────────────────
import os
from pathlib import Path
from datetime import datetime

try:
    from memory_pipeline import (
        handle_fleet_log,
        handle_ki_queue_list,
        handle_ki_queue_promote,
        FLEET_LOG_DIR as LOG_DIR,
        KI_QUEUE_FILE,
        KI_KNOWLEDGE_DIR,
        KI_COLAB_NOTEBOOK,
    )
except ImportError:
    import importlib.util as _mp_ilu
    _mp_spec = _mp_ilu.spec_from_file_location(
        "memory_pipeline", str(Path(__file__).parent / "memory_pipeline.py")
    )
    _mp_mod = _mp_ilu.module_from_spec(_mp_spec)
    _mp_spec.loader.exec_module(_mp_mod)  # type: ignore[union-attr]
    handle_fleet_log = _mp_mod.handle_fleet_log
    handle_ki_queue_list = _mp_mod.handle_ki_queue_list
    handle_ki_queue_promote = _mp_mod.handle_ki_queue_promote
    LOG_DIR = _mp_mod.FLEET_LOG_DIR
    KI_QUEUE_FILE = _mp_mod.KI_QUEUE_FILE
    KI_KNOWLEDGE_DIR = _mp_mod.KI_KNOWLEDGE_DIR
    KI_COLAB_NOTEBOOK = _mp_mod.KI_COLAB_NOTEBOOK


# ─── Fusion Gate relay ───────────────────────────────────────────────────────
def call_fusion_gate(tool_name: str, arguments: dict) -> dict:
    """fusion-gate の HTTP API にリクエストを送る"""
    url = f"{FUSION_GATE_URL}/api/tool/{tool_name}"
    data = json.dumps(arguments).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"FusionGate unreachable: {e}"}
    except Exception as e:
        return {"error": str(e)}


def handle_request(request: dict) -> "dict | None":
    """JSON-RPC リクエストを処理する"""
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "fleet-bridge", "version": "1.0.0"}
        }}
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]} for t in TOOLS]
        }}
    elif method == "tools/call":
        params = request.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        # fleet_log はローカル処理
        if name == "fleet_log":
            result = handle_fleet_log(arguments)
        elif name == "ki_queue_list":
            result = handle_ki_queue_list(arguments)
        elif name == "ki_queue_promote":
            result = handle_ki_queue_promote(arguments)
        else:
            # fusion-gate API に中継
            result = call_fusion_gate(name, arguments)

        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
        }}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def main():
    """stdio で JSON-RPC を受け取り、処理する"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            pass


if __name__ == "__main__":
    main()
