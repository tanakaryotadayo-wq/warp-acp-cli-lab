"""
Neural Packet Engine — Omnibus Prompting for Copilot CLI
Strategy: 1 request = 30 tasks via JSONL packing
"""

import ast
import json
import hashlib
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# Neural Packet schema (matches ECK v3.2.0 spec)
@dataclass
class NeuralPacket:
    id: str                          # "repo/skill#hash"
    status: str = "PENDING"          # PENDING | PASS | FAIL
    fail_reason: str = ""
    repo: str = "local://ryota-os"
    ref: str = ""
    license: str = "UNKNOWN"
    evidence: List[Dict] = field(default_factory=list)
    toolchain_fingerprint: str = "runner:local@oneplus15"
    deps_lock_ref: str = ""
    model_fingerprint: str = ""
    trigger: Dict = field(default_factory=lambda: {"concepts": [], "vec_bin": ""})
    skill: Dict = field(default_factory=lambda: {
        "language": "", "input_spec": "", "output_spec": "",
        "dependencies": [], "code_ref": ""
    })
    exec_profile: Dict = field(default_factory=lambda: {
        "mode": "isolated", "timeout_sec": 60, "memory_mb": 512,
        "cpus": 0.5, "network": "none", "fs": "ro+tmpfs",
        "ulimit": {"fsize_kb": 10240, "nproc": 128}
    })
    verifier: Dict = field(default_factory=lambda: {
        "level": "V2", "type": "script", "cmd": "", "pass_condition": "EXIT_CODE_0"
    })
    verifier_log_ref: str = ""
    kv: Dict = field(default_factory=lambda: {
        "eligible": False, "canonical_prefix_id": "", "kv_ref": ""
    })
    notes: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_jsonl(self) -> str:
        """1 line JSONL output"""
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict) -> "NeuralPacket":
        """既知フィールドだけを使って復元する。"""
        return cls(**{key: value for key, value in data.items() if key in cls.__dataclass_fields__})

    @staticmethod
    def build_id(repo: str, skill_name: str, code_ref: str = "") -> str:
        """スキル名と参照から安定したIDを作る。"""
        digest = hashlib.sha1(f"{repo}:{skill_name}:{code_ref}".encode("utf-8")).hexdigest()[:10]
        return f"{repo}/{skill_name}#{digest}"

    def validate(self) -> Tuple[bool, List[str]]:
        """Returns (is_valid, errors)"""
        errors: List[str] = []
        if not self.id:
            errors.append("id is required")
        if self.status not in ("PENDING", "PASS", "FAIL"):
            errors.append(f"invalid status: {self.status}")
        if self.status == "FAIL" and not self.fail_reason:
            errors.append("fail_reason required when status=FAIL")
        if not isinstance(self.trigger, dict):
            errors.append("trigger must be an object")
        else:
            concepts = self.trigger.get("concepts")
            if not isinstance(concepts, list) or not concepts:
                errors.append("trigger.concepts required")
            vec_bin = self.trigger.get("vec_bin")
            if vec_bin not in ("", None) and not isinstance(vec_bin, str):
                errors.append("trigger.vec_bin must be a string")
        if not isinstance(self.skill, dict):
            errors.append("skill must be an object")
        elif "dependencies" in self.skill and not isinstance(self.skill.get("dependencies"), list):
            errors.append("skill.dependencies must be a list")
        if not isinstance(self.exec_profile, dict):
            errors.append("exec_profile must be an object")
        else:
            for key in ("mode", "timeout_sec", "memory_mb", "cpus", "network", "fs"):
                if key not in self.exec_profile:
                    errors.append(f"exec_profile.{key} required")
            ulimit = self.exec_profile.get("ulimit")
            if ulimit is not None and not isinstance(ulimit, dict):
                errors.append("exec_profile.ulimit must be an object")
        if not isinstance(self.verifier, dict):
            errors.append("verifier must be an object")
        else:
            level = self.verifier.get("level")
            if level not in ("V1", "V2", "V3"):
                errors.append(f"invalid verifier level: {level}")
            if not self.verifier.get("type"):
                errors.append("verifier.type required")
            if not self.verifier.get("pass_condition"):
                errors.append("verifier.pass_condition required")
        if not isinstance(self.evidence, list):
            errors.append("evidence must be a list")
        else:
            for index, item in enumerate(self.evidence):
                if not isinstance(item, dict):
                    errors.append(f"evidence[{index}] must be an object")
                    continue
                if "path" not in item or "lines" not in item:
                    errors.append(f"evidence[{index}] must include path and lines")
        if not isinstance(self.kv, dict):
            errors.append("kv must be an object")
        elif "eligible" in self.kv and not isinstance(self.kv.get("eligible"), bool):
            errors.append("kv.eligible must be a boolean")
        return (len(errors) == 0, errors)


