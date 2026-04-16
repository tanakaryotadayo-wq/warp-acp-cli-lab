#!/usr/bin/env python3
"""JS/TSファイルを正規表現でNeural Packetへ収穫するヘルパー。"""

import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Iterable, List

from neural_packet import NeuralPacket, PacketLedger


JS_PATTERNS = [
    re.compile(r"(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:class|function)\s+([A-Za-z_$][\w$]*)"),
    re.compile(r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
    re.compile(r"module\.exports\s*=\s*(?:class|function)?\s*([A-Za-z_$][\w$]*)"),
    re.compile(r"exports\.([A-Za-z_$][\w$]*)\s*="),
]

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
TARGET_EXTS = {".js", ".mjs", ".cjs", ".ts", ".tsx"}


def iter_source_files(source_dir: Path) -> Iterable[Path]:
    """対象ディレクトリ配下のJS/TSファイルを列挙する。"""
    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        root_path = Path(root)
        for filename in files:
            if filename.endswith(".d.ts"):
                continue
            if Path(filename).suffix.lower() in TARGET_EXTS:
                yield root_path / filename


def extract_symbols(content: str) -> List[Dict]:
    """正規表現ベースでJS/TSシンボルを抽出する。"""
    lines = content.splitlines()
    symbols: List[Dict] = []
    seen = set()

    for pattern in JS_PATTERNS:
        for match in pattern.finditer(content):
            name = match.group(1) if match.lastindex else ""
            if not name or len(name) < 2 or name in seen:
                continue
            seen.add(name)

            line_no = content.count("\n", 0, match.start()) + 1
            start = max(0, line_no - 2)
            end = min(len(lines), line_no + 10)
            snippet = "\n".join(lines[start:end])[:500]
            symbols.append(
                {
                    "name": name,
                    "line": line_no,
                    "snippet": snippet,
                }
            )

    return symbols


def build_symbol_packet(repo_name: str, source_dir: Path, file_path: Path, symbol: Dict) -> NeuralPacket:
    """シンボル単位のNeural Packetを作る。"""
    rel_path = file_path.relative_to(source_dir).as_posix()
    code_ref = f"code://{rel_path}#L{symbol['line']}"
    return NeuralPacket(
        id=f"{repo_name}/{rel_path}#{symbol['name']}",
        repo=f"local://{source_dir}",
        ref=rel_path,
        license="UNKNOWN",
        trigger={"concepts": [symbol["name"].lower(), file_path.stem.lower()], "vec_bin": "10"},
        skill={
            "language": "javascript",
            "input_spec": f"symbol: {symbol['name']}",
            "output_spec": "code asset",
            "dependencies": [],
            "code_ref": code_ref,
        },
        evidence=[{"path": rel_path, "lines": f"L{symbol['line']}"}],
        verifier={
            "level": "V1",
            "type": "grep",
            "cmd": f"grep -n \"{symbol['name']}\" {rel_path}",
            "pass_condition": "EXIT_CODE_0",
        },
        notes=symbol["snippet"],
    )


def build_module_packet(repo_name: str, source_dir: Path, file_path: Path, content: str) -> NeuralPacket:
    """シンボル抽出できないファイル向けにモジュール単位Packetを作る。"""
    rel_path = file_path.relative_to(source_dir).as_posix()
    line_count = content.count("\n") + (1 if content else 0)
    snippet = "\n".join(content.splitlines()[:12])[:500]
    return NeuralPacket(
        id=f"{repo_name}/{rel_path}#module",
        repo=f"local://{source_dir}",
        ref=rel_path,
        license="UNKNOWN",
        trigger={"concepts": [file_path.stem.lower()], "vec_bin": "10"},
        skill={
            "language": "javascript",
            "input_spec": f"module: {rel_path}",
            "output_spec": "code asset",
            "dependencies": [],
            "code_ref": f"code://{rel_path}#L1",
        },
        evidence=[{"path": rel_path, "lines": f"L1-L{max(line_count, 1)}"}],
        verifier={
            "level": "V1",
            "type": "test",
            "cmd": f"test -f {rel_path}",
            "pass_condition": "EXIT_CODE_0",
        },
        notes=snippet,
    )


def main() -> int:
    source_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/ryota/.local/lib/node_modules/@google/gemini-cli/dist").resolve()
    repo_name = sys.argv[2] if len(sys.argv) > 2 else "gemini-cli"

    if not source_dir.exists():
        print(f"❌ source_dir not found: {source_dir}")
        return 1

    files = sorted(iter_source_files(source_dir))
    ledger = PacketLedger()
    packets: List[NeuralPacket] = []
    total_lines = 0
    start = time.time()

    for file_path in files:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        total_lines += content.count("\n") + (1 if content else 0)
        symbols = extract_symbols(content)
        if symbols:
            packets.extend(build_symbol_packet(repo_name, source_dir, file_path, symbol) for symbol in symbols)
        else:
            packets.append(build_module_packet(repo_name, source_dir, file_path, content))

    ledger.store_many(packets)
    elapsed = time.time() - start

    with ledger._connect() as conn:
        repo_count = conn.execute("SELECT COUNT(*) FROM packets WHERE id LIKE ?", (f"{repo_name}/%",)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]

    print(f"Found {len(files)} JS/TS files in {source_dir}")
    print(f"Total lines: {total_lines:,}")
    print("")
    print("=== Gemini CLI Compression Results ===")
    print(f"  Source: {source_dir}")
    print(f"  Files: {len(files)}")
    print(f"  Lines: {total_lines:,}")
    print(f"  Packets stored: {len(packets):,}")
    print(f"  Elapsed: {elapsed:.2f}s")
    print(f"  Speed: {len(packets) / max(elapsed, 0.001):.0f} packets/sec")
    print("")
    print(f"  Ledger total: {total_count:,} packets")
    print(f"  {repo_name}: {repo_count:,} packets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
