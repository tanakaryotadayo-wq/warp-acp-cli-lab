#!/usr/bin/env python3
# Copyright 2025 Ryota Tanaka. All rights reserved.
"""
memory_pipeline.py — Standalone memory pipeline module for VORTEX.

Extracted from gemini_a2a_bridge.py and fleet_bridge.py so the memory chain
(fleet_log → KI queue → promote → index → recall) can be tested and reused
independently of the VS Code extension or A2A bridge.

Usage:
    from memory_pipeline import (
        emit_fleet_log,
        try_recall,
        handle_fleet_log,
        handle_ki_queue_list,
        handle_ki_queue_promote,
    )
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger("memory_pipeline")

# ─── Shared paths (env-overridable) ──────────────────────────────────────────

FLEET_LOG_DIR = Path(
    os.environ.get("FLEET_LOG_DIR", os.path.expanduser("~/.gemini/antigravity/fleet-logs"))
)
KI_QUEUE_FILE = Path(
    os.environ.get("KI_QUEUE_FILE", os.path.expanduser("~/.gemini/antigravity/ki-promotion-queue.jsonl"))
)
KI_KNOWLEDGE_DIR = Path(
    os.environ.get("KI_KNOWLEDGE_DIR", os.path.expanduser("~/.gemini/antigravity/knowledge"))
)
KI_COLAB_NOTEBOOK = os.environ.get(
    "KI_COLAB_NOTEBOOK",
    os.path.expanduser("~/Newgate/ki_agent_system/colab_ki_vectorizer.ipynb"),
)

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()


def _slugify(value: str, fallback: str = "ki_candidate") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", (value or "").strip().lower()).strip("_")
    return normalized or fallback


# ─── KI Queue CRUD ───────────────────────────────────────────────────────────

def load_queue() -> List[dict]:
    if not KI_QUEUE_FILE.exists():
        return []
    entries: List[dict] = []
    with open(KI_QUEUE_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def write_queue(entries: List[dict]) -> None:
    KI_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(KI_QUEUE_FILE, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


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
    entries = load_queue()
    for existing in entries:
        if existing.get("id") == entry["id"]:
            existing.update({
                "updated_at": _now_iso(),
                "status": existing.get("status", "pending"),
                "task": entry["task"],
                "result": entry["result"],
                "summary": entry["summary"],
                "tags": entry.get("tags", []),
                "log_file": entry["log_file"],
                "notebook_path": entry["notebook_path"],
            })
            write_queue(entries)
            return
    entries.append(entry)
    write_queue(entries)


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


# ─── Auto-index via ConversationMemory ───────────────────────────────────────

def _load_conversation_memory():
    """Best-effort load ~/Newgate/intelligence/conversation_memory.ConversationMemory."""
    newgate_root = Path(os.environ.get("NEWGATE_ROOT", os.path.expanduser("~/Newgate")))
    cm_path = newgate_root / "intelligence" / "conversation_memory.py"
    if not cm_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("conversation_memory", str(cm_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.ConversationMemory()


def _try_index_knowledge(ki_dir: str) -> bool:
    """Best-effort index newly promoted KI. Returns True on success.

    ConversationMemory.index_knowledge(force: bool) re-scans the whole
    knowledge dir; we force=True so the just-promoted KI is picked up.
    `ki_dir` is retained for logging/debug only.
    """
    try:
        cm = _load_conversation_memory()
        if cm is None:
            return False
        cm.index_knowledge(force=True)
        _log.debug("indexed knowledge after promote (new KI dir: %s)", ki_dir)
        return True
    except Exception as exc:
        _log.debug("index_knowledge failed (non-fatal): %s", exc)
        return False


# ─── Public API — fleet_log ──────────────────────────────────────────────────

def emit_fleet_log(event_type: str, task: str, result: str, route_id: str = "") -> None:
    """Write a fleet_log JSONL entry + KI queue candidate (fire-and-forget)."""
    try:
        FLEET_LOG_DIR.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "task": task[:400],
            "result": result[:800],
            "tags": [route_id] if route_id else [],
        }
        log_file = FLEET_LOG_DIR / f"fleet_{datetime.now().strftime('%Y%m%d')}.jsonl"
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

        if event_type == "success":
            KI_QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
            fingerprint = hashlib.sha256(f"{task}\n{result}".encode()).hexdigest()[:16]
            qi = {
                "id": f"ki_{fingerprint}",
                "status": "pending",
                "created_at": entry["timestamp"],
                "updated_at": entry["timestamp"],
                "title": task[:120],
                "summary": result[:400],
                "task": task[:400],
                "result": result[:800],
                "tags": entry["tags"],
                "suggested_ki_name": re.sub(r"[^a-zA-Z0-9_-]+", "_", task.lower().strip())[:60] or "ki_candidate",
                "log_file": str(log_file),
            }
            with open(KI_QUEUE_FILE, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(qi, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log.debug("fleet_log emit failed (non-fatal): %s", exc)


# ─── Public API — recall ─────────────────────────────────────────────────────

def try_recall(query: str, top_k: int = 3, min_score: float = 0.05) -> str:
    """Best-effort memory recall via ConversationMemory.search.

    Returns a short context block ready to be injected into a prompt,
    or "" on any failure. ConversationMemory exposes `search(query,
    top_k, min_score)`, not `recall`; this wrapper hides that detail
    from callers.

    Default `min_score` is tuned for the Qwen3-Embedding-8B scale used
    by the local KI store (cosine similarity on these embeddings runs
    roughly in [0, 0.3] for related content, not [0, 1] like OpenAI
    ada). Keep it low enough that real related items come through.
    """
    try:
        cm = _load_conversation_memory()
        if cm is None:
            return ""
        results = cm.search(query, top_k=top_k, min_score=min_score)
        if not results:
            return ""
        lines = ["[Memory Recall]"]
        for item in results[:top_k]:
            text = (
                item.get("text")
                or item.get("content")
                or item.get("chunk")
                or item.get("snippet")
                or item.get("content_preview")
                or ""
            )
            meta = item.get("metadata") or {}
            src = (
                item.get("source_type")
                or meta.get("source_type")
                or item.get("type")
                or ""
            )
            cid = (
                item.get("chunk_id")
                or item.get("source_doc_uri")
                or item.get("id")
                or ""
            )
            header = f"- [{src} {cid}]".rstrip()
            lines.append(header)
            if text:
                lines.append(f"  {text[:300]}")
        return "\n".join(lines) + "\n---\n"
    except Exception as exc:
        _log.debug("try_recall failed (non-fatal): %s", exc)
        return ""


# ─── Public API — MCP handlers ───────────────────────────────────────────────

def handle_fleet_log(arguments: dict) -> dict:
    """Log an event + auto-create KI queue entry + auto-promote on success."""
    entry = {
        "timestamp": datetime.now().isoformat(),
        "event_type": arguments.get("event_type", "unknown"),
        "task": arguments.get("task", ""),
        "result": arguments.get("result", ""),
        "cause": arguments.get("cause"),
        "fix": arguments.get("fix"),
        "tags": arguments.get("tags", []),
    }
    entry = {k: v for k, v in entry.items() if v is not None}

    FLEET_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = FLEET_LOG_DIR / f"fleet_{datetime.now().strftime('%Y%m%d')}.jsonl"
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    queue_entry = None
    if entry.get("event_type") == "success" and (entry.get("task") or entry.get("result")):
        queue_entry = _make_queue_entry(arguments, log_file)
        _append_queue_entry(queue_entry)

    result: Dict[str, Any] = {"status": "logged", "file": str(log_file), "entry": entry}
    if queue_entry is not None:
        result["ki_queue_entry"] = queue_entry
        result["ki_queue_file"] = str(KI_QUEUE_FILE)
        promote_result = handle_ki_queue_promote({"entry_id": queue_entry["id"]})
        result["auto_promoted"] = promote_result
    return result


def handle_ki_queue_list(arguments: dict) -> dict:
    """List KI queue entries, optionally filtered by status."""
    status = arguments.get("status", "pending")
    limit = max(1, int(arguments.get("limit", 20)))
    entries = load_queue()
    if status != "all":
        entries = [e for e in entries if e.get("status") == status]
    entries = sorted(entries, key=lambda item: item.get("updated_at", ""), reverse=True)
    return {
        "status": "ok",
        "queue_file": str(KI_QUEUE_FILE),
        "knowledge_dir": str(KI_KNOWLEDGE_DIR),
        "notebook_path": KI_COLAB_NOTEBOOK,
        "count": len(entries),
        "entries": entries[:limit],
    }


def handle_ki_queue_promote(arguments: dict) -> dict:
    """Promote a KI queue entry to knowledge/ with metadata, artifact, and auto-index."""
    entry_id = arguments.get("entry_id", "").strip()
    if not entry_id:
        return {"error": "entry_id is required"}

    entries = load_queue()
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
        with open(metadata_path, "r", encoding="utf-8") as fh:
            metadata = json.load(fh)
    else:
        metadata = {}
    metadata.setdefault("title", title)
    metadata.setdefault("summary", summary)
    references = metadata.setdefault("references", [])
    references.append({"type": "fleet_queue", "value": entry_id})
    references.append({"type": "file", "value": f"artifacts/{artifact_name}"})
    metadata["summary"] = summary
    with open(metadata_path, "w", encoding="utf-8") as fh:
        json.dump(metadata, fh, ensure_ascii=False, indent=2)

    artifact_path = artifacts_dir / artifact_name
    with open(artifact_path, "w", encoding="utf-8") as fh:
        fh.write(content)

    timestamps_path = ki_dir / "timestamps.json"
    now = _now_iso()
    timestamps = {"created": now, "modified": now, "accessed": now}
    if timestamps_path.exists():
        with open(timestamps_path, "r", encoding="utf-8") as fh:
            existing = json.load(fh)
        timestamps["created"] = existing.get("created", now)
    with open(timestamps_path, "w", encoding="utf-8") as fh:
        json.dump(timestamps, fh, ensure_ascii=False, indent=2)

    entry["status"] = "promoted"
    entry["updated_at"] = now
    entry["promoted_at"] = now
    entry["knowledge_dir"] = str(ki_dir)
    entry["artifact_path"] = str(artifact_path)
    write_queue(entries)

    indexed = _try_index_knowledge(str(ki_dir))

    return {
        "status": "promoted",
        "entry": entry,
        "knowledge_dir": str(ki_dir),
        "artifact_path": str(artifact_path),
        "notebook_path": KI_COLAB_NOTEBOOK,
        "indexed": indexed,
    }


# ─── CLI self-test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    print("memory_pipeline self-test")
    print(f"  FLEET_LOG_DIR:   {FLEET_LOG_DIR}")
    print(f"  KI_QUEUE_FILE:   {KI_QUEUE_FILE}")
    print(f"  KI_KNOWLEDGE_DIR:{KI_KNOWLEDGE_DIR}")
    result = handle_fleet_log({
        "event_type": "success",
        "task": "memory_pipeline self-test",
        "result": "Module loaded and executed successfully",
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    recall = try_recall("memory pipeline test")
    print(f"  recall: {recall[:200] if recall else '(empty)'}")
    sys.exit(0)
