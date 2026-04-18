import * as vscode from 'vscode';
import * as fs from 'fs';
import * as path from 'path';

export function getDashboardHtml(extensionUri: vscode.Uri, logDir: string, getStatus: () => any): string {
    const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
    const logFile = path.join(logDir, `fleet_${today}.jsonl`);
    
    let logs: any[] = [];
    let successCount = 0;
    let failureCount = 0;
    let recoveryCount = 0;

    try {
        if (fs.existsSync(logFile)) {
            const content = fs.readFileSync(logFile, 'utf-8');
            const lines = content.trim().split('\n').filter(Boolean);
            logs = lines.map(line => JSON.parse(line)).reverse().slice(0, 20); // Last 20
            
            lines.forEach(line => {
                try {
                    const parsed = JSON.parse(line);
                    if (parsed.event_type === 'success') successCount++;
                    if (parsed.event_type === 'failure') failureCount++;
                    if (parsed.event_type === 'recovery') recoveryCount++;
                } catch(e) {}
            });
        }
    } catch {
        // Ignore parsing errors
    }

    const total = successCount + failureCount + recoveryCount;
    const successRate = total > 0 ? Math.round(((successCount + recoveryCount) / total) * 100) : 0;
    const status = getStatus();
    const newgate = status.newgate ?? {};
    const bridgeRuntime = status.bridgeRuntime ?? {};
    const kiQueue = status.kiQueue ?? {};
    const pipelineOne = status.pipelineOne ?? {};
    const newgateConnected = Boolean(newgate.connected);
    const newgateBadgeClass = newgateConnected ? 'badge-success' : 'badge-failure';
    const newgateBridgeLabel = bridgeRuntime.note || bridgeRuntime.bridgeUrl || newgate.bridgeUrl || 'not configured';
    const kiNotebookBadgeClass = kiQueue.notebookExists ? 'badge-success' : 'badge-recovery';
    const kiNotebookLabel = kiQueue.notebookExists ? '準備完了' : '未設定';
    const pipelineOneStage = pipelineOne.stage || '未起動';
    const pipelineOneStageBadge = pipelineOneStage === 'completed'
        ? 'badge-success'
        : pipelineOneStage === 'failed'
            ? 'badge-failure'
            : 'badge-recovery';
    const pipelineOneMountBadge = pipelineOne.mounted ? 'badge-success' : 'badge-failure';
    const pipelineOneCbfBadge = pipelineOne.cbfHealthy ? 'badge-success' : 'badge-failure';
    const pipelineOneN8nBadge = pipelineOne.n8nReady ? 'badge-success' : 'badge-recovery';

    // Premium UI Design
    return `<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VORTEX ダッシュボード</title>
    <style>
        :root {
            --bg: #0f111a;
            --surface: #1e2130;
            --surface-hover: #2a2d3e;
            --primary: #00d2ff;
            --secondary: #3a7bd5;
            --success: #2ecc71;
            --danger: #e74c3c;
            --warning: #f1c40f;
            --text: #e2e8f0;
            --text-muted: #94a3b8;
            --glass: rgba(30, 33, 48, 0.7);
            --glass-border: rgba(255, 255, 255, 0.1);
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background: radial-gradient(circle at top left, #12172b 0%, var(--bg) 100%);
            color: var(--text);
            margin: 0;
            padding: 15px;
            animation: fadeIn 0.6s ease-out;
            min-height: 100vh;
            font-size: 13px;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .header {
            display: flex;
            flex-direction: column;
            gap: 15px;
            margin-bottom: 25px;
            border-bottom: 1px solid var(--glass-border);
            padding-bottom: 15px;
        }

        .header-title {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 5px;
        }

        .logo {
            font-size: 1.8em;
            background: -webkit-linear-gradient(45deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: 800;
            letter-spacing: 1px;
            margin-bottom: 2px;
        }

        .grid {
            display: flex;
            flex-direction: column;
            gap: 15px;
            margin-bottom: 25px;
        }

        .card {
            background: var(--glass);
            backdrop-filter: blur(10px);
            border: 1px solid var(--glass-border);
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.3);
            transition: transform 0.3s ease, box-shadow 0.3s ease;
        }

        .card:hover {
            transform: translateY(-2px);
            box-shadow: 0 12px 40px 0 rgba(0, 210, 255, 0.15);
        }

        .card h3 {
            margin: 0 0 12px 0;
            color: var(--text-muted);
            font-size: 0.85em;
            text-transform: uppercase;
            letter-spacing: 1px;
        }

        .stat-value {
            font-size: 2.2em;
            font-weight: bold;
            display: flex;
            align-items: baseline;
            gap: 8px;
        }

        .stat-subtitle {
            font-size: 0.35em;
            color: var(--text-muted);
            font-weight: normal;
        }

        .color-success { color: var(--success); }
        .color-danger { color: var(--danger); }
        .color-warning { color: var(--warning); }
        .color-primary { color: var(--primary); }

        .progress-bar {
            width: 100%;
            height: 8px;
            background: #2a2d3e;
            border-radius: 4px;
            margin-top: 15px;
            overflow: hidden;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--secondary), var(--primary));
            width: ${successRate}%;
            border-radius: 4px;
            box-shadow: 0 0 10px var(--primary);
        }

        /* Logs Table */
        .logs-container {
            background: var(--glass);
            border: 1px solid var(--glass-border);
            border-radius: 12px;
            padding: 15px;
            overflow-x: auto;
        }

        .log-list {
            display: flex;
            flex-direction: column;
            gap: 12px;
        }

        .log-item {
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid rgba(255, 255, 255, 0.03);
            border-radius: 8px;
            padding: 12px;
            display: flex;
            flex-direction: column;
            gap: 8px;
            transition: background 0.2s;
        }

        .log-item:hover {
            background: rgba(255, 255, 255, 0.02);
            border-color: rgba(255, 255, 255, 0.1);
        }

        .log-header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .log-task {
            font-weight: 500;
            font-size: 0.9em;
            color: var(--text);
            line-height: 1.3;
        }

        .log-result {
            font-size: 0.8em;
            color: var(--text-muted);
            background: rgba(0, 0, 0, 0.15);
            padding: 8px;
            border-radius: 4px;
            word-break: break-word;
        }

        .badge {
            padding: 4px 10px;
            border-radius: 20px;
            font-size: 0.75em;
            font-weight: bold;
            text-transform: uppercase;
        }

        .badge-success { background: rgba(46, 204, 113, 0.2); border: 1px solid var(--success); color: var(--success); }
        .badge-failure { background: rgba(231, 76, 60, 0.2); border: 1px solid var(--danger); color: var(--danger); }
        .badge-recovery { background: rgba(241, 196, 15, 0.2); border: 1px solid var(--warning); color: var(--warning); }

        .actions {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            width: 100%;
            margin-top: 5px;
        }

        .btn {
            background: transparent;
            border: 1px solid var(--primary);
            color: var(--primary);
            padding: 10px 20px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }

        .btn:hover {
            background: var(--primary);
            color: var(--bg);
            box-shadow: 0 0 15px var(--primary);
        }

        .btn-primary {
            background: var(--primary);
            color: var(--bg);
        }

        .btn-primary:hover {
            background: #fff;
            border-color: #fff;
            box-shadow: 0 0 20px #fff;
        }

        .critic-panel {
            display: flex;
            flex-direction: column;
            gap: 10px;
        }
        
        .status-indicator {
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: var(--success);
            box-shadow: 0 0 8px var(--success);
            margin-right: 8px;
        }
    </style>
</head>
<body>

    <div class="header">
        <div class="header-title">
            <div class="logo">🌀 VORTEX</div>
            <div style="display: flex; align-items: center;">
                <span class="status-indicator"></span> 
                <span style="color: var(--text-muted); font-size: 0.85em; font-weight: 500; letter-spacing: 1px;">システム稼働中</span>
            </div>
        </div>
        <div class="actions">
            <button class="btn" onclick="postMessage('refresh')">🔄 Refresh</button>
            <button class="btn btn-primary" onclick="postMessage('runAudit')">⚡ Run Audit</button>
            <button class="btn" onclick="postMessage('startGeminiBridge')">🚀 Gemini Bridge 起動</button>
            <button class="btn" onclick="postMessage('packetizeAntigravity')">📦 Antigravity Packet 抽出</button>
            <button class="btn" onclick="postMessage('runPipelineOne')">🧬 Pipeline① 開始</button>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h3>フリート状況</h3>
            <div class="stat-value color-primary">
                ${successRate}% <span class="stat-subtitle">成功率</span>
            </div>
            <div class="progress-bar">
                <div class="progress-fill"></div>
            </div>
            <div style="margin-top: 15px; font-size: 0.75em; display: flex; justify-content: space-between; color: var(--text-muted); font-weight: 600;">
                <span style="color: var(--success)">${successCount} OK</span>
                <span style="color: var(--warning)">${recoveryCount} FIX</span>
                <span style="color: var(--danger)">${failureCount} ERR</span>
            </div>
        </div>

        <div class="card">
            <h3>Critic 状態</h3>
            <div class="critic-panel">
                <div>
                    <span style="color: var(--text-muted); font-size: 0.8em;">Preset:</span> 
                    <span class="badge badge-success" style="float:right;">#${status.preset}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Engine:</span> 
                    <span style="float:right;">DeepSeek VORTEX</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">直近判定:</span> 
                    <span style="float:right;" class="${status.lastVerdict === 'VERIFIED' ? 'color-success' : status.lastVerdict === 'UNVERIFIED' ? 'color-danger' : 'color-warning'}">
                        ${status.lastVerdict || '保留'}
                    </span>
                </div>
            </div>
        </div>

        <div class="card">
            <h3>Gemini Bridge / Newgate</h3>
            <div class="critic-panel">
                <div>
                    <span style="color: var(--text-muted); font-size: 0.8em;">Bridge:</span>
                    <span class="badge ${newgateBadgeClass}" style="float:right;">${newgateConnected ? '接続中' : '切断中'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Version:</span>
                    <span style="float:right;">${newgate.version || '-'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Launcher:</span>
                    <span style="float:right;">${bridgeRuntime.launcher || '-'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Session:</span>
                    <span style="float:right;">${bridgeRuntime.tmuxSession || '-'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">埋め込み:</span>
                    <span style="float:right;">${newgate.embeddingModel || '-'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">記憶:</span>
                    <span style="float:right;">${newgate.recallStatus || '-'} / ${newgate.storeStatus || '-'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">P0項目:</span>
                    <span style="float:right;">${newgate.p0Count ?? 0} 件</span>
                </div>
            </div>
            <div class="log-result" style="margin-top: 12px;">${newgateBridgeLabel}</div>
            ${newgate.error ? `<div style="margin-top: 8px; color: var(--warning); font-size: 0.8em;">${newgate.error}</div>` : ''}
            <div class="actions" style="margin-top: 12px;">
                <button class="btn" onclick="postMessage('startGeminiBridge')">🚀 Bridge 起動</button>
                <button class="btn" onclick="postMessage('openGeminiBridge')">📡 Bridge Snapshot</button>
            </div>
        </div>

        <div class="card">
            <h3>KI 昇格キュー</h3>
            <div class="critic-panel">
                <div>
                    <span style="color: var(--text-muted); font-size: 0.8em;">未昇格:</span>
                    <span style="float:right;" class="color-warning">${kiQueue.pendingCount ?? 0}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">昇格済み:</span>
                    <span style="float:right;" class="color-success">${kiQueue.promotedCount ?? 0}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Colab:</span>
                    <span class="badge ${kiNotebookBadgeClass}" style="float:right;">${kiNotebookLabel}</span>
                </div>
            </div>
            <div class="log-result" style="margin-top: 12px;">${kiQueue.latestPendingTitle || '未昇格の KI 候補はありません'}</div>
            <div style="margin-top: 8px; color: var(--text-muted); font-size: 0.78em;">${kiQueue.queueFile || 'queue file 未設定'}</div>
            <div class="actions" style="margin-top: 12px;">
                <button class="btn" onclick="postMessage('openKiQueue')">📚 キューを開く</button>
                <button class="btn" onclick="postMessage('promoteKi')">⬆️ 昇格する</button>
            </div>
            <div style="margin-top: 10px;">
                <button class="btn" onclick="postMessage('openKiNotebook')">📓 Colab Notebook を開く</button>
            </div>
        </div>

        <div class="card">
            <h3>Pipeline①</h3>
            <div class="critic-panel">
                <div>
                    <span style="color: var(--text-muted); font-size: 0.8em;">Stage:</span>
                    <span class="badge ${pipelineOneStageBadge}" style="float:right;">${pipelineOneStage}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Drive mount:</span>
                    <span class="badge ${pipelineOneMountBadge}" style="float:right;">${pipelineOne.mounted ? 'ready' : 'down'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">CBF:</span>
                    <span class="badge ${pipelineOneCbfBadge}" style="float:right;">${pipelineOne.cbfHealthy ? 'ready' : 'down'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">n8n:</span>
                    <span class="badge ${pipelineOneN8nBadge}" style="float:right;">${pipelineOne.n8nReady ? 'ready' : 'boot'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Runtime:</span>
                    <span style="float:right;">${pipelineOne.containerRuntime || '-'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">CBF起動:</span>
                    <span style="float:right;">${pipelineOne.cbfLauncher || '-'}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Packets:</span>
                    <span style="float:right;">${pipelineOne.packetCount ?? 0}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">Issues:</span>
                    <span style="float:right;">${pipelineOne.issueCount ?? 0}</span>
                </div>
                <div style="margin-top: 10px;">
                    <span style="color: var(--text-muted); font-size: 0.8em;">ECK runs:</span>
                    <span style="float:right;">${pipelineOne.eckRuns ?? 0}</span>
                </div>
            </div>
            <div class="log-result" style="margin-top: 12px;">${pipelineOne.repoPath || 'repo 未設定'}</div>
            <div style="margin-top: 8px; color: var(--text-muted); font-size: 0.78em;">${pipelineOne.mountPath || 'mount 未設定'}</div>
            <div style="margin-top: 6px; color: var(--text-muted); font-size: 0.75em;">docker context: ${pipelineOne.dockerContext || '-'}</div>
            ${pipelineOne.error ? `<div style="margin-top: 8px; color: var(--warning); font-size: 0.8em;">${pipelineOne.error}</div>` : ''}
            <div class="actions" style="margin-top: 12px;">
                <button class="btn" onclick="postMessage('runPipelineOne')">▶ 開始</button>
                <button class="btn" onclick="postMessage('openPipelineOne')">📄 Snapshot</button>
            </div>
        </div>
    </div>

    <div class="logs-container">
        <h3 style="margin: 0 0 20px 0; color: var(--text-muted); letter-spacing: 1px; font-size: 0.9em;">フリート運用ログ</h3>
        ${logs.length > 0 ? `
        <div class="log-list">
            ${logs.map(log => `
            <div class="log-item">
                <div class="log-header-row">
                    <span class="badge badge-${log.event_type}">${log.event_type}</span>
                    <span style="color: var(--text-muted); font-size: 0.8em;">${new Date(log.timestamp).toLocaleTimeString()}</span>
                </div>
                <div class="log-task">${log.task || '-'}</div>
                <div class="log-result">
                    ${log.result || log.cause || '-'}
                    ${(log.tags || []).length > 0 ? `<div style="margin-top: 5px; color: var(--primary); opacity: 0.8;">${(log.tags || []).map((t: string) => `#${t}`).join(' ')}</div>` : ''}
                </div>
            </div>
            `).join('')}
        </div>
        ` : `
        <div style="text-align: center; padding: 40px; color: var(--text-muted);">
            今日はまだフリートログがありません。
        </div>
        `}
    </div>

    <script>
        const vscode = acquireVsCodeApi();
        function postMessage(command) {
            vscode.postMessage({ command });
        }
    </script>
</body>
</html>`;
}