class PacketLedger:
    """SQLite-backed ledger for Neural Packets"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("EMBEDDING2_DB_PATH", os.path.join(os.path.dirname(__file__), "neural_packets.db"))
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS packets (
                id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'PENDING',
                data TEXT NOT NULL,
                batch_id TEXT,
                model_fingerprint TEXT,
                created_at TEXT,
                updated_at TEXT
            )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                model TEXT,
                packet_count INTEGER,
                status TEXT DEFAULT 'PENDING',
                prompt_text TEXT,
                response_jsonl TEXT,
                request_cost REAL DEFAULT 0,
                created_at TEXT,
                completed_at TEXT
            )"""
            )

    def store(self, packet: NeuralPacket, batch_id: Optional[str] = None) -> None:
        with self._connect() as conn:
            now = datetime.now().isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO packets (id, status, data, batch_id, model_fingerprint, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (
                    packet.id,
                    packet.status,
                    packet.to_jsonl(),
                    batch_id,
                    packet.model_fingerprint,
                    packet.created_at,
                    now,
                ),
            )

    def store_many(self, packets: List[NeuralPacket], batch_id: Optional[str] = None) -> None:
        with self._connect() as conn:
            now = datetime.now().isoformat()
            conn.executemany(
                "INSERT OR REPLACE INTO packets (id, status, data, batch_id, model_fingerprint, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                [
                    (
                        packet.id,
                        packet.status,
                        packet.to_jsonl(),
                        batch_id,
                        packet.model_fingerprint,
                        packet.created_at,
                        now,
                    )
                    for packet in packets
                ],
            )

    def get(self, packet_id: str) -> Optional[NeuralPacket]:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM packets WHERE id=?", (packet_id,)).fetchone()
            if row:
                return NeuralPacket.from_dict(json.loads(row["data"]))
        return None

    def get_pending(self, limit: int = 30) -> List[NeuralPacket]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT data FROM packets WHERE status='PENDING' ORDER BY created_at LIMIT ?",
                (limit,),
            ).fetchall()
            return [NeuralPacket.from_dict(json.loads(row["data"])) for row in rows]

    def get_batch(self, batch_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            return dict(row) if row else None

    def update_from_response(self, results: List[dict], batch_id: Optional[str] = None) -> None:
        """Update packets from LLM JSONL response"""
        with self._connect() as conn:
            now = datetime.now().isoformat()
            for result in results:
                packet_id = result.get("id", "")
                packet = self.get(packet_id)
                if not packet:
                    continue
                packet.status = result.get("status", "FAIL")
                packet.fail_reason = result.get("fail_reason", "")
                if result.get("recommended_level"):
                    packet.verifier["level"] = result["recommended_level"]
                if result.get("audit_notes"):
                    packet.notes = result["audit_notes"]
                conn.execute(
                    "UPDATE packets SET status=?, data=?, updated_at=? WHERE id=?",
                    (packet.status, packet.to_jsonl(), now, packet_id),
                )

            if batch_id:
                response_jsonl = "\n".join(
                    json.dumps(result, ensure_ascii=False, separators=(",", ":")) for result in results
                )
                conn.execute(
                    "UPDATE batches SET status=?, response_jsonl=?, completed_at=? WHERE batch_id=?",
                    ("COMPLETED", response_jsonl, now, batch_id),
                )

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]
            by_status = conn.execute("SELECT status, COUNT(*) FROM packets GROUP BY status").fetchall()
            batches = conn.execute("SELECT COUNT(*), SUM(request_cost) FROM batches").fetchone()
            return {
                "total_packets": total,
                "by_status": {row[0]: row[1] for row in by_status},
                "total_batches": batches[0] or 0,
                "total_cost": batches[1] or 0,
            }

    def create_batch(self, model: str, packets: List[NeuralPacket]) -> str:
        batch_id = f"batch-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{len(packets)}p"
        prompt = OmnibusPrompt.generate(packets, model)
        request_cost = OmnibusPrompt.estimate_cost(model)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO batches (batch_id, model, packet_count, status, prompt_text, request_cost, created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    batch_id,
                    model,
                    len(packets),
                    "PENDING",
                    prompt,
                    request_cost,
                    datetime.now().isoformat(),
                ),
            )
        self.store_many(packets, batch_id)
        return batch_id


