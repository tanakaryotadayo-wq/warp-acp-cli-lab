# Gemini Bridge Module

## 定義

Gemini モジュールは、VORTEX の**実働レーン本体**です。  
UI から来た要求を A2A / MCP / memory pipeline に流し、最終的に local lane や Fusion Gate へ渡します。

## 管轄ファイル

| File | Role |
|---|---|
| `assets/gemini/gemini_a2a_bridge.py` | A2A HTTP bridge。本番の入口 |
| `assets/gemini/memory_pipeline.py` | fleet log / KI queue / promote / index / recall |
| `assets/gemini/fleet_bridge.py` | GPT-5 mini / Copilot CLI 系向け MCP stdio bridge |
| `assets/gemini/titan_mcp_bridge.py` | MBA から Mac Studio の HTTP tool surface へ中継 |
| `assets/gemini/pcc_critic.py` | Gemini CLI を使う PCC critic |
| `assets/gemini/pcc_critic_standalone.py` | 単体 critic 実行 |
| `assets/gemini/bootstrap_gemini_code_assist.sh` | bridge 起動補助 |
| `assets/gemini/newgate_profile.json` | Newgate profile |

## A2A bridge の厳密契約

### HTTP surface

`gemini_a2a_bridge.py` は少なくとも次を提供します。

| Endpoint | Role |
|---|---|
| `/.well-known/agent-card.json` | agent card |
| `/v1/card` | card alias |
| `/v1/message:send` | 単発メッセージ処理 |
| `/v1/message:stream` | SSE stream |
| `/tasks` | task 作成 |
| `/executeCommand` | command 実行 |

### ルーティング

| Route | Backend |
|---|---|
| `conversation` | `8103` |
| `agent` | `8102` |
| `utility` | `8101` |

prefix / alias で route を強制できます。  
また `normalize_message()` は REST client 由来の `content` 形式と `parts` 形式の両方を正規化対象にします。

### recall 注入

`_build_openai_messages()` で最新 user turn に対して `try_recall()` を呼び、  
`[Memory Recall]` を system prompt に差し込みます。

この recall は**best-effort**です。失敗しても lane 実行自体は止めません。

### 起動モード

`bootstrap_gemini_code_assist.sh` は次の 2 モードを持ちます。

- `tmux` — 既定。singleton session で bridge を常駐させる
- `subprocess` — fallback / 単発起動

`GEMINI_A2A_LAUNCHER=tmux` と `GEMINI_A2A_TMUX_SESSION=<name>` を使うと、**IDE window ごとの多重起動を避けやすい**です。

## Memory pipeline の厳密契約

### 永続データ

| Path | Role |
|---|---|
| `~/.gemini/antigravity/fleet-logs/` | fleet JSONL |
| `~/.gemini/antigravity/ki-promotion-queue.jsonl` | 昇格待ち queue |
| `~/.gemini/antigravity/knowledge/` | promoted KI |
| `~/.gemini/antigravity/brain/` | raw memory |

### 公開 API

| Function | Contract |
|---|---|
| `emit_fleet_log()` | fire-and-forget で fleet log を書く |
| `handle_fleet_log()` | MCP 向け log handler |
| `handle_ki_queue_list()` | queue 一覧 |
| `handle_ki_queue_promote()` | `entry_id` を要求し、knowledge 化 + auto-index |
| `try_recall()` | recall block を返す。失敗時は `""` |

### promote 結果

promotion は `knowledge/<ki_name>/` に以下を作ります。

- `metadata.json`
- `timestamps.json`
- `artifacts/*.md`

さらに `_try_index_knowledge()` が成功すれば、その場で index 更新まで進みます。

## Fleet bridge / Titan bridge

### `fleet_bridge.py`

- stdio JSON-RPC で受ける
- `TOOLS` でツール一覧を返す
- memory pipeline と Fusion Gate relay の間をつなぐ

### `titan_mcp_bridge.py`

- MBA 側 stdio MCP を Mac Studio HTTP API に流す
- remote tool surface をローカル MCP 風に見せる

## 依存

| Dependency | 用途 |
|---|---|
| local OpenAI-compatible endpoints | conversation / agent / utility lane |
| Fusion Gate | provider invoke / cache / protocol injection |
| `ConversationMemory` | recall / knowledge index |
| Gemini CLI | PCC critic |

## 失敗時の契約

| 事象 | 振る舞い |
|---|---|
| recall 失敗 | prompt から recall を抜いて継続 |
| image / multimodal 非対応 | fail-fast |
| `handle_ki_queue_promote()` に `entry_id` が無い | `{"error": "entry_id is required"}` |
| Fusion Gate / Titan 不達 | bridge 側で error payload を返す |

## 非責務

- VS Code UI
- status bar / sidebar 描画
- packet harvest

## 改修ルール

1. lane routing を変えるときは、**A2A surface と prefix 強制の両方**を見る。  
2. memory pipeline の fail-soft は、**運用継続のための fail-soft**だけに使う。  
3. queue schema を変えるときは、writer / reader / promote 処理を同時に更新する。  
