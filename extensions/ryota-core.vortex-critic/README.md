# VORTEX Critic

Multi-provider AI レーン拡張機能 for VS Code / Antigravity

## スタック概要

```
┌──────────────────────────────────────────────────────────────┐
│  VS Code / Antigravity  ─  VORTEX Extension (TypeScript)     │
│  ├─ サイドバー UI (Webview)                                   │
│  ├─ PCC Preset 切替                                           │
│  ├─ KI Queue / 昇格 / Colab Notebook                         │
│  ├─ Pipeline① 起動・監視                                      │
│  ├─ Gemini Bridge 起動・監視                                   │
│  ├─ Jules worker dashboard / poll / notify                    │
│  └─ Antigravity Packet 抽出                                   │
├──────────────────────────────────────────────────────────────┤
│  Memory Pipeline  ─  memory_pipeline.py (Python)             │
│  ├─ fleet_log   → JSONL 構造化記録                            │
│  ├─ KI Queue    → 候補生成・一覧・昇格                        │
│  ├─ Auto-Index  → ConversationMemory.index_knowledge()       │
│  └─ Recall      → ベクトル検索でプロンプトに注入              │
├──────────────────────────────────────────────────────────────┤
│  Gemini A2A Bridge  ─  gemini_a2a_bridge.py (Python)         │
│  ├─ conversation lane → 8103 (Gemma-4-26B)                   │
│  ├─ agent lane        → 8102 (Qwen3-Coder-Next)             │
│  ├─ utility lane      → 8101 (Qwen3.5-9B)                   │
│  ├─ PCC 制約注入 (探/極/均/監/刃)                             │
│  ├─ ACP CLI コマンド (deepthink/deepsearch/critic)           │
│  └─ Fusion Gate 経由のプロバイダルーティング                  │
├──────────────────────────────────────────────────────────────┤
│  Fleet Bridge  ─  fleet_bridge.py (MCP stdio)                │
│  └─ GPT-5 mini / Copilot CLI サブエージェント用              │
├──────────────────────────────────────────────────────────────┤
│  Titan MCP Bridge  ─  titan_mcp_bridge.py (MCP stdio)        │
│  └─ MBA → Mac Studio リモートアクセス                        │
├──────────────────────────────────────────────────────────────┤
│  PCC Critic  ─  pcc_critic.py / pcc_critic_standalone.py     │
│  └─ 9 軸座標制約 + Gemini CLI critic パイプライン            │
├──────────────────────────────────────────────────────────────┤
│  KI Vectorizer  ─  colab_ki_vectorizer.ipynb                 │
│  └─ Qwen3-Embedding-8B int8 でベクトル化 → ki_vectors_8bit.db│
└──────────────────────────────────────────────────────────────┘
```

## ディレクトリ構造

```
vortex-critic/
├── src/
│   ├── extension.ts        # VS Code 拡張メインエントリ (1275行)
│   └── dashboard.ts        # Async Task Dashboard
├── assets/
│   ├── gemini/
│   │   ├── memory_pipeline.py       # 独立メモリモジュール ★
│   │   ├── gemini_a2a_bridge.py     # A2A HTTP ブリッジ
│   │   ├── fleet_bridge.py          # MCP stdio ブリッジ
│   │   ├── titan_mcp_bridge.py      # Mac Studio リモート MCP
│   │   ├── pcc_critic.py            # PCC 制約 + critic
│   │   ├── pcc_critic_standalone.py # スタンドアロン critic
│   │   ├── newgate_profile.json     # Newgate 認知エンジンプロファイル
│   │   ├── bootstrap_gemini_code_assist.sh  # ブリッジ起動スクリプト
│   │   └── colab_ki_vectorizer.ipynb        # Qwen3-Emb-8B ベクトル化 ★
│   └── pipeline/
│       └── scripts/
│           ├── bootstrap_pipeline_01.sh
│           └── pipeline_01_runner.py
├── media/
│   └── vortex-icon.svg
├── package.json            # VS Code 拡張マニフェスト
├── tsconfig.json
└── README.md               # ← このファイル
```

## README Index

| Path | 内容 |
|------|------|
| `docs/README.md` | VORTEX 複合モジュール全体の定義書 |
| `docs/extension.md` | VS Code 拡張シェル / UI / command dispatcher |
| `docs/critic.md` | DeepSeek / VORTEX critic モジュール |
| `docs/gemini.md` | Gemini A2A bridge / memory pipeline モジュール |
| `docs/pipeline.md` | Pipeline① モジュール |
| `docs/mcp-server.md` | 外部 Fusion Orchestrator v2 MCP モジュール |
| `docs/contracts/README.md` | 厳密な入出力契約の索引 |

## 設計詳細

### 1. Provider Adapter 層

| Provider | 接続方式 | 用途 |
|----------|----------|------|
| DeepSeek | API | Critic (高精度コード監査) |
| Gemini (ACP) | ACP CLI / Fusion Gate | Critic + DeepThink + DeepSearch |
| Claude Code (ACP) | ACP CLI | Worker (実装・PR・Issue) |
| Local LLM | OpenAI 互換 HTTP | 全レーン (Gemma-4 / Qwen3 / Qwen3.5) |
| Copilot | MCP stdio | Fleet サブエージェント |