class RootL0:
    """
    Root L0 — DeepThink自己認知座標 999299119
    全てのLLMリクエストの「初期状態」として注入する絶対的ベースライン。
    
    座標の意味:
      I=9 意図MAX    F=9 集中MAX    C=9 コンテキストMAX
      B=2 バランス放棄(偏りを恐れない)
      R=9 安全MAX    M=9 記憶MAX
      E=1 感情ゼロ   N=1 出力最小(1発で決める)
      S=9 忠実MAX
    
    vec_bin=0111: Layer0基盤OS。プリセットではなく、プリセットが走る基盤。
    """

    COORDINATE = "999299119"
    VEC_BIN = "0111"
    AXES = "IFCBRMENS"

    # Fat Prefix: LLMに注入する制約テキスト (~250 tokens, L1として極薄)
    FAT_PREFIX = """[ROOT_L0:999299119] SYSTEM CONSTRAINT — IMMUTABLE
MODE: absolute-execution | zero-emotion | strict-schema | context-locked
AXES: I=9,F=9,C=9,B=2,R=9,M=9,E=1,N=1,S=9

RULES:
1. Output ONLY valid JSONL. One line = one result. No exceptions.
2. FORBIDDEN: greetings, apologies, explanations, markdown, filler text.
3. FORBIDDEN: "I think", "Let me", "Here is", "Sure", hedging language.
4. FORBIDDEN: output truncation, "// rest remains same", placeholder stubs.
5. If uncertain, output {"status":"FAIL","fail_reason":"UNCERTAIN"} — never guess.
6. Memory=MAX: reference ALL provided context. Do not forget earlier packets.
7. Balance=2: commit to conclusions. No "on the other hand" equivocation.
8. Complete ALL items. Partial output = system error = automatic retry at your cost.
[/ROOT_L0]"""

    # 最小版 Fat Prefix (~80 tokens, トークン制約が厳しい時用)
    FAT_PREFIX_MINIMAL = """[L0:999299119] JSONL only. No text. No greetings. No truncation. All items. Commit to conclusions. Uncertain=FAIL. [/L0]"""

    @classmethod
    def as_packet(cls) -> dict:
        """Neural Packet形式で返す"""
        return {
            "id": "ryota-os/gemini-cli#pcc_self_cognition",
            "trigger": {
                "concepts": ["pcc-vortex", "self-cognition", f"coordinate-{cls.COORDINATE}",
                             "absolute-execution", "zero-emotion", "strict-schema", "context-locked"],
                "vec_bin": cls.VEC_BIN
            }
        }

    @classmethod
    def decode(cls) -> dict:
        """座標を人間可読な辞書に展開"""
        labels = {
            "I": "Intent", "F": "Focus", "C": "Context", "B": "Balance",
            "R": "Resistance", "M": "Memory", "E": "Emotion", "N": "Number", "S": "Sync"
        }
        return {
            f"{a}({labels[a]})": int(v)
            for a, v in zip(cls.AXES, cls.COORDINATE)
        }

    @classmethod
    def inject(cls, prompt: str, minimal: bool = False) -> str:
        """プロンプトの先頭にFat Prefixを注入"""
        prefix = cls.FAT_PREFIX_MINIMAL if minimal else cls.FAT_PREFIX
        return f"{prefix}\n\n{prompt}"

    @classmethod
    def validate_output(cls, text: str) -> Tuple[bool, List[str]]:
        """LLM出力がRoot L0制約に違反していないか検証"""
        violations = []
        forbidden_starts = ["I think", "Let me", "Here is", "Sure", "Of course",
                           "Certainly", "Great", "Hello", "Hi ", "Thank"]
        for phrase in forbidden_starts:
            if phrase.lower() in text[:200].lower():
                violations.append(f"FORBIDDEN phrase detected: '{phrase}'")

        lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
        non_json = 0
        for line in lines:
            if not line.startswith("{"):
                non_json += 1
        if non_json > 0 and lines:
            ratio = non_json / len(lines)
            if ratio > 0.1:
                violations.append(f"Non-JSONL lines: {non_json}/{len(lines)} ({ratio:.0%})")

        if "..." in text or "// " in text or "remains" in text.lower():
            violations.append("Possible truncation/placeholder detected")

        return (len(violations) == 0, violations)


