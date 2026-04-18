# KI Queue Schema

## Scope

source of truth: `assets/gemini/memory_pipeline.py`

queue file:

```text
~/.gemini/antigravity/ki-promotion-queue.jsonl
```

## Storage format

- **JSONL**
- 1行 = 1 queue entry
- `write_queue()` は全件を書き直す

## Queue entry schema

`_make_queue_entry()` が生成する基本形:

```json
{
  "id": "ki_abcd1234ef567890",
  "status": "pending",
  "created_at": "2026-04-18T00:00:00+09:00",
  "updated_at": "2026-04-18T00:00:00+09:00",
  "title": "Task title",
  "summary": "Short result summary",
  "task": "original task",
  "result": "result body",
  "cause": null,
  "fix": null,
  "tags": [],
  "suggested_ki_name": "task_title",
  "log_file": "/abs/path/fleet_20260418.jsonl",
  "notebook_path": "/abs/path/colab_ki_vectorizer.ipynb"
}
```

## Field meanings

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `task + result` hash ベースの queue id |
| `status` | string | `pending` or `promoted` |
| `created_at` | string | queue 作成時刻 |
| `updated_at` | string | 最終更新時刻 |
| `title` | string | 表示用タイトル |
| `summary` | string | 400文字以内の要約 |
| `task` | string | 元タスク |
| `result` | string | 元結果 |
| `cause` | string/null | failure / recovery 文脈 |
| `fix` | string/null | 修正内容 |
| `tags` | array | 任意タグ |
| `suggested_ki_name` | string | knowledge dir 候補 |
| `log_file` | string | 元になった fleet log |
| `notebook_path` | string | vectorizer notebook |

## Promote-time mutation

`handle_ki_queue_promote()` 成功後、entry には追加で以下が入る:

```json
{
  "status": "promoted",
  "promoted_at": "2026-04-18T00:10:00+09:00",
  "knowledge_dir": "/abs/path/knowledge/my_ki",
  "artifact_path": "/abs/path/knowledge/my_ki/artifacts/ki_xxx.md"
}
```

## API contracts

### `handle_ki_queue_list(arguments)`

#### Input

```json
{
  "status": "pending",
  "limit": 20
}
```

#### Output

```json
{
  "status": "ok",
  "queue_file": "/abs/path/ki-promotion-queue.jsonl",
  "knowledge_dir": "/abs/path/knowledge",
  "notebook_path": "/abs/path/notebook.ipynb",
  "count": 3,
  "entries": []
}
```

### `handle_ki_queue_promote(arguments)`

#### Required input

```json
{
  "entry_id": "ki_abcd1234ef567890"
}
```

#### Optional overrides

- `ki_name`
- `title`
- `summary`
- `artifact_name`
- `content`

#### Success output

```json
{
  "status": "promoted",
  "entry": {},
  "knowledge_dir": "/abs/path/knowledge/my_ki",
  "artifact_path": "/abs/path/knowledge/my_ki/artifacts/ki_xxx.md",
  "notebook_path": "/abs/path/notebook.ipynb",
  "indexed": true
}
```

#### Failure output

```json
{ "error": "entry_id is required" }
```

or

```json
{ "error": "queue entry not found: ki_xxx" }
```

## Related knowledge directory shape

promotion 先の `knowledge/<ki_name>/` には最低限:

- `metadata.json`
- `timestamps.json`
- `artifacts/*.md`

## Compatibility rule

`entry_id` が唯一の必須 key。  
`id` は **promote API 引数では無効** とみなす。  
