#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
INTELLIGENCE_ROOT = ROOT / "intelligence"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(INTELLIGENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(INTELLIGENCE_ROOT))

from eck_bridge import ECKBridge  # type: ignore
from neural_packet import NeuralPacket, PacketLedger  # type: ignore


DEFAULT_STATE_DIR = ROOT / "data" / "pipeline_01"
DEFAULT_PACKET_DB = DEFAULT_STATE_DIR / "oss_packets.db"
DEFAULT_ISSUE_DB = DEFAULT_STATE_DIR / "issue_packets.db"
DEFAULT_STATUS_PATH = DEFAULT_STATE_DIR / "status.json"
DEFAULT_MOUNT_PATH = Path.home() / "GoogleDriveCache" / "oss"
DEFAULT_REPO_PATH = Path.home() / "vscode-oss"
FUSION_GATE_ENDPOINT = os.getenv("PIPELINE_01_FUSION_GATE_ENDPOINT", "http://127.0.0.1:9800")
CBF_ENDPOINT = os.getenv("PIPELINE_01_CBF_ENDPOINT", "http://127.0.0.1:9801")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def is_http_ready(url: str, timeout: int = 5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            pass
        return True
    except Exception:
        return False


def is_mounted(path: Path) -> bool:
    probe = subprocess.run(
        ["/bin/sh", "-lc", f"mount | grep -F 'on {path} ' >/dev/null"],
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def post_json(url: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str, timeout: int = 15) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def record_cbf_step(x: int, y: int, z: int, task: str) -> dict[str, Any]:
    try:
        return post_json(
            f"{CBF_ENDPOINT}/v1/cbf/step",
            {"x": x, "y": y, "z": z, "task": task, "save_log": True},
            timeout=15,
        )
    except Exception as exc:  # pragma: no cover - best effort status capture
        return {"error": str(exc)}


def run_packetizer(repo_path: Path, repo_name: str, packet_db: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["EMBEDDING2_DB_PATH"] = str(packet_db)
    proc = subprocess.run(
        [sys.executable, str(INTELLIGENCE_ROOT / "harvest_js_packets.py"), str(repo_path), repo_name],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def summarize_packets(packet_db: Path, repo_name: str) -> dict[str, Any]:
    if not packet_db.exists():
        return {"count": 0, "sample_packets": []}

    with sqlite3.connect(packet_db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM packets WHERE id LIKE ?", (f"{repo_name}/%",)).fetchone()[0]
        rows = conn.execute(
            "SELECT id, data FROM packets WHERE id LIKE ? ORDER BY updated_at DESC LIMIT 12",
            (f"{repo_name}/%",),
        ).fetchall()

    sample_packets: list[dict[str, Any]] = []
    for packet_id, raw_data in rows:
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            data = {}
        sample_packets.append(
            {
                "id": packet_id,
                "ref": data.get("ref", ""),
                "concepts": data.get("trigger", {}).get("concepts", [])[:6],
                "evidence": data.get("evidence", [])[:2],
            }
        )
    return {"count": int(total), "sample_packets": sample_packets}


def invoke_fusion_gate(prompt: str, provider: str, pcc_input: str) -> dict[str, Any]:
    return post_json(
        f"{FUSION_GATE_ENDPOINT}/v1/gate/invoke",
        {
            "prompt": prompt,
            "provider": provider,
            "cache": True,
            "pcc": pcc_input,
        },
        timeout=300,
    )


def extract_json_payload(text: str) -> Any:
    text = text.strip()
    if not text:
        raise ValueError("empty response")
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()
    for candidate in (text, _extract_balanced(text, "{", "}"), _extract_balanced(text, "[", "]")):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("no valid JSON payload found")


def _extract_balanced(text: str, open_char: str, close_char: str) -> str | None:
    start = text.find(open_char)
    end = text.rfind(close_char)
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def build_issue_prompt(repo_name: str, claude_analysis: dict[str, Any]) -> str:
    return (
        "Return JSON only.\n"
        "Produce an object with key `issues` containing 1-5 concrete issue candidates.\n"
        "Each issue must contain: title, body, labels, priority, packet_concepts.\n"
        "Priorities must be P0/P1/P2.\n"
        f"Repository: {repo_name}\n"
        f"Claude analysis:\n{json.dumps(claude_analysis, ensure_ascii=False, indent=2)}"
    )


def normalize_issue_candidates(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and isinstance(payload.get("issues"), list):
        items = payload["issues"]
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "")).strip() or f"Pipeline issue {index}"
        body = str(item.get("body", "")).strip() or str(item.get("problem", "")).strip() or title
        labels = item.get("labels")
        if not isinstance(labels, list):
            labels = ["pipeline-01", "needs-triage"]
        packet_concepts = item.get("packet_concepts")
        if not isinstance(packet_concepts, list):
            packet_concepts = []
        normalized.append(
            {
                "title": title,
                "body": body,
                "labels": [str(label) for label in labels if str(label).strip()],
                "priority": str(item.get("priority", "P1")).upper(),
                "packet_concepts": [str(concept) for concept in packet_concepts if str(concept).strip()],
            }
        )
    return normalized


def fallback_issue_candidates(repo_name: str, claude_response: str) -> list[dict[str, Any]]:
    preview = claude_response.strip()[:600] or f"{repo_name} packet analysis did not return structured output."
    return [
        {
            "title": f"{repo_name}: follow up packet analysis",
            "body": preview,
            "labels": ["pipeline-01", "claude-analysis"],
            "priority": "P1",
            "packet_concepts": ["pipeline-01", repo_name],
        }
    ]


def issue_to_packet(repo_name: str, issue: dict[str, Any], index: int) -> NeuralPacket:
    title = issue["title"]
    concepts = [repo_name, "pipeline-01", "issue", *issue.get("packet_concepts", [])]
    unique_concepts = []
    for concept in concepts:
        normalized = str(concept).strip().lower().replace(" ", "-")
        if normalized and normalized not in unique_concepts:
            unique_concepts.append(normalized)
    slug = "-".join(title.lower().split())[:48] or f"issue-{index}"
    return NeuralPacket(
        id=f"pipeline-01/{repo_name}#{slug}-{index}",
        repo=f"pipeline://{repo_name}",
        ref=f"issues/{index}.json",
        license="INTERNAL",
        trigger={"concepts": unique_concepts[:8], "vec_bin": "1010"},
        skill={
            "language": "json",
            "input_spec": title,
            "output_spec": "issue artifact",
            "dependencies": issue.get("labels", []),
            "code_ref": f"issue://{repo_name}/{index}",
        },
        evidence=[{"path": f"issues/{index}.json", "lines": "L1-L1"}],
        verifier={
            "level": "V2",
            "type": "artifact",
            "cmd": f"test -f issues/{index}.json",
            "pass_condition": "EXIT_CODE_0",
        },
        notes=issue["body"],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline 1: OSS packet -> Claude -> Gemini -> ECK")
    parser.add_argument("--repo-path", default=str(DEFAULT_REPO_PATH))
    parser.add_argument("--repo-name", default="vscode-oss")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--packet-db", default=str(DEFAULT_PACKET_DB))
    parser.add_argument("--issue-db", default=str(DEFAULT_ISSUE_DB))
    parser.add_argument("--status-path", default=str(DEFAULT_STATUS_PATH))
    parser.add_argument("--mount-path", default=str(DEFAULT_MOUNT_PATH))
    parser.add_argument("--drive-remote", default=os.getenv("PIPELINE_01_RCLONE_REMOTE", "gdrive"))
    parser.add_argument("--drive-subpath", default=os.getenv("PIPELINE_01_RCLONE_SUBPATH", ""))
    parser.add_argument("--claude-provider", default="claude")
    parser.add_argument("--gemini-provider", default="gemini")
    parser.add_argument("--pcc", default="#監 pipeline-01 oss packet review")
    parser.add_argument("--allow-empty-packets", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    repo_path = Path(args.repo_path).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    status_path = Path(args.status_path).expanduser().resolve()
    packet_db = Path(args.packet_db).expanduser().resolve()
    issue_db = Path(args.issue_db).expanduser().resolve()
    state_dir.mkdir(parents=True, exist_ok=True)

    status: dict[str, Any] = {
        **read_json(status_path),
        "pipeline": "pipeline-01",
        "stage": "starting",
        "started_at": started_at,
        "repo_path": str(repo_path),
        "repo_name": args.repo_name,
        "mount_path": str(Path(args.mount_path).expanduser()),
        "drive_remote": args.drive_remote,
        "drive_subpath": args.drive_subpath,
        "mounted": is_mounted(Path(args.mount_path).expanduser()),
        "cbfHealthy": is_http_ready(f"{CBF_ENDPOINT}/v1/cbf/health"),
        "n8nReady": is_http_ready("http://127.0.0.1:5678/"),
        "artifacts": {},
    }
    status.pop("error", None)
    status.pop("failed_at", None)
    write_json(status_path, status)

    try:
        if not repo_path.exists():
            raise FileNotFoundError(f"repo_path not found: {repo_path}")

        status["stage"] = "packetizing"
        write_json(status_path, status)
        packetizer = run_packetizer(repo_path, args.repo_name, packet_db)
        status["packetizer"] = {
            "exit_code": packetizer["exit_code"],
            "stderr": packetizer["stderr"][-2000:],
        }
        if packetizer["exit_code"] != 0:
            raise RuntimeError(packetizer["stderr"].strip() or packetizer["stdout"].strip() or "packetizer failed")

        packet_summary = summarize_packets(packet_db, args.repo_name)
        status["packet_summary"] = packet_summary
        write_json(status_path, status)
        if int(packet_summary.get("count", 0)) <= 0 and not args.allow_empty_packets:
            raise RuntimeError(
                f"packetizer produced zero packets for {args.repo_name}; "
                "check harvest scope, mount state, or pass --allow-empty-packets to override"
            )
        status["cbf"] = {"packetized": record_cbf_step(1, 0, 1, f"packetized {args.repo_name}")}
        write_json(status_path, status)

        claude_prompt = (
            "Return JSON only.\n"
            "Produce an object with keys summary, risks, expected_outputs, issue_candidates.\n"
            "Each issue_candidate must contain title, problem, evidence, labels, priority.\n"
            f"Pipeline: mounted OSS -> Fusion Gate cache/PCC/CBF -> Claude packet analysis -> Gemini issue subdivision -> ECK persistence.\n"
            f"Repository packet summary:\n{json.dumps(packet_summary, ensure_ascii=False, indent=2)}"
        )
        status["stage"] = "claude_analysis"
        write_json(status_path, status)
        claude_result = invoke_fusion_gate(claude_prompt, args.claude_provider, args.pcc)
        claude_response = str(claude_result.get("response", ""))
        claude_path = state_dir / "claude_analysis.json"
        try:
            claude_payload = extract_json_payload(claude_response)
        except ValueError:
            claude_payload = {"raw": claude_response}
        write_json(
            claude_path,
            {
                "provider": claude_result.get("provider", args.claude_provider),
                "from_cache": claude_result.get("from_cache", False),
                "raw_response": claude_response,
                "parsed": claude_payload,
            },
        )
        status["artifacts"]["claude_analysis"] = str(claude_path)
        status["cbf"]["claude_analysis"] = record_cbf_step(2, 1, 2, f"analyzed packets for {args.repo_name}")
        write_json(status_path, status)

        status["stage"] = "gemini_issue_split"
        write_json(status_path, status)
        claude_issue_candidates = []
        if isinstance(claude_payload, dict):
            claude_issue_candidates = normalize_issue_candidates({"issues": claude_payload.get("issue_candidates", [])})
        gemini_result: dict[str, Any] | None = None
        gemini_response = ""
        gemini_path = state_dir / "gemini_issue_candidates.json"
        try:
            gemini_result = invoke_fusion_gate(
                build_issue_prompt(args.repo_name, claude_payload if isinstance(claude_payload, dict) else {"payload": claude_payload}),
                args.gemini_provider,
                args.pcc,
            )
            gemini_response = str(gemini_result.get("response", ""))
            gemini_payload = extract_json_payload(gemini_response)
            issues = normalize_issue_candidates(gemini_payload)
        except Exception as exc:
            gemini_payload = {
                "error": str(exc),
                "raw": gemini_response,
                "fallback": "claude_issue_candidates",
            }
            issues = claude_issue_candidates
        if not issues:
            issues = claude_issue_candidates or fallback_issue_candidates(args.repo_name, claude_response)
        write_json(
            gemini_path,
            {
                "provider": (gemini_result or {}).get("provider", args.gemini_provider),
                "from_cache": (gemini_result or {}).get("from_cache", False),
                "raw_response": gemini_response,
                "parsed": gemini_payload,
                "issues": issues,
            },
        )
        status["artifacts"]["gemini_issue_candidates"] = str(gemini_path)
        status["issue_count"] = len(issues)
        status["cbf"]["gemini_issue_split"] = record_cbf_step(3, 1, 3, f"subdivided issues for {args.repo_name}")
        write_json(status_path, status)

        status["stage"] = "eck_persistence"
        packets = [issue_to_packet(args.repo_name, issue, index + 1) for index, issue in enumerate(issues)]
        issue_ledger = PacketLedger(db_path=str(issue_db))
        issue_ledger.store_many(packets)
        issue_packets_path = state_dir / "issue_packets.jsonl"
        issue_packets_path.write_text("\n".join(packet.to_jsonl() for packet in packets) + "\n", encoding="utf-8")
        archive_dir = state_dir / f"eck_archive_{time.strftime('%Y%m%dT%H%M%S')}"
        bridge = ECKBridge(archive_dir=str(archive_dir))
        bridge.runs_dir = archive_dir / "_isolated_runs"
        bridge._run_count = bridge._persistent_archive_count()
        bridge._genesis_done = False
        eck_results = []
        for packet in packets:
            bridge._genesis_done = False
            eck_results.append(bridge.validate_packet(json.loads(packet.to_jsonl()), previous=None))
        eck_path = state_dir / "eck_results.json"
        write_json(
            eck_path,
            {
                "results": [result.to_dict() for result in eck_results],
                "bridge": bridge.stats(),
            },
        )
        status["artifacts"]["issue_packets"] = str(issue_packets_path)
        status["artifacts"]["eck_results"] = str(eck_path)
        status["artifacts"]["eck_archive_dir"] = str(archive_dir)
        status["eck"] = read_json(eck_path)
        status["cbf"]["eck_persistence"] = record_cbf_step(4, 2, 4, f"persisted issue packets for {args.repo_name}")
        status["stage"] = "completed"
        status["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_json(status_path, status)
        return 0
    except Exception as exc:
        status["stage"] = "failed"
        status["error"] = str(exc)
        status["failed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        write_json(status_path, status)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
