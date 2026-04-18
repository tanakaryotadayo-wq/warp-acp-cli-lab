# VORTEX Detailed Manual

このディレクトリは、VORTEX を「単なる拡張機能」ではなく**複数モジュールの複合体**として定義するための説明書です。
目的は、機能説明ではなく**責務境界・入出力・依存・失敗条件**を固定することです。

## システム定義

| Module | Source of truth | 受け取るもの | 返すもの / 出すもの | 永続データ |
|---|---|---|---|---|
| Extension Shell | `src/`, `package.json` | VS Code command, 設定, editor state | UI, output channel, subprocess 起動 | VS Code settings / snapshot files |
| Critic | `assets/critic/` | prompt JSON, workspace path | hook payload (`additionalContext`) | なし |
| Gemini Runtime | `assets/gemini/` | A2A request, MCP request, queue 操作 | lane response, queue result, recall block | fleet logs, KI queue, knowledge, brain |
| Pipeline① | `assets/pipeline/` | repo path, state dir, provider settings | status JSON, issue packets, artifacts | packet DB, issue DB, status JSON |
| External MCP | `../fusion-copilot/mcp-server/` | stdio MCP JSON-RPC | orchestration tool responses | 外部モジュール側で管理 |

## モジュール境界ルール

1. **Extension Shell は UI と起動制御だけを持つ。** provider routing や memory indexing の本体は入れない。  
2. **Critic は read-only。** 実装やファイル変更を担当しない。次ターン改善のための文脈だけ返す。  
3. **Gemini Runtime は運用レーン本体。** lane routing、fleet log、KI queue、recall 注入はここで扱う。  
4. **Pipeline① は packet 化と監査のためのバッチ系。** 日常 UI 状態管理は持たない。  
5. **External MCP は隣接モジュール。** VORTEX 本体 repo に同梱されていなくても、運用スタック上の依存として明示する。  

## 典型データフロー

```text
VS Code command / UI
  -> Extension Shell
  -> (A) Critic hook
  -> (B) Gemini A2A Bridge
       -> local lanes / Fusion Gate
       -> fleet_log
       -> KI queue
       -> knowledge + index
       -> memory recall
  -> (C) Pipeline①
       -> packet DB
       -> Claude/Gemini analysis
       -> issue packet DB
```

## 読み分け

| 欲しい情報 | 読むファイル |
|---|---|
| UI, command, settings の責務 | `docs/extension.md` |
| read-only critic の厳密契約 | `docs/critic.md` |
| A2A / queue / recall の厳密契約 | `docs/gemini.md` |
| packetize / audit batch の厳密契約 | `docs/pipeline.md` |
| 外部 MCP オーケストレーション | `docs/mcp-server.md` |

## AI 向けに相性がいい docs 構成

VORTEX のような複合システムでは、`docs/` 配下を次の粒度で切ると AI も人間も読みやすくなります。

| Folder / File | 用途 |
|---|---|
| `docs/README.md` | 全体像と索引 |
| `docs/extension.md` | UI / shell / commands |
| `docs/critic.md` | read-only critic 契約 |
| `docs/gemini.md` | runtime / memory / bridge 契約 |
| `docs/pipeline.md` | batch / packet / audit 契約 |
| `docs/mcp-server.md` | 外部 orchestration backend |
| `docs/architecture/` | 将来、図や依存関係を増やす場所 |
| `docs/contracts/` | JSON schema, endpoint, file format 契約 |
| `docs/operations/` | 起動手順, 復旧手順, runbook |
| `docs/decisions/` | 設計判断の記録 (ADR 系) |

## 変更時の原則

1. 新機能を足す前に、**どのモジュールの責務か**をここで決める。  
2. データの保存先を増やす場合は、**どこが writer でどこが reader か**を書く。  
3. 「失敗しても黙る」処理は、**どこで fail-soft / fail-closed にするか**を README に追記する。  
4. 外部 repo や外部プロセスに依存する場合は、**相対パスと役割**を必ず明記する。  