export function getJulesHtml(content: string, isLoading: boolean): string {
    let initialData = '[]';
    let rawContentHtml = '';

    if (!isLoading) {
        if (content.startsWith('[')) {
            // It's JSON from GitHub API
            initialData = content;
        } else {
            rawContentHtml = '<div class="error-box"><pre>' + content + '</pre></div>';
        }
    }

    return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Jules Operations</title>
    <style>
        :root {
            --bg: #0f111a;
            --panel-bg: rgba(30, 33, 48, 0.7);
            --primary: #00d2ff;
            --secondary: #3a7bd5;
            --success: #2ecc71;
            --warning: #f1c40f;
            --text: #e2e8f0;
            --text-muted: #94a3b8;
            --glass-border: rgba(255, 255, 255, 0.1);
            --row-hover: rgba(255, 255, 255, 0.05);
            --comment-bg: rgba(255, 255, 255, 0.03);
            --comment-jules: rgba(0, 210, 255, 0.08);
        }

        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 20px;
            height: 100vh;
            box-sizing: border-box;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }

        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid var(--glass-border);
            flex-shrink: 0;
        }

        .title {
            font-size: 1.8em;
            background: -webkit-linear-gradient(45deg, var(--primary), var(--secondary));
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            font-weight: bold;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .btn {
            background: transparent;
            border: 1px solid var(--primary);
            color: var(--primary);
            padding: 8px 16px;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            transition: all 0.2s;
        }
        .btn:hover {
            background: var(--primary);
            color: var(--bg);
            box-shadow: 0 0 10px var(--primary);
        }
        .btn:disabled {
            opacity: 0.45;
            cursor: default;
            box-shadow: none;
            background: transparent;
            color: var(--text-muted);
            border-color: var(--glass-border);
        }
        .btn-sm {
            padding: 6px 10px;
            font-size: 0.8em;
        }

        .layout {
            display: flex;
            flex: 1;
            gap: 20px;
            overflow: hidden;
        }

        .sidebar {
            width: 35%;
            background: var(--panel-bg);
            border: 1px solid var(--glass-border);
            border-radius: 12px;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
        }

        .main-panel {
            width: 65%;
            background: var(--panel-bg);
            border: 1px solid var(--glass-border);
            border-radius: 12px;
            display: flex;
            flex-direction: column;
            display: none; /* hidden until selected */
            overflow: hidden;
        }

        .issue-item {
            padding: 15px;
            border-bottom: 1px solid var(--glass-border);
            cursor: pointer;
            transition: background 0.2s;
        }
        .issue-item:hover, .issue-item.active {
            background: var(--row-hover);
        }
        .issue-title {
            font-weight: bold;
            font-size: 0.95em;
            margin-bottom: 8px;
            line-height: 1.4;
            color: #fff;
        }
        .issue-meta {
            font-size: 0.8em;
            color: var(--text-muted);
            display: flex;
            justify-content: space-between;
        }
        
        .badge {
            padding: 3px 8px;
            border-radius: 8px;
            font-size: 0.75em;
            font-weight: bold;
            border: 1px solid;
            white-space: nowrap;
        }
        .badge-awaiting { color: var(--warning); border-color: var(--warning); background: rgba(241, 196, 15, 0.1); }
        .badge-active { color: var(--primary); border-color: var(--primary); background: rgba(0, 210, 255, 0.1); }

        .chat-header {
            padding: 15px 20px;
            border-bottom: 1px solid var(--glass-border);
            background: rgba(0,0,0,0.2);
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
        }
        .chat-header-title {
            font-weight: bold;
            font-size: 1.1em;
            word-break: break-all;
        }
        .chat-header-actions {
            display: flex;
            gap: 8px;
            flex-shrink: 0;
        }
        .chat-history {
            flex: 1;
            padding: 20px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 15px;
        }
        
        .comment {
            background: var(--comment-bg);
            border: 1px solid var(--glass-border);
            padding: 15px;
            border-radius: 8px;
            font-size: 0.9em;
            line-height: 1.5;
        }
        .comment.jules {
            background: var(--comment-jules);
            border-color: rgba(0, 210, 255, 0.3);
        }
        .comment-header {
            display: flex;
            justify-content: space-between;
            margin-bottom: 10px;
            font-size: 0.85em;
            color: var(--text-muted);
            border-bottom: 1px solid rgba(255,255,255,0.05);
            padding-bottom: 5px;
        }
        .comment-body {
            white-space: pre-wrap;
            word-wrap: break-word;
        }

        .chat-input {
            padding: 15px;
            border-top: 1px solid var(--glass-border);
            display: flex;
            flex-direction: column;
            gap: 10px;
            background: rgba(0,0,0,0.2);
        }
        textarea {
            width: 100%;
            background: rgba(0,0,0,0.3);
            border: 1px solid var(--glass-border);
            color: white;
            padding: 12px;
            border-radius: 6px;
            resize: vertical;
            min-height: 80px;
            font-family: inherit;
            box-sizing: border-box;
        }
        .quick-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            justify-content: flex-end;
        }
        .hint {
            color: var(--text-muted);
            font-size: 0.8em;
        }
        textarea:focus {
            outline: none;
            border-color: var(--primary);
        }
        .chat-actions {
            display: flex;
            justify-content: flex-end;
        }

        /* Loading */
        .loader {
            color: var(--primary); 
            text-align: center; 
            padding: 40px; 
            font-size: 1.2em;
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse { 0% { opacity: 0.5; } 50% { opacity: 1; } 100% { opacity: 0.5; } }
    </style>
</head>
<body>
    <div class="header">
        <div class="title">✨ Jules Communicator</div>
        <button class="btn" onclick="vscode.postMessage({ command: 'refresh' })">🔄 Refresh</button>
    </div>
    
    ` + (isLoading ? '<div class="loader">Scanning active sessions...</div>' : '') + `
    ` + rawContentHtml + `

    <div class="layout" id="app-layout" style="` + ((isLoading || rawContentHtml) ? 'display: none;' : '') + `">
        <div class="sidebar" id="sidebar"></div>
        <div class="main-panel" id="main-panel">
            <div class="chat-header">
                <div class="chat-header-title" id="chat-header">Select an issue</div>
                <div class="chat-header-actions">
                    <button class="btn btn-sm" id="open-issue-btn" disabled>Open Issue</button>
                    <button class="btn btn-sm" id="open-pr-btn" disabled>Open Linked PR</button>
                </div>
            </div>
            <div class="chat-history" id="chat-history"></div>
            <div class="chat-input" id="chat-input-container">
                <div class="quick-actions">
                    <button class="btn btn-sm" data-template="implementPr">Implement + PR</button>
                    <button class="btn btn-sm" data-template="reviewFix">Review 対応</button>
                    <button class="btn btn-sm" data-template="pushRetry">Push / Sync Retry</button>
                </div>
                <div class="hint">Jules は非同期 worker です。数十秒〜数分の遅延を前提に、Issue / PR / コメントを往復します。</div>
                <textarea id="reply-box" placeholder="Type your reply to Jules..."></textarea>
                <div class="chat-actions">
                    <button class="btn" id="send-btn">Send Reply</button>
                </div>
            </div>
        </div>
    </div>

    <script>
        const vscode = acquireVsCodeApi();
        const issues = ` + initialData + `;
        let activeIssue = null;
        let activeComments = [];

        function timeAgo(dateString) {
            const date = new Date(dateString);
            const seconds = Math.floor((new Date() - date) / 1000);
            let interval = seconds / 3600;
            if (interval > 24) return Math.floor(interval / 24) + " days ago";
            if (interval >= 1) return Math.floor(interval) + " h ago";
            interval = seconds / 60;
            if (interval >= 1) return Math.floor(interval) + " m ago";
            return Math.floor(seconds) + " s ago";
        }

        function renderSidebar() {
            const sidebar = document.getElementById('sidebar');
            sidebar.innerHTML = '';

            if (issues.length === 0) {
                sidebar.innerHTML = '<div style="padding: 20px; color: var(--text-muted); text-align:center;">No active Jules issues found.</div>';
                return;
            }

            // Sort: Assume issues with "awaiting" or more recent comments are higher priority
            issues.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));

            issues.forEach(issue => {
                const el = document.createElement('div');
                el.className = 'issue-item';
                el.dataset.issueId = String(issue.id);
                
                // Try to infer status from labels
                let isAwaiting = false;
                if (issue.labels) {
                    isAwaiting = issue.labels.some(l => l.name.toLowerCase().includes('awaiting'));
                }
                const badgeClass = isAwaiting ? 'badge-awaiting' : 'badge-active';
                const badgeText = isAwaiting ? 'Awaiting Feedback' : 'Active';

                el.innerHTML = 
                    '<div class="issue-title">' + issue.title + '</div>' +
                    '<div class="issue-meta">' +
                        '<span>#' + issue.number + '</span>' +
                        '<span class="badge ' + badgeClass + '">' + badgeText + '</span>' +
                    '</div>' +
                    '<div class="issue-meta" style="margin-top: 5px;">' +
                        '<span>' + timeAgo(issue.updated_at) + '</span>' +
                    '</div>';

                el.onclick = () => selectIssue(issue, el);
                sidebar.appendChild(el);
            });
        }

        function selectIssue(issue, element) {
            activeIssue = issue;
            activeComments = [];
            document.querySelectorAll('.issue-item').forEach(el => el.classList.remove('active'));
            if (element) element.classList.add('active');

            const mainPanel = document.getElementById('main-panel');
            mainPanel.style.display = 'flex';

            document.getElementById('chat-header').textContent = issue.title;
            const history = document.getElementById('chat-history');
            history.innerHTML = '<div class="loader">Loading conversation...</div>';
            updateLinkButtons();

            // Request comments from the extension (authenticated fetch)
            vscode.postMessage({
                command: 'fetchComments',
                commentsUrl: issue.comments_url,
                issueId: issue.id
            });
        }

        function renderComments(comments) {
            const history = document.getElementById('chat-history');
            history.innerHTML = '';
            activeComments = Array.isArray(comments) ? comments.slice() : [];

            // Add original issue body
            if (activeIssue && activeIssue.body) {
                comments.unshift({
                    user: activeIssue.user,
                    created_at: activeIssue.created_at,
                    body: activeIssue.body
                });
            }

            if (!comments || comments.length === 0) {
                history.innerHTML = '<div style="color: var(--text-muted);">No comments yet.</div>';
                return;
            }

            comments.forEach(c => {
                const isJules = c.user.login.toLowerCase().includes('jules');
                const el = document.createElement('div');
                el.className = 'comment ' + (isJules ? 'jules' : '');
                el.innerHTML = 
                    '<div class="comment-header">' +
                        '<strong style="color: ' + (isJules ? 'var(--primary)' : 'var(--text)') + '">' + c.user.login + '</strong>' +
                        '<span>' + new Date(c.created_at).toLocaleString() + '</span>' +
                    '</div>' +
                    '<div class="comment-body">' + escapeHtml(c.body) + '</div>';
                history.appendChild(el);
            });

            // Scroll to bottom
            history.scrollTop = history.scrollHeight;
            updateLinkButtons();
        }

        function extractPullUrls(text) {
            if (!text) return [];
            const matches = String(text).match(/https:\/\/github\.com\/[^\s/]+\/[^\s/]+\/pull\/\d+/g) || [];
            return [...new Set(matches)];
        }

        function getPrimaryPullUrl() {
            const urls = [];
            if (activeIssue && activeIssue.body) {
                urls.push(...extractPullUrls(activeIssue.body));
            }
            activeComments.forEach(comment => {
                urls.push(...extractPullUrls(comment.body));
            });
            return [...new Set(urls)][0] || null;
        }

        function updateLinkButtons() {
            const issueBtn = document.getElementById('open-issue-btn');
            const prBtn = document.getElementById('open-pr-btn');
            if (issueBtn) {
                issueBtn.disabled = !(activeIssue && activeIssue.html_url);
            }
            const prUrl = getPrimaryPullUrl();
            if (prBtn) {
                prBtn.disabled = !prUrl;
            }
        }

        function prefillReply(kind) {
            const box = document.getElementById('reply-box');
            const templates = {
                implementPr: 'Jules, please implement the requested changes on your working branch, run the relevant checks, push the branch, and open or update the PR. If you are blocked, comment with the exact blocker and the next command you need.',
                reviewFix: 'Jules, please address the latest review feedback, rerun the relevant checks, push the updated branch, and summarize the exact diff in your next comment.',
                pushRetry: 'Jules, please sync with the latest base branch, retry the push / PR update flow, and report whether the PR is ready or still blocked.'
            };
            box.value = templates[kind] || '';
            box.focus();
        }
        
        function escapeHtml(unsafe) {
            if (!unsafe) return "";
            return String(unsafe)
                 .replace(/&/g, "&amp;")
                 .replace(/</g, "&lt;")
                 .replace(/>/g, "&gt;")
                 .replace(/"/g, "&quot;")
                 .replace(/'/g, "&#039;");
        }

        document.getElementById('send-btn').addEventListener('click', () => {
            const box = document.getElementById('reply-box');
            const text = box.value.trim();
            if (!text || !activeIssue) return;

            // Send reply via extension
            vscode.postMessage({
                command: 'postReply',
                commentsUrl: activeIssue.comments_url,
                issueId: activeIssue.id,
                body: text
            });

            box.value = '';
            document.getElementById('send-btn').textContent = 'Sending...';
        });

        document.getElementById('open-issue-btn').addEventListener('click', () => {
            if (activeIssue && activeIssue.html_url) {
                vscode.postMessage({ command: 'openExternal', url: activeIssue.html_url });
            }
        });

        document.getElementById('open-pr-btn').addEventListener('click', () => {
            const prUrl = getPrimaryPullUrl();
            if (prUrl) {
                vscode.postMessage({ command: 'openExternal', url: prUrl });
            }
        });

        document.querySelectorAll('[data-template]').forEach((button) => {
            button.addEventListener('click', () => prefillReply(button.dataset.template));
        });

        window.addEventListener('message', event => {
            const msg = event.data;
            if (msg.command === 'renderComments' && activeIssue && activeIssue.id === msg.issueId) {
                renderComments(msg.comments);
            } else if (msg.command === 'refreshComments') {
                if (activeIssue && activeIssue.id === msg.issueId) {
                    document.getElementById('send-btn').textContent = 'Send Reply';
                    selectIssue(activeIssue, document.querySelector('.issue-item.active'));
                }
            } else if (msg.command === 'replaceIssues' && Array.isArray(msg.issues)) {
                issues.splice(0, issues.length, ...msg.issues);
                renderSidebar();
                if (activeIssue) {
                    const updated = issues.find(issue => issue.id === activeIssue.id);
                    if (updated) {
                        const activeEl = document.querySelector('[data-issue-id="' + updated.id + '"]');
                        selectIssue(updated, activeEl);
                    } else {
                        activeIssue = null;
                        activeComments = [];
                        document.getElementById('main-panel').style.display = 'none';
                        updateLinkButtons();
                    }
                }
            }
        });

        if (issues.length > 0) {
            renderSidebar();
        }
    </script>
</body>
</html>`;
}