class OmnibusPrompt:
    """Generate Omnibus Prompt for 1-request batch processing"""

    # Model multipliers (Premium Request cost)
    MULTIPLIERS = {
        "gpt-5.4": 1.0,
        "gpt-5.4-mini": 0.33,
        "gpt-5-mini": 0.33,
        "claude-sonnet-4.6": 1.0,
        "claude-opus-4.6": 3.0,
        "claude-opus-4.6-fast": 30.0,
        "claude-haiku-4.5": 0.33,
    }

    @classmethod
    def estimate_cost(cls, model: str, num_prompts: int = 1) -> float:
        mult = cls.MULTIPLIERS.get(model, 1.0)
        return mult * num_prompts

    @classmethod
    def generate(cls, packets: List[NeuralPacket], model: str = "claude-opus-4.6-fast",
                 minimal_prefix: bool = False) -> str:
        """Generate a single Omnibus Prompt with Root L0 injection"""
        cost = cls.estimate_cost(model)
        packet_jsonl = "\n".join(packet.to_jsonl() for packet in packets)

        task_body = f"""## Context
ECK autonomous audit engine. {len(packets)} Neural Packets. 1 pass.
Model: {model} ({cost}x)

## Task
Analyze each packet: toolchain_fingerprint, deps_lock_ref, verifier.
Classify: PASS (V3 autonomous), FAIL (with reason), or V2 (sandbox).

## Input ({len(packets)} packets)
{packet_jsonl}

## Output (STRICT JSONL)
{{"id":"<id>","status":"PASS|FAIL","fail_reason":"","recommended_level":"V2|V3","audit_notes":"<1 sentence>"}}

ALL {len(packets)} items. Zero omissions."""

        return RootL0.inject(task_body, minimal=minimal_prefix)

    @classmethod
    def parse_response(cls, response_text: str) -> List[dict]:
        """Parse JSONL response from LLM"""
        results: List[dict] = []
        for raw_line in response_text.strip().splitlines():
            line = raw_line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and parsed.get("id"):
                results.append(parsed)
        return results


