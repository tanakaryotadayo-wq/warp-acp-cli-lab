#!/usr/bin/env python3
"""
gemini_a2a_bridge.py - Local A2A bridge for Gemini Code Assist UI.

Exposes the HTTP+JSON A2A surface Gemini Code Assist expects and routes tasks to
local OpenAI-compatible backends:

- conversation lane -> 8103 (Gemma-4-26B-A4B-Heretic-MLX-8bit)
- agent lane       -> 8102 (Qwen3-Coder-Next-Abliterated-8bit)
- utility lane     -> 8101 (Qwen3.5-9B-abliterated-MLX-4bit)

The bridge is intentionally text-first. If image inputs arrive without a real
VL backend behind one of the routes, the task fails explicitly instead of
pretending multimodal support exists.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

_pipeline_log = logging.getLogger("pipeline")

# ─── Memory pipeline (extracted module) ─────────────────────────────────────
try:
    from memory_pipeline import emit_fleet_log as _emit_fleet_log, try_recall as _try_recall
except ImportError:
    # Fallback: resolve from same directory
    import importlib.util as _mp_ilu
    _mp_spec = _mp_ilu.spec_from_file_location(
        "memory_pipeline", str(Path(__file__).parent / "memory_pipeline.py")
    )
    _mp_mod = _mp_ilu.module_from_spec(_mp_spec)
    _mp_spec.loader.exec_module(_mp_mod)  # type: ignore[union-attr]
    _emit_fleet_log = _mp_mod.emit_fleet_log
    _try_recall = _mp_mod.try_recall


JSON_CONTENT_TYPE = "application/json"
SSE_CONTENT_TYPE = "text/event-stream"

ROLE_USER = "user"
ROLE_AGENT = "agent"

PROTO_ROLE_BY_ROLE = {
    ROLE_USER: "ROLE_USER",
    ROLE_AGENT: "ROLE_AGENT",
    "ROLE_USER": "ROLE_USER",
    "ROLE_AGENT": "ROLE_AGENT",
}

PROTO_STATE_BY_STATE = {
    "unknown": "TASK_STATE_UNSPECIFIED",
    "submitted": "TASK_STATE_SUBMITTED",
    "working": "TASK_STATE_WORKING",
    "completed": "TASK_STATE_COMPLETED",
    "failed": "TASK_STATE_FAILED",
    "canceled": "TASK_STATE_CANCELLED",
    "input-required": "TASK_STATE_INPUT_REQUIRED",
    "rejected": "TASK_STATE_REJECTED",
    "auth-required": "TASK_STATE_AUTH_REQUIRED",
}

STATE_BY_PROTO_STATE = {value: key for key, value in PROTO_STATE_BY_STATE.items()}

ROUTE_ALIASES = {
    "chat": "conversation",
    "conversation": "conversation",
    "conv": "conversation",
    "talk": "conversation",
    "gemma": "conversation",
    "8103": "conversation",
    "agent": "agent",
    "worker": "agent",
    "implementation": "agent",
    "implement": "agent",
    "coder": "agent",
    "code": "agent",
    "qwen-coder": "agent",
    "8102": "agent",
    "utility": "utility",
    "util": "utility",
    "misc": "utility",
    "routing": "utility",
    "qwen35": "utility",
    "qwen3.5": "utility",
    "8101": "utility",
}

PREFIX_PATTERNS = (
    (re.compile(r"^\s*(?:[@#]|/)?(?:chat|conversation|conv|gemma|8103)\b[:\s-]*", re.IGNORECASE), "conversation"),
    (re.compile(r"^\s*(?:[@#]|/)?(?:agent|worker|implementation|implement|coder|8102)\b[:\s-]*", re.IGNORECASE), "agent"),
    (re.compile(r"^\s*(?:[@#]|/)?(?:utility|util|misc|routing|8101)\b[:\s-]*", re.IGNORECASE), "utility"),
)

AGENT_HINT_TERMS = (
    "implement",
    "implementation",
    "fix",
    "patch",
    "edit",
    "write code",
    "write tests",
    "refactor",
    "bug",
    "issue",
    "pull request",
    "pr ",
    "diff",
    "compile",
    "build",
    "test",
    "failing",
    "code review",
)

UTILITY_HINT_TERMS = (
    "summarize",
    "summary",
    "classify",
    "route",
    "routing",
    "inventory",
    "grep",
    "search",
    "count",
    "list",
    "status",
    "health",
    "triage",
    "transform",
)

ACP_COMMAND_DEFAULTS = {
    "acp_deepthink": {
        "description": "ACP×CLI×PCC で設計・アーキテクチャの深掘り分析を実行する。",
        "default_runtime": "gemini",
        "default_model": "deep",
        "default_preset": "刃",
        "mode_prompt": (
            "Mode: ACP DEEPTHINK\n"
            "Goal: architecture and design analysis.\n"
            "Focus on structure, tradeoffs, failure modes, alternatives, missing evidence, "
            "and the safest next move."
        ),
    },
    "acp_deepsearch": {
        "description": "ACP×CLI×PCC で技術調査・論点探索を実行する。",
        "default_runtime": "gemini",
        "default_model": "standard",
        "default_preset": "探",
        "mode_prompt": (
            "Mode: ACP DEEPSEARCH\n"
            "Goal: technical research and issue exploration.\n"
            "Surface unknowns, compare options, identify missing facts, and avoid unsupported certainty."
        ),
    },
}

ACP_RUNTIME_MODELS = {
    "gemini": {
        "fast": "gemini-2.5-flash",
        "standard": "gemini-2.5-pro",
        "deep": "gemini-3.1-pro-preview",
    },
    "claude": {
        "fast": "sonnet",
        "standard": "sonnet",
        "deep": "opus",
    },
    "copilot": {
        "fast": "gpt-5-mini",
        "standard": "gpt-5.4-mini",
        "deep": "gpt-5.4",
    },
}

CLI_SEARCH_DIRS = (
    "/opt/homebrew/bin",
    os.path.expanduser("~/.local/bin"),
)

NEWGATE_PROFILE_PATH = Path(__file__).with_name("newgate_profile.json")
FUSION_GATE_URL = os.environ.get("GEMINI_A2A_FUSION_GATE_URL", "http://127.0.0.1:9800").rstrip("/")
USE_FUSION_GATE_FOR_ACP = os.environ.get("GEMINI_A2A_USE_FUSION_GATE", "true").lower() == "true"
FUSION_GATE_CLI_FALLBACK = os.environ.get("GEMINI_A2A_FUSION_GATE_FALLBACK", "false").lower() == "true"
FUSION_GATE_ALLOW_FAILOVER = os.environ.get("GEMINI_A2A_FUSION_GATE_ALLOW_FAILOVER", "false").lower() == "true"

NEWGATE_SECTION_COMMANDS = {
    "newgate_status": {
        "description": "Newgate の現状・埋め込み基盤・主要ステータスを返す。",
        "section": "status",
    },
    "newgate_compare": {
        "description": "Newgate の競合比較と独自 IP を返す。",
        "section": "competition",
    },
    "newgate_roadmap": {
        "description": "Newgate の優先課題と戦略ビジョンを返す。",
        "section": "roadmap",
    },
    "newgate_memory_pipeline": {
        "description": "Newgate の file-first memory pipeline 状態を返す。",
        "section": "memory",
    },
}

NEWGATE_ACP_COMMANDS = {
    "newgate_deepthink": {
        "description": "Newgate 文脈つきで ACP DEEPTHINK を実行する。",
        "spec": "acp_deepthink",
        "focus": "architecture",
    },
    "newgate_deepsearch": {
        "description": "Newgate 文脈つきで ACP DEEPSEARCH を実行する。",
        "spec": "acp_deepsearch",
        "focus": "research",
    },
}

_PCC_CRITIC = None
_PCC_CRITIC_PATH = Path(__file__).with_name("pcc_critic.py")
if _PCC_CRITIC_PATH.exists():
    spec = importlib.util.spec_from_file_location("bridge_pcc_critic", _PCC_CRITIC_PATH)
    if spec and spec.loader:
        _PCC_CRITIC = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_PCC_CRITIC)

_CBF_MODULE = None
_CBF_MODULE_PATH = Path(
    os.environ.get(
        "NEWGATE_CBF_MODULE",
        str(Path.home() / "Newgate" / "gate" / "cbf.py"),
    )
).expanduser()
if _CBF_MODULE_PATH.exists():
    spec = importlib.util.spec_from_file_location("bridge_cbf", _CBF_MODULE_PATH)
    if spec and spec.loader:
        _CBF_MODULE = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_CBF_MODULE)


def clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def load_newgate_profile() -> Dict[str, Any]:
    if not NEWGATE_PROFILE_PATH.exists():
        return {
            "name": "Newgate Cognitive Engine",
            "version": "missing-profile",
            "embedding": {"primaryModel": "qwen3-embedding-8b"},
            "priorities": [],
            "memory": {},
            "competition": {},
            "vision": [],
        }
    with NEWGATE_PROFILE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id() -> str:
    return str(uuid.uuid4())


def coerce_role(role: Optional[str]) -> str:
    if role in (ROLE_AGENT, "ROLE_AGENT"):
        return ROLE_AGENT
    return ROLE_USER


def coerce_state(state: Optional[str]) -> str:
    if not state:
        return "unknown"
    if state in STATE_BY_PROTO_STATE:
        return STATE_BY_PROTO_STATE[state]
    normalized = str(state).strip().lower()
    if normalized in PROTO_STATE_BY_STATE:
        return normalized
    return "unknown"


def resolve_route_alias(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return ROUTE_ALIASES.get(normalized)


def json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def message_part_text(part: Dict[str, Any]) -> str:
    kind = part.get("kind")
    if kind == "text":
        return str(part.get("text", ""))
    if kind == "file":
        file_info = part.get("file", {})
        mime_type = str(file_info.get("mimeType", "application/octet-stream"))
        if "uri" in file_info:
            return f"[file:{mime_type}:{file_info['uri']}]"
        if "bytes" in file_info:
            return f"[file-bytes:{mime_type}:{len(str(file_info['bytes']))} base64-chars]"
    if kind == "data":
        return f"[data:{json.dumps(part.get('data', {}), ensure_ascii=False)}]"
    return ""


def flatten_parts(parts: Iterable[Dict[str, Any]]) -> str:
    chunks = [message_part_text(part).strip() for part in parts]
    return "\n".join(chunk for chunk in chunks if chunk).strip()


def has_image_part(parts: Iterable[Dict[str, Any]]) -> bool:
    for part in parts:
        if part.get("kind") != "file":
            continue
        mime_type = str(part.get("file", {}).get("mimeType", "")).lower()
        if mime_type.startswith("image/"):
            return True
    return False


def normalize_part(raw_part: Dict[str, Any]) -> Dict[str, Any]:
    if "kind" in raw_part:
        return raw_part
    if "text" in raw_part:
        return {"kind": "text", "text": raw_part.get("text", "")}
    if "file" in raw_part:
        file_payload = raw_part.get("file", {})
        if "fileWithUri" in file_payload:
            return {
                "kind": "file",
                "file": {
                    "uri": file_payload.get("fileWithUri", ""),
                    "mimeType": file_payload.get("mimeType", ""),
                },
            }
        if "fileWithBytes" in file_payload:
            return {
                "kind": "file",
                "file": {
                    "bytes": file_payload.get("fileWithBytes", ""),
                    "mimeType": file_payload.get("mimeType", ""),
                },
            }
    if "data" in raw_part:
        return {"kind": "data", "data": raw_part.get("data")}
    return {"kind": "text", "text": json.dumps(raw_part, ensure_ascii=False)}


def normalize_message(raw_message: Dict[str, Any]) -> Dict[str, Any]:
    if raw_message.get("kind") == "message":
        return {
            "kind": "message",
            "messageId": raw_message.get("messageId") or new_id(),
            "contextId": raw_message.get("contextId", ""),
            "taskId": raw_message.get("taskId", ""),
            "role": coerce_role(raw_message.get("role")),
            "parts": [normalize_part(part) for part in raw_message.get("parts", [])],
            "metadata": dict(raw_message.get("metadata") or {}),
        }

    # REST A2A v0.3 uses "content"; many A2A clients also send "parts"
    # without the "kind":"message" envelope. Accept both so the bridge
    # doesn't silently drop the user's text.
    raw_parts = raw_message.get("content")
    if not raw_parts:
        raw_parts = raw_message.get("parts", [])
    return {
        "kind": "message",
        "messageId": raw_message.get("messageId") or raw_message.get("message_id") or new_id(),
        "contextId": raw_message.get("contextId") or raw_message.get("context_id", ""),
        "taskId": raw_message.get("taskId") or raw_message.get("task_id", ""),
        "role": coerce_role(raw_message.get("role")),
        "parts": [normalize_part(part) for part in raw_parts],
        "metadata": dict(raw_message.get("metadata") or {}),
    }


def normalized_message_to_rest(message: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "messageId": message["messageId"],
        "contextId": message.get("contextId", ""),
        "taskId": message.get("taskId", ""),
        "role": PROTO_ROLE_BY_ROLE.get(message.get("role"), "ROLE_USER"),
        "content": [normalized_part_to_rest(part) for part in message.get("parts", [])],
        "metadata": message.get("metadata") or {},
    }


def normalized_part_to_rest(part: Dict[str, Any]) -> Dict[str, Any]:
    kind = part.get("kind")
    if kind == "text":
        return {"text": part.get("text", "")}
    if kind == "file":
        file_info = part.get("file", {})
        if "uri" in file_info:
            return {
                "file": {
                    "fileWithUri": file_info.get("uri", ""),
                    "mimeType": file_info.get("mimeType", ""),
                }
            }
        return {
            "file": {
                "fileWithBytes": file_info.get("bytes", ""),
                "mimeType": file_info.get("mimeType", ""),
            }
        }
    return {"data": {"data": part.get("data")}}


def normalized_artifact_to_rest(artifact: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "artifactId": artifact["artifactId"],
        "name": artifact.get("name", ""),
        "description": artifact.get("description", ""),
        "parts": [normalized_part_to_rest(part) for part in artifact.get("parts", [])],
        "metadata": artifact.get("metadata") or {},
    }


def normalized_status_to_rest(status: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "state": PROTO_STATE_BY_STATE[coerce_state(status.get("state"))],
        "message": normalized_message_to_rest(status["message"]) if status.get("message") else None,
        "timestamp": status.get("timestamp") or now_iso(),
    }


def normalized_task_to_rest(task: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": task["id"],
        "contextId": task["contextId"],
        "status": normalized_status_to_rest(task["status"]),
        "artifacts": [normalized_artifact_to_rest(artifact) for artifact in task.get("artifacts", [])],
        "history": [normalized_message_to_rest(message) for message in task.get("history", [])],
        "metadata": task.get("metadata") or {},
    }


def normalized_status_update_to_rest(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "taskId": event["taskId"],
        "contextId": event["contextId"],
        "status": normalized_status_to_rest(event["status"]),
        "final": bool(event.get("final", False)),
        "metadata": event.get("metadata") or {},
    }


def normalized_task_to_jsonrpc(task: Dict[str, Any]) -> Dict[str, Any]:
    return task


def build_text_message(role: str, text: str, task_id: str, context_id: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "kind": "message",
        "messageId": new_id(),
        "taskId": task_id,
        "contextId": context_id,
        "role": role,
        "parts": [{"kind": "text", "text": text}],
        "metadata": dict(metadata or {}),
    }


def inject_pcc_prompt(prompt: str, preset: str) -> str:
    if _PCC_CRITIC and hasattr(_PCC_CRITIC, "inject_pcc"):
        return _PCC_CRITIC.inject_pcc(prompt, preset)
    return prompt


def audit_acp_response(text: str) -> Dict[str, Any]:
    if _PCC_CRITIC and hasattr(_PCC_CRITIC, "audit_response"):
        return _PCC_CRITIC.audit_response(text)
    return {"verdict": "NEEDS_EVIDENCE", "sycophancy": 0.0, "evidence_count": 0, "words": len(text.split())}


def runtime_model(runtime: str, requested_model: str) -> str:
    normalized_runtime = str(runtime).strip().lower()
    aliases = ACP_RUNTIME_MODELS.get(normalized_runtime, {})
    return aliases.get(requested_model, requested_model)


def build_newgate_context(profile: Dict[str, Any], focus: str, user_prompt: str) -> str:
    summary = {
        "name": profile.get("name"),
        "version": profile.get("version"),
        "workspace": profile.get("workspace"),
        "scale": profile.get("scale"),
        "embedding": profile.get("embedding"),
        "routing": profile.get("routing"),
        "priorities": profile.get("priorities"),
        "memory": profile.get("memory"),
        "competition": profile.get("competition"),
        "vision": profile.get("vision"),
        "validatedFacts": profile.get("validatedFacts"),
        "focus": focus,
    }
    recall_block = _try_recall(user_prompt)
    return (
        "[Newgate Context]\n"
        "Treat the following as the current architecture snapshot and operating assumptions.\n"
        f"{json.dumps(summary, ensure_ascii=False, indent=2)}\n"
        "---\n"
        f"{recall_block}"
        "[User Request]\n"
        f"{user_prompt}"
    )


@dataclass(frozen=True)
class AcpCommandSpec:
    name: str
    description: str
    default_runtime: str
    default_model: str
    default_preset: str
    mode_prompt: str


@dataclass
class RouteConfig:
    route_id: str
    display_name: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    description: str
    system_prompt: str

    @property
    def completion_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/v1/chat/completions"


@dataclass
class TaskRecord:
    task_id: str
    context_id: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    history: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    route_id: str = "conversation"
    state: str = "submitted"
    status_message: Optional[Dict[str, Any]] = None
    cancel_requested: bool = False
    execution_count: int = 0
    last_error: Optional[str] = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    running: bool = False

    def snapshot(self, bridge: "LocalGeminiA2ABridge", history_length: Optional[int] = None) -> Dict[str, Any]:
        history = self.history
        if history_length and history_length > 0:
            history = history[-history_length:]

        route = bridge.routes[self.route_id]
        status_message = self.status_message or build_text_message(
            ROLE_AGENT,
            f"{route.display_name} is {self.state}.",
            self.task_id,
            self.context_id,
        )
        metadata = dict(self.metadata)
        metadata.update(
            {
                "routeId": self.route_id,
                "routeLabel": route.display_name,
                "backendUrl": route.base_url,
                "model": route.model,
                "state": self.state,
                "createdAt": self.created_at,
                "updatedAt": self.updated_at,
                "executionCount": self.execution_count,
                "cancelRequested": self.cancel_requested,
            }
        )
        if self.last_error:
            metadata["lastError"] = self.last_error

        return {
            "kind": "task",
            "id": self.task_id,
            "contextId": self.context_id,
            "status": {
                "state": self.state,
                "message": status_message,
                "timestamp": self.updated_at,
            },
            "artifacts": list(self.artifacts),
            "history": list(history),
            "metadata": metadata,
        }


class BackendError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.details = dict(details or {})


class LocalGeminiA2ABridge:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        timeout_seconds: int = 120,
        advertise_host: Optional[str] = None,
        routes: Optional[Dict[str, RouteConfig]] = None,
    ):
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self.advertise_host = advertise_host or ("127.0.0.1" if host in ("0.0.0.0", "::") else host)
        self.routes = routes or self._build_default_routes()
        self.acp_commands = {
            name: AcpCommandSpec(name=name, **config) for name, config in ACP_COMMAND_DEFAULTS.items()
        }
        self.newgate_profile = load_newgate_profile()
        self.fusion_gate_url = FUSION_GATE_URL
        self.use_fusion_gate_for_acp = USE_FUSION_GATE_FOR_ACP
        self.fusion_gate_cli_fallback = FUSION_GATE_CLI_FALLBACK
        self.fusion_gate_allow_failover = FUSION_GATE_ALLOW_FAILOVER
        self.tasks: Dict[str, TaskRecord] = {}
        self._lock = threading.RLock()
        self._base_url = f"http://{self.advertise_host}:{self.port}"

    def set_bound_port(self, port: int) -> None:
        self.port = port
        self._base_url = f"http://{self.advertise_host}:{self.port}"

    def _build_default_routes(self) -> Dict[str, RouteConfig]:
        return {
            "conversation": RouteConfig(
                route_id="conversation",
                display_name="Conversation Lane",
                base_url=os.environ.get("GEMINI_A2A_CHAT_BASE_URL", "http://127.0.0.1:8103"),
                model=os.environ.get("GEMINI_A2A_CHAT_MODEL", "Gemma-4-26B-A4B-Heretic-MLX-8bit"),
                temperature=float(os.environ.get("GEMINI_A2A_CHAT_TEMPERATURE", "0.6")),
                max_tokens=int(os.environ.get("GEMINI_A2A_CHAT_MAX_TOKENS", "2048")),
                description="General conversation and planning lane backed by Gemma 4 on :8103.",
                system_prompt=(
                    "You are the conversation lane behind Gemini Code Assist UI. "
                    "Be concise, collaborative, and text-first. "
                    "Do not claim file writes, tests, or tool execution unless explicit evidence is present. "
                    "User-visible prose should default to natural Japanese unless the user explicitly asks for another language. "
                    "Keep code, identifiers, shell commands, file paths, API names, and literal error strings as-is."
                ),
            ),
            "agent": RouteConfig(
                route_id="agent",
                display_name="Implementation Lane",
                base_url=os.environ.get("GEMINI_A2A_AGENT_BASE_URL", "http://127.0.0.1:8102"),
                model=os.environ.get("GEMINI_A2A_AGENT_MODEL", "Qwen3-Coder-Next-Abliterated-8bit"),
                temperature=float(os.environ.get("GEMINI_A2A_AGENT_TEMPERATURE", "0.2")),
                max_tokens=int(os.environ.get("GEMINI_A2A_AGENT_MAX_TOKENS", "3072")),
                description="Bounded implementation lane backed by Qwen3 Coder Next on :8102.",
                system_prompt=(
                    "You are the bounded implementation lane behind Gemini Code Assist UI. "
                    "Focus on code, diffs, bugs, tests, and concrete edits. "
                    "Never present a task as completed unless the evidence is explicit in the prompt or history. "
                    "User-visible prose should default to natural Japanese unless the user explicitly asks for another language. "
                    "Keep code, identifiers, shell commands, file paths, API names, and literal error strings as-is."
                ),
            ),
            "utility": RouteConfig(
                route_id="utility",
                display_name="Utility Lane",
                base_url=os.environ.get("GEMINI_A2A_UTILITY_BASE_URL", "http://127.0.0.1:8101"),
                model=os.environ.get("GEMINI_A2A_UTILITY_MODEL", "Qwen3.5-9B-abliterated-MLX-4bit"),
                temperature=float(os.environ.get("GEMINI_A2A_UTILITY_TEMPERATURE", "0.1")),
                max_tokens=int(os.environ.get("GEMINI_A2A_UTILITY_MAX_TOKENS", "1536")),
                description="Routing, summarization, and miscellaneous helper lane backed by Qwen3.5 on :8101.",
                system_prompt=(
                    "You are the utility lane behind Gemini Code Assist UI. "
                    "Handle routing, triage, summarization, transforms, and lightweight chores. "
                    "Prefer compact, structured outputs. "
                    "User-visible prose should default to natural Japanese unless the user explicitly asks for another language. "
                    "Keep code, identifiers, shell commands, file paths, API names, and literal error strings as-is."
                ),
            ),
        }

    def agent_card(self) -> Dict[str, Any]:
        return {
            "name": "Ryota Local Lane Bridge",
            "description": "Routes Gemini Code Assist A2A traffic to local conversation, implementation, and utility runtimes.",
            "url": f"{self._base_url}/",
            "preferredTransport": "HTTP+JSON",
            "additionalInterfaces": [
                {
                    "url": f"{self._base_url}/",
                    "transport": "JSONRPC",
                }
            ],
            "provider": {
                "organization": "Ryota Core",
                "url": "https://github.com",
            },
            "protocolVersion": "0.3.0",
            "version": "0.1.0",
            "capabilities": {
                "streaming": True,
                "pushNotifications": False,
                "stateTransitionHistory": True,
            },
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [
                {
                    "id": "conversation_lane",
                    "name": "Conversation Lane",
                    "description": self.routes["conversation"].description,
                    "tags": ["chat", "planning", "explanation", "gemma", "8103"],
                    "examples": [
                        "Explain this architecture tradeoff.",
                        "Plan the migration before coding.",
                    ],
                    "inputModes": ["text"],
                    "outputModes": ["text"],
                },
                {
                    "id": "implementation_lane",
                    "name": "Implementation Lane",
                    "description": self.routes["agent"].description,
                    "tags": ["code", "fix", "refactor", "tests", "qwen-coder", "8102"],
                    "examples": [
                        "Fix this failing test.",
                        "Propose a patch for this bug.",
                    ],
                    "inputModes": ["text"],
                    "outputModes": ["text"],
                },
                {
                    "id": "utility_lane",
                    "name": "Utility Lane",
                    "description": self.routes["utility"].description,
                    "tags": ["summary", "triage", "routing", "utility", "qwen35", "8101"],
                    "examples": [
                        "Summarize the latest errors.",
                        "Classify this task into the best lane.",
                    ],
                    "inputModes": ["text"],
                    "outputModes": ["text"],
                },
                {
                    "id": "newgate_system",
                    "name": "Newgate System Context",
                    "description": "Embedded Newgate architecture, roadmap, memory pipeline, and competition snapshot.",
                    "tags": ["newgate", "memory", "roadmap", "embedding", "critic"],
                    "examples": [
                        "Show the Newgate roadmap.",
                        "Compare Newgate with existing tools.",
                    ],
                    "inputModes": ["text"],
                    "outputModes": ["text"],
                },
            ],
            "supportsAuthenticatedExtendedCard": False,
        }

    def create_task(self, agent_settings: Optional[Dict[str, Any]] = None, context_id: Optional[str] = None) -> TaskRecord:
        task_id = new_id()
        context_id = context_id or new_id()
        route_id = self._route_from_agent_settings(agent_settings) or "conversation"
        metadata = {"agentSettings": dict(agent_settings or {})}
        task = TaskRecord(
            task_id=task_id,
            context_id=context_id,
            metadata=metadata,
            route_id=route_id,
            state="submitted",
        )
        task.status_message = build_text_message(
            ROLE_AGENT,
            f"Created task on {self.routes[route_id].display_name}.",
            task_id,
            context_id,
        )
        with self._lock:
            self.tasks[task_id] = task
        return task

    def list_commands(self) -> Dict[str, Any]:
        commands = [
            {
                "name": "route-chat",
                "description": "Pin a task to the conversation lane (8103 / Gemma 4).",
                "arguments": [{"name": "taskId", "required": False}],
                "subCommands": [],
            },
            {
                "name": "route-agent",
                "description": "Pin a task to the implementation lane (8102 / Qwen3 Coder Next).",
                "arguments": [{"name": "taskId", "required": False}],
                "subCommands": [],
            },
            {
                "name": "route-utility",
                "description": "Pin a task to the utility lane (8101 / Qwen3.5).",
                "arguments": [{"name": "taskId", "required": False}],
                "subCommands": [],
            },
            {
                "name": "show-routes",
                "description": "Show the currently configured local lane map.",
                "arguments": [],
                "subCommands": [],
            },
        ]
        for spec in self.acp_commands.values():
            commands.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "arguments": [
                        {"name": "prompt", "required": True},
                        {"name": "runtime", "required": False},
                        {"name": "model", "required": False},
                        {"name": "preset", "required": False},
                        {"name": "timeout", "required": False},
                    ],
                    "subCommands": [],
                }
            )
        for name, config in NEWGATE_SECTION_COMMANDS.items():
            commands.append(
                {
                    "name": name,
                    "description": config["description"],
                    "arguments": [],
                    "subCommands": [],
                }
            )
        for name, config in NEWGATE_ACP_COMMANDS.items():
            commands.append(
                {
                    "name": name,
                    "description": config["description"],
                    "arguments": [
                        {"name": "prompt", "required": True},
                        {"name": "runtime", "required": False},
                        {"name": "model", "required": False},
                        {"name": "preset", "required": False},
                        {"name": "timeout", "required": False},
                    ],
                    "subCommands": [],
                }
            )
        return {"commands": commands}

    def execute_command(self, command: str, args: Optional[List[Any]] = None) -> Dict[str, Any]:
        args = list(args or [])
        if command == "show-routes":
            return {
                "routes": {
                    route_id: {
                        "label": route.display_name,
                        "baseUrl": route.base_url,
                        "model": route.model,
                    }
                    for route_id, route in self.routes.items()
                }
            }
        if command in self.acp_commands:
            return self._execute_acp_command(self.acp_commands[command], args)
        if command in NEWGATE_SECTION_COMMANDS:
            return self._execute_newgate_section_command(command)
        if command in NEWGATE_ACP_COMMANDS:
            return self._execute_newgate_acp_command(command, args)

        route_id = {
            "route-chat": "conversation",
            "route-agent": "agent",
            "route-utility": "utility",
        }.get(command)
        if route_id is None:
            raise KeyError(command)

        task_id = str(args[0]) if args else ""
        if not task_id:
            return {
                "routeId": route_id,
                "label": self.routes[route_id].display_name,
                "hint": f"Prefix the next prompt with @{route_id} to force that lane.",
            }

        with self._lock:
            task = self.tasks.get(task_id)
            if not task:
                raise ValueError(f"Task not found: {task_id}")
            task.route_id = route_id
            task.updated_at = now_iso()
            task.metadata["manualRouteOverride"] = route_id

        return {
            "taskId": task_id,
            "routeId": route_id,
            "label": self.routes[route_id].display_name,
        }

    def newgate_snapshot(self) -> Dict[str, Any]:
        profile = clone_json(self.newgate_profile)
        return {
            "bridge": {
                "baseUrl": self._base_url,
                "preferredTransport": "HTTP+JSON",
                "routes": {
                    route_id: {
                        "label": route.display_name,
                        "baseUrl": route.base_url,
                        "model": route.model,
                    }
                    for route_id, route in self.routes.items()
                },
                "commands": [
                    "newgate_status",
                    "newgate_compare",
                    "newgate_roadmap",
                    "newgate_memory_pipeline",
                    "newgate_deepthink",
                    "newgate_deepsearch",
                ],
            },
            "profile": profile,
        }

    def _execute_newgate_section_command(self, command: str) -> Dict[str, Any]:
        snapshot = self.newgate_snapshot()
        profile = snapshot["profile"]

        if command == "newgate_status":
            return {
                "command": command,
                "project": {
                    "name": profile.get("name"),
                    "version": profile.get("version"),
                    "workspace": profile.get("workspace"),
                },
                "scale": profile.get("scale"),
                "embedding": profile.get("embedding"),
                "kiVectorization": profile.get("kiVectorization"),
                "routing": profile.get("routing"),
                "bridge": snapshot["bridge"],
            }
        if command == "newgate_compare":
            return {
                "command": command,
                "competition": profile.get("competition"),
                "validatedFacts": profile.get("validatedFacts"),
            }
        if command == "newgate_roadmap":
            return {
                "command": command,
                "priorities": profile.get("priorities"),
                "vision": profile.get("vision"),
                "routing": profile.get("routing"),
            }
        if command == "newgate_memory_pipeline":
            return {
                "command": command,
                "memory": profile.get("memory"),
                "embedding": profile.get("embedding"),
                "nextFocus": [
                    "raw -> normalized 自動蒸留",
                    "会話終了時の自動パケット化",
                    "週次 8B 再ベクトル化",
                ],
            }
        raise KeyError(command)

    def _execute_newgate_acp_command(self, command: str, args: List[Any]) -> Dict[str, Any]:
        config = NEWGATE_ACP_COMMANDS[command]
        spec = self.acp_commands[config["spec"]]
        options = self._normalize_acp_args(spec, args)
        prompt_text = build_newgate_context(self.newgate_profile, config["focus"], options["prompt"])
        payload = self._run_acp_command(spec, options, prompt_text, command_name=command, public_prompt=options["prompt"])
        payload["newgateFocus"] = config["focus"]
        payload["newgateVersion"] = self.newgate_profile.get("version")
        return payload

    def _execute_acp_command(self, spec: AcpCommandSpec, args: List[Any]) -> Dict[str, Any]:
        options = self._normalize_acp_args(spec, args)
        return self._run_acp_command(spec, options, options["prompt"])

    def _build_cbf_context(self, spec: AcpCommandSpec, prompt_text: str) -> Dict[str, Any]:
        if _CBF_MODULE is None:
            return {
                "available": False,
                "reason": "cbf_module_missing",
            }

        try:
            engine = _CBF_MODULE.CBFEngine(use_history=False)
            saved = engine.load_log()
            history_stats = _CBF_MODULE.CBFHistory().get_stats()
            current_position = saved.get("current_position")
            if not current_position:
                recent = (history_stats or {}).get("recent", [])
                if recent:
                    current_position = recent[0].get("corrected") or recent[0].get("actual")

            recommended = self._recommend_cbf_coordinate(spec, prompt_text, history_stats)
            parsed_current = self._parse_cbf_position(current_position)
            distance = (
                self._cbf_distance(parsed_current, recommended)
                if parsed_current is not None
                else None
            )

            lines = [
                "[CBF Protocol]",
                f"Current position: {current_position or 'untracked'}",
                (
                    f"Recommended step: {recommended['text']} "
                    f"({recommended['stageName']}, reason: {recommended['reason']})"
                ),
            ]
            if distance is not None:
                lines.append(f"Distance from current: {distance:.2f}")
            lines.append(f"Recorded drifts: {int((history_stats or {}).get('total_drifts', 0))}")
            lines.append(
                "Respect the recommended coordinate unless the user explicitly requires a different stage."
            )
            return {
                "available": True,
                "currentPosition": current_position,
                "recommendedStep": recommended,
                "distanceFromCurrent": distance,
                "totalDrifts": int((history_stats or {}).get("total_drifts", 0)),
                "protocol": "\n".join(lines),
            }
        except Exception as exc:
            return {
                "available": False,
                "reason": "cbf_unavailable",
                "error": str(exc),
            }

    def _recommend_cbf_coordinate(
        self,
        spec: AcpCommandSpec,
        prompt_text: str,
        history_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        lowered = f"{spec.name} {spec.mode_prompt} {prompt_text}".lower()
        stage = 1
        reason = "research-default"
        if spec.name == "acp_deepthink" or any(
            token in lowered for token in ("architecture", "design", "設計", "構想", "tradeoff")
        ):
            stage = 2
            reason = "deepthink-design"
        elif any(
            token in lowered for token in ("implement", "implementation", "fix", "patch", "実装", "修正")
        ):
            stage = 3
            reason = "implementation-keywords"

        layer = 2 if any(
            token in lowered
            for token in (
                "extension",
                "workspace",
                "session",
                "lane",
                "status bar",
                "tree view",
                "sidebar",
                "hook",
                "integration",
                "統合",
            )
        ) else 3

        hard_drifts = int((history_stats or {}).get("status_counts", {}).get("drift", 0))
        total_drifts = int((history_stats or {}).get("total_drifts", 0))
        base_stability = 4 if stage in (1, 2) else 5
        stability = max(2, min(9, base_stability - min(hard_drifts, 2) - min(total_drifts // 300, 1)))

        return {
            "x": stage,
            "y": layer,
            "z": stability,
            "text": f"[{stage}.{layer}.{stability}]",
            "stageName": {
                1: "Planning",
                2: "Design",
                3: "Implementation",
                4: "Integration",
                5: "Deployment",
            }.get(stage, f"Stage-{stage}"),
            "reason": reason,
        }

    def _parse_cbf_position(self, text: Any) -> Optional[Tuple[int, int, int]]:
        if not isinstance(text, str):
            return None
        match = re.match(r"^\[(\d+)\.(\d+)\.(\d+)\]$", text.strip())
        if not match:
            return None
        return (int(match.group(1)), int(match.group(2)), int(match.group(3)))

    def _cbf_distance(self, current: Tuple[int, int, int], recommended: Dict[str, Any]) -> float:
        return float(
            abs(recommended["x"] - current[0]) * 3.0
            + abs(recommended["y"] - current[1]) * 1.0
            + abs(recommended["z"] - current[2]) * 0.3
        )

    def _run_acp_command(
        self,
        spec: AcpCommandSpec,
        options: Dict[str, Any],
        prompt_text: str,
        command_name: Optional[str] = None,
        public_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        cbf = self._build_cbf_context(spec, prompt_text)
        prompt_parts = [spec.mode_prompt]
        if cbf.get("protocol"):
            prompt_parts.append(str(cbf["protocol"]))
        prompt_parts.append(f"---\n{prompt_text}")
        final_prompt = inject_pcc_prompt(
            "\n\n".join(prompt_parts),
            options["preset"],
        )
        result = self._invoke_acp_cli(
            runtime=options["runtime"],
            prompt=final_prompt,
            model=options["model"],
            timeout=options["timeout"],
        )
        audit = audit_acp_response(result["text"])
        return {
            "command": command_name or spec.name,
            "runtime": options["runtime"],
            "model": options["model"],
            "preset": options["preset"],
            "timeout": options["timeout"],
            "prompt": public_prompt if public_prompt is not None else options["prompt"],
            "response": result["text"],
            "audit": audit,
            "cbf": cbf,
            "gateway": {
                "used": bool(result.get("gateway_used", False)),
                "provider": result.get("provider"),
                "fromCache": bool(result.get("from_cache", False)),
                "reason": result.get("reason"),
                "url": self.fusion_gate_url if result.get("gateway_used", False) else None,
            },
            "exitCode": result["exit_code"],
            "elapsed": result["elapsed"],
            "invocation": result["command"],
        }

    def _normalize_acp_args(self, spec: AcpCommandSpec, args: List[Any]) -> Dict[str, Any]:
        raw: Dict[str, Any]
        if args and isinstance(args[0], dict):
            raw = dict(args[0])
        else:
            raw = {
                "prompt": args[0] if len(args) > 0 else "",
                "runtime": args[1] if len(args) > 1 else None,
                "model": args[2] if len(args) > 2 else None,
                "preset": args[3] if len(args) > 3 else None,
                "timeout": args[4] if len(args) > 4 else None,
            }

        prompt = str(raw.get("prompt") or "").strip()
        if not prompt:
            raise ValueError(f"{spec.name} requires a non-empty prompt.")

        runtime = str(raw.get("runtime") or spec.default_runtime).strip().lower()
        if runtime not in ACP_RUNTIME_MODELS:
            raise ValueError(f"Unsupported runtime: {runtime}")

        requested_model = str(raw.get("model") or spec.default_model).strip()
        model = runtime_model(runtime, requested_model)
        preset = str(raw.get("preset") or spec.default_preset).strip()
        timeout = int(raw.get("timeout") or self.timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero.")

        return {
            "prompt": prompt,
            "runtime": runtime,
            "model": model,
            "preset": preset,
            "timeout": timeout,
        }

    def _invoke_acp_cli(self, runtime: str, prompt: str, model: str, timeout: int) -> Dict[str, Any]:
        if self.use_fusion_gate_for_acp:
            try:
                return self._invoke_acp_via_fusion_gate(runtime, prompt, model, timeout)
            except BackendError:
                if not self.fusion_gate_cli_fallback:
                    raise
        return self._invoke_acp_direct_cli(runtime, prompt, model, timeout)

    def _invoke_acp_via_fusion_gate(self, runtime: str, prompt: str, model: str, timeout: int) -> Dict[str, Any]:
        provider = self._fusion_gate_provider(runtime, model)
        payload = {
            "prompt": prompt,
            "provider": provider,
            "use_cache": True,
            "skip_proxy": False,
            "allow_failover": self.fusion_gate_allow_failover,
        }
        request = Request(
            f"{self.fusion_gate_url}/v1/gate/invoke",
            data=json_bytes(payload),
            headers={"Content-Type": JSON_CONTENT_TYPE, "Accept": JSON_CONTENT_TYPE},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            details: Dict[str, Any] = {
                "gateway": {
                    "used": True,
                    "provider": provider,
                    "url": self.fusion_gate_url,
                    "allowFailover": self.fusion_gate_allow_failover,
                    "fallbackEnabled": self.fusion_gate_cli_fallback,
                }
            }
            message = body or error.reason
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict):
                provider_name = str(payload.get("provider") or provider)
                details.update(
                    {
                        "provider": provider_name,
                        "reason": payload.get("reason"),
                        "fromCache": bool(payload.get("from_cache", False)),
                    }
                )
                error_code = str(payload.get("error_code") or "").strip()
                if error_code:
                    details["errorCode"] = error_code
                hint = str(payload.get("hint") or "").strip()
                if hint:
                    details["hint"] = hint
                payload_error = str(payload.get("error") or "").strip()
                if payload_error:
                    message = f"Fusion Gate provider {provider_name} failed: {payload_error}"
            raise BackendError(
                f"Fusion Gate returned HTTP {error.code}: {message}",
                error.code,
                details,
            ) from error
        except URLError as error:
            raise BackendError(f"Fusion Gate is unreachable: {error.reason}") from error
        except TimeoutError as error:
            raise BackendError(f"Fusion Gate timed out after {timeout}s.") from error
        except json.JSONDecodeError as error:
            raise BackendError("Fusion Gate returned invalid JSON.") from error

        text = str(data.get("response", "") or "").strip()
        if not text:
            error_text = str(data.get("error", "") or "").strip()
            if error_text:
                raise BackendError(f"Fusion Gate provider {provider} failed: {error_text}")
            raise BackendError(f"Fusion Gate provider {provider} returned no output.")

        return {
            "text": text,
            "exit_code": 0,
            "elapsed": round(float(data.get("latency_ms", (time.monotonic() - started) * 1000.0)) / 1000.0, 1),
            "command": ["fusion-gate", provider],
            "provider": str(data.get("provider") or provider),
            "reason": str(data.get("reason") or ""),
            "from_cache": bool(data.get("from_cache", False)),
            "gateway_used": True,
        }

    def _fusion_gate_provider(self, runtime: str, model: str) -> str:
        if runtime == "gemini":
            return "gemini"
        if runtime == "claude":
            return "claude"
        if runtime == "copilot":
            lowered = model.lower()
            return "copilot_mini" if "mini" in lowered else "copilot"
        raise BackendError(f"Unsupported runtime: {runtime}")

    def _invoke_acp_direct_cli(self, runtime: str, prompt: str, model: str, timeout: int) -> Dict[str, Any]:
        env = os.environ.copy()
        existing_path = env.get("PATH", "")
        prepend = [directory for directory in CLI_SEARCH_DIRS if os.path.isdir(directory)]
        if prepend:
            env["PATH"] = ":".join(prepend + [existing_path]) if existing_path else ":".join(prepend)

        binary_name = runtime
        binary_path = shutil.which(binary_name, path=env.get("PATH"))
        if not binary_path:
            raise BackendError(f"{runtime} CLI is not installed or not on PATH.")

        command = self._build_acp_command(binary_path, runtime, prompt, model)
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                env=env,
                cwd=os.getcwd(),
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise BackendError(f"{runtime} CLI timed out after {timeout}s.") from error

        text = (result.stdout or "").strip()
        if not text:
            text = (result.stderr or "").strip()
        if not text:
            raise BackendError(f"{runtime} CLI returned no output.")
        if result.returncode not in (0, 1):
            raise BackendError(f"{runtime} CLI failed with exit code {result.returncode}: {text}")

        return {
            "text": text,
            "exit_code": result.returncode,
            "elapsed": round(time.monotonic() - started, 1),
            "command": command,
            "provider": runtime,
            "reason": "direct_cli",
            "from_cache": False,
            "gateway_used": False,
        }

    def _build_acp_command(self, binary_path: str, runtime: str, prompt: str, model: str) -> List[str]:
        if runtime == "gemini":
            return [binary_path, "--approval-mode", "plan", "-p", prompt, "-m", model]
        if runtime == "claude":
            return [
                binary_path,
                "-p",
                prompt,
                "--permission-mode",
                "plan",
                "--output-format",
                "text",
                "--model",
                model,
            ]
        if runtime == "copilot":
            return [
                binary_path,
                "-p",
                prompt,
                "--plan",
                "--output-format",
                "text",
                "--model",
                model,
            ]
        raise BackendError(f"Unsupported runtime: {runtime}")

    def list_task_metadata(self) -> List[Dict[str, Any]]:
        with self._lock:
            tasks = list(self.tasks.values())
        return [self.task_metadata(task) for task in tasks]

    def task_metadata(self, task: TaskRecord) -> Dict[str, Any]:
        route = self.routes[task.route_id]
        return {
            "taskId": task.task_id,
            "contextId": task.context_id,
            "state": task.state,
            "routeId": task.route_id,
            "routeLabel": route.display_name,
            "backendUrl": route.base_url,
            "model": route.model,
            "createdAt": task.created_at,
            "updatedAt": task.updated_at,
            "executionCount": task.execution_count,
            "cancelRequested": task.cancel_requested,
            "lastError": task.last_error,
        }

    def get_task(self, task_id: str, history_length: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            return task.snapshot(self, history_length)

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            task.cancel_requested = True
            task.running = False
            task.state = "canceled"
            task.updated_at = now_iso()
            task.status_message = build_text_message(
                ROLE_AGENT,
                "Task canceled by user request.",
                task.task_id,
                task.context_id,
            )
            task.history.append(task.status_message)
            return task.snapshot(self)

    def handle_rest_message_send(self, body: Dict[str, Any]) -> Dict[str, Any]:
        message = normalize_message(body.get("message") or body.get("request") or {})
        configuration = body.get("configuration") or {}
        top_level_metadata = dict(body.get("metadata") or {})
        task = self._handle_message(message, configuration, top_level_metadata, blocking=bool(configuration.get("blocking", True)))
        return {"task": normalized_task_to_rest(task)}

    def handle_rest_message_stream_events(self, body: Dict[str, Any]) -> List[Dict[str, Any]]:
        message = normalize_message(body.get("message") or body.get("request") or {})
        configuration = body.get("configuration") or {}
        top_level_metadata = dict(body.get("metadata") or {})
        return [self._event_to_rest(event) for event in self._handle_message_stream_events(message, configuration, top_level_metadata)]

    def handle_jsonrpc(self, method: str, params: Optional[Dict[str, Any]]) -> Any:
        params = params or {}
        if method == "agent/getAuthenticatedExtendedCard":
            return self.agent_card()
        if method == "message/send":
            message = normalize_message(params.get("message") or {})
            configuration = params.get("configuration") or {}
            metadata = dict(params.get("metadata") or {})
            return normalized_task_to_jsonrpc(
                self._handle_message(message, configuration, metadata, blocking=bool(configuration.get("blocking", True)))
            )
        if method == "tasks/get":
            return self.get_task(str(params.get("id", "")), params.get("historyLength"))
        if method == "tasks/cancel":
            return self.cancel_task(str(params.get("id", "")))
        raise KeyError(method)

    def handle_jsonrpc_stream_events(self, method: str, params: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        params = params or {}
        if method == "message/stream":
            message = normalize_message(params.get("message") or {})
            configuration = params.get("configuration") or {}
            metadata = dict(params.get("metadata") or {})
            return self._handle_message_stream_events(message, configuration, metadata)
        if method == "tasks/resubscribe":
            task_id = str(params.get("id", ""))
            return self._resubscribe_events(task_id)
        raise KeyError(method)

    def _event_to_rest(self, event: Dict[str, Any]) -> Dict[str, Any]:
        kind = event.get("kind")
        if kind == "status-update":
            return {"statusUpdate": normalized_status_update_to_rest(event)}
        if kind == "task":
            return {"task": normalized_task_to_rest(event)}
        if kind == "message":
            return {"message": normalized_message_to_rest(event)}
        raise ValueError(f"Unsupported stream event kind: {kind}")

    def _handle_message_stream_events(
        self,
        message: Dict[str, Any],
        configuration: Dict[str, Any],
        top_level_metadata: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        prepared_message, route_id, route_reason = self._prepare_message(message, top_level_metadata)
        task = self._ensure_task(prepared_message, route_id, top_level_metadata)

        working_message = build_text_message(
            ROLE_AGENT,
            f"Routing to {self.routes[route_id].display_name} ({route_reason}).",
            task.task_id,
            task.context_id,
        )

        with self._lock:
            task.running = True
            task.state = "working"
            task.updated_at = now_iso()
            task.execution_count += 1
            task.status_message = working_message
            task.metadata["routeReason"] = route_reason
            task.history.append(prepared_message)
            working_event = {
                "kind": "status-update",
                "taskId": task.task_id,
                "contextId": task.context_id,
                "status": {
                    "state": "working",
                    "message": working_message,
                    "timestamp": task.updated_at,
                },
                "metadata": {"routeId": route_id},
                "final": False,
            }

        final_task = self._execute_task(task.task_id, configuration)
        return [working_event, final_task]

    def _handle_message(
        self,
        message: Dict[str, Any],
        configuration: Dict[str, Any],
        top_level_metadata: Dict[str, Any],
        blocking: bool,
    ) -> Dict[str, Any]:
        prepared_message, route_id, route_reason = self._prepare_message(message, top_level_metadata)
        task = self._ensure_task(prepared_message, route_id, top_level_metadata)

        with self._lock:
            if task.running:
                raise BackendError(f"Task {task.task_id} is already working.")
            task.running = True
            task.state = "working"
            task.updated_at = now_iso()
            task.execution_count += 1
            task.metadata["routeReason"] = route_reason
            task.history.append(prepared_message)
            task.status_message = build_text_message(
                ROLE_AGENT,
                f"Routing to {self.routes[route_id].display_name} ({route_reason}).",
                task.task_id,
                task.context_id,
            )

        if not blocking:
            worker = threading.Thread(
                target=self._execute_task,
                args=(task.task_id, configuration),
                daemon=True,
            )
            worker.start()
            return task.snapshot(self)

        return self._execute_task(task.task_id, configuration)

    def _prepare_message(self, message: Dict[str, Any], top_level_metadata: Dict[str, Any]) -> Tuple[Dict[str, Any], str, str]:
        prepared = {
            "kind": "message",
            "messageId": message.get("messageId") or new_id(),
            "contextId": message.get("contextId", ""),
            "taskId": message.get("taskId", ""),
            "role": coerce_role(message.get("role")),
            "parts": [dict(part) for part in message.get("parts", [])],
            "metadata": dict(message.get("metadata") or {}),
        }

        explicit_route = self._explicit_route(prepared, top_level_metadata)
        prefix_route = self._strip_route_prefix(prepared)
        route_id = explicit_route or prefix_route
        if route_id:
            return prepared, route_id, "explicit route override"

        agent_settings = self._extract_agent_settings(prepared, top_level_metadata)
        task_id = prepared.get("taskId", "")
        with self._lock:
            existing_task = self.tasks.get(task_id) if task_id else None
        if existing_task and existing_task.route_id:
            return prepared, existing_task.route_id, "sticky session route"
        if agent_settings and agent_settings.get("autoExecute"):
            return prepared, "agent", "agent autoExecute"
        if has_image_part(prepared["parts"]):
            return prepared, "conversation", "image input"
        if any(part.get("kind") != "text" for part in prepared["parts"]):
            return prepared, "agent", "non-text context"

        text = flatten_parts(prepared["parts"]).lower()
        if any(term in text for term in AGENT_HINT_TERMS):
            return prepared, "agent", "agent heuristic"
        if any(term in text for term in UTILITY_HINT_TERMS):
            return prepared, "utility", "utility heuristic"
        return prepared, "conversation", "default conversation heuristic"

    def _ensure_task(self, message: Dict[str, Any], route_id: str, top_level_metadata: Dict[str, Any]) -> TaskRecord:
        task_id = message.get("taskId") or new_id()
        context_id = message.get("contextId") or new_id()
        message["taskId"] = task_id
        message["contextId"] = context_id

        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                task = TaskRecord(
                    task_id=task_id,
                    context_id=context_id,
                    route_id=route_id,
                    state="submitted",
                    metadata=dict(top_level_metadata),
                )
                self.tasks[task_id] = task
            else:
                task.route_id = route_id
                task.metadata.update(top_level_metadata)
            task.updated_at = now_iso()
        return task

    def _execute_task(self, task_id: str, configuration: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        configuration = configuration or {}
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            route = self.routes[task.route_id]
            history_length = int(configuration.get("historyLength") or 0)
            history = task.history[-history_length:] if history_length > 0 else list(task.history)

        try:
            if history and has_image_part(history[-1]["parts"]):
                raise BackendError(
                    "Image input received, but no reliable local VL lane is configured. "
                    "Current local routes are text-first only."
                )

            openai_messages = self._build_openai_messages(route, history)
            reply_text = self._call_backend(route, openai_messages)

            with self._lock:
                task = self.tasks[task_id]
                if task.cancel_requested:
                    task.running = False
                    task.state = "canceled"
                    task.updated_at = now_iso()
                    task.status_message = build_text_message(
                        ROLE_AGENT,
                        "Task canceled before the backend result was accepted.",
                        task.task_id,
                        task.context_id,
                    )
                    task.history.append(task.status_message)
                    return task.snapshot(self)

                reply_message = build_text_message(
                    ROLE_AGENT,
                    reply_text,
                    task.task_id,
                    task.context_id,
                    {"routeId": task.route_id},
                )
                task.history.append(reply_message)
                task.artifacts = [
                    {
                        "artifactId": f"artifact-{task.execution_count}",
                        "name": f"{self.routes[task.route_id].display_name} reply",
                        "description": "Final assistant response from the routed local backend.",
                        "parts": [{"kind": "text", "text": reply_text}],
                        "metadata": {"routeId": task.route_id},
                    }
                ]
                task.state = "completed"
                task.running = False
                task.updated_at = now_iso()
                task.last_error = None
                task.status_message = reply_message
                user_text = flatten_parts(history[-1].get("parts", [])) if history else ""
                snapshot = task.snapshot(self)

            # Auto fleet_log (outside lock, fire-and-forget thread)
            threading.Thread(
                target=_emit_fleet_log,
                args=("success", user_text[:400], reply_text[:800], task.route_id),
                daemon=True,
            ).start()
            return snapshot
        except BackendError as error:
            with self._lock:
                task = self.tasks[task_id]
                task.running = False
                task.state = "failed"
                task.updated_at = now_iso()
                task.last_error = str(error)
                task.status_message = build_text_message(
                    ROLE_AGENT,
                    str(error),
                    task.task_id,
                    task.context_id,
                    {"routeId": task.route_id},
                )
                task.history.append(task.status_message)
                user_text = flatten_parts(history[-1].get("parts", [])) if history else ""
                snapshot = task.snapshot(self)

            threading.Thread(
                target=_emit_fleet_log,
                args=("failure", user_text[:400], str(error)[:800], task.route_id),
                daemon=True,
            ).start()
            return snapshot

    def _build_openai_messages(self, route: RouteConfig, history: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        system_prompt = route.system_prompt

        # Inject Newgate memory recall for the latest user turn so every
        # lane request gets the same external-memory context that ACP
        # commands already receive via build_newgate_context().
        latest_user_text = ""
        for item in reversed(history):
            if item.get("role") != ROLE_AGENT:
                latest_user_text = flatten_parts(item.get("parts", []))
                if latest_user_text:
                    break
        if latest_user_text:
            try:
                recall_block = _try_recall(latest_user_text)
            except Exception as recall_error:  # never break routing on recall failure
                _pipeline_log.debug("memory recall failed: %s", recall_error)
                recall_block = ""
            if recall_block:
                system_prompt = f"{system_prompt}\n\n{recall_block}"
                _pipeline_log.info(
                    "memory_recall_injected route=%s chars=%d query_head=%r",
                    route.route_id if hasattr(route, "route_id") else "?",
                    len(recall_block),
                    latest_user_text[:80],
                )

        messages = [{"role": "system", "content": system_prompt}]
        for item in history:
            role = "assistant" if item.get("role") == ROLE_AGENT else "user"
            content = flatten_parts(item.get("parts", []))
            if not content:
                continue
            messages.append({"role": role, "content": content})
        return messages

    def _call_backend(self, route: RouteConfig, messages: List[Dict[str, str]]) -> str:
        payload = {
            "model": route.model,
            "messages": messages,
            "temperature": route.temperature,
            "max_tokens": route.max_tokens,
            "stream": False,
        }
        request = Request(
            route.completion_url,
            data=json_bytes(payload),
            headers={"Content-Type": JSON_CONTENT_TYPE, "Accept": JSON_CONTENT_TYPE},
            method="POST",
        )

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise BackendError(
                f"{route.display_name} backend returned HTTP {error.code}: {body or error.reason}",
                error.code,
            ) from error
        except URLError as error:
            raise BackendError(f"{route.display_name} backend is unreachable: {error.reason}") from error
        except TimeoutError as error:
            raise BackendError(f"{route.display_name} backend timed out after {self.timeout_seconds}s.") from error
        except json.JSONDecodeError as error:
            raise BackendError(f"{route.display_name} backend returned invalid JSON.") from error

        text = self._extract_backend_text(data)
        if not text:
            raise BackendError(
                f"{route.display_name} backend returned no assistant content. "
                "Increase max_tokens or fix the local model route."
            )
        return text

    def _extract_backend_text(self, payload: Dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        choice = choices[0] or {}
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                    continue
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            return "\n".join(part.strip() for part in parts if part.strip()).strip()
        if isinstance(choice.get("text"), str):
            return choice["text"].strip()
        return ""

    def _explicit_route(self, message: Dict[str, Any], top_level_metadata: Dict[str, Any]) -> Optional[str]:
        for container in (
            message.get("metadata") or {},
            top_level_metadata,
            self._extract_agent_settings(message, top_level_metadata) or {},
        ):
            for key in ("route", "routeId", "lane", "skill", "agent"):
                route_id = resolve_route_alias(container.get(key))
                if route_id:
                    return route_id
        return None

    def _strip_route_prefix(self, message: Dict[str, Any]) -> Optional[str]:
        for part in message.get("parts", []):
            if part.get("kind") != "text":
                continue
            text = str(part.get("text", ""))
            for pattern, route_id in PREFIX_PATTERNS:
                match = pattern.match(text)
                if not match:
                    continue
                stripped = text[match.end():].lstrip()
                if stripped:
                    part["text"] = stripped
                return route_id
        return None

    def _extract_agent_settings(self, message: Dict[str, Any], top_level_metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        candidate = (message.get("metadata") or {}).get("coderAgent")
        if isinstance(candidate, dict):
            return candidate
        candidate = top_level_metadata.get("coderAgent")
        if isinstance(candidate, dict):
            return candidate
        candidate = top_level_metadata.get("agentSettings")
        if isinstance(candidate, dict):
            return candidate
        return None

    def _route_from_agent_settings(self, agent_settings: Optional[Dict[str, Any]]) -> Optional[str]:
        if not isinstance(agent_settings, dict):
            return None
        for key in ("route", "routeId", "lane", "skill", "agent"):
            route_id = resolve_route_alias(agent_settings.get(key))
            if route_id:
                return route_id
        if agent_settings.get("autoExecute"):
            return "agent"
        return None

    def _resubscribe_events(self, task_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            return [
                {
                    "kind": "status-update",
                    "taskId": task.task_id,
                    "contextId": task.context_id,
                    "status": {
                        "state": task.state,
                        "message": task.status_message
                        or build_text_message(
                            ROLE_AGENT,
                            f"Task is {task.state}.",
                            task.task_id,
                            task.context_id,
                        ),
                        "timestamp": task.updated_at,
                    },
                    "metadata": {"routeId": task.route_id},
                    "final": task.state in {"completed", "failed", "canceled"},
                },
                task.snapshot(self),
            ]


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: Tuple[str, int], bridge: LocalGeminiA2ABridge):
        super().__init__(server_address, GeminiA2ABridgeHandler)
        self.bridge = bridge
        self.bridge.set_bound_port(self.server_address[1])


class GeminiA2ABridgeHandler(BaseHTTPRequestHandler):
    server_version = "GeminiA2ABridge/0.1.0"

    @property
    def bridge(self) -> LocalGeminiA2ABridge:
        return self.server.bridge  # type: ignore[attr-defined]

    def log_message(self, _format: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self._write_common_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path in ("/.well-known/agent-card.json", "/v1/card"):
                self._send_json(HTTPStatus.OK, self.bridge.agent_card())
                return
            if path == "/newgate/profile":
                self._send_json(HTTPStatus.OK, self.bridge.newgate_snapshot())
                return
            if path == "/listCommands":
                self._send_json(HTTPStatus.OK, self.bridge.list_commands())
                return
            if path == "/tasks/metadata":
                metadata = self.bridge.list_task_metadata()
                if not metadata:
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self._write_common_headers()
                    self.end_headers()
                    return
                self._send_json(HTTPStatus.OK, metadata)
                return
            match = re.fullmatch(r"/tasks/([^/]+)/metadata", path)
            if match:
                task = self.bridge.get_task(match.group(1))
                self._send_json(HTTPStatus.OK, {"metadata": task["metadata"]})
                return
            match = re.fullmatch(r"/v1/tasks/([^/:]+)", path)
            if match:
                params = parse_qs(parsed.query)
                history_length = params.get("historyLength", [None])[0]
                task = self.bridge.get_task(match.group(1), int(history_length) if history_length else None)
                self._send_json(HTTPStatus.OK, normalized_task_to_rest(task))
                return
            if path == "/":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "name": "Ryota Local Lane Bridge",
                        "agentCard": f"{self.bridge._base_url}/.well-known/agent-card.json",
                        "preferredTransport": "HTTP+JSON",
                    },
                )
                return
        except KeyError:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Task not found"})
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown path: {path}"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            if path == "/":
                self._handle_jsonrpc_request()
                return
            if path == "/tasks":
                body = self._read_json_body()
                task = self.bridge.create_task(body.get("agentSettings"), body.get("contextId"))
                self._send_json(HTTPStatus.CREATED, task.task_id)
                return
            if path == "/executeCommand":
                body = self._read_json_body()
                command = body.get("command")
                args = body.get("args") or []
                if not isinstance(command, str):
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": 'Invalid "command" field.'})
                    return
                try:
                    result = self.bridge.execute_command(command, args)
                except KeyError:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Command not found: {command}"})
                    return
                except ValueError as error:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                    return
                self._send_json(HTTPStatus.OK, result)
                return
            if path == "/v1/message:send":
                body = self._read_json_body()
                payload = self.bridge.handle_rest_message_send(body)
                self._send_json(HTTPStatus.OK, payload)
                return
            if path == "/v1/message:stream":
                body = self._read_json_body()
                events = self.bridge.handle_rest_message_stream_events(body)
                self._send_sse(events)
                return
            match = re.fullmatch(r"/v1/tasks/([^/:]+):cancel", path)
            if match:
                task = self.bridge.cancel_task(match.group(1))
                self._send_json(HTTPStatus.OK, normalized_task_to_rest(task))
                return
            match = re.fullmatch(r"/v1/tasks/([^/:]+):subscribe", path)
            if match:
                events = [self.bridge._event_to_rest(event) for event in self.bridge._resubscribe_events(match.group(1))]
                self._send_sse(events)
                return
        except KeyError as error:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Not found: {error}"})
            return
        except BackendError as error:
            payload = {"error": str(error)}
            if error.details:
                payload["details"] = error.details
            self._send_json(HTTPStatus.BAD_GATEWAY, payload)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"error": f"Unknown path: {path}"})

    def _handle_jsonrpc_request(self) -> None:
        body = self._read_json_body()
        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        if not isinstance(method, str):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32600, "message": "Invalid JSON-RPC request"}},
            )
            return

        accept = self.headers.get("Accept", JSON_CONTENT_TYPE)
        is_stream = accept.startswith(SSE_CONTENT_TYPE) or method in {"message/stream", "tasks/resubscribe"}
        if is_stream:
            try:
                events = self.bridge.handle_jsonrpc_stream_events(method, params)
            except KeyError:
                self._send_json(
                    HTTPStatus.NOT_FOUND,
                    {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}},
                )
                return
            serialized = [{"jsonrpc": "2.0", "id": request_id, "result": event} for event in events]
            self._send_sse(serialized)
            return

        try:
            result = self.bridge.handle_jsonrpc(method, params)
        except KeyError:
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}},
            )
            return
        except BackendError as error:
            payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(error)}}
            if error.details:
                payload["error"]["data"] = error.details
            self._send_json(
                HTTPStatus.BAD_GATEWAY,
                payload,
            )
            return

        self._send_json(HTTPStatus.OK, {"jsonrpc": "2.0", "id": request_id, "result": result})

    def _read_json_body(self) -> Dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        payload = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            body = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            raise BackendError("Invalid JSON payload.")
        if not isinstance(body, dict):
            raise BackendError("JSON payload must be an object.")
        return body

    def _write_common_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Cache-Control", "no-cache")

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        data = json_bytes(payload)
        self.send_response(status)
        self._write_common_headers()
        self.send_header("Content-Type", f"{JSON_CONTENT_TYPE}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_sse(self, events: List[Any]) -> None:
        self.send_response(HTTPStatus.OK)
        self._write_common_headers()
        self.send_header("Content-Type", SSE_CONTENT_TYPE)
        self.end_headers()
        for event in events:
            payload = json.dumps(event, ensure_ascii=False)
            self.wfile.write(f"event: message\ndata: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local A2A bridge for Gemini Code Assist.")
    parser.add_argument("--host", default=os.environ.get("GEMINI_A2A_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GEMINI_A2A_PORT", "8765")))
    parser.add_argument(
        "--advertise-host",
        default=os.environ.get("GEMINI_A2A_ADVERTISE_HOST"),
        help="Host written into the agent card URL. Defaults to the bind host unless it is 0.0.0.0.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("GEMINI_A2A_TIMEOUT", "120")),
        help="Backend request timeout in seconds.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    bridge = LocalGeminiA2ABridge(
        host=args.host,
        port=args.port,
        timeout_seconds=args.timeout,
        advertise_host=args.advertise_host,
    )
    server = BridgeHTTPServer((args.host, args.port), bridge)
    print(f"[gemini-a2a-bridge] listening on http://{bridge.host}:{server.server_address[1]}")
    print(f"[gemini-a2a-bridge] agent card: {bridge._base_url}/.well-known/agent-card.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
