# Contracts Index

このディレクトリは、VORTEX の**機械可読に近い入出力契約**を置く場所です。
モジュール説明よりも優先して参照される前提です。

## Current contracts

| Contract | File | Source of truth |
|---|---|---|
| A2A request / response | `docs/contracts/a2a-request-shape.md` | `assets/gemini/gemini_a2a_bridge.py` |
| KI queue JSONL | `docs/contracts/ki-queue-schema.md` | `assets/gemini/memory_pipeline.py` |
| Pipeline status JSON | `docs/contracts/status-json-schema.md` | `assets/pipeline/scripts/bootstrap_pipeline_01.sh`, `assets/pipeline/scripts/pipeline_01_runner.py` |
| Critic hook I/O | `docs/contracts/critic-hook-io.md` | `assets/critic/deepseek-critic.py`, `assets/critic/vortex-critic.py` |

## Rules

1. 実装を変えたら、この folder も同じ commit で更新する。  
2. 説明文より**field 名と fail behavior**を優先する。  
3. 互換性のために残っている legacy key は、削除前にここへ明記する。  
