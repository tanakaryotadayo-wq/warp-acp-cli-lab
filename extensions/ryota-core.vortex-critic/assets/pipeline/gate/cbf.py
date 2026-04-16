#!/usr/bin/env python3
"""
CBF (Coordinate Build Framework) v1.3 — 状態制御カーネル
=========================================================
ビルドプロセスを3次元座標空間として定義し、逸脱を検知・制御する。

役割: 測定 → 判定 → 記録
非役割: 判断しない、提案しない、代替案を出さない

3軸座標系:
  X = Stage (工程): 1-Planning, 2-Design, 3-Implementation, 4-Integration, 5-Deployment
  Y = Layer (階層深度): 1-Core, 2-Module, 3-Function, n-任意
  Z = Stability (安定度): 0-10 検証状態

座標表記: [X.Y.Z] 例: [3.2.5] = Implementation, Module level, 部分検証済み
"""
import http.server
import json
import os
import signal
import socketserver
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

MODULE_DIR = Path(__file__).resolve().parent
CBF_CONFIG_PATH = os.getenv("CBF_CONFIG", "coordinate_build_map.yaml")
CBF_LOG_PATH = os.getenv("CBF_LOG", "DriftLogger.json")
CBF_DB_PATH = os.getenv("CBF_DB", os.path.expanduser("~/.cbf-history.db"))
STAGE_NAMES = {1: "Planning", 2: "Design", 3: "Implementation", 4: "Integration", 5: "Deployment"}
DEFAULT_CONFIG = {
    "framework": {
        "name": "Coordinate Build Framework",
        "version": "1.3",
        "threshold": 5.0,
        "weights": {"x": 3.0, "y": 1.0, "z": 0.3},
    },
    "stages": STAGE_NAMES,
}
STATUS_ICONS = {"stable": "✓", "auto-corrected": "⚠️", "drift": "⛔"}


def _log(message: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [CBF] {message}", file=sys.stderr)


def _to_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_path(value: Optional[str], default_name: str) -> Path:
    path = Path(value or default_name).expanduser()
    return path if path.is_absolute() else (MODULE_DIR / path).resolve()


def _ensure_parent(path: Path) -> None:
    if path.parent and not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)


@dataclass
class Coordinate:
    x: int
    y: int
    z: int

    def __post_init__(self) -> None:
        self.x, self.y, self.z = int(self.x), int(self.y), int(self.z)

    def __str__(self) -> str:
        return f"[{self.x}.{self.y}.{self.z}]"

    @property
    def stage_name(self) -> str:
        return STAGE_NAMES.get(self.x, f"Stage-{self.x}")

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "z": self.z, "text": str(self), "stage_name": self.stage_name}

    @classmethod
    def from_dict(cls, payload: dict) -> "Coordinate":
        if not isinstance(payload, dict):
            raise ValueError("coordinate payload must be object")
        return cls(_to_int(payload.get("x")), _to_int(payload.get("y")), _to_int(payload.get("z")))

    @classmethod
    def from_line(cls, line: str) -> Tuple["Coordinate", str]:
        """stdin の1行を座標へ変換する。"""
        text = line.strip()
        if not text:
            raise ValueError("empty line")
        if text.startswith("{"):
            payload = json.loads(text)
            return cls.from_dict(payload), str(payload.get("task", ""))
        parts = [p.strip() for p in text.split(",")] if "," in text else text.split()
        if len(parts) < 3:
            raise ValueError("expected x y z")
        return cls(parts[0], parts[1], parts[2]), " ".join(parts[3:]).strip()


class Status(str, Enum):
    STABLE = "stable"
    AUTO_CORRECTED = "auto-corrected"
    DRIFT = "drift"

    @property
    def icon(self) -> str:
        return STATUS_ICONS[self.value]


