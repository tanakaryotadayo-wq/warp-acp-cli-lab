# Critic Hooks

`assets/critic/` は read-only critic lane 用のスクリプト群です。

## スクリプト

| Script | Purpose |
|---|---|
| `deepseek-critic.py` | DeepSeek API を使う基本 critic hook |
| `vortex-critic.py` | git diff とテスト証跡を見て、Completion Illusion を潰す VORTEX critic |

## 使い方

各スクリプトは stdin から JSON を受け取り、hook 互換の JSON を stdout に返します。

```bash
echo '{"prompt":"Review the diff"}' | python3 assets/critic/vortex-critic.py
```

## 付属設定

- `deepseek-critic.config.json`
- `vortex-critic.config.json`

どちらも任意で、未配置ならスクリプト内のデフォルト設定を使います。
