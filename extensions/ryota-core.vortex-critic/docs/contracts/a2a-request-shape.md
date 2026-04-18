# A2A Request Shape

## Scope

対象 surface:

- `POST /v1/message:send`
- `POST /v1/message:stream`
- `POST /tasks`
- `POST /executeCommand`
- `GET /.well-known/agent-card.json`

source of truth: `assets/gemini/gemini_a2a_bridge.py`

## Request body for `/v1/message:send`

### Accepted top-level keys

| Key | Required | Type | Notes |
|---|---|---|---|
| `message` | preferred | object | 正式な入力 |
| `request` | alias | object | `message` の alias として受理 |
| `configuration` | no | object | `blocking` など |
| `metadata` | no | object | top-level metadata |

### Accepted message shape

REST 経由では、bridge は次の2形式を受理します。

#### Form A: explicit envelope

```json
{
  "message": {
    "kind": "message",
    "messageId": "msg-1",
    "contextId": "ctx-1",
    "taskId": "",
    "role": "user",
    "parts": [
      { "kind": "text", "text": "utility: summarize this" }
    ],
    "metadata": {}
  }
}
```

#### Form B: envelope-less REST form

```json
{
  "message": {
    "role": "user",
    "parts": [
      { "kind": "text", "text": "utility: summarize this" }
    ]
  },
  "configuration": {
    "blocking": true
  }
}
```

### Normalization rules

`normalize_message()` は以下を保証します。

1. `kind: "message"` があれば、その `parts` を使う  
2. それが無い場合は `content` を見る  
3. `content` が空なら `parts` を使う  
4. `messageId`, `contextId`, `taskId` は snake_case alias も吸収する  
5. `role` は内部 role に coercion される

## Supported part shapes

| Input shape | Normalized kind |
|---|---|
| `{ "kind": "text", "text": "..." }` | `text` |
| `{ "text": "..." }` | `text` |
| `{ "file": { ... } }` | `file` |
| `{ "data": ... }` | `data` |

## Response body for `/v1/message:send`

```json
{
  "task": {
    "id": "task-id",
    "contextId": "context-id",
    "status": {
      "state": "TASK_STATE_COMPLETED",
      "message": {
        "messageId": "msg-id",
        "contextId": "context-id",
        "taskId": "task-id",
        "role": "ROLE_AGENT",
        "content": [
          { "text": "..." }
        ],
        "metadata": {}
      },
      "timestamp": "2026-04-18T00:00:00+09:00"
    },
    "artifacts": [],
    "history": [],
    "metadata": {}
  }
}
```

## Streaming response for `/v1/message:stream`

返るイベントは REST 変換された status update です。

```json
{
  "taskId": "task-id",
  "contextId": "context-id",
  "status": {
    "state": "TASK_STATE_WORKING",
    "message": null,
    "timestamp": "2026-04-18T00:00:00+09:00"
  },
  "final": false,
  "metadata": {}
}
```

## `POST /tasks`

### Input

```json
{
  "agentSettings": {
    "route": "utility"
  },
  "contextId": "optional-context-id"
}
```

### Output

created task id だけを返す。

## `POST /executeCommand`

この endpoint は bridge command surface 用。  
`command` と command-specific payload を受ける。

## Compatibility notes

- REST A2A client が `kind:"message"` を付けなくても text は落とさない  
- route 強制は本文 prefix (`utility:`, `agent:`, `chat:`) でも行える  
- multimodal backend が無い場合、image 系入力は fail-fast する  
