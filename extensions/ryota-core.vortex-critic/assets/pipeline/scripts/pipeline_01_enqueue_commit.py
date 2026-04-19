#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
DEFAULT_WORKER_SESSION = "vortex-pipeline-queue"
DEFAULT_LAUNCHER = os.environ.get("PIPELINE_01_QUEUE_LAUNCHER", "tmux")
DEFAULT_POLL_INTERVAL = int(os.environ.get("PIPELINE_01_QUEUE_POLL_INTERVAL", "15"))
WORKER_SCRIPT = SCRIPTS_DIR / "pipeline_01_commit_worker.py"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enqueue the latest commit for Pipeline① processing.")
    parser.add_argument("--repo-root", default=str(Path.cwd()))
    parser.add_argument("--commit", default="")
    parser.add_argument("--state-dir", default="")
    parser.add_argument("--queue-file", default="")
    parser.add_argument("--worker-session", default=DEFAULT_WORKER_SESSION)
    parser.add_argument("--worker-log", default="")
    parser.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--launcher", choices=["tmux", "subprocess"], default=DEFAULT_LAUNCHER if DEFAULT_LAUNCHER in {"tmux", "subprocess"} else "tmux")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--publish-issue", dest="publish_issue", action="store_true", default=True)
    parser.add_argument("--no-publish-issue", dest="publish_issue", action="store_false")
    return parser.parse_args()


def default_state_dir(repo_root: Path) -> Path:
    return repo_root / ".build" / "ryota" / "pipeline_01"


def run_git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def parse_github_repo(url: str) -> str | None:
    cleaned = url.strip()
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    if cleaned.startswith("git@github.com:"):
        return cleaned.split("git@github.com:", 1)[1]
    prefix = "https://github.com/"
    if cleaned.startswith(prefix):
        return cleaned[len(prefix):]
    return None


def resolve_target_repo(repo_root: Path) -> str | None:
    for remote_name in ("ryota-fork", "origin"):
        try:
            url = run_git(repo_root, "remote", "get-url", remote_name)
        except subprocess.CalledProcessError:
            continue
        parsed = parse_github_repo(url)
        if parsed:
            return parsed
    return None


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


def queue_entry_for_commit(repo_root: Path, commit_sha: str, publish_issue: bool) -> dict[str, Any]:
    repo_name = repo_root.name
    subject = run_git(repo_root, "log", "-1", "--format=%s", commit_sha)
    body = run_git(repo_root, "log", "-1", "--format=%b", commit_sha)
    committed_at = run_git(repo_root, "log", "-1", "--format=%cI", commit_sha)
    author = run_git(repo_root, "log", "-1", "--format=%an <%ae>", commit_sha)
    branch = run_git(repo_root, "branch", "--show-current") or "detached"
    changed_files = [line.strip() for line in run_git(repo_root, "show", "--format=", "--name-only", commit_sha).splitlines() if line.strip()]
    diff_stat = run_git(repo_root, "show", "--stat", "--format=", commit_sha)
    return {
        "id": f"commit-{commit_sha}",
        "status": "pending",
        "sha": commit_sha,
        "repo_root": str(repo_root),
        "repo_name": repo_name,
        "branch": branch,
        "subject": subject,
        "body": body,
        "author": author,
        "committed_at": committed_at,
        "changed_files": changed_files,
        "diff_stat": diff_stat,
        "publish_issue": publish_issue,
        "target_repo": resolve_target_repo(repo_root),
        "enqueued_at": now_iso(),
        "updated_at": now_iso(),
    }


def ensure_worker_started(args: argparse.Namespace) -> dict[str, Any]:
    worker_log = Path(args.worker_log).expanduser().resolve()
    worker_log.parent.mkdir(parents=True, exist_ok=True)

    if args.launcher == "tmux":
        tmux = shutil_which("tmux")
        if tmux:
            session = args.worker_session
            has_session = subprocess.run([tmux, "has-session", "-t", session], capture_output=True, text=True, check=False)
            if has_session.returncode == 0:
                return {"launcher": "tmux", "session": session, "started": False}
            command = (
                f"cd {shlex.quote(str(ROOT))} && "
                f"exec {shlex.quote(sys.executable)} {shlex.quote(str(WORKER_SCRIPT))} "
                f"--queue-file {shlex.quote(str(Path(args.queue_file).expanduser().resolve()))} "
                f"--state-dir {shlex.quote(str(Path(args.state_dir).expanduser().resolve()))} "
                f"--poll-interval {int(args.poll_interval)} "
                f">>{shlex.quote(str(worker_log))} 2>&1"
            )
            subprocess.run([tmux, "new-session", "-d", "-s", session, command], check=True)
            return {"launcher": "tmux", "session": session, "started": True}

    subprocess.Popen(
        [
            sys.executable,
            str(WORKER_SCRIPT),
            "--queue-file",
            str(Path(args.queue_file).expanduser().resolve()),
            "--state-dir",
            str(Path(args.state_dir).expanduser().resolve()),
            "--poll-interval",
            str(int(args.poll_interval)),
        ],
        cwd=str(ROOT),
        stdout=worker_log.open("a", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    return {"launcher": "subprocess", "started": True, "log": str(worker_log)}


def shutil_which(binary: str) -> str | None:
    for base in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(base) / binary
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve()

    if args.queue_file.strip():
        queue_file = Path(args.queue_file).expanduser().resolve()
    else:
        queue_file = None

    if args.state_dir.strip():
        state_dir = Path(args.state_dir).expanduser().resolve()
    elif queue_file is not None:
        state_dir = queue_file.parent
    else:
        state_dir = default_state_dir(repo_root)

    if queue_file is None:
        queue_file = state_dir / "commit_queue.jsonl"

    if args.worker_log.strip():
        worker_log = Path(args.worker_log).expanduser().resolve()
    else:
        worker_log = state_dir / "commit_queue_worker.log"

    args.state_dir = str(state_dir)
    args.queue_file = str(queue_file)
    args.worker_log = str(worker_log)
    queue_file.parent.mkdir(parents=True, exist_ok=True)

    commit_sha = args.commit.strip()
    if not commit_sha:
        commit_sha = run_git(repo_root, "rev-parse", "HEAD")

    entry = queue_entry_for_commit(repo_root, commit_sha, args.publish_issue)

    with queue_file.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        entries = load_queue(handle)
        existing = next((item for item in entries if item.get("sha") == commit_sha), None)
        if existing and not args.force:
            existing["updated_at"] = now_iso()
            write_queue(handle, entries)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            worker = ensure_worker_started(args)
            print(json.dumps({"status": "already_queued", "entry": existing, "worker": worker}, ensure_ascii=False))
            return 0

        if existing and args.force:
            entries = [item for item in entries if item.get("sha") != commit_sha]
        entries.append(entry)
        write_queue(handle, entries)
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    worker = ensure_worker_started(args)
    print(json.dumps({"status": "queued", "entry": entry, "worker": worker, "queue_file": str(queue_file)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
