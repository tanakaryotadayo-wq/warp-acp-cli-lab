# Extension Shell Module

## 定義

`src/extension.ts` を中心とした TypeScript 側は、VORTEX の**UI シェル兼 command dispatcher**です。  
ここは Python 実行系の代わりではなく、**VS Code から各モジュールへ橋を架ける層**です。

## 管轄ファイル

| File | Role |
|---|---|
| `src/extension.ts` | command 登録、設定読取、webview、status bar、外部プロセス起動 |
| `src/dashboard.ts` | Async Task Dashboard の HTML |
| `package.json` | commands / settings / view container / activation |

## このモジュールが持つ責務

1. `vortex.*` command を VS Code に登録する  
2. VORTEX sidebar webview を表示し、UI 操作を command に変換する  
3. Gemini bridge / Pipeline① / packetizer を起動する  
4. 設定 (`vortex.*`) を読み、各 runtime に渡す  
5. 状態表示を行う  
   - status bar
   - snapshot document
   - output channel
   - sidebar refresh

## このモジュールが持たない責務

- lane routing の本体
- KI queue の永続化ロジック
- memory recall の本体
- packetization の本体
- orchestration MCP server の実装

それらは `assets/gemini/`, `assets/pipeline/`, `../fusion-copilot/mcp-server/` にあるべきです。

## 公開 surface

### Commands

主要 command は次の 5 群に分かれます。

| Group | Commands |
|---|---|
| Audit | `vortex.runAudit`, `vortex.runAuditSelection`, `vortex.switchPreset` |
| Bridge | `vortex.startGeminiBridge`, `vortex.openGeminiBridgeSnapshot` |
| Pipeline | `vortex.runPipelineOne`, `vortex.openPipelineOneSnapshot` |
| Memory / KI | `vortex.openNewgateSnapshot`, `vortex.openKiQueueSnapshot`, `vortex.openKiColabNotebook`, `vortex.promoteKiCandidate` |
| Harvest / Ops | `vortex.packetizeAntigravity`, `vortex.viewLogs`, `vortex.clearLogs`, `vortex.refreshSidebar`, `vortex.openAsyncTaskDashboard` |

### Settings

設定は `package.json` の `contributes.configuration.properties` に定義されます。

| Namespace | Purpose |
|---|---|
| `vortex.*` | critic / bridge / pipeline の基本設定 |
| `vortex.fleet.*` | utility / agent / conversation / embedding / health check |
| `vortex.memory.*` | brain dir, recall, auto index |
| `vortex.harvest.*` | packet DB, target languages, semantic diff |
| `vortex.stateStream.*` | IDE 状態の POST 配信 |
| `vortex.actionServer.*` | localhost 限定 action server |
| `vortex.jules.*` | Jules worker lane の GitHub poll / notify / search query |

常駐 runtime まわりで特に重要なのは次です。

- `vortex.runtime.backgroundLauncher` — `tmux` / `subprocess`
- `vortex.geminiBridgeTmuxSession`
- `vortex.pipelineOneCbfTmuxSession`
- `vortex.pipelineOneContainerRuntime` — `auto` / `orbstack` / `docker`

## ランタイム機能

### Fleet health check

- `startFleetHealthCheck()` が `Fusion Gate`, `utility`, `agent`, `conversation`, `embedding` を定期監視します。
- 結果は `currentFleetHealth` に保持され、status bar tooltip に反映されます。

### State streaming

- `startStateStreaming()` は `vortex.stateStream.enable=true` のときだけ起動します。
- 送信 payload には editor / workspace / fleet が入り、必要なら diagnostics / git も付与します。
- 送信失敗は**silent fail**です。UI を止めないのが契約です。

### Action server

- `startActionServer()` は `vortex.actionServer.enable=true` のときだけ起動します。
- localhost 以外からのアクセスは拒否する前提です。
- 実行できる action は `allowedActions` の allowlist に制限されます。

### tmux / OrbStack 運用

- 常駐させたい bridge / CBF は、既定で `tmux` singleton として起動できます。
- これにより **VS Code window ごとの background process 増殖** を避けやすくなります。
- Pipeline① は `vortex.pipelineOneContainerRuntime=auto` のとき、現在の docker context が `orbstack` なら OrbStack 実験モードとして status に反映します。

### Jules worker lane

- `vortex.openAsyncTaskDashboard` は **Jules issue queue の operator UI** です。
- ここでやることは「Jules を本体にする」ではなく、**Issue / PR / reply を往復する async worker lane** として扱うことです。
- `vortex.jules.enablePolling=true` のとき、VORTEX は GitHub issue search を定期ポーリングして次を検知します。
  - human feedback 待ちへの遷移
  - linked PR の出現
  - active queue からの消滅（完了候補）
- Dashboard には次の運用導線があります。
  - `Open Issue`
  - `Open Linked PR`
  - reply template (`Implement + PR`, `Review 対応`, `Push / Sync Retry`)

推奨運用は次です。

1. ローカル runtime で状態を作る（tmux / status.json / verify）  
2. Jules に bounded task を渡す  
3. delay-aware polling で GitHub 側の進捗を待つ  
4. feedback が必要になったら reply template で返す  
5. linked PR が出たら review / merge 判定をする  

## 失敗時の扱い

| 事象 | 振る舞い |
|---|---|
| backend endpoint が落ちている | health check に赤表示。UI 全体は継続 |
| state stream endpoint 不在 | 送信を黙って失敗させる |
| action server で未許可 action | 明示的に reject する |
| active editor 不在 | audit 系 command は早期に止める |
| Jules auth 不在 | poll は fail-soft。Dashboard を開くときだけ auth を要求する |

## 改修ルール

1. 新しい provider 追加時も、Extension Shell には**起動・設定・表示**しか追加しない。  
2. 新しい永続データを作る場合は、TypeScript 側で直接保存せず、担当 runtime に寄せる。  
3. silent fail を増やすときは、**UI 継続のためか / 不具合隠しになるか**を判断してからにする。  