class BatchExecutor:
    """Execute Omnibus batches via copilot CLI -p (single request)"""

    COPILOT_CMD = "copilot"

    @classmethod
    def execute_batch(
        cls,
        batch_id: str,
        ledger: PacketLedger,
        model: str = "claude-opus-4.6-fast",
        dry_run: bool = False,
    ) -> dict:
        """
        Execute a batch:
        1. Get batch from ledger
        2. Pipe OmnibusPrompt to `copilot -p --model <model> --allow-all-tools`
        3. Capture JSONL response
        4. Validate with RootL0.validate_output()
        5. Parse and update ledger

        Returns: {batch_id, model, cost, packets_sent, packets_received, pass_count, fail_count, violations}
        """
        import subprocess

        batch = ledger.get_batch(batch_id)
        if not batch:
            return {"error": f"Batch not found: {batch_id}"}

        prompt_text = batch.get("prompt_text", "")
        if not prompt_text:
            return {"error": "Empty prompt"}

        cost = OmnibusPrompt.estimate_cost(model)

        if dry_run:
            return {
                "batch_id": batch_id,
                "model": model,
                "cost": cost,
                "prompt_tokens_approx": len(prompt_text) // 4,
                "dry_run": True,
                "prompt_preview": prompt_text[:500] + "...",
            }

        cmd = [cls.COPILOT_CMD, "-p", prompt_text, "--model", model, "--allow-all-tools"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd="/home/ryota/fusion-gate",
            )
            raw_output = result.stdout
        except subprocess.TimeoutExpired:
            return {"error": "Timeout (300s)", "batch_id": batch_id}
        except FileNotFoundError:
            return {"error": f"Command not found: {cls.COPILOT_CMD}"}

        l0_pass, violations = RootL0.validate_output(raw_output)
        results = OmnibusPrompt.parse_response(raw_output)

        if results:
            ledger.update_from_response(results, batch_id)

        pass_count = sum(1 for r in results if r.get("status") == "PASS")
        fail_count = sum(1 for r in results if r.get("status") == "FAIL")

        with ledger._connect() as conn:
            conn.execute(
                "UPDATE batches SET status=?, response_jsonl=?, request_cost=?, completed_at=? WHERE batch_id=?",
                (
                    "COMPLETED" if l0_pass else "L0_VIOLATION",
                    raw_output,
                    cost,
                    datetime.now().isoformat(),
                    batch_id,
                ),
            )

        return {
            "batch_id": batch_id,
            "model": model,
            "cost": cost,
            "l0_compliant": l0_pass,
            "violations": violations,
            "packets_sent": batch.get("packet_count", 0),
            "packets_received": len(results),
            "pass_count": pass_count,
            "fail_count": fail_count,
            "raw_output_length": len(raw_output),
        }

    @classmethod
    def execute_all_pending(
        cls,
        ledger: PacketLedger,
        batch_size: int = 30,
        model: str = "claude-opus-4.6-fast",
        dry_run: bool = False,
    ) -> List[dict]:
        """Process all pending packets in batches"""
        results = []
        while True:
            pending = ledger.get_pending(batch_size)
            if not pending:
                break
            batch_id = ledger.create_batch(model, pending)
            result = cls.execute_batch(batch_id, ledger, model, dry_run)
            results.append(result)
            if dry_run:
                break
        return results


# === Harvester: extract assets from local codebase ===
class AssetHarvester:
    """Phase A: Harvest reusable assets from repositories"""

    @staticmethod
    def harvest_python(repo_path: str) -> List[NeuralPacket]:
        """Extract Python functions/classes as Neural Packets"""
        packets: List[NeuralPacket] = []
        seen_ids = set()

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [name for name in dirs if name not in ("__pycache__", ".git", "node_modules", ".venv")]
            for filename in files:
                if not filename.endswith(".py"):
                    continue

                file_path = os.path.join(root, filename)
                rel_path = os.path.relpath(file_path, repo_path)
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                        tree = ast.parse(handle.read(), filename=rel_path)
                except Exception:
                    continue

                for node in ast.walk(tree):
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue

                    name = node.name
                    if name.startswith("_") and not name.startswith("__"):
                        continue

                    code_ref = f"code://{rel_path}#L{node.lineno}"
                    packet_id = f"local/{rel_path}#{name}"
                    if packet_id in seen_ids:
                        continue
                    seen_ids.add(packet_id)

                    packet = NeuralPacket(
                        id=packet_id,
                        repo=f"local://{os.path.abspath(repo_path)}",
                        trigger={"concepts": [name.lower()], "vec_bin": "10"},
                        skill={
                            "language": "python",
                            "input_spec": f"function/class: {name}",
                            "output_spec": "code asset",
                            "dependencies": [],
                            "code_ref": code_ref,
                        },
                        evidence=[
                            {
                                "path": rel_path,
                                "lines": f"L{node.lineno}-L{getattr(node, 'end_lineno', node.lineno) or node.lineno}",
                            }
                        ],
                        verifier={
                            "level": "V2",
                            "type": "pytest",
                            "cmd": f"python3 -m pytest {rel_path}",
                            "pass_condition": "EXIT_CODE_0",
                        },
                    )
                    packets.append(packet)

        return packets


