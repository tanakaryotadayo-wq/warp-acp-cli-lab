"""
ECK Bridge — Connects ECK Engine validation to fusion-gate Neural Packets
Auto-Recovery Patchflow: FAIL packets → ECK drift analysis → correction proposals
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _resolve_fusion_gate_root() -> Path:
    """Resolve the live fusion-gate checkout instead of assuming a Linux-only path."""
    env_root = os.getenv("FUSION_GATE_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _resolve_eck_root(fusion_gate_root: Path) -> Path:
    """Locate an ECK checkout from env or common local archive/revival locations."""
    env_root = os.getenv("ECK_ROOT")
    home = Path.home()
    candidates: List[Path] = []

    if env_root:
        candidates.append(Path(env_root).expanduser())

    candidates.extend(
        [
            fusion_gate_root / "eck-engine",
            fusion_gate_root.parent / "eck-engine",
            home / "俺のフュージョンゲートまとめ" / "eck-engine",
            home / "俺のフュージョンゲートまとめ" / "復活のフュージョンゲート" / "eck-engine",
            home / "eck-engine",
            home / "Documents" / "ECK",
        ]
    )

    for candidate in candidates:
        runner = candidate / "src" / "eck_engine" / "runner.py"
        spec = candidate / "spec" / "vomega_inf_spec_v1.json"
        if runner.exists() and spec.exists():
            return candidate.resolve()

    if env_root:
        return Path(env_root).expanduser().resolve()
    return (fusion_gate_root / "eck-engine").resolve()


# Paths
FUSION_GATE_ROOT = _resolve_fusion_gate_root()
ECK_ROOT = _resolve_eck_root(FUSION_GATE_ROOT)
ECK_RUNNER_PATH = Path(os.getenv("ECK_RUNNER_PATH", str(ECK_ROOT / "src" / "eck_engine" / "runner.py"))).expanduser().resolve()
ECK_SPEC_PATH = Path(os.getenv("ECK_SPEC_PATH", str(ECK_ROOT / "spec" / "vomega_inf_spec_v1.json"))).expanduser().resolve()


def _load_eck_runner():
    """gate.py の httpx 依存を避けて runner.py を直接ロードする"""
    if not ECK_RUNNER_PATH.exists():
        raise RuntimeError(f"ECK runner not found: {ECK_RUNNER_PATH}")
    spec = importlib.util.spec_from_file_location("eck_runner", str(ECK_RUNNER_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"ECK runner load failed: {ECK_RUNNER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_eck = None


def eck():
    """ECK runner を遅延ロードする"""
    global _eck
    if _eck is None:
        _eck = _load_eck_runner()
    return _eck


@dataclass
class ECKResult:
    """ECK 判定結果 + Neural Packet 文脈"""

    packet_id: str
    eck_status: str
    drift: float
    layers_passed: List[str]
    errors: List[str]
    proposal: Optional[Dict] = None
    archive_path: Optional[str] = None

    def to_dict(self) -> dict:
        """CLI 出力向け辞書化"""
        return asdict(self)


class ECKBridge:
    """Neural Packet Engine と ECK validation を接続するブリッジ"""

    def __init__(self, eck_spec_path: Optional[str] = None, archive_dir: Optional[str] = None):
        self.eck_root = ECK_ROOT
        self.runs_dir = self.eck_root / "runs"
        self.eck_runner_path = ECK_RUNNER_PATH
        self.eck_spec_path = Path(eck_spec_path).expanduser().resolve() if eck_spec_path else ECK_SPEC_PATH
        if not self.eck_spec_path.exists():
            raise RuntimeError(f"ECK spec not found: {self.eck_spec_path}")
        self.spec = json.loads(self.eck_spec_path.read_text(encoding="utf-8"))

        # runner.py は相対 archive/ を前提にしているため、既定は fusion-gate/archive に合わせる。
        self.archive_dir = Path(archive_dir) if archive_dir else (FUSION_GATE_ROOT / "archive")
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        self._run_count = 0
        self._genesis_done = False
        self._sync_persisted_state()

    @contextmanager
    def _runner_cwd(self):
        """runner.py の archive 相対参照を安定させる"""
        prev_cwd = Path.cwd()
        os.chdir(FUSION_GATE_ROOT)
        try:
            yield
        finally:
            os.chdir(prev_cwd)

    def validate_packet(self, packet: dict, previous: Optional[dict] = None) -> ECKResult:
        """
        Neural Packet を ECK の 6-layer pipeline で検証する。

        Args:
            packet: NeuralPacket.to_dict() または JSONL 由来の dict
            previous: 直前のベースライン packet
        """
        packet_id = packet.get("id", "unknown")
        is_first = previous is None and not self._genesis_done

        eck_input = self._packet_to_eck_input(packet, previous=previous)
        prev_eck = self._packet_to_eck_input(previous) if previous else None

        with self._runner_cwd():
            result = eck().run_vomega(eck_input, self.spec, is_first_run=is_first)

        if is_first:
            self._genesis_done = True

        status = result.get("status", "FAIL")
        trace = result.get("trace", [])
        layers_passed = [item.get("layer", "?") for item in trace if item.get("verdict") == "PASS"]
        errors = list(result.get("errors", []))

        drift = 0.0
        sigma_trace = next((item for item in trace if item.get("layer") == "Σ∞"), None)
        if sigma_trace:
            drift = float(sigma_trace.get("drift", 0.0))
        elif prev_eck is not None:
            drift = float(
                eck().compute_structural_drift(
                    eck().flatten(prev_eck),
                    eck().flatten(eck_input),
                )
            )

        proposal = None
        if status == "FORK" or result.get("fork"):
            proposal = self._generate_recovery(packet, previous, drift)

        archive_path = None
        self._run_count += 1
        run_id = f"np-{self._sanitize_packet_id(packet_id)}-{self._run_count}"
        try:
            archive_path = self._archive_result(run_id, result, eck_input)
        except Exception:
            archive_path = None
        self._sync_persisted_state()

        return ECKResult(
            packet_id=packet_id,
            eck_status=status,
            drift=drift,
            layers_passed=layers_passed,
            errors=errors,
            proposal=proposal,
            archive_path=archive_path,
        )

    def validate_batch(self, packets: List[dict], baseline: Optional[dict] = None) -> List[ECKResult]:
        """バッチ検証。PASS した packet を次のベースラインとして連鎖させる"""
        results: List[ECKResult] = []
        prev = baseline
        for packet in packets:
            result = self.validate_packet(packet, previous=prev)
            results.append(result)
            if result.eck_status == "PASS":
                prev = packet
        return results

    def check_drift(self, current: dict, baseline: dict) -> Tuple[float, str]:
        """フル pipeline を通さず structural drift だけを即時計算する"""
        current_flat = eck().flatten(self._packet_to_eck_input(current))
        baseline_flat = eck().flatten(self._packet_to_eck_input(baseline))
        drift = float(eck().compute_structural_drift(baseline_flat, current_flat))
        max_drift = self.spec.get("validation", {}).get("max_drift_per_cycle", 0.2)
        verdict = "FORK" if drift > max_drift else "PASS"
        return drift, verdict

    def protect_root_l0(self, packet: dict) -> bool:
        """
        Root L0 (999299119) の自己申告 packet が改ざんされていないか検証する。
        """
        trigger = packet.get("trigger", {})
        concepts = trigger.get("concepts", [])

        claims_root = "self-cognition" in concepts or any(
            str(item).startswith("coordinate-") for item in concepts
        )
        if not claims_root:
            return True

        if "coordinate-999299119" not in concepts:
            return False
        if trigger.get("vec_bin") != "0111":
            return False

        axes = trigger.get("axes")
        if axes is not None:
            expected_axes = {"I": 9, "F": 9, "C": 9, "B": 2, "R": 9, "M": 9, "E": 1, "N": 1, "S": 9}
            if axes != expected_axes:
                return False

        return True

    def stats(self) -> dict:
        """Bridge の簡易統計"""
        archive_count = self._persistent_archive_count()
        eck_run_count = self._persistent_eck_run_count()
        persisted_runs = self._sync_persisted_state()
        return {
            "runs": self._run_count,
            "persisted_runs": persisted_runs,
            "genesis_done": self._genesis_done,
            "archive_count": archive_count,
            "eck_runs": eck_run_count,
            "fusion_gate_root": str(FUSION_GATE_ROOT),
            "eck_root": str(self.eck_root),
            "runs_dir": str(self.runs_dir),
            "archive_dir": str(self.archive_dir),
            "eck_runner_path": str(self.eck_runner_path),
            "eck_spec_path": str(self.eck_spec_path),
            "eck_spec_version": self.spec.get("version"),
            "max_drift": self.spec.get("validation", {}).get("max_drift_per_cycle"),
        }

    def cleanup_archives(self, keep: Optional[set[str]] = None) -> int:
        """archive_dir 配下の JSON archive を削除する"""
        keep = keep or set()
        removed = 0
        if not self.archive_dir.exists():
            return 0
        for path in self.archive_dir.iterdir():
            if path.suffix != ".json":
                continue
            if path.name in keep:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def _packet_to_eck_input(self, packet: Optional[dict], previous: Optional[dict] = None) -> dict:
        """Neural Packet → ECK input 変換"""
        if packet is None:
            return {}

        nodes: List[dict] = []

        if packet.get("trigger"):
            nodes.append({"type": "intent", "value": json.dumps(packet["trigger"], ensure_ascii=False, sort_keys=True)})
        if packet.get("skill"):
            nodes.append({"type": "function", "value": json.dumps(packet["skill"], ensure_ascii=False, sort_keys=True)})
        if packet.get("exec_profile"):
            nodes.append(
                {"type": "structure", "value": json.dumps(packet["exec_profile"], ensure_ascii=False, sort_keys=True)}
            )
        if packet.get("verifier"):
            nodes.append({"type": "constraint", "value": json.dumps(packet["verifier"], ensure_ascii=False, sort_keys=True)})
        metadata = {
            "license": packet.get("license", ""),
            "toolchain_fingerprint": packet.get("toolchain_fingerprint", ""),
            "deps_lock_ref": packet.get("deps_lock_ref", ""),
            "model_fingerprint": packet.get("model_fingerprint", ""),
            "verifier_log_ref": packet.get("verifier_log_ref", ""),
            "notes": packet.get("notes", ""),
        }
        supplemental = {}
        if packet.get("evidence"):
            supplemental["evidence"] = packet["evidence"]
        if packet.get("kv"):
            supplemental["acceleration"] = packet["kv"]
        if any(metadata.values()):
            supplemental["metadata"] = metadata
        if supplemental:
            nodes.append({"type": "structure", "value": json.dumps(supplemental, ensure_ascii=False, sort_keys=True)})

        nodes.append(
            {
                "type": "output",
                "value": json.dumps(
                    {
                        "id": packet.get("id", "unknown"),
                        "status": packet.get("status", "PENDING"),
                        "fail_reason": packet.get("fail_reason", ""),
                        "license": packet.get("license", ""),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            }
        )

        data = {
            "kernel_id": self.spec["kernel_id"],
            "nodes": nodes,
        }

        if packet.get("_parent_hash"):
            data["parent_hash"] = packet["_parent_hash"]
        elif previous:
            data["parent_hash"] = eck().hash_obj(self._packet_to_eck_input(previous))

        if previous:
            data["previous"] = self._packet_to_eck_input(previous)

        return data

    def _generate_recovery(self, packet: dict, previous: Optional[dict], drift: float) -> dict:
        """FORK / drift 超過時の自動回復提案を作る"""
        max_drift = self.spec.get("validation", {}).get("max_drift_per_cycle", 0.2)
        suggestions: List[dict] = []

        if previous:
            prev_flat = eck().flatten(self._packet_to_eck_input(previous))
            curr_flat = eck().flatten(self._packet_to_eck_input(packet))

            for key in sorted(set(prev_flat.keys()) | set(curr_flat.keys())):
                prev_val = prev_flat.get(key)
                curr_val = curr_flat.get(key)
                if prev_val == curr_val:
                    continue
                if key not in prev_flat:
                    suggestions.append({"action": "REMOVE_FIELD", "field": key})
                elif key not in curr_flat:
                    suggestions.append({"action": "RESTORE_FIELD", "field": key, "to": str(prev_val)})
                else:
                    suggestions.append(
                        {
                            "action": "REVERT_FIELD",
                            "field": key,
                            "from": str(curr_val),
                            "to": str(prev_val),
                        }
                    )

        return {
            "type": "auto_recovery",
            "packet_id": packet.get("id"),
            "current_drift": drift,
            "max_allowed": max_drift,
            "suggestions_count": len(suggestions),
            "suggestions": suggestions[:10],
            "risk": min(1.0, len(suggestions) * 0.1),
        }

    def _archive_result(self, run_id: str, result: dict, eck_input: dict) -> Optional[str]:
        """runner.py の create_archive を使って archive を作り、必要なら所定位置へ移す"""
        with self._runner_cwd():
            raw_path, _ = eck().create_archive(
                run_id,
                result,
                self.spec,
                eck_input,
                str(self.eck_spec_path),
                str(self.eck_runner_path),
            )

        if not raw_path:
            return None

        raw_archive_path = (FUSION_GATE_ROOT / raw_path).resolve()
        target_path = (self.archive_dir / Path(raw_path).name).resolve()

        if raw_archive_path != target_path:
            self.archive_dir.mkdir(parents=True, exist_ok=True)
            raw_archive_path.replace(target_path)
            self._cleanup_empty_archive_dir(raw_archive_path.parent)
        else:
            target_path = raw_archive_path

        return str(target_path)

    def _cleanup_empty_archive_dir(self, archive_root: Path) -> None:
        """空になった一時 archive ディレクトリを消す"""
        try:
            if archive_root == self.archive_dir:
                return
            if archive_root.exists() and not any(archive_root.iterdir()):
                archive_root.rmdir()
        except OSError:
            pass

    @staticmethod
    def _sanitize_packet_id(packet_id: str) -> str:
        """run_id 用に packet id を安全化する"""
        return (
            packet_id.replace("/", "_")
            .replace("#", "_")
            .replace(":", "_")
            .replace(" ", "_")
        )

    def _persistent_archive_count(self) -> int:
        if not self.archive_dir.is_dir():
            return 0
        return len([path for path in self.archive_dir.iterdir() if path.suffix == ".json"])

    def _persistent_eck_run_count(self) -> int:
        if not self.runs_dir.is_dir():
            return 0
        return len([path for path in self.runs_dir.iterdir() if path.is_dir()])

    def _sync_persisted_state(self) -> int:
        persisted_runs = self._persistent_archive_count() + self._persistent_eck_run_count()
        if persisted_runs > self._run_count:
            self._run_count = persisted_runs
        if persisted_runs > 0:
            self._genesis_done = True
        return persisted_runs


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _snapshot_archive(dir_path: Path) -> set[str]:
    if not dir_path.exists():
        return set()
    return {path.name for path in dir_path.iterdir() if path.suffix == ".json"}


def _self_test() -> int:
    bridge = ECKBridge()
    print("ECK Bridge Self-Test")
    print("=" * 50)

    passed = 0
    total = 0
    archive_before = _snapshot_archive(bridge.archive_dir)

    def T(name: str, cond: bool):
        nonlocal passed, total
        total += 1
        if cond:
            passed += 1
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ {name}")

    try:
        try:
            runner_mod = eck()
            T("ECK runner loads", runner_mod is not None)
        except Exception as ex:
            T(f"ECK runner loads ({ex})", False)

        T("ECK spec loads", bridge.spec.get("kernel_id") == "vomega-inf-001")

        test_pkt = {
            "id": "test/self-test#001",
            "status": "PENDING",
            "trigger": {"concepts": ["self-test"], "vec_bin": "0000"},
            "skill": {"language": "python"},
        }
        result = bridge.validate_packet(test_pkt)
        T(f"Genesis validation: {result.eck_status}", result.eck_status == "PASS")
        T("6 layers executed", len(result.layers_passed) >= 5)

        pkt_a = {"id": "test/a", "trigger": {"concepts": ["a"], "vec_bin": "0000"}}
        pkt_b = {
            "id": "test/b",
            "trigger": {"concepts": ["completely-different", "new-concept"], "vec_bin": "1111"},
            "skill": {"language": "rust"},
        }
        drift, verdict = bridge.check_drift(pkt_b, pkt_a)
        T(f"Drift detection: {drift:.3f} → {verdict}", drift > 0.0)

        good_l0 = {
            "trigger": {
                "concepts": ["self-cognition", "coordinate-999299119"],
                "vec_bin": "0111",
                "axes": {"I": 9, "F": 9, "C": 9, "B": 2, "R": 9, "M": 9, "E": 1, "N": 1, "S": 9},
            }
        }
        bad_l0 = {
            "trigger": {
                "concepts": ["self-cognition", "coordinate-111111111"],
                "vec_bin": "0111",
            }
        }
        T("Root L0 valid", bridge.protect_root_l0(good_l0))
        T("Root L0 tampered", not bridge.protect_root_l0(bad_l0))

        batch = [
            {"id": "batch/1", "status": "PASS", "trigger": {"concepts": ["a"], "vec_bin": "0001"}},
            {"id": "batch/2", "status": "PASS", "trigger": {"concepts": ["a", "b"], "vec_bin": "0010"}},
            {"id": "batch/3", "status": "PASS", "trigger": {"concepts": ["a", "b", "c"], "vec_bin": "0011"}},
        ]
        results = bridge.validate_batch(batch)
        T(f"Batch: {len(results)} results", len(results) == 3)
        T("Batch statuses valid", all(item.eck_status in {"PASS", "FORK", "FAIL"} for item in results))

        stats = bridge.stats()
        T(f"Stats: {stats['runs']} runs", stats["runs"] > 0)
    finally:
        created = _snapshot_archive(bridge.archive_dir) - archive_before
        cleaned = bridge.cleanup_archives(keep=archive_before)
        if created:
            print(f"  🧹 Cleaned archives: {cleaned}")

    print()
    print("=" * 50)
    print(f"  ECK Bridge: {passed}/{total} PASS")
    if passed == total:
        print("  ✅ BRIDGE FULLY OPERATIONAL")
        print("=" * 50)
        return 0

    print(f"  ⚠️  {total - passed} FAILURES")
    print("=" * 50)
    return 1


def _print_help() -> None:
    print(
        """ECK Bridge — Neural Packet × ECK Validation
