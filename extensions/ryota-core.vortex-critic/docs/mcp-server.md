# MCP Server Module

## 定義

このモジュールは **VORTEX nested repo の内側には存在しません**。  
実体は隣接パスの `../fusion-copilot/mcp-server/` にある、**外部 orchestration モジュール**です。

VORTEX の説明書に含める理由は、運用上このサーバーが **Qwen / Gemini / Jules / n8n orchestration の公開面**だからです。

## 実体パス

```text
/Users/ryyota/vscode-oss/extensions/fusion-copilot/mcp-server
```

## package 契約

- package 名: `fusion-orchestrator-v2`
- transport: stdio MCP
- runtime: Node.js + TypeScript

## 責務

1. Qwen chat / code / batch を MCP tool として公開する  
2. Jules / Gemini CLI / n8n trigger を外部クライアントから呼べるようにする  
3. orchestration を VS Code 拡張本体から分離する  

## ツール群

README と `package.json` から見える主な公開群:

- `qwen_chat`
- `qwen_code`
- `qwen_batch`
- `jules_*`
- `gemini_deepthink`
- `gemini_deepsearch`
- `n8n_trigger`
- `qwen_health`
- `orchestrate`

## 依存

| Dependency | Purpose |
|---|---|
| `jules` CLI | 非同期 coding session |
| `gemini` CLI | DEEPTHINK / DEEPSEARCH |
| `gh` CLI | GitHub 連携 |
| `n8n` | workflow trigger |

## VORTEX 本体との境界

- VORTEX nested repo は**このサーバーの UI や説明を持てる**
- ただし **実装 source of truth は external module 側**
- VORTEX から見れば「隣の orchestration backend」

## 改修ルール

1. このモジュールの仕様変更は、VORTEX 本体 README でも**external dependency 変更**として追記する。  
2. nested repo 側で存在しないパスを、同梱物のように書かない。  
3. MCP ツール増減時は、**VORTEX がどこまで直接依存しているか**も明記する。  
