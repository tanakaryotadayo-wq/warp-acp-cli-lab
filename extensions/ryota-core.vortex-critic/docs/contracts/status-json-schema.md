# Pipeline Status JSON Schema

## Scope

source of truth:

- `assets/pipeline/scripts/bootstrap_pipeline_01.sh`
- `assets/pipeline/scripts/pipeline_01_runner.py`

この schema は **1つの `status.json` が bootstrap 段階と runner 段階で拡張される** 前提です。

## Bootstrap status

`bootstrap_pipeline_01.sh` がまず書く形:

```json
{
  "pipeline": "pipeline-01",
  "stage": "bootstrapped",
  "mounted": true,
  "mountMode": "nfs",
  "cbfHealthy": true,
  "n8nReady": true,
  "mountPath": "/abs/path",
  "driveRemote": "gdrive",
  "driveSubpath": "",
  "rclonePath": "/Users/me/.local/bin/rclone",
  "mountError": "",
  "rcloneLog": "/abs/path/rclone_nfs.log",
  "rcloneMountLog": "/abs/path/rclone_mount.log",
  "rcloneNfsLog": "/abs/path/rclone_nfs.log",
  "rcloneNfsPidFile": "/abs/path/rclone_nfs.pid",
  "rcloneNfsAddr": "127.0.0.1",
  "rcloneNfsPort": "39091",
  "workflowJson": "/abs/path/n8n-workflow-pipeline-01.json",
  "bootstrapScript": "/abs/path/bootstrap_pipeline_01.sh"
}
```

## Runner status

`pipeline_01_runner.py` が同じ `status.json` を読み込み、以下の key を付け足しながら上書きします。

### Common runner fields

```json
{
  "pipeline": "pipeline-01",
  "stage": "starting",
  "started_at": "2026-04-18T00:00:00",
  "repo_path": "/abs/path/repo",
  "repo_name": "vscode-oss",
  "mount_path": "/abs/path/mount",
  "drive_remote": "gdrive",
  "drive_subpath": "",
  "mounted": false,
  "cbfHealthy": true,
  "n8nReady": true,
  "artifacts": {}
}
```

### Stage progression

allowed stage values:

- `bootstrapped`
- `starting`
- `packetizing`
- `claude_analysis`
- `gemini_issue_split`
- `eck_persistence`
- `completed`
- `failed`

## Stage-specific fields

### After packetizing

```json
{
  "packetizer": {
    "exit_code": 0,
    "stderr": ""
  },
  "packet_summary": {
    "count": 123,
    "sample_packets": []
  },
  "cbf": {
    "packetized": {}
  }
}
```

### After Claude analysis

```json
{
  "artifacts": {
    "claude_analysis": "/abs/path/claude_analysis.json"
  },
  "cbf": {
    "claude_analysis": {}
  }
}
```

### After Gemini issue split

```json
{
  "artifacts": {
    "gemini_issue_candidates": "/abs/path/gemini_issue_candidates.json"
  },
  "issue_count": 5,
  "cbf": {
    "gemini_issue_split": {}
  }
}
```

### After ECK persistence

```json
{
  "artifacts": {
    "issue_packets": "/abs/path/issue_packets.jsonl",
    "eck_results": "/abs/path/eck_results.json",
    "eck_archive_dir": "/abs/path/eck_archive_20260418T000000"
  },
  "eck": {},
  "cbf": {
    "eck_persistence": {}
  },
  "completed_at": "2026-04-18T00:10:00"
}
```

### Failure shape

```json
{
  "stage": "failed",
  "error": "packetizer produced zero packets for vscode-oss; check harvest scope, mount state, or pass --allow-empty-packets to override",
  "failed_at": "2026-04-18T00:02:00"
}
```

## Fail-closed rule

`packet_summary.count <= 0` かつ `--allow-empty-packets` 未指定なら、status は `failed` へ移行する。

## Compatibility notes

この file には camelCase と snake_case が混在する。

| Legacy / bootstrap key | Runner key |
|---|---|
| `mountPath` | `mount_path` |
| `driveRemote` | `drive_remote` |
| `driveSubpath` | `drive_subpath` |

reader 実装は両系統を受けられる前提で扱うこと。  

## Mount mode notes

- `mountMode: "fuse"` は従来の `rclone mount`
- `mountMode: "nfs"` は Darwin fallback の `rclone serve nfs` + `mount_nfs`

`rcloneLog` は現在有効な mount mode の主ログを指す。  
