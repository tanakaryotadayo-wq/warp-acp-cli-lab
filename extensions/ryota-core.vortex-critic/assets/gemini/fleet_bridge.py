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

# ログファイルパス
import os
from pathlib import Path
from datetime import datetime

LOG_DIR = Path(os.environ.get("FLEET_LOG_DIR", os.path.expanduser("~/.gemini/antigravity/fleet-logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
KI_QUEUE_FILE = Path(
    os.environ.get("KI_QUEUE_FILE", os.path.expanduser("~/.gemini/antigravity/ki-promotion-queue.jsonl"))
)
KI_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
KI_KNOWLEDGE_DIR = Path(
    os.environ.get("KI_KNOWLEDGE_DIR", os.path.expanduser("~/.gemini/antigravity/knowledge"))
)
KI_COLAB_NOTEBOOK = os.environ.get(
    "KI_COLAB_NOTEBOOK",
    os.path.expanduser("~/Newgate/ki_agent_system/colab_ki_vectorizer.ipynb"),
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _slugify(value: str, fallback: str = "ki_candidate") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "").strip().lower()).strip("_")
    return normalized or fallback


def _load_queue() -> list[dict]:
    if not KI_QUEUE_FILE.exists():
        return []
    entries = []
    with open(KI_QUEUE_FILE, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _write_queue(entries: list[dict]) -> None:
    with open(KI_QUEUE_FILE, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _make_queue_entry(arguments: dict, log_file: Path) -> dict:
    task = arguments.get("task", "").strip()
    result = arguments.get("result", "").strip()
    raw_title = task or result[:80] or "KI Candidate"
    fingerprint = hashlib.sha256(f"{task}\n{result}".encode("utf-8")).hexdigest()[:16]
    return {
        "id": f"ki_{fingerprint}",
        "status": "pending",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "title": raw_title[:120],
        "summary": (result or task)[:400],
        "task": task,
        "result": result,
        "cause": arguments.get("cause"),
        "fix": arguments.get("fix"),
        "tags": arguments.get("tags", []),
        "suggested_ki_name": _slugify(task or raw_title),
        "log_file": str(log_file),
        "notebook_path": KI_COLAB_NOTEBOOK,
    }


def _append_queue_entry(entry: dict) -> None:
    entries = _load_queue()
    for existing in entries:
        if existing.get("id") == entry["id"]:
            existing.update(
                {
                    "updated_at": _now_iso(),
                    "status": existing.get("status", "pending"),
                    "task": entry["task"],
                    "result": entry["result"],
                    "summary": entry["summary"],
                    "tags": entry.get("tags", []),
                    "log_file": entry["log_file"],
                    "notebook_path": entry["notebook_path"],
                }
            )
            _write_queue(entries)
            return
    entries.append(entry)
    _write_queue(entries)


def handle_ki_queue_list(arguments: dict) -> dict:
    status = arguments.get("status", "pending")
    limit = max(1, int(arguments.get("limit", 20)))
    entries = _load_queue()
    if status != "all":
        entries = [entry for entry in entries if entry.get("status") == status]
    entries = sorted(entries, key=lambda item: item.get("updated_at", ""), reverse=True)
    return {
        "status": "ok",
        "queue_file": str(KI_QUEUE_FILE),
        "knowledge_dir": str(KI_KNOWLEDGE_DIR),
        "notebook_path": KI_COLAB_NOTEBOOK,
        "count": len(entries),
        "entries": entries[:limit],
    }


def _default_artifact_content(entry: dict, title: str) -> str:
    lines = [
        f"# {title}",
        "",
        "## Source Task",
        entry.get("task", ""),
        "",
        "## Result",
        entry.get("result", ""),
    ]
    if entry.get("fix"):
        lines.extend(["", "## Fix", entry["fix"]])
    if entry.get("cause"):
        lines.extend(["", "## Cause", entry["cause"]])
    if entry.get("tags"):
        lines.extend(["", "## Tags", " ".join(f"#{tag}" for tag in entry["tags"])])
    return "\n".join(line for line in lines if line is not None).strip() + "\n"


def handle_ki_queue_promote(arguments: dict) -> dict:
    entry_id = arguments.get("entry_id", "").strip()
    if not entry_id:
        return {"error": "entry_id is required"}

    entries = _load_queue()
    entry = next((item for item in entries if item.get("id") == entry_id), None)
    if entry is None:
        return {"error": f"queue entry not found: {entry_id}"}

    ki_name = _slugify(arguments.get("ki_name") or entry.get("suggested_ki_name") or entry.get("title", ""))
    title = (arguments.get("title") or entry.get("title") or ki_name).strip()
    summary = (arguments.get("summary") or entry.get("summary") or title).strip()
    artifact_name = arguments.get("artifact_name") or f"{entry_id}.md"
    if not artifact_name.endswith(".md"):
        artifact_name += ".md"
    content = (arguments.get("content") or _default_artifact_content(entry, title)).strip() + "\n"

    ki_dir = KI_KNOWLEDGE_DIR / ki_name
    artifacts_dir = ki_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    metadata_path = ki_dir / "metadata.json"
    if metadata_path.exists():
        with open(metadata_path, "r", encoding="utf-8") as handle:
            metadata = json.load(handle)
    else:
        metadata = {}
    metadata.setdefault("title", title)
    metadata.setdefault("summary", summary)
    references = metadata.setdefault("references", [])
    references.append({"type": "fleet_queue", "value": entry_id})
    references.append({"type": "file", "value": f"artifacts/{artifact_name}"})
    metadata["summary"] = summary
    with open(metadata_path, "w", encoding="utf-8") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)

    artifact_path = artifacts_dir / artifact_name
    with open(artifact_path, "w", encoding="utf-8") as handle:
        handle.write(content)

    timestamps_path = ki_dir / "timestamps.json"
    now = _now_iso()
    timestamps = {"created": now, "modified": now, "accessed": now}
    if timestamps_path.exists():
        with open(timestamps_path, "r", encoding="utf-8") as handle:
            existing = json.load(handle)
        timestamps["created"] = existing.get("created", now)
    with open(timestamps_path, "w", encoding="utf-8") as handle:
        json.dump(timestamps, handle, ensure_ascii=False, indent=2)

    entry["status"] = "promoted"
    entry["updated_at"] = now
    entry["promoted_at"] = now
    entry["knowledge_dir"] = str(ki_dir)
    entry["artifact_path"] = str(artifact_path)
    _write_queue(entries)

    # Auto-index: try to run ConversationMemory.index_knowledge() (fail-soft)
    indexed = False
    try:
        import importlib.util as _ilu
        newgate_root = Path(os.environ.get("NEWGATE_ROOT", os.path.expanduser("~/Newgate")))
        cm_path = newgate_root / "intelligence" / "conversation_memory.py"
        if cm_path.exists():
            spec = _ilu.spec_from_file_location("conversation_memory", str(cm_path))
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            cm = mod.ConversationMemory()
            cm.index_knowledge(str(ki_dir))
            indexed = True
    except Exception:
        pass

    return {
        "status": "promoted",
        "entry": entry,
        "knowledge_dir": str(ki_dir),
        "artifact_path": str(artifact_path),
        "notebook_path": KI_COLAB_NOTEBOOK,
        "indexed": indexed,
    }


def handle_fleet_log(arguments: dict) -> dict:
    """ローカルにログを構造化記録"""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "event_type": arguments.get("event_type", "unknown"),
        "task": arguments.get("task", ""),
        "result": arguments.get("result", ""),
        "cause": arguments.get("cause"),
        "fix": arguments.get("fix"),
        "tags": arguments.get("tags", []),
    }
    # Remove None values
    entry = {k: v for k, v in entry.items() if v is not None}

    log_file = LOG_DIR / f"fleet_{datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    queue_entry = None
    if entry.get("event_type") == "success" and (entry.get("task") or entry.get("result")):
        queue_entry = _make_queue_entry(arguments, log_file)
        _append_queue_entry(queue_entry)

    result = {"status": "logged", "file": str(log_file), "entry": entry}
    if queue_entry is not None:
        result["ki_queue_entry"] = queue_entry
        result["ki_queue_file"] = str(KI_QUEUE_FILE)
        # Auto-promote: immediately move to knowledge/
        promote_result = handle_ki_queue_promote({"entry_id": queue_entry["id"]})
        result["auto_promoted"] = promote_result
    return result


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