@dataclass
class DriftEvent:
    timestamp: str
    expected: str
    actual: str
    corrected: str
    delta: float
    task: str
    status: str
    session_id: str = ""
    meta: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "expected": self.expected,
            "actual": self.actual,
            "corrected": self.corrected,
            "delta": round(float(self.delta), 2),
            "task": self.task,
            "status": self.status,
            "session_id": self.session_id,
            "meta": dict(self.meta),
        }


class CBFHistory:
    """SQLite 永続履歴。"""

    def __init__(self, db_path: str = CBF_DB_PATH):
        self.db_path = str(Path(db_path).expanduser())
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        _ensure_parent(Path(self.db_path))
        sql = (
            "CREATE TABLE IF NOT EXISTS drift_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "session_id TEXT NOT NULL,timestamp TEXT NOT NULL,expected TEXT NOT NULL,"
            "actual TEXT NOT NULL,corrected TEXT NOT NULL,delta REAL NOT NULL,"
            "task TEXT NOT NULL,status TEXT NOT NULL,meta_json TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS idx_cbf_status ON drift_events(status);"
            "CREATE INDEX IF NOT EXISTS idx_cbf_session ON drift_events(session_id, timestamp);"
        )
        with self._conn() as conn:
            conn.executescript(sql)

    def record(self, event: DriftEvent) -> None:
        payload = event.to_dict()
        sql = (
            "INSERT INTO drift_events "
            "(session_id,timestamp,expected,actual,corrected,delta,task,status,meta_json) "
            "VALUES (?,?,?,?,?,?,?,?,?)"
        )
        args = (
            payload["session_id"], payload["timestamp"], payload["expected"], payload["actual"],
            payload["corrected"], payload["delta"], payload["task"], payload["status"],
            json.dumps(payload["meta"], ensure_ascii=False, sort_keys=True),
        )
        with self._lock, self._conn() as conn:
            conn.execute(sql, args)
            conn.commit()

    def get_recent(self, limit: int = 20) -> List[dict]:
        sql = (
            "SELECT session_id,timestamp,expected,actual,corrected,delta,task,status,meta_json "
            "FROM drift_events ORDER BY id DESC LIMIT ?"
        )
        with self._conn() as conn:
            rows = conn.execute(sql, (int(limit),)).fetchall()
        items: List[dict] = []
        for row in rows:
            try:
                meta = json.loads(row[8] or "{}")
            except json.JSONDecodeError:
                meta = {}
            items.append(
                {
                    "session_id": row[0], "timestamp": row[1], "expected": row[2], "actual": row[3],
                    "corrected": row[4], "delta": row[5], "task": row[6], "status": row[7], "meta": meta,
                }
            )
        return items

    def get_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM drift_events").fetchone()[0]
            avg_delta = conn.execute("SELECT COALESCE(AVG(delta),0) FROM drift_events").fetchone()[0]
            counts = conn.execute("SELECT status,COUNT(*) FROM drift_events GROUP BY status").fetchall()
            patterns = conn.execute(
                "SELECT expected || ' -> ' || actual, COUNT(*) FROM drift_events "
                "GROUP BY expected, actual ORDER BY COUNT(*) DESC, expected ASC, actual ASC LIMIT 5"
            ).fetchall()
        return {
            "db_path": self.db_path,
            "total_drifts": int(total),
            "avg_delta": round(float(avg_delta or 0.0), 2),
            "status_counts": {row[0]: row[1] for row in counts},
            "most_common_drift_patterns": [{"pattern": row[0], "count": row[1]} for row in patterns],
            "recent": self.get_recent(10),
        }


