#!/usr/bin/env python3
"""
titan-mcp-bridge — Mac Studio の MCP サーバーに MBA から接続する stdio bridge

MBA の Antigravity から stdio MCP として起動され、
Mac Studio の HTTP API (http://100.73.164.45:9000) にリクエストを中継する。
"""
import json
import sys
import urllib.request
import urllib.error

TITAN_URL = "http://100.73.164.45:9000"

# Mac Studio の全ツール定義
TOOLS = [
    {"name": "eck_read", "description": "ECK/KI履歴読み込み", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer", "default": 10}}}},
    {"name": "eck_write", "description": "ECKに追記", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean", "default": True}}, "required": ["path", "content"]}},
    {"name": "compress", "description": "圧縮エンジン呼び出し", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}, "offline": {"type": "boolean", "default": True}}, "required": ["file_path"]}},
    {"name": "dod_audit", "description": "DoD監査", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}},
    {"name": "pcc_encode", "description": "PCC座標生成", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "local_ai", "description": "ローカルLLM呼び出し (MLX)", "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}, "model": {"type": "string"}, "system": {"type": "string"}}, "required": ["prompt"]}},
    {"name": "titan_ai", "description": "Titan Core AI (MLX)", "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}, "model": {"type": "string"}, "system": {"type": "string"}}, "required": ["prompt"]}},
    {"name": "get_stats", "description": "Titanシステム統計", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "mothership_audit", "description": "Mothershipフルパイプライン", "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}, "full_pipeline": {"type": "boolean", "default": True}}, "required": ["file_path"]}},
    {"name": "mothership_architect", "description": "Mothership Brain (DeepSeek-R1)", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "context": {"type": "string"}}, "required": ["query"]}},
    {"name": "mothership_creative", "description": "Mothership Scribe (Qwen3-235B)", "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string"}, "tone": {"type": "string"}}, "required": ["prompt"]}},
    {"name": "mothership_engineer", "description": "Mothership Builder (Qwen3-Coder)", "inputSchema": {"type": "object", "properties": {"instruction": {"type": "string"}, "code_snippet": {"type": "string"}}, "required": ["instruction"]}},
    {"name": "ryota_core_memory", "description": "Ryota-Core設計図", "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "section": {"type": "string"}}, "required": ["query"]}},
    {"name": "simulation_run", "description": "AI-in-AIシミュレーション", "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}, "context": {"type": "string"}}, "required": ["command"]}},
    {"name": "titan_batch", "description": "並列バッチ処理 (M3 Ultra最適化)", "inputSchema": {"type": "object", "properties": {"tasks": {"type": "array"}, "concurrency": {"type": "integer", "default": 8}, "model": {"type": "string"}}, "required": ["tasks"]}},
    {"name": "pcc_critic_run", "description": "PCC制約注入×マルチAI Criticパイプライン", "inputSchema": {"type": "object", "properties": {"prompt": {"type": "string", "description": "批評対象"}, "preset": {"type": "string", "enum": ["探", "極", "均", "監", "刃"], "default": "探"}, "runtime": {"type": "string", "enum": ["gemini", "claude"], "default": "gemini"}, "model": {"type": "string", "default": "deep"}, "timeout": {"type": "integer", "default": 120}}, "required": ["prompt"]}},
    {"name": "pcc_critic_audit", "description": "テキストの品質監査（迎合検知・evidence判定）", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
]


def call_titan(tool_name: str, arguments: dict) -> dict:
    """Mac Studio の HTTP API にリクエストを送る"""
    url = f"{TITAN_URL}/api/tool/{tool_name}"
    data = json.dumps(arguments).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        return {"error": f"Titan unreachable: {e}"}
    except Exception as e:
        return {"error": str(e)}


def handle_request(request: dict) -> dict:
    """JSON-RPC リクエストを処理する"""
    method = request.get("method", "")
    req_id = request.get("id")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "titan-bridge", "version": "1.0.0"}
        }}
    elif method == "notifications/initialized":
        return None  # no response for notifications
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "tools": [{"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]} for t in TOOLS]
        }}
    elif method == "tools/call":
        params = request.get("params", {})
        name = params.get("name", "")
        arguments = params.get("arguments", {})
        result = call_titan(name, arguments)
        return {"jsonrpc": "2.0", "id": req_id, "result": {
            "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]
        }}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


def main():
    """stdio で JSON-RPC を受け取り、Mac Studio に中継する"""
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
