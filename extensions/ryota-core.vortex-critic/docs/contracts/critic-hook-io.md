# Critic Hook I/O

## Scope

source of truth:

- `assets/critic/deepseek-critic.py`
- `assets/critic/vortex-critic.py`

両者とも **stdin JSON -> stdout hook payload** という同じ I/O 契約を持つ。

## Input contract

### Minimum input

```json
{
  "prompt": "review this user request"
}
```

### Common optional fields

```json
{
  "prompt": "review this user request",
  "activeFile": "/abs/path/file.ts",
  "workspaceRoot": "/abs/path/repo"
}
```

### VORTEX-specific optional evidence fields

`vortex-critic.py` は次も受理する:

```json
{
  "test_exit_code": 0,
  "lint_exit_code": 0,
  "scope_files": ["src/a.ts", "src/b.ts"]
}
```

## Input parsing behavior

`read_stdin_json()` の契約:

1. stdin が空なら `{}`  
2. stdin が object JSON ならそのまま  
3. JSON parse に失敗したら `{ "prompt": raw_stdin }`

つまり、**plain text だけ流しても prompt として扱う**。

## Output contract

### Success with additional context

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit",
    "additionalContext": "[VORTEX Critic]\n..."
  }
}
```

### No-op / fail-safe output

```json
{
  "hookSpecificOutput": {
    "hookEventName": "UserPromptSubmit"
  }
}
```

## Invariants

1. stdout は常に valid JSON を返す  
2. `hookSpecificOutput.hookEventName` は常に `UserPromptSubmit`  
3. `additionalContext` は**生成できたときだけ**入る  
4. hook 失敗時でもプロセス全体を壊さない

## Failure behavior

| Condition | Behavior |
|---|---|
| config invalid | default config に戻る |
| prompt missing | no-op payload を返す |
| API key missing | stderr に書きつつ no-op payload を返す |
| provider no response | no-op payload を返す |

## deepseek vs vortex

| Script | Extra behavior |
|---|---|
| `deepseek-critic.py` | prompt 중심の critic |
| `vortex-critic.py` | `workspaceRoot` があれば git / test artifact を証跡として集める |

## Non-goals

- ファイル編集
- テスト実行 orchestration
- queue 更新
- lane routing