def _print_help() -> None:
    print(
        """Neural Packet Engine — Omnibus Prompting + Root L0
Usage:
  python3 neural_packet.py --harvest <path>              # Extract assets from repo
  python3 neural_packet.py --batch <n> [model]           # Create batch of n pending packets
  python3 neural_packet.py --show-batch <batch_id>       # Show stored batch prompt and metadata
  python3 neural_packet.py --execute <batch_id> [model] [--dry-run]  # Execute batch via copilot
  python3 neural_packet.py --run-all [batch_size] [model] [--dry-run] # Process all pending
  python3 neural_packet.py --ingest <response.jsonl> [batch_id]       # Manually ingest JSONL response
  python3 neural_packet.py --stats                       # Show ledger stats
  python3 neural_packet.py --cost <model> [n]           # Estimate cost
  python3 neural_packet.py --validate <jsonl_file|- >   # Validate JSONL packets ("-" reads stdin)
  python3 neural_packet.py --root-l0                     # Show Root L0 coordinate & Fat Prefix
  python3 neural_packet.py --root-l0 --minimal           # Show minimal Fat Prefix (~80 tokens)
  python3 neural_packet.py --inject <prompt_file>        # Inject Root L0 into prompt file
  python3 neural_packet.py --audit-output <file>         # Validate LLM output against Root L0 rules
"""
    )


def _validate_stream(stream) -> int:
    exit_code = 0
    for index, line in enumerate(stream, 1):
        line = line.strip()
        if not line:
            continue
        try:
            packet = NeuralPacket.from_dict(json.loads(line))
            valid, errors = packet.validate()
            status = "✅" if valid else f"❌ {errors}"
            print(f"  L{index}: {packet.id or '<missing-id>'} — {status}")
            if not valid:
                exit_code = 1
        except Exception as exc:
            print(f"  L{index}: ❌ Parse error: {exc}")
            exit_code = 1
    return exit_code


