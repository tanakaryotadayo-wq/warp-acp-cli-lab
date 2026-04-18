#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
BOOTSTRAP_SCRIPT = SCRIPTS_DIR / "bootstrap_pipeline_01.sh"
RUNNER_SCRIPT = SCRIPTS_DIR / "pipeline_01_runner.py"
DEFAULT_STATE_DIR = ROOT / "data" / "pipeline_01"
DEFAULT_QUEUE_FILE = DEFAULT_STATE_DIR / "commit_queue.jsonl"
DEFAULT_QUEUE_STATUS_FILE = DEFAULT_STATE_DIR / "commit_queue_status.json"
DEFAULT_POLL_INTERVAL = int(os.environ.get("PIPELINE_01_QUEUE_POLL_INTERVAL", "15"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline① commit queue worker")
    parser.add_argument("--queue-file", default=str(DEFAULT_QUEUE_FILE))
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE_DIR))
    parser.add_argument("--status-file", default=str(DEFAULT_QUEUE_STATUS_FILE))
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args()


def load_queue(handle) -> list[dict[str, Any]]:
    handle.seek(0)
    entries: list[dict[str, Any]] = []
    for line in handle.read().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def write_queue(handle, entries: list[dict[str, Any]]) -> None:
    handle.seek(0)
    handle.truncate()
    for entry in entries:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    handle.flush()