class CBFEngine:
    """測定・判定・記録を担当する。"""

    def __init__(
        self,
        config: Optional[dict] = None,
        config_path: Optional[str] = None,
        log_path: Optional[str] = None,
        db_path: Optional[str] = None,
        use_history: bool = True,
    ):
        self.config_path = _resolve_path(config_path, CBF_CONFIG_PATH)
        self.log_path = _resolve_path(log_path, CBF_LOG_PATH)
        self.db_path = str(Path(db_path or CBF_DB_PATH).expanduser())
        self.session_id = time.strftime("%Y-%m-%dT%H:%M:%S")
        self._lock = threading.RLock()
        self.config = self._normalize_config(config or self._load_config())
        self.threshold = self.config["framework"]["threshold"]
        self.weights = self.config["framework"]["weights"]
        self.prev: Optional[Coordinate] = None
        self.drifts: List[DriftEvent] = []
        self.history: List[dict] = []
        self.history_db = CBFHistory(self.db_path) if use_history else None

    def _load_config(self) -> dict:
        path = self.config_path
        if path.exists():
            try:
                import yaml  # type: ignore

                with path.open(encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                if isinstance(data, dict):
                    return data
            except ImportError:
                _log("PyYAML 未導入のため JSON フォールバックを試行")
            except Exception as exc:
                _log(f"YAML 読み込み失敗: {exc}")
            try:
                with path.open(encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                _log(f"JSON フォールバック失敗: {exc}")
        json_path = path.with_suffix(".json")
        if json_path.exists():
            with json_path.open(encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        return dict(DEFAULT_CONFIG)

    def _normalize_config(self, config: dict) -> dict:
        framework = dict(DEFAULT_CONFIG["framework"])
        framework.update(config.get("framework", {}))
        weights = dict(DEFAULT_CONFIG["framework"]["weights"])
        weights.update(framework.get("weights", {}))
        framework["weights"] = {
            "x": _to_float(weights.get("x"), 3.0),
            "y": _to_float(weights.get("y"), 1.0),
            "z": _to_float(weights.get("z"), 0.3),
        }
        framework["threshold"] = _to_float(framework.get("threshold"), 5.0)
        framework["name"] = str(framework.get("name", DEFAULT_CONFIG["framework"]["name"]))
        framework["version"] = str(framework.get("version", "1.3"))
        stages: Dict[int, str] = {}
        for key, value in dict(config.get("stages", STAGE_NAMES)).items():
            try:
                stages[int(key)] = str(value)
            except (TypeError, ValueError):
                pass
        return {"framework": framework, "stages": stages or dict(STAGE_NAMES)}

    def calculate_distance(self, prev: Coordinate, curr: Coordinate) -> float:
        return (
            abs(curr.x - prev.x) * self.weights["x"]
            + abs(curr.y - prev.y) * self.weights["y"]
            + abs(curr.z - prev.z) * self.weights["z"]
        )

    def serialize_result(self, item: dict) -> dict:
        status = item.get("status")
        return {
            "coord": item.get("coord"),
            "task": item.get("task", ""),
            "status": status.value if isinstance(status, Status) else str(status),
            "delta": round(_to_float(item.get("delta")), 2),
            "corrected": item.get("corrected"),
            "stage_name": item.get("stage_name"),
        }

    def _make_result(
        self,
        coord: Coordinate,
        task: str,
        status: Status,
        delta: float,
        corrected: Optional[Coordinate],
    ) -> dict:
        return {
            "coord": str(coord),
            "task": task,
            "status": status,
            "delta": round(delta, 2),
            "corrected": str(corrected) if corrected else None,
            "stage_name": coord.stage_name,
        }

    def _record_drift(
        self,
        expected: Coordinate,
        actual: Coordinate,
        corrected: Coordinate,
        delta: float,
        task: str,
        status: Status,
    ) -> None:
        event = DriftEvent(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            expected=str(expected),
            actual=str(actual),
            corrected=str(corrected),
            delta=round(delta, 2),
            task=task,
            status=status.value,
            session_id=self.session_id,
            meta={
                "expected_stage": expected.stage_name,
                "actual_stage": actual.stage_name,
                "threshold": self.threshold,
                "weights": dict(self.weights),
            },
        )
        self.drifts.append(event)
        if self.history_db is not None:
            self.history_db.record(event)

    def step(self, coord: Coordinate, task: str = "") -> dict:
        """
        1ステップ処理する。
        戻り値: {"coord", "task", "status", "delta", "corrected"}
        """
        with self._lock:
            if self.prev is None:
                self.prev = coord
                result = self._make_result(coord, task, Status.STABLE, 0.0, None)
                self.history.append(result)
                return result
            dist = self.calculate_distance(self.prev, coord)
            original = Coordinate(coord.x, coord.y, coord.z)
            if dist > self.threshold:
                corrected = Coordinate(self.prev.x, self.prev.y, self.prev.z) if dist <= self.threshold * 2 else original
                status = Status.AUTO_CORRECTED if dist <= self.threshold * 2 else Status.DRIFT
                self._record_drift(self.prev, original, corrected, dist, task, status)
                self.prev = corrected
                result = self._make_result(original, task, status, dist, corrected)
            else:
                self.prev = coord
                result = self._make_result(coord, task, Status.STABLE, dist, None)
            self.history.append(result)
            return result

    def reset(self) -> None:
        with self._lock:
            self.session_id = time.strftime("%Y-%m-%dT%H:%M:%S")
            self.prev = None
            self.drifts.clear()
            self.history.clear()

    def save_log(self, path: Optional[str] = None) -> str:
        target = _resolve_path(path, str(self.log_path))
        _ensure_parent(target)
        payload = {
            "session": self.session_id,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "config": {
                "name": self.config["framework"]["name"],
                "version": self.config["framework"]["version"],
                "threshold": self.threshold,
                "weights": self.weights,
            },
            "total_steps": len(self.history),
            "total_drifts": len(self.drifts),
            "current_position": str(self.prev) if self.prev else None,
            "history": [self.serialize_result(item) for item in self.history],
            "drifts": [item.to_dict() for item in self.drifts],
        }
        with target.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return str(target)

    def load_log(self, path: Optional[str] = None) -> dict:
        target = _resolve_path(path, str(self.log_path))
        if not target.exists():
            return {
                "session": None,
                "saved_at": None,
                "config": {"threshold": self.threshold, "weights": self.weights},
                "total_steps": 0,
                "total_drifts": 0,
                "current_position": str(self.prev) if self.prev else None,
                "history": [],
                "drifts": [],
            }
        with target.open(encoding="utf-8") as f:
            return json.load(f)

    def get_summary(self) -> dict:
        return {
            "session_id": self.session_id,
            "total_steps": len(self.history),
            "drifts": len(self.drifts),
            "auto_corrected": sum(1 for d in self.drifts if d.status == Status.AUTO_CORRECTED.value),
            "hard_drifts": sum(1 for d in self.drifts if d.status == Status.DRIFT.value),
            "current_position": str(self.prev) if self.prev else None,
            "threshold": self.threshold,
            "weights": dict(self.weights),
            "config_path": str(self.config_path),
            "log_path": str(self.log_path),
            "db_path": self.db_path,
        }

    def get_status_payload(self) -> dict:
        return {
            "framework": self.config["framework"],
            "stages": self.config["stages"],
            "summary": self.get_summary(),
            "history": [self.serialize_result(item) for item in self.history],
            "drifts": [item.to_dict() for item in self.drifts],
        }


def check_coordinates(
    strict: bool = True,
    config_path: Optional[str] = None,
    log_path: Optional[str] = None,
) -> dict:
    """
    Git hook 用。
    strict=True: hard drift があれば drift、soft drift のみなら auto-corrected。
    """
    engine = CBFEngine(config_path=config_path, log_path=log_path, use_history=False)
    drifts = list(engine.load_log().get("drifts", []))
    if strict:
        hard_drifts = [d for d in drifts if d.get("status") == Status.DRIFT.value]
        if hard_drifts:
            return {"status": "drift", "drifts": len(hard_drifts), "details": hard_drifts}
        if drifts:
            return {"status": "auto-corrected", "drifts": len(drifts), "details": drifts}
        return {"status": "stable", "drifts": 0, "details": []}
    return {"status": "stable" if not drifts else "auto-corrected", "drifts": len(drifts), "details": drifts}


class CBFRequestHandler(http.server.BaseHTTPRequestHandler):
    """vector_proxy.py と同系統の BaseHTTPRequestHandler 実装。"""

    engine: CBFEngine = None  # type: ignore[assignment]
    state_lock = threading.RLock()

    def do_GET(self) -> None:
        path = self.path.rstrip("/")
        if path == "/v1/cbf/health":
            self._respond(
                200,
                {
                    "status": "ok",
                    "name": self.engine.config["framework"]["name"],
                    "version": self.engine.config["framework"]["version"],
                    "session_id": self.engine.session_id,
                    "threshold": self.engine.threshold,
                    "weights": self.engine.weights,
                    "current_position": str(self.engine.prev) if self.engine.prev else None,
                },
            )
        elif path == "/v1/cbf/status":
            with self.state_lock:
                self._respond(200, self.engine.get_status_payload())
        elif path == "/v1/cbf/log":
            self._respond(200, self.engine.load_log())
        elif path == "/v1/cbf/history":
            stats = None if self.engine.history_db is None else self.engine.history_db.get_stats()
            self._respond(200, {"enabled": self.engine.history_db is not None, "stats": stats})
        else:
            self._respond(404, {"error": f"unknown path: {path}"})

    def do_POST(self) -> None:
        path = self.path.rstrip("/")
        try:
            body = self._read_json()
        except ValueError as exc:
            self._respond(400, {"error": "invalid JSON", "detail": str(exc)})
            return
        if path == "/v1/cbf/step":
            try:
                coord = Coordinate.from_dict(body)
            except ValueError as exc:
                self._respond(400, {"error": "invalid coordinate", "detail": str(exc)})
                return
            task = str(body.get("task", ""))
            save_log = bool(body.get("save_log", True))
            with self.state_lock:
                result = self.engine.step(coord, task)
                if save_log:
                    self.engine.save_log()
                self._respond(200, self.engine.serialize_result(result))
        elif path == "/v1/cbf/reset":
            with self.state_lock:
                self.engine.reset()
                self.engine.save_log()
                self._respond(200, {"status": "reset", "session_id": self.engine.session_id})
        else:
            self._respond(404, {"error": f"unknown path: {path}"})

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("body must be object")
        return payload

    def _respond(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(engine: Optional[CBFEngine] = None, port: int = 9801, host: str = "127.0.0.1") -> None:
    """HTTP API 起動。"""
    engine = engine or CBFEngine()
    CBFRequestHandler.engine = engine
    server = ThreadedHTTPServer((host, int(port)), CBFRequestHandler)

    def shutdown_handler(sig: int, frame: object) -> None:
        _log(f"シグナル {sig} 受信。シャットダウンします")
        engine.save_log()
        server.shutdown()
        server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    _log("=" * 60)
    _log("Coordinate Build Framework v1.3 起動")
    _log(f"  Listen   : http://{host}:{port}")
    _log(f"  Config   : {engine.config_path}")
    _log(f"  Log      : {engine.log_path}")
    _log(f"  DB       : {engine.db_path}")
    _log(f"  Threshold: {engine.threshold}")
    _log(f"  Weights  : {json.dumps(engine.weights, ensure_ascii=False)}")
    _log("Endpoints:")
    _log("  GET  /v1/cbf/health")
    _log("  GET  /v1/cbf/status")
    _log("  GET  /v1/cbf/log")
    _log("  GET  /v1/cbf/history")
    _log("  POST /v1/cbf/step")
    _log("  POST /v1/cbf/reset")
    _log("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("KeyboardInterrupt で停止")
        engine.save_log()
        server.shutdown()
        server.server_close()


def format_result(result: dict) -> str:
    status = result["status"]
    icon = status.icon if isinstance(status, Status) else STATUS_ICONS.get(str(status), "?")
    delta = _to_float(result.get("delta"), 0.0)
    corrected = f" → {result['corrected']}" if result.get("corrected") else ""
    return f"{icon} {result.get('coord')} {result.get('task', '')}{' (initial)' if delta == 0 else f' (Δ={delta:.2f})'}{corrected}".rstrip()


def run_demo(engine: Optional[CBFEngine] = None, save_log: bool = True) -> dict:
    engine = engine or CBFEngine()
    plan = [(Coordinate(1, 1, 1), "Plan"), (Coordinate(2, 1, 2), "Design"), (Coordinate(8, 7, 2), "Anomaly")]
    for coord, task in plan:
        print(format_result(engine.step(coord, task)))
    if save_log:
        engine.save_log()
    summary = engine.get_summary()
    print(f"\n⚠️ {summary['drifts']} drift(s) detected." if summary["drifts"] else "\n✅ Build stable. No drift detected.")
    return summary


def process_stream(engine: Optional[CBFEngine] = None, save_log: bool = True) -> dict:
    """stdin から JSON 行または x y z task を処理する。"""
    engine = engine or CBFEngine()
    processed = 0
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            coord, task = Coordinate.from_line(line)
            print(format_result(engine.step(coord, task)))
            processed += 1
        except Exception as exc:
            print(f"⛔ parse error: {exc} :: {line}", file=sys.stderr)
    if save_log:
        engine.save_log()
    summary = engine.get_summary()
    summary["processed_lines"] = processed
    return summary


def build_arg_parser() -> "argparse.ArgumentParser":
    import argparse

    parser = argparse.ArgumentParser(description="CBF v1.3 — Coordinate Build Framework")
    parser.add_argument("--check", action="store_true", help="Git hook mode")
    parser.add_argument("--strict", dest="strict", action="store_true", help="Strict check mode")
    parser.add_argument("--no-strict", dest="strict", action="store_false", help="Non strict mode")
    parser.add_argument("--serve", action="store_true", help="Start HTTP API server")
    parser.add_argument("--port", type=int, default=9801)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--demo", action="store_true", help="Run demo plan")
    parser.add_argument("--config", default=str(_resolve_path(CBF_CONFIG_PATH, CBF_CONFIG_PATH)))
    parser.add_argument("--log", default=str(_resolve_path(CBF_LOG_PATH, CBF_LOG_PATH)))
    parser.add_argument("--db", default=str(Path(CBF_DB_PATH).expanduser()))
    parser.add_argument("--no-save-log", action="store_true", help="Skip DriftLogger.json write")
    parser.set_defaults(strict=True)
    return parser


def main() -> None:
    """CLI エントリポイント。"""
    parser = build_arg_parser()
    args = parser.parse_args()
    if args.check:
        result = check_coordinates(args.strict, args.config, args.log)
        if result["status"] == Status.DRIFT.value:
            print(f"⛔ {result['drifts']} drift(s) detected", file=sys.stderr)
            sys.exit(1)
        if result["status"] == Status.AUTO_CORRECTED.value:
            print(f"⚠ {result['drifts']} auto-corrected", file=sys.stderr)
            sys.exit(0)
        print("✓ stable", file=sys.stderr)
        sys.exit(0)
    engine = CBFEngine(config_path=args.config, log_path=args.log, db_path=args.db)
    save_log = not args.no_save_log
    if args.serve:
        if not save_log:
            engine.save_log = lambda path=None: str(_resolve_path(path, str(engine.log_path)))  # type: ignore[assignment]
        serve(engine=engine, port=args.port, host=args.host)
        return
    if args.demo:
        run_demo(engine, save_log)
        return
    if not sys.stdin.isatty():
        summary = process_stream(engine, save_log)
        if summary["drifts"]:
            print(
                f"\n⚠️ stream completed: {summary['drifts']} drift(s), {summary['processed_lines']} line(s) processed.",
                file=sys.stderr,
            )
        else:
            print(f"\n✅ stream completed: {summary['processed_lines']} line(s), no drift.", file=sys.stderr)
        return
    run_demo(engine, save_log)


if __name__ == "__main__":
    main()
