# Pipeline 01

`assets/pipeline/` は OSS packet 化と監査を回す Pipeline① の実装です。

## 役割

- repo を packet 化する
- packet を Neural Packet ledger に保存する
- CBF / n8n / mount 状態をまとめて status に出す
- VORTEX サイドバーから状態を確認できるようにする
- commit hook から queue へ積み、**issue-oriented follow-up** を非同期で回す
- Darwin では FUSE が無くても NFS fallback で Google Drive mount を成立させる

## サブディレクトリ

| Path | Purpose |
|---|---|
| `scripts/` | 起動・実行スクリプト |
| `intelligence/` | packet / harvest / bridge モジュール |
| `gate/` | CBF サーバー |
| `integration/` | n8n workflow 定義 |
