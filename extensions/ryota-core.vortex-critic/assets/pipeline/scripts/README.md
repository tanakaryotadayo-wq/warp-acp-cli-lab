# Pipeline Scripts

このディレクトリは Pipeline① の起動スクリプトです。

| Script | Purpose |
|---|---|
| `bootstrap_pipeline_01.sh` | rclone mount / CBF / n8n をまとめて起動し、status JSON を書く |
| `pipeline_01_runner.py` | packet harvest → issue packet 化 → レポート生成の runner |
| `pipeline_01_enqueue_commit.py` | commit を queue に積み、必要なら worker を起動する |
| `pipeline_01_commit_worker.py` | queue を drain し、commit snapshot を Pipeline① に流して issue 化する |
| `install_pipeline_01_git_hook.sh` | `post-commit` hook を install し、commit → enqueue を自動化する |

## 実行例

```bash
bash assets/pipeline/scripts/bootstrap_pipeline_01.sh
python3 assets/pipeline/scripts/pipeline_01_runner.py
python3 assets/pipeline/scripts/pipeline_01_enqueue_commit.py --repo-root /path/to/repo
bash assets/pipeline/scripts/install_pipeline_01_git_hook.sh
```

## Commit-driven workflow

1. `install_pipeline_01_git_hook.sh` で `post-commit` hook を入れる  
2. commit 完了直後に `pipeline_01_enqueue_commit.py` が queue に投入  
3. worker が必要なら自動起動  
4. worker は `git archive <sha>` で **clean snapshot** を作り、その commit だけを Pipeline① に流す  
5. 生成された issue candidate は 1 本の GitHub issue として publish できる  

## Mount fallback on macOS

- official rclone を `~/.local/bin/rclone` に置くと、bootstrap はそれを優先する  
- macFUSE / FUSE-T が無い Darwin 環境では、`rclone mount` 失敗を放置せず  
  `rclone serve nfs` + `mount_nfs` に fallback する  
- status JSON の `mountMode` で `fuse` / `nfs` を判別できる  
