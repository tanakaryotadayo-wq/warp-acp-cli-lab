# Critic Module

## 定義

Critic モジュールは、VORTEX の**read-only 監査レーン**です。  
ここは「実装」ではなく「次のターンを改善する追加文脈」を返す層です。

## 管轄ファイル

| File | Role |
|---|---|
| `assets/critic/deepseek-critic.py` | 基本 DeepSeek critic hook |
| `assets/critic/vortex-critic.py` | evidence 優先の VORTEX critic hook |
| `assets/critic/deepseek-critic.config.json` | DeepSeek critic runtime 設定 |
| `assets/critic/vortex-critic.config.json` | VORTEX critic runtime 設定 |

## 契約

### 入力

stdin から JSON を受け取ります。最低限必要なのは `prompt` です。

```json
{
  "prompt": "review this change",
  "activeFile": "/abs/path/file.ts",
  "workspaceRoot": "/abs/path/repo"
}
```

### 出力

stdout には hook 互換 JSON を返します。  
追加文脈が生成できたときだけ `hookSpecificOutput.additionalContext` が入ります。

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "[VORTEX Critic]\\n..."
  }
}
```

## スクリプト差分

| Script | 特徴 |
|---|---|
| `deepseek-critic.py` | prompt 中心。外部証跡の収集はしない |
| `vortex-critic.py` | prompt + workspace evidence。git / test artifact を拾って Completion Illusion を検出する |

## 設定解決順

1. `*.config.json`
2. `DEEPSEEK_API_KEY`
3. macOS keychain (`security find-generic-password`)
4. fallback service names

設定が壊れていても、スクリプトは**デフォルト設定にフォールバック**します。

## VORTEX critic の evidence ソース

`vortex-critic.py` は `workspaceRoot` があれば次を収集します。

- `git diff --stat HEAD`
- `git diff --name-only HEAD`
- `git diff --name-only --cached`
- `git log -1 --oneline`
- 代表的な test artifact の存在

ここでの原則は、**自然言語の完了報告より証跡を優先する**ことです。

## 失敗時の契約

| 事象 | 振る舞い |
|---|---|
| `prompt` が無い | `emit(None)` で空 hook payload を返す |
| API key が無い | stderr に出しつつ、空 hook payload を返す |
| provider から応答が無い | 空 hook payload を返す |
| config JSON が壊れている | デフォルト設定に戻る |

つまり、**hook を壊さない**のが最優先契約です。

## 非責務

- ファイル編集
- テスト実行の orchestration
- queue 生成
- KI 永続化
- lane routing

## 改修ルール

1. Critic の出力は**追加文脈**であって、実行命令ではない。  
2. `vortex-critic.py` に証跡を足す場合は、**workspace が無くても壊れない**ことを維持する。  
3. completion illusion 検出ロジックは強化してよいが、**write side effect** は持ち込まない。  