Usage:
  python3 eck_bridge.py --validate <packet.json>       # 単体 packet を検証
  python3 eck_bridge.py --validate-batch <file.jsonl>  # JSONL バッチを検証
  python3 eck_bridge.py --drift <baseline.json> <current.json>  # drift を即時計算
  python3 eck_bridge.py --check-l0 <packet.json>       # Root L0 整合性チェック
  python3 eck_bridge.py --self-test                    # 自己診断
  python3 eck_bridge.py --stats                        # 統計表示
"""
    )


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        _print_help()
        sys.exit(0)

    bridge = ECKBridge()

    if args[0] == "--self-test":
        sys.exit(_self_test())

    if args[0] == "--validate" and len(args) > 1:
        packet = _load_json(args[1])
        result = bridge.validate_packet(packet)
        print(
            json.dumps(
                {
                    "packet_id": result.packet_id,
                    "eck_status": result.eck_status,
                    "drift": result.drift,
                    "layers": result.layers_passed,
                    "errors": result.errors,
                    "proposal": result.proposal,
                    "archive_path": result.archive_path,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        sys.exit(0)

    if args[0] == "--validate-batch" and len(args) > 1:
        lines = Path(args[1]).read_text(encoding="utf-8").splitlines()
        packets = [json.loads(line) for line in lines if line.strip()]
        results = bridge.validate_batch(packets)
        for result in results:
            print(
                json.dumps(
                    {
                        "id": result.packet_id,
                        "status": result.eck_status,
                        "drift": round(result.drift, 4),
                    },
                    ensure_ascii=False,
                )
            )
        sys.exit(0)

    if args[0] == "--drift" and len(args) > 2:
        baseline = _load_json(args[1])
        current = _load_json(args[2])
        drift, verdict = bridge.check_drift(current, baseline)
        print(
            json.dumps(
                {
                    "drift": round(drift, 4),
                    "verdict": verdict,
                    "max": bridge.spec["validation"]["max_drift_per_cycle"],
                },
                ensure_ascii=False,
            )
        )
        sys.exit(0)

    if args[0] == "--check-l0" and len(args) > 1:
        packet = _load_json(args[1])
        print(json.dumps({"root_l0_intact": bridge.protect_root_l0(packet)}, ensure_ascii=False))
        sys.exit(0)

    if args[0] == "--stats":
        print(json.dumps(bridge.stats(), ensure_ascii=False, indent=2))
        sys.exit(0)

    print(f"Unknown: {args[0]}")
    sys.exit(1)
