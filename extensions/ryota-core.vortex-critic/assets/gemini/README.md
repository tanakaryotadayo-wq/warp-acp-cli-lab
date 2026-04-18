# Gemini Bridge and Memory Pipeline

`assets/gemini/` は Gemini Code Assist の A2A ブリッジと、VORTEX の memory pipeline をまとめた領域です。

## 主要スクリプト

| Script | Purpose |
|---|---|
| `bootstrap_gemini_code_assist.sh` | Gemini ブリッジの起動補助 |
| `gemini_a2a_bridge.py` | A2A HTTP ブリッジ本体 |
| `memory_pipeline.py` | fleet_log → KI queue → promote → index → recall |
| `fleet_bridge.py` | MCP / fleet 側の配線 |
| `pcc_critic.py` | PCC 制約付き critic |
| `pcc_critic_standalone.py` | 単体実行用 critic |
| `titan_mcp_bridge.py` | Titan / remote MCP ブリッジ |

## 付属資産

- `newgate_profile.json`
- `colab_ki_vectorizer.ipynb`

## 典型フロー

1. `gemini_a2a_bridge.py` が lane を受ける
2. `memory_pipeline.py` が成功ログと KI 候補を蓄積する
3. promote 後に `ConversationMemory.index_knowledge()` で索引化する
4. recall を bridge prompt に注入する

## 実行例

```bash
python3 assets/gemini/gemini_a2a_bridge.py --host 127.0.0.1 --port 8765
```