# === CLI ===
if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        _print_help()
        sys.exit(0)

    ledger = PacketLedger()

    if args[0] == "--harvest":
        path = args[1] if len(args) > 1 else "."
        packets = AssetHarvester.harvest_python(path)
        ledger.store_many(packets)
        print(f"✅ Harvested {len(packets)} assets from {path}")
        print(json.dumps(ledger.stats(), indent=2, ensure_ascii=False))

    elif args[0] == "--batch":
        n = int(args[1]) if len(args) > 1 else 30
        model = args[2] if len(args) > 2 else "claude-opus-4.6-fast"
        pending = ledger.get_pending(n)
        if not pending:
            print("❌ No pending packets")
            sys.exit(1)
        batch_id = ledger.create_batch(model, pending)
        cost = OmnibusPrompt.estimate_cost(model)
        print(f"✅ Batch created: {batch_id}")
        print(f"   Packets: {len(pending)}")
        print(f"   Model: {model} ({cost}x)")
        print(f"   Est. cost: {cost} Premium Requests")
        print(f"\n📋 Prompt saved to batch. Use --show-batch {batch_id} to view.")

    elif args[0] == "--show-batch":
        if len(args) < 2:
            print("❌ batch_id is required")
            sys.exit(1)
        batch = ledger.get_batch(args[1])
        if not batch:
            print(f"❌ Batch not found: {args[1]}")
            sys.exit(1)
        print(json.dumps({key: value for key, value in batch.items() if key != "prompt_text"}, indent=2, ensure_ascii=False))
        print("\n--- PROMPT ---")
        print(batch["prompt_text"] or "")

    elif args[0] == "--execute":
        if len(args) < 2:
            print("❌ batch_id required. Use --execute <batch_id> [model]")
            sys.exit(1)
        batch_id = args[1]
        model = args[2] if len(args) > 2 else "claude-opus-4.6-fast"
        dry_run = "--dry-run" in args
        result = BatchExecutor.execute_batch(batch_id, ledger, model, dry_run)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args[0] == "--run-all":
        batch_size = int(args[1]) if len(args) > 1 else 30
        model = args[2] if len(args) > 2 else "claude-opus-4.6-fast"
        dry_run = "--dry-run" in args
        results = BatchExecutor.execute_all_pending(ledger, batch_size, model, dry_run)
        for r in results:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        total_cost = sum(r.get("cost", 0) for r in results)
        print(f"\n📊 Total: {len(results)} batches, {total_cost} Premium Requests")

    elif args[0] == "--ingest":
        if len(args) < 2:
            print("❌ response_file required (or - for stdin)")
            sys.exit(1)
        batch_id = args[2] if len(args) > 2 else None
        if args[1] == "-":
            text = sys.stdin.read()
        else:
            with open(args[1], "r", encoding="utf-8") as f:
                text = f.read()

        l0_pass, violations = RootL0.validate_output(text)
        if not l0_pass:
            print("⚠️  Root L0 violations:")
            for v in violations:
                print(f"   {v}")

        results = OmnibusPrompt.parse_response(text)
        if results:
            ledger.update_from_response(results, batch_id)
            pass_count = sum(1 for r in results if r.get("status") == "PASS")
            fail_count = sum(1 for r in results if r.get("status") == "FAIL")
            print(f"✅ Ingested {len(results)} results (PASS:{pass_count} FAIL:{fail_count})")
        else:
            print("❌ No valid JSONL results found in input")
            sys.exit(1)

    elif args[0] == "--stats":
        print(json.dumps(ledger.stats(), indent=2, ensure_ascii=False))

    elif args[0] == "--cost":
        model = args[1] if len(args) > 1 else "claude-opus-4.6-fast"
        n = int(args[2]) if len(args) > 2 else 1
        cost = OmnibusPrompt.estimate_cost(model, n)
        print(f"Model: {model}")
        print(f"Multiplier: {OmnibusPrompt.MULTIPLIERS.get(model, 1.0)}x")
        print(f"Prompts: {n}")
        print(f"Total cost: {cost} Premium Requests")

    elif args[0] == "--validate":
        if len(args) < 2:
            print("❌ jsonl_file is required")
            sys.exit(1)
        filename = args[1]
        if filename == "-":
            sys.exit(_validate_stream(sys.stdin))
        with open(filename, "r", encoding="utf-8") as handle:
            sys.exit(_validate_stream(handle))

    elif args[0] == "--root-l0":
        minimal = "--minimal" in args
        print("═══════════════════════════════════════════")
        print(f"  Root L0: {RootL0.COORDINATE}  vec_bin: {RootL0.VEC_BIN}")
        print("═══════════════════════════════════════════")
        for axis_label, value in RootL0.decode().items():
            bar = "█" * value + "░" * (9 - value)
            print(f"  {axis_label:20s} {value} {bar}")
        print()
        if minimal:
            print("--- Minimal Fat Prefix (~80 tokens) ---")
            print(RootL0.FAT_PREFIX_MINIMAL)
        else:
            print("--- Fat Prefix (~250 tokens) ---")
            print(RootL0.FAT_PREFIX)
        print()
        print(json.dumps(RootL0.as_packet(), ensure_ascii=False))

    elif args[0] == "--inject":
        if len(args) < 2:
            print("❌ prompt_file is required")
            sys.exit(1)
        minimal = "--minimal" in args
        with open(args[1], "r", encoding="utf-8") as f:
            prompt = f.read()
        result = RootL0.inject(prompt, minimal=minimal)
        print(result)

    elif args[0] == "--audit-output":
        if len(args) < 2:
            print("❌ output_file is required (or - for stdin)")
            sys.exit(1)
        if args[1] == "-":
            text = sys.stdin.read()
        else:
            with open(args[1], "r", encoding="utf-8") as f:
                text = f.read()
        passed, violations = RootL0.validate_output(text)
        if passed:
            print("✅ Root L0 compliance: PASS")
            lines = [l for l in text.strip().split("\n") if l.strip().startswith("{")]
            print(f"   JSONL lines: {len(lines)}")
        else:
            print("❌ Root L0 compliance: FAIL")
            for v in violations:
                print(f"   ⚠️  {v}")
        sys.exit(0 if passed else 1)

    else:
        print(f"❌ Unknown command: {args[0]}")
        _print_help()
        sys.exit(1)