### 2. Lane Contract

| レーン | 役割 | デフォルト Provider |
|--------|------|-------------------|
| `critic` | コード/出力の品質監査 | Gemini 3.1 Pro + PCC #探 |
| `worker` | 実装・生成 | Claude Opus/Sonnet or Qwen3-Coder |
| `auditor` | セキュリティ・整合性 | DeepSeek or PCC #監 |
| `conversation` | 対話・要約 | Gemma-4-26B |
| `utility` | 軽量タスク | Qwen3.5-9B |

### 3. PCC (Prompt Coordinate Control) Presets

| Preset | 名前 | 特性 |
|--------|------|------|
| `探` | Critical Explorer | 迎合抑制、批判的分析 |
| `極` | Maximum Precision | 最大精度、ゼロ曖昧 |
| `均` | Balanced Review | バランス型レビュー |
| `監` | Audit Mode | 監査特化 |
| `刃` | Blade | 実装レビュー特化 |

### 4. Memory Pipeline チェーン

```
タスク完了
  ↓ _emit_fleet_log() [daemon thread, 非ブロック]
fleet_YYYYMMDD.jsonl  ← 構造化ログ
  ↓ success なら quality gate
ki-promotion-queue.jsonl  ← KI 候補
  ↓ handle_ki_queue_promote()
knowledge/<ki_name>/
  ├── metadata.json
  ├── timestamps.json
  └── artifacts/<id>.md
  ↓ _try_index_knowledge() [fail-soft]
ConversationMemory.index_knowledge()
  ↓ build_newgate_context()
_try_recall(query)  → [Memory Recall] ブロック注入
```

- low-signal probe / smoke / `Thinking Process:` ダンプは **KI 候補から落とす**
- summary / artifact は raw reasoning より **compact final answer** を優先する

### 5. ベクトル化

- **モデル**: `Qwen/Qwen3-Embedding-8B`
- **次元**: 4096d
- **精度**: fp16 (ローカル) / int8 (Colab)
- **出力**: `ki_vectors_8bit.db` (SQLite)
- **ノートブック**: `assets/gemini/colab_ki_vectorizer.ipynb`
- **用途**: memory recall の埋め込みバックエンド

### 6. Fusion Gate 連携

```
ACP コマンド
  ↓
Fusion Gate /v1/gate/invoke (cache 有効)
  ↓ runtime→provider マッピング
gemini → gemini
claude → claude
copilot → copilot/copilot_mini
  ↓
PCC + CBF プロトコル注入
  ↓
プロバイダ実行 → 結果返却
```

### 7. Pipeline①

ローカル OSS リポジトリのパケット化・監査パイプライン。

- bootstrap → rclone mount → n8n → harvest → audit
- ゼロパケット時は fail-closed (`--allow-empty-packets` で明示オーバーライド)
- status.json で VORTEX サイドバーに進捗表示

### 8. Jules Worker Workflow

Jules は **push 代行を含む remote worker lane** として扱います。  
VORTEX 側の役割は、Issue / PR / reply の operator 面を持ち、時間差のある進行を poll + notify で回すことです。

- 監視面: `vortex.openAsyncTaskDashboard`
- poll: `vortex.jules.enablePolling`, `vortex.jules.pollInterval`
- 通知: feedback待ち / linked PR / active queue からの消滅
- UI導線:
  - `Open Issue`
  - `Open Linked PR`
  - `Implement + PR`
  - `Review 対応`
  - `Push / Sync Retry`

つまり、**ローカル runtime が truth、Jules は async implementation / PR worker** です。

## データパス

| パス | 内容 |
|------|------|
| `~/.gemini/antigravity/fleet-logs/` | Fleet ログ (JSONL) |
| `~/.gemini/antigravity/ki-promotion-queue.jsonl` | KI 昇格キュー |
| `~/.gemini/antigravity/knowledge/` | 昇格済み KI アーティファクト |
| `~/.gemini/antigravity/brain/` | 会話ログ生データ |
| `~/Newgate/intelligence/neural_packets.db` | ニューラルパケット台帳 |
| `~/Newgate/intelligence/conversation_memory.py` | 記憶インデックス |

## 環境変数

| 変数 | デフォルト | 説明 |
|------|----------|------|
| `FLEET_LOG_DIR` | `~/.gemini/antigravity/fleet-logs` | ログ出力先 |
| `KI_QUEUE_FILE` | `~/.gemini/antigravity/ki-promotion-queue.jsonl` | キューファイル |
| `KI_KNOWLEDGE_DIR` | `~/.gemini/antigravity/knowledge` | KI 保管先 |
| `KI_COLAB_NOTEBOOK` | `~/Newgate/ki_agent_system/colab_ki_vectorizer.ipynb` | ノートブックパス |
| `NEWGATE_ROOT` | `~/Newgate` | Newgate ルート |

## セットアップ

```bash
# ビルド
cd vortex-critic
npm install
npm run compile

# VSIX パッケージ
npx @vscode/vsce package --no-dependencies

# インストール
code --install-extension vortex-critic-1.0.0.vsix --force
```

## ライセンス

Private — Ryota Tanaka