def queue_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
    for entry in entries:
        status = str(entry.get("status", "pending"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def write_queue_status(path: Path, queue_file: Path, entries: list[dict[str, Any]]) -> None:
    counts = queue_counts(entries)
    active = next((entry for entry in entries if entry.get("status") == "in_progress"), None)
    payload = {
        "queueFile": str(queue_file),
        "updatedAt": now_iso(),
        "counts": counts,
        "activeEntry": active,
        "latestCompleted": next((entry for entry in reversed(entries) if entry.get("status") == "completed"), None),
        "latestFailed": next((entry for entry in reversed(entries) if entry.get("status") == "failed"), None),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_entry_status(queue_file: Path, status_file: Path, entry_id: str, **updates: Any) -> dict[str, Any] | None:
    with queue_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        entries = load_queue(handle)
        updated_entry = None
        for entry in entries:
            if entry.get("id") == entry_id:
                entry.update(updates)
                entry["updated_at"] = now_iso()
                updated_entry = entry
                break
        write_queue(handle, entries)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    write_queue_status(status_file, queue_file, entries)
    return updated_entry


def reserve_next_entry(queue_file: Path, status_file: Path) -> dict[str, Any] | None:
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    with queue_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        entries = load_queue(handle)
        reserved = None
        for entry in entries:
            if entry.get("status", "pending") == "pending":
                entry["status"] = "in_progress"
                entry["started_at"] = now_iso()
                entry["updated_at"] = now_iso()
                reserved = entry
                break
        write_queue(handle, entries)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    write_queue_status(status_file, queue_file, entries)
    return reserved


def run_command(command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, check=False)


def create_commit_snapshot(entry: dict[str, Any], snapshot_dir: Path) -> None:
    repo_root = Path(str(entry["repo_root"])).expanduser().resolve()
    sha = str(entry["sha"])
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    archive = subprocess.Popen(
        ["git", "-C", str(repo_root), "archive", "--format=tar", sha],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
    )
    extract = subprocess.run(["tar", "-xf", "-", "-C", str(snapshot_dir)], stdin=archive.stdout, capture_output=True, text=True, check=False)
    assert archive.stdout is not None
    archive.stdout.close()
    stderr = archive.stderr.read().decode("utf-8", errors="replace") if archive.stderr else ""
    archive.wait()
    if archive.returncode != 0 or extract.returncode != 0:
        raise RuntimeError(f"git archive failed: {stderr or extract.stderr}")


def write_commit_context(entry: dict[str, Any], run_dir: Path) -> tuple[Path, Path]:
    context_json = run_dir / "commit_context.json"
    context_md = run_dir / "commit_context.md"
    context_json.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    body = [
        f"# Commit Context {entry['sha'][:8]}",
        "",
        f"- repo: `{entry.get('repo_root')}`",
        f"- branch: `{entry.get('branch')}`",
        f"- commit: `{entry.get('sha')}`",
        f"- author: `{entry.get('author')}`",
        f"- committed_at: `{entry.get('committed_at')}`",
        "",
        "## Subject",
        "",
        str(entry.get("subject", "")),
        "",
        "## Body",
        "",
        str(entry.get("body", "") or "(empty)"),
        "",
        "## Changed Files",
        "",
    ]
    changed_files = entry.get("changed_files") or []
    if changed_files:
        body.extend(f"- `{item}`" for item in changed_files)
    else:
        body.append("(none)")
    body.extend([
        "",
        "## Diff Stat",
        "",
        "```",
        str(entry.get("diff_stat", "")).strip(),
        "```",
        "",
    ])
    context_md.write_text("\n".join(body), encoding="utf-8")
    return context_json, context_md


def ensure_pipeline_bootstrapped(state_dir: Path) -> dict[str, Any]:
    env = os.environ.copy()
    env["PIPELINE_01_STATE_DIR"] = str(state_dir)
    env["PIPELINE_01_STATUS_FILE"] = str(state_dir / "status.json")
    proc = run_command(["bash", str(BOOTSTRAP_SCRIPT)], cwd=ROOT, env=env)
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "status_path": str(state_dir / "status.json"),
    }


def build_issue_body(entry: dict[str, Any], candidates: list[dict[str, Any]], run_dir: Path, runner_status: dict[str, Any]) -> str:
    lines = [
        f"# Pipeline① commit follow-up for `{entry['sha'][:8]}`",
        "",
        f"- subject: {entry.get('subject', '')}",
        f"- branch: `{entry.get('branch', '')}`",
        f"- commit: `{entry.get('sha', '')}`",
        f"- repo_root: `{entry.get('repo_root', '')}`",
        f"- run_dir: `{run_dir}`",
        f"- issue_candidates: `{len(candidates)}`",
        "",
        "## Changed Files",
        "",
    ]
    changed_files = entry.get("changed_files") or []
    if changed_files:
        lines.extend(f"- `{item}`" for item in changed_files)
    else:
        lines.append("(none)")
    lines.extend([
        "",
        "## Candidate Issues",
        "",
    ])
    if not candidates:
        lines.append("- No structured issue candidates were generated.")
    for index, candidate in enumerate(candidates, start=1):
        lines.extend([
            f"### {index}. {candidate.get('title', 'Untitled issue')}",
            "",
            f"- priority: `{candidate.get('priority', 'P1')}`",
            f"- labels: {', '.join(candidate.get('labels', [])) or '(none)'}",
            f"- packet_concepts: {', '.join(candidate.get('packet_concepts', [])) or '(none)'}",
            "",
            candidate.get("body", "").strip(),
            "",
        ])
    lines.extend([
        "## Pipeline Status",
        "",
        "```json",
        json.dumps(runner_status, ensure_ascii=False, indent=2),
        "```",
        "",
    ])
    return "\n".join(lines)


def maybe_publish_issue(entry: dict[str, Any], run_dir: Path, runner_status: dict[str, Any]) -> dict[str, Any]:
    if not entry.get("publish_issue"):
        return {"published": False, "reason": "publish_disabled"}
    target_repo = str(entry.get("target_repo") or "").strip()
    if not target_repo:
        return {"published": False, "reason": "target_repo_missing"}
    if shutil.which("gh") is None:
        return {"published": False, "reason": "gh_missing"}

    issue_candidates_path = run_dir / "gemini_issue_candidates.json"
    candidates: list[dict[str, Any]] = []
    if issue_candidates_path.exists():
        try:
            payload = json.loads(issue_candidates_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                candidates = [item for item in payload if isinstance(item, dict)]
            elif isinstance(payload, dict) and isinstance(payload.get("issues"), list):
                candidates = [item for item in payload["issues"] if isinstance(item, dict)]
        except json.JSONDecodeError:
            candidates = []

    issue_title = f"[pipeline-01] {entry.get('repo_name')} {str(entry.get('sha'))[:8]} follow-up"
    issue_body_path = run_dir / "github_issue.md"
    issue_body_path.write_text(build_issue_body(entry, candidates, run_dir, runner_status), encoding="utf-8")
    proc = run_command(["gh", "issue", "create", "-R", target_repo, "--title", issue_title, "--body-file", str(issue_body_path)], cwd=run_dir)
    if proc.returncode != 0:
        return {"published": False, "reason": "gh_issue_create_failed", "stderr": proc.stderr.strip()}
    issue_url = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return {"published": True, "issue_url": issue_url, "candidate_count": len(candidates), "issue_body_path": str(issue_body_path)}


def process_entry(entry: dict[str, Any], state_dir: Path) -> dict[str, Any]:
    sha = str(entry["sha"])
    repo_name = str(entry.get("repo_name") or "repo")
    run_dir = state_dir / "commit_runs" / f"{sha[:8]}-{int(time.time())}"
    snapshot_dir = run_dir / "snapshot"
    run_dir.mkdir(parents=True, exist_ok=True)

    bootstrap = ensure_pipeline_bootstrapped(state_dir)
    bootstrap_log = run_dir / "bootstrap.log"
    bootstrap_log.write_text((bootstrap.get("stdout") or "") + ("\n--- STDERR ---\n" + bootstrap.get("stderr") if bootstrap.get("stderr") else ""), encoding="utf-8")

    context_json, context_md = write_commit_context(entry, run_dir)
    create_commit_snapshot(entry, snapshot_dir)

    runner_cmd = [
        sys.executable,
        str(RUNNER_SCRIPT),
        "--repo-path",
        str(snapshot_dir),
        "--repo-name",
        f"{repo_name}-{sha[:8]}",
        "--state-dir",
        str(run_dir),
        "--packet-db",
        str(run_dir / "oss_packets.db"),
        "--issue-db",
        str(run_dir / "issue_packets.db"),
        "--status-path",
        str(run_dir / "status.json"),
    ]
    proc = run_command(runner_cmd, cwd=ROOT, env=os.environ.copy())
    (run_dir / "runner.stdout.log").write_text(proc.stdout, encoding="utf-8")
    (run_dir / "runner.stderr.log").write_text(proc.stderr, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"pipeline runner failed with exit code {proc.returncode}")

    runner_status_path = run_dir / "status.json"
    runner_status = json.loads(runner_status_path.read_text(encoding="utf-8")) if runner_status_path.exists() else {}
    issue_publish = maybe_publish_issue(entry, run_dir, runner_status)
    return {
        "run_dir": str(run_dir),
        "snapshot_dir": str(snapshot_dir),
        "bootstrap_log": str(bootstrap_log),
        "context_json": str(context_json),
        "context_md": str(context_md),
        "status_path": str(runner_status_path),
        "runner_exit_code": proc.returncode,
        "issue_publish": issue_publish,
    }


def main() -> int:
    args = parse_args()
    queue_file = Path(args.queue_file).expanduser().resolve()
    state_dir = Path(args.state_dir).expanduser().resolve()
    status_file = Path(args.status_file).expanduser().resolve()
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    while True:
        entry = reserve_next_entry(queue_file, status_file)
        if entry is None:
            if args.once:
                return 0
            time.sleep(max(5, int(args.poll_interval)))
            continue

        try:
            result = process_entry(entry, state_dir)
            update_entry_status(
                queue_file,
                status_file,
                str(entry["id"]),
                status="completed",
                completed_at=now_iso(),
                result=result,
            )
        except Exception as exc:
            update_entry_status(
                queue_file,
                status_file,
                str(entry["id"]),
                status="failed",
                failed_at=now_iso(),
                error=str(exc),
            )

        if args.once:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
