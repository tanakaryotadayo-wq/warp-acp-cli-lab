import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { getDashboardHtml, getJulesHtml } from './dashboard';

// ── Constants ───────────────────────────────────────────────────────────────

const CRITIC_SCRIPT_NAME = 'vortex-critic.py';
const FLEET_LOG_DIR = path.join(
  process.env.HOME ?? '/Users/ryyota',
  '.gemini/antigravity/fleet-logs'
);
const DEFAULT_KI_QUEUE_FILE = path.join(
  process.env.HOME ?? '/Users/ryyota',
  '.gemini/antigravity/ki-promotion-queue.jsonl'
);
const DEFAULT_KI_KNOWLEDGE_DIR = path.join(
  process.env.HOME ?? '/Users/ryyota',
  '.gemini/antigravity/knowledge'
);
const DEFAULT_KI_COLAB_NOTEBOOK = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'Newgate/ki_agent_system/colab_ki_vectorizer.ipynb'
);
const DEFAULT_ANTIGRAVITY_APP_PATH = '/Applications/Antigravity.app';
const DEFAULT_ANTIGRAVITY_PACKET_SCRIPT = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'Newgate/intelligence/harvest_antigravity_packets.py'
);
const DEFAULT_ANTIGRAVITY_PACKET_DB_PATH = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'Newgate/intelligence/neural_packets.db'
);
const DEFAULT_ANTIGRAVITY_PACKET_SUMMARY_PATH = path.join(
  process.env.HOME ?? '/Users/ryyota',
  '.gemini/antigravity/antigravity_packet_snapshot.json'
);
const DEFAULT_PIPELINE_ONE_REPO_PATH = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'vscode-oss'
);
const DEFAULT_GEMINI_BRIDGE_PORT = 8765;
const DEFAULT_ANTIGRAVITY_EXTENSIONS_DIR = path.join(
  process.env.HOME ?? '/Users/ryyota',
  '.antigravity/extensions'
);
const DEFAULT_ANTIGRAVITY_DISABLED_EXTENSIONS_DIR = path.join(
  process.env.HOME ?? '/Users/ryyota',
  '.antigravity/disabled-extensions'
);
const DEFAULT_ANTIGRAVITY_WORKSPACE_STORAGE = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'Library/Application Support/Antigravity/User/workspaceStorage'
);
const DEFAULT_ANTIGRAVITY_SETTINGS_PATH = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'Library/Application Support/Antigravity/User/settings.json'
);
const DEFAULT_BRAIN_DIR = path.join(
  process.env.HOME ?? '/Users/ryyota',
  '.gemini/antigravity/brain'
);
const DEFAULT_HARVEST_PACKET_DB = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'Newgate/intelligence/neural_packets.db'
);

// ── AI OS Configuration Readers ─────────────────────────────────────────────

function getVortexConfig() {
  return vscode.workspace.getConfiguration('vortex');
}

function getFusionGateUrl(): string {
  return getVortexConfig().get<string>('fusionGateUrl')?.trim() || 'http://127.0.0.1:9800';
}

interface FleetEndpoints {
  utility: string;
  agent: string;
  conversation: string;
  embedding: string;
  embeddingModel: string;
  fusionGate: string;
}

function getFleetEndpoints(): FleetEndpoints {
  const cfg = getVortexConfig();
  return {
    utility: cfg.get<string>('fleet.utilityEndpoint')?.trim() || 'http://127.0.0.1:8101',
    agent: cfg.get<string>('fleet.agentEndpoint')?.trim() || 'http://127.0.0.1:8102',
    conversation: cfg.get<string>('fleet.conversationEndpoint')?.trim() || 'http://127.0.0.1:8103',
    embedding: cfg.get<string>('fleet.embeddingEndpoint')?.trim() || 'http://127.0.0.1:8093/v1/embeddings',
    embeddingModel: cfg.get<string>('fleet.embeddingModel')?.trim() || 'nv-embed-v2',
    fusionGate: getFusionGateUrl(),
  };
}

function getFleetHealthCheckInterval(): number {
  return getVortexConfig().get<number>('fleet.healthCheckInterval') ?? 30000;
}

function getBrainDir(): string {
  return getVortexConfig().get<string>('memory.brainDir')?.trim() || DEFAULT_BRAIN_DIR;
}

function getHarvestPacketDbPath(): string {
  return getVortexConfig().get<string>('harvest.packetDbPath')?.trim() || DEFAULT_HARVEST_PACKET_DB;
}

function getHarvestTargetLanguages(): string[] {
  return getVortexConfig().get<string[]>('harvest.targetLanguages') ?? ['typescript', 'python', 'javascript'];
}

interface JulesIssueLabel {
  name: string;
}

interface JulesIssue {
  id: number;
  number: number;
  title: string;
  body?: string;
  html_url: string;
  comments_url: string;
  updated_at: string;
  created_at: string;
  state: string;
  labels?: JulesIssueLabel[];
}

interface JulesIssueSnapshot {
  id: number;
  number: number;
  title: string;
  htmlUrl: string;
  updatedAt: string;
  awaitingFeedback: boolean;
  linkedPrs: string[];
}

let julesPanel: vscode.WebviewPanel | undefined;
let julesPanelReady = false;
let julesPollTimer: ReturnType<typeof setInterval> | undefined;
let lastJulesSnapshots = new Map<number, JulesIssueSnapshot>();
let latestJulesToken = '';

function isJulesPollingEnabled(): boolean {
  return getVortexConfig().get<boolean>('jules.enablePolling') ?? true;
}

function getJulesPollInterval(): number {
  const configured = getVortexConfig().get<number>('jules.pollInterval') ?? 120000;
  return Number.isFinite(configured) && configured >= 30000 ? configured : 120000;
}

function getJulesSearchQuery(): string {
  return getVortexConfig().get<string>('jules.searchQuery')?.trim() || 'involves:@me "jules" is:open is:issue';
}

function shouldNotifyOnJulesAwaitingFeedback(): boolean {
  return getVortexConfig().get<boolean>('jules.notifyOnAwaitingFeedback') ?? true;
}

function shouldNotifyOnJulesCompletion(): boolean {
  return getVortexConfig().get<boolean>('jules.notifyOnCompletion') ?? true;
}

function shouldNotifyOnJulesLinkedPr(): boolean {
  return getVortexConfig().get<boolean>('jules.notifyOnLinkedPr') ?? true;
}

function isJulesAwaitingFeedback(labels?: JulesIssueLabel[]): boolean {
  const names = (labels ?? []).map((label) => label.name.toLowerCase());
  return names.some((name) =>
    ['awaiting', 'feedback', 'needs-human', 'needs-user', 'question', 'review'].some((keyword) => name.includes(keyword))
  );
}

function extractPullRequestUrls(text?: string): string[] {
  if (!text) {
    return [];
  }
  const matches = text.match(/https:\/\/github\.com\/[^\s/]+\/[^\s/]+\/pull\/\d+/g) ?? [];
  return Array.from(new Set(matches));
}

function buildJulesSnapshot(issue: JulesIssue): JulesIssueSnapshot {
  return {
    id: issue.id,
    number: issue.number,
    title: issue.title,
    htmlUrl: issue.html_url,
    updatedAt: issue.updated_at,
    awaitingFeedback: isJulesAwaitingFeedback(issue.labels),
    linkedPrs: extractPullRequestUrls(issue.body),
  };
}

async function getGitHubRepoToken(createIfNone: boolean): Promise<string | undefined> {
  try {
    const session = await vscode.authentication.getSession('github', ['repo'], { createIfNone });
    return session?.accessToken;
  } catch {
    return undefined;
  }
}

async function fetchJulesIssues(token: string): Promise<JulesIssue[]> {
  const response = await (globalThis as any).fetch(
    `https://api.github.com/search/issues?q=${encodeURIComponent(getJulesSearchQuery())}&per_page=50&sort=updated&order=desc`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github.v3+json',
      },
      signal: AbortSignal.timeout(5000),
    }
  );

  if (!response.ok) {
    throw new Error(`GitHub API ${response.status}: ${response.statusText}`);
  }

  const data = await response.json() as { items?: JulesIssue[] };
  return Array.isArray(data.items) ? data.items : [];
}

async function openExternalUrl(url: string): Promise<void> {
  await vscode.env.openExternal(vscode.Uri.parse(url));
}

async function notifyJulesAwaitingFeedback(issue: JulesIssueSnapshot): Promise<void> {
  const action = await vscode.window.showInformationMessage(
    `Jules が返信待ちです: #${issue.number} ${issue.title}`,
    'Issue を開く',
    'Dashboard を開く'
  );
  if (action === 'Issue を開く') {
    await openExternalUrl(issue.htmlUrl);
  } else if (action === 'Dashboard を開く') {
    await vscode.commands.executeCommand('vortex.openAsyncTaskDashboard');
  }
}

async function notifyJulesLinkedPr(issue: JulesIssueSnapshot, prUrl: string): Promise<void> {
  const action = await vscode.window.showInformationMessage(
    `Jules が PR を参照しました: #${issue.number} ${issue.title}`,
    'PR を開く',
    'Issue を開く'
  );
  if (action === 'PR を開く') {
    await openExternalUrl(prUrl);
  } else if (action === 'Issue を開く') {
    await openExternalUrl(issue.htmlUrl);
  }
}

async function notifyJulesCompletion(issue: JulesIssueSnapshot): Promise<void> {
  const action = await vscode.window.showInformationMessage(
    `Jules issue が active queue から外れました: #${issue.number} ${issue.title}`,
    'Dashboard を開く',
    'Issue を開く'
  );
  if (action === 'Dashboard を開く') {
    await vscode.commands.executeCommand('vortex.openAsyncTaskDashboard');
  } else if (action === 'Issue を開く') {
    await openExternalUrl(issue.htmlUrl);
  }
}

async function syncJulesIssues(options?: {
  createSessionIfNeeded?: boolean;
  notify?: boolean;
  resetPanel?: boolean;
}): Promise<JulesIssue[]> {
  const token = latestJulesToken || await getGitHubRepoToken(options?.createSessionIfNeeded ?? false);
  if (!token) {
    throw new Error('GitHub Authentication is required to access Jules operations.');
  }

  latestJulesToken = token;
  const issues = await fetchJulesIssues(token);
  const nextSnapshots = new Map<number, JulesIssueSnapshot>(
    issues.map((issue) => [issue.id, buildJulesSnapshot(issue)])
  );

  if (options?.notify && lastJulesSnapshots.size > 0) {
    for (const issue of issues) {
      const next = nextSnapshots.get(issue.id);
      const previous = lastJulesSnapshots.get(issue.id);
      if (!next || !previous) {
        continue;
      }
      if (!previous.awaitingFeedback && next.awaitingFeedback && shouldNotifyOnJulesAwaitingFeedback()) {
        void notifyJulesAwaitingFeedback(next);
      }
      if (shouldNotifyOnJulesLinkedPr()) {
        const newLinkedPr = next.linkedPrs.find((url) => !previous.linkedPrs.includes(url));
        if (newLinkedPr) {
          void notifyJulesLinkedPr(next, newLinkedPr);
        }
      }
    }

    if (shouldNotifyOnJulesCompletion()) {
      for (const previous of lastJulesSnapshots.values()) {
        if (!nextSnapshots.has(previous.id)) {
          void notifyJulesCompletion(previous);
        }
      }
    }
  }

  lastJulesSnapshots = nextSnapshots;

  if (julesPanel) {
    if (options?.resetPanel || !julesPanelReady) {
      julesPanel.webview.html = getJulesHtml(JSON.stringify(issues), false);
      julesPanelReady = true;
    } else {
      void julesPanel.webview.postMessage({ command: 'replaceIssues', issues });
    }
  }

  return issues;
}

function startJulesPolling() {
  if (!isJulesPollingEnabled()) {
    return;
  }

  const interval = getJulesPollInterval();
  const poll = async () => {
    try {
      await syncJulesIssues({ notify: true });
    } catch {
      // GitHub auth may not be available yet; polling should fail-soft.
    }
  };

  void poll();
  julesPollTimer = setInterval(() => {
    void poll();
  }, interval);
}

function stopJulesPolling() {
  if (julesPollTimer) {
    clearInterval(julesPollTimer);
    julesPollTimer = undefined;
  }
}

// ── Fleet Health Check ──────────────────────────────────────────────────────

interface FleetHealth {
  [key: string]: { url: string; alive: boolean; latencyMs: number; error?: string };
}

let currentFleetHealth: FleetHealth = {};
let fleetHealthTimer: ReturnType<typeof setInterval> | undefined;

async function checkEndpointHealth(name: string, url: string): Promise<{ alive: boolean; latencyMs: number; error?: string }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 3000);
  const start = Date.now();
  try {
    const baseUrl = url.replace(/\/v1\/.*$/, '');
    const response = await (globalThis as any).fetch(baseUrl, { signal: controller.signal, method: 'GET' });
    return { alive: response.ok || response.status < 500, latencyMs: Date.now() - start };
  } catch (error: any) {
    return { alive: false, latencyMs: Date.now() - start, error: error?.name === 'AbortError' ? 'timeout' : (error?.message ?? 'unknown') };
  } finally {
    clearTimeout(timeout);
  }
}

async function runFleetHealthCheck(): Promise<FleetHealth> {
  const endpoints = getFleetEndpoints();
  const checks = await Promise.all([
    checkEndpointHealth('Fusion Gate', endpoints.fusionGate).then(r => ['fusionGate', { url: endpoints.fusionGate, ...r }] as const),
    checkEndpointHealth('Utility (Qwen 3.5)', endpoints.utility).then(r => ['utility', { url: endpoints.utility, ...r }] as const),
    checkEndpointHealth('Agent (Qwen3 Coder)', endpoints.agent).then(r => ['agent', { url: endpoints.agent, ...r }] as const),
    checkEndpointHealth('Conversation (Gemma 4)', endpoints.conversation).then(r => ['conversation', { url: endpoints.conversation, ...r }] as const),
    checkEndpointHealth('Embedding (NV-Embed)', endpoints.embedding).then(r => ['embedding', { url: endpoints.embedding, ...r }] as const),
  ]);
  const health: FleetHealth = {};
  for (const [name, result] of checks) {
    health[name] = result;
  }
  currentFleetHealth = health;
  return health;
}

function startFleetHealthCheck(statusBar: vscode.StatusBarItem) {
  const interval = getFleetHealthCheckInterval();
  if (interval <= 0) { return; }

  const update = async () => {
    const health = await runFleetHealthCheck();
    const aliveCount = Object.values(health).filter(h => h.alive).length;
    const total = Object.values(health).length;
    statusBar.text = `$(shield) VORTEX ${aliveCount}/${total}`;
    statusBar.tooltip = Object.entries(health)
      .map(([name, h]) => `${h.alive ? '🟢' : '🔴'} ${name}: ${h.alive ? `${h.latencyMs}ms` : (h.error ?? 'down')}`)
      .join('\n');
  };

  void update();
  fleetHealthTimer = setInterval(update, interval);
}

function stopFleetHealthCheck() {
  if (fleetHealthTimer) {
    clearInterval(fleetHealthTimer);
    fleetHealthTimer = undefined;
  }
}

// ── State Streaming ─────────────────────────────────────────────────────────

let stateStreamTimer: ReturnType<typeof setInterval> | undefined;

function buildStatePayload(): any {
  const editor = vscode.window.activeTextEditor;
  const doc = editor?.document;
  const cfg = getVortexConfig();

  const payload: any = {
    type: 'ide-state',
    timestamp: Date.now(),
    editor: doc ? {
      fileName: doc.fileName,
      languageId: doc.languageId,
      lineCount: doc.lineCount,
      cursor: editor?.selection.active ? { line: editor.selection.active.line, character: editor.selection.active.character } : null,
      isDirty: doc.isDirty,
    } : null,
    workspace: {
      root: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? null,
      openFiles: vscode.workspace.textDocuments.map(d => d.fileName),
    },
    fleet: currentFleetHealth,
  };

  if (cfg.get<boolean>('stateStream.includeDiagnostics') && doc) {
    payload.diagnostics = vscode.languages.getDiagnostics(doc.uri).map(d => ({
      message: d.message,
      severity: d.severity,
      range: { start: { line: d.range.start.line, character: d.range.start.character }, end: { line: d.range.end.line, character: d.range.end.character } },
    }));
  }

  if (cfg.get<boolean>('stateStream.includeGit')) {
    try {
      const gitExt = vscode.extensions.getExtension('vscode.git')?.exports;
      const repo = gitExt?.getAPI(1)?.repositories?.[0];
      if (repo) {
        payload.git = {
          branch: repo.state?.HEAD?.name ?? null,
          changedFiles: repo.state?.workingTreeChanges?.length ?? 0,
        };
      }
    } catch { /* git not available */ }
  }

  return payload;
}

function startStateStreaming() {
  const cfg = getVortexConfig();
  if (!cfg.get<boolean>('stateStream.enable')) { return; }

  const endpoint = cfg.get<string>('stateStream.endpoint') ?? 'http://127.0.0.1:9800/v1/ide/state';
  const interval = cfg.get<number>('stateStream.interval') ?? 5000;

  const send = async () => {
    try {
      const payload = buildStatePayload();
      await (globalThis as any).fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(3000),
      });
    } catch { /* endpoint may not exist yet — silent fail */ }
  };

  stateStreamTimer = setInterval(send, interval);
}

function stopStateStreaming() {
  if (stateStreamTimer) {
    clearInterval(stateStreamTimer);
    stateStreamTimer = undefined;
  }
}

// ── Action Server ───────────────────────────────────────────────────────────

import * as http from 'http';

let actionServer: http.Server | undefined;

async function handleAction(action: any): Promise<{ ok: boolean; message: string }> {
  const cfg = getVortexConfig();
  const allowed = cfg.get<string[]>('actionServer.allowedActions') ?? ['openFile', 'insertText', 'applyDiff', 'runCommand'];

  if (!allowed.includes(action?.action)) {
    return { ok: false, message: `Action "${action?.action}" is not in allowedActions` };
  }

  switch (action.action) {
    case 'openFile': {
      const doc = await vscode.workspace.openTextDocument(action.file);
      await vscode.window.showTextDocument(doc);
      return { ok: true, message: `Opened ${action.file}` };
    }
    case 'insertText': {
      const doc = await vscode.workspace.openTextDocument(action.file);
      const editor = await vscode.window.showTextDocument(doc);
      await editor.edit(edit => {
        edit.insert(new vscode.Position(action.position?.line ?? 0, action.position?.character ?? 0), action.text ?? '');
      });
      return { ok: true, message: `Inserted text at ${action.position?.line}:${action.position?.character}` };
    }
    case 'runCommand': {
      await vscode.commands.executeCommand(action.command, ...(action.args ?? []));
      return { ok: true, message: `Executed command: ${action.command}` };
    }
    case 'applyDiff': {
      // Future: unified diff parser
      return { ok: false, message: 'applyDiff not yet implemented' };
    }
    default:
      return { ok: false, message: `Unknown action: ${action.action}` };
  }
}

function startActionServer() {
  const cfg = getVortexConfig();
  if (!cfg.get<boolean>('actionServer.enable')) { return; }

  const port = cfg.get<number>('actionServer.port') ?? 19800;

  actionServer = http.createServer(async (req, res) => {
    // Security: localhost only
    const remoteAddr = req.socket.remoteAddress ?? '';
    if (!['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(remoteAddr)) {
      res.writeHead(403, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, message: 'localhost only' }));
      return;
    }

    if (req.method === 'GET' && req.url === '/health') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, type: 'vortex-action-server', fleet: currentFleetHealth }));
      return;
    }

    if (req.method === 'GET' && req.url === '/state') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(buildStatePayload()));
      return;
    }

    if (req.method === 'POST' && req.url === '/action') {
      let body = '';
      req.on('data', chunk => { body += chunk; });
      req.on('end', async () => {
        try {
          const action = JSON.parse(body);
          const result = await handleAction(action);
          res.writeHead(result.ok ? 200 : 400, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify(result));
        } catch (error: any) {
          res.writeHead(500, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify({ ok: false, message: error?.message ?? 'internal error' }));
        }
      });
      return;
    }

    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: false, message: 'Not found. Available: GET /health, GET /state, POST /action' }));
  });

  actionServer.listen(port, '127.0.0.1', () => {
    console.log(`VORTEX Action Server listening on http://127.0.0.1:${port}`);
  });

  actionServer.on('error', (err: any) => {
    console.error('VORTEX Action Server failed to start:', err?.message ?? err);
    actionServer = undefined;
  });
}

function stopActionServer() {
  if (actionServer) {
    actionServer.close();
    actionServer = undefined;
  }
}

// ── Sovereign Memory Recall ─────────────────────────────────────────────────

async function runSovereignMemoryRecall(channel: vscode.OutputChannel) {
  const cfg = getVortexConfig();
  if (!cfg.get<boolean>('memory.recallOnActivate')) { return; }

  const fusionGateUrl = getFusionGateUrl();
  const workspaceName = vscode.workspace.workspaceFolders?.[0]?.name ?? 'unknown';

  try {
    const response = await (globalThis as any).fetch(`${fusionGateUrl}/v1/memory/recall`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: workspaceName, top_k: 5 }),
      signal: AbortSignal.timeout(5000),
    });

    if (response.ok) {
      const data = await response.json();
      channel.appendLine('[Memory Recall] Success');
      channel.appendLine(JSON.stringify(data, null, 2));
    } else {
      channel.appendLine(`[Memory Recall] HTTP ${response.status}`);
    }
  } catch (error: any) {
    channel.appendLine(`[Memory Recall] ${error?.message ?? 'failed'} (Fusion Gate may not be running)`);
  }
}

interface NewgateStatus {
  connected: boolean;
  bridgeUrl: string;
  version: string;
  embeddingModel: string;
  recallStatus: string;
  storeStatus: string;
  p0Count: number;
  error?: string;
  profile?: any;
}

interface BridgeRuntimeStatus {
  configured: boolean;
  statusPath: string;
  running: boolean;
  bridgeUrl: string;
  launcher: string;
  tmuxSession?: string;
  note?: string;
  error?: string;
}

interface KiQueueEntry {
  id: string;
  status: 'pending' | 'promoted' | string;
  title?: string;
  summary?: string;
  task?: string;
  result?: string;
  cause?: string;
  fix?: string;
  tags?: string[];
  suggested_ki_name?: string;
  created_at?: string;
  updated_at?: string;
  knowledge_dir?: string;
  artifact_path?: string;
  notebook_path?: string;
}

interface KiQueueStatus {
  queueFile: string;
  knowledgeDir: string;
  notebookPath: string;
  notebookExists: boolean;
  pendingCount: number;
  promotedCount: number;
  latestPendingTitle: string | null;
}

interface PipelineOneStatus {
  configured: boolean;
  statusPath: string;
  repoPath: string;
  mountPath: string;
  stage: string;
  mounted: boolean;
  cbfHealthy: boolean;
  n8nReady: boolean;
  containerRuntime?: string;
  dockerContext?: string;
  cbfLauncher?: string;
  cbfTmuxSession?: string;
  packetCount: number;
  issueCount: number;
  eckRuns: number;
  error?: string;
}

function getWorkspaceRoot(): string {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? DEFAULT_PIPELINE_ONE_REPO_PATH;
}

function getKiQueueFile(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('kiQueueFile')?.trim() || DEFAULT_KI_QUEUE_FILE;
}

function getKiKnowledgeDir(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('kiKnowledgeDir')?.trim() || DEFAULT_KI_KNOWLEDGE_DIR;
}

function getKiColabNotebook(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('kiColabNotebook')?.trim() || DEFAULT_KI_COLAB_NOTEBOOK;
}

function getAntigravityAppPath(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('antigravityAppPath')?.trim() || DEFAULT_ANTIGRAVITY_APP_PATH;
}

function getAntigravityPacketScript(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('antigravityPacketScript')?.trim() || DEFAULT_ANTIGRAVITY_PACKET_SCRIPT;
}

function getAntigravityPacketDbPath(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('antigravityPacketDbPath')?.trim() || DEFAULT_ANTIGRAVITY_PACKET_DB_PATH;
}

function getAntigravityPacketSummaryPath(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('antigravityPacketSummaryPath')?.trim() || DEFAULT_ANTIGRAVITY_PACKET_SUMMARY_PATH;
}

function getPipelineOneRepoPath(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneRepoPath')?.trim() || DEFAULT_PIPELINE_ONE_REPO_PATH;
}

function getPipelineOneStatusPath(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneStatusPath')?.trim()
    || path.join(getWorkspaceRoot(), '.build/ryota/pipeline_01/status.json');
}

function getPipelineOneStateDir(): string {
  return path.dirname(getPipelineOneStatusPath());
}

function getPipelineOneBootstrapScript(context: vscode.ExtensionContext): string {
  const configured = vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneBootstrapScript')?.trim();
  if (configured) {
    return configured;
  }

  const workspaceCandidate = path.join(
    getWorkspaceRoot(),
    'extensions/ryota-core.vortex-critic/assets/pipeline/scripts/bootstrap_pipeline_01.sh'
  );
  if (fs.existsSync(workspaceCandidate)) {
    return workspaceCandidate;
  }

  return path.join(context.extensionPath, 'assets', 'pipeline', 'scripts', 'bootstrap_pipeline_01.sh');
}

function getPipelineOneRunnerScript(context: vscode.ExtensionContext): string {
  const configured = vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneRunnerScript')?.trim();
  if (configured) {
    return configured;
  }

  const workspaceCandidate = path.join(
    getWorkspaceRoot(),
    'extensions/ryota-core.vortex-critic/assets/pipeline/scripts/pipeline_01_runner.py'
  );
  if (fs.existsSync(workspaceCandidate)) {
    return workspaceCandidate;
  }

  return path.join(context.extensionPath, 'assets', 'pipeline', 'scripts', 'pipeline_01_runner.py');
}

function getGeminiBridgePort(): number {
  const configured = vscode.workspace.getConfiguration('vortex').get<number>('geminiBridgePort');
  return configured && Number.isFinite(configured) && configured > 0 ? configured : DEFAULT_GEMINI_BRIDGE_PORT;
}

function getBackgroundLauncher(): string {
  const configured = vscode.workspace.getConfiguration('vortex').get<string>('runtime.backgroundLauncher')?.trim().toLowerCase();
  return configured === 'subprocess' ? 'subprocess' : 'tmux';
}

function getGeminiBridgeTmuxSession(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('geminiBridgeTmuxSession')?.trim()
    || 'vortex-gemini-bridge';
}

function getPipelineOneCbfTmuxSession(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneCbfTmuxSession')?.trim()
    || 'vortex-pipeline-cbf';
}

function getPipelineOneContainerRuntime(): string {
  const configured = vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneContainerRuntime')?.trim().toLowerCase();
  if (configured === 'docker' || configured === 'orbstack') {
    return configured;
  }
  return 'auto';
}

function getGeminiBridgeStatusPath(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('geminiBridgeStatusPath')?.trim()
    || path.join(getWorkspaceRoot(), '.build/ryota/gemini-code-assist/status.json');
}

function getGeminiBridgeBootstrapScript(context: vscode.ExtensionContext): string {
  const configured = vscode.workspace.getConfiguration('vortex').get<string>('geminiBridgeBootstrapScript')?.trim();
  if (configured) {
    return configured;
  }

  const workspaceCandidate = path.join(
    getWorkspaceRoot(),
    'extensions/ryota-core.vortex-critic/assets/gemini/bootstrap_gemini_code_assist.sh'
  );
  if (fs.existsSync(workspaceCandidate)) {
    return workspaceCandidate;
  }

  return path.join(context.extensionPath, 'assets', 'gemini', 'bootstrap_gemini_code_assist.sh');
}

function slugifyKiName(value: string, fallback = 'ki_candidate'): string {
  const normalized = value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, '_')
    .replace(/^_+|_+$/g, '');
  return normalized || fallback;
}

function loadKiQueueEntries(): KiQueueEntry[] {
  const queueFile = getKiQueueFile();
  if (!fs.existsSync(queueFile)) {
    return [];
  }
  const raw = fs.readFileSync(queueFile, 'utf-8');
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .flatMap((line) => {
      try {
        return [JSON.parse(line) as KiQueueEntry];
      } catch {
        return [];
      }
    });
}

function saveKiQueueEntries(entries: KiQueueEntry[]): void {
  const queueFile = getKiQueueFile();
  fs.mkdirSync(path.dirname(queueFile), { recursive: true });
  fs.writeFileSync(
    queueFile,
    entries.map((entry) => JSON.stringify(entry)).join('\n') + (entries.length > 0 ? '\n' : ''),
    'utf-8'
  );
}

async function fetchKiQueueStatus(): Promise<KiQueueStatus> {
  const queueFile = getKiQueueFile();
  const knowledgeDir = getKiKnowledgeDir();
  const notebookPath = getKiColabNotebook();
  const entries = loadKiQueueEntries();
  const pendingEntries = entries.filter((entry) => entry.status !== 'promoted');
  const promotedEntries = entries.filter((entry) => entry.status === 'promoted');
  return {
    queueFile,
    knowledgeDir,
    notebookPath,
    notebookExists: fs.existsSync(notebookPath),
    pendingCount: pendingEntries.length,
    promotedCount: promotedEntries.length,
    latestPendingTitle: pendingEntries[0]?.title ?? null,
  };
}

async function fetchPipelineOneStatus(): Promise<PipelineOneStatus> {
  const statusPath = getPipelineOneStatusPath();
  const repoPath = getPipelineOneRepoPath();
  if (!fs.existsSync(statusPath)) {
    return {
      configured: false,
      statusPath,
      repoPath,
      mountPath: path.join(process.env.HOME ?? '/Users/ryyota', 'GoogleDriveCache/oss'),
      stage: '未起動',
      mounted: false,
      cbfHealthy: false,
      n8nReady: false,
      containerRuntime: 'auto',
      dockerContext: '',
      cbfLauncher: getBackgroundLauncher(),
      cbfTmuxSession: getPipelineOneCbfTmuxSession(),
      packetCount: 0,
      issueCount: 0,
      eckRuns: 0,
      error: 'status.json がまだありません',
    };
  }

  try {
    const raw = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
    return {
      configured: true,
      statusPath,
      repoPath: String(raw.repo_path ?? raw.repoPath ?? repoPath),
      mountPath: String(raw.mount_path ?? raw.mountPath ?? path.join(process.env.HOME ?? '/Users/ryyota', 'GoogleDriveCache/oss')),
      stage: String(raw.stage ?? '不明'),
      mounted: Boolean(raw.mounted ?? false),
      cbfHealthy: Boolean(raw.cbfHealthy ?? raw.cbf?.packetized),
      n8nReady: Boolean(raw.n8nReady ?? false),
      containerRuntime: String(raw.containerRuntime ?? 'auto'),
      dockerContext: String(raw.dockerContext ?? ''),
      cbfLauncher: String(raw.cbfLauncher ?? getBackgroundLauncher()),
      cbfTmuxSession: raw.cbfTmuxSession ? String(raw.cbfTmuxSession) : getPipelineOneCbfTmuxSession(),
      packetCount: Number(raw.packet_summary?.count ?? raw.packetCount ?? 0),
      issueCount: Number(raw.issue_count ?? raw.issueCount ?? 0),
      eckRuns: Number(raw.eck?.bridge?.runs ?? raw.eckRuns ?? 0),
      error: raw.error
        ? String(raw.error)
        : (raw.mountError ? String(raw.mountError) : (raw.containerRuntimeNote ? String(raw.containerRuntimeNote) : undefined)),
    };
  } catch (error: any) {
    return {
      configured: false,
      statusPath,
      repoPath,
      mountPath: path.join(process.env.HOME ?? '/Users/ryyota', 'GoogleDriveCache/oss'),
      stage: '読み込み失敗',
      mounted: false,
      cbfHealthy: false,
      n8nReady: false,
      containerRuntime: 'auto',
      dockerContext: '',
      cbfLauncher: getBackgroundLauncher(),
      cbfTmuxSession: getPipelineOneCbfTmuxSession(),
      packetCount: 0,
      issueCount: 0,
      eckRuns: 0,
      error: error?.message ?? 'status parse failed',
    };
  }
}

async function fetchBridgeRuntimeStatus(): Promise<BridgeRuntimeStatus> {
  const statusPath = getGeminiBridgeStatusPath();
  const bridgeUrl = `http://127.0.0.1:${getGeminiBridgePort()}`;
  if (!fs.existsSync(statusPath)) {
    return {
      configured: false,
      statusPath,
      running: false,
      bridgeUrl,
      launcher: getBackgroundLauncher(),
      tmuxSession: getGeminiBridgeTmuxSession(),
      error: 'status.json がまだありません',
    };
  }

  try {
    const raw = JSON.parse(fs.readFileSync(statusPath, 'utf-8'));
    return {
      configured: true,
      statusPath,
      running: Boolean(raw.running ?? false),
      bridgeUrl: String(raw.bridgeUrl ?? bridgeUrl),
      launcher: String(raw.launcher ?? getBackgroundLauncher()),
      tmuxSession: raw.tmuxSession ? String(raw.tmuxSession) : getGeminiBridgeTmuxSession(),
      note: raw.note ? String(raw.note) : undefined,
      error: raw.error ? String(raw.error) : undefined,
    };
  } catch (error: any) {
    return {
      configured: false,
      statusPath,
      running: false,
      bridgeUrl,
      launcher: getBackgroundLauncher(),
      tmuxSession: getGeminiBridgeTmuxSession(),
      error: error?.message ?? 'status parse failed',
    };
  }
}

function buildKiArtifactMarkdown(entry: KiQueueEntry, title: string): string {
  const sections = [
    `# ${title}`,
    '',
    '## Source Task',
    entry.task || '',
    '',
    '## Result',
    entry.result || '',
  ];
  if (entry.fix) {
    sections.push('', '## Fix', entry.fix);
  }
  if (entry.cause) {
    sections.push('', '## Cause', entry.cause);
  }
  if (Array.isArray(entry.tags) && entry.tags.length > 0) {
    sections.push('', '## Tags', entry.tags.map((tag) => `#${tag}`).join(' '));
  }
  return sections.filter((line) => line !== undefined).join('\n').trim() + '\n';
}

function promoteKiQueueEntry(entry: KiQueueEntry, kiNameOverride?: string): { kiDir: string; artifactPath: string } {
  const knowledgeDir = getKiKnowledgeDir();
  const kiName = slugifyKiName(kiNameOverride || entry.suggested_ki_name || entry.title || entry.id);
  const kiDir = path.join(knowledgeDir, kiName);
  const artifactsDir = path.join(kiDir, 'artifacts');
  fs.mkdirSync(artifactsDir, { recursive: true });

  const title = (entry.title || entry.suggested_ki_name || kiName).trim();
  const summary = (entry.summary || entry.task || title).trim();
  const artifactFile = `${entry.id}.md`;
  const metadataPath = path.join(kiDir, 'metadata.json');
  const timestampsPath = path.join(kiDir, 'timestamps.json');
  const artifactPath = path.join(artifactsDir, artifactFile);

  let metadata: any = {};
  if (fs.existsSync(metadataPath)) {
    try {
      metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf-8'));
    } catch {
      metadata = {};
    }
  }
  const references = Array.isArray(metadata.references) ? metadata.references : [];
  references.push({ type: 'fleet_queue', value: entry.id });
  references.push({ type: 'file', value: `artifacts/${artifactFile}` });
  metadata = {
    ...metadata,
    title: metadata.title || title,
    summary,
    references,
  };
  fs.writeFileSync(metadataPath, JSON.stringify(metadata, null, 2), 'utf-8');
  fs.writeFileSync(artifactPath, buildKiArtifactMarkdown(entry, title), 'utf-8');

  const now = new Date().toISOString();
  let created = now;
  if (fs.existsSync(timestampsPath)) {
    try {
      created = JSON.parse(fs.readFileSync(timestampsPath, 'utf-8')).created || now;
    } catch {
      created = now;
    }
  }
  fs.writeFileSync(
    timestampsPath,
    JSON.stringify({ created, modified: now, accessed: now }, null, 2),
    'utf-8'
  );

  const entries = loadKiQueueEntries();
  const updatedEntries = entries.map((candidate) =>
    candidate.id === entry.id
      ? {
          ...candidate,
          status: 'promoted',
          updated_at: now,
          promoted_at: now,
          knowledge_dir: kiDir,
          artifact_path: artifactPath,
        }
      : candidate
  );
  saveKiQueueEntries(updatedEntries);

  return { kiDir, artifactPath };
}

function getBridgeUrl(): string {
  const configured = vscode.workspace.getConfiguration().get<string>('geminicodeassist.a2a.address') ?? '';
  return configured.trim().replace(/\/$/, '');
}

async function fetchNewgateStatus(): Promise<NewgateStatus> {
  const bridgeUrl = getBridgeUrl();
  if (!bridgeUrl) {
    return {
      connected: false,
      bridgeUrl: '',
      version: '-',
      embeddingModel: '-',
      recallStatus: '不明',
      storeStatus: '不明',
      p0Count: 0,
      error: 'geminicodeassist.a2a.address が未設定',
    };
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2500);

  try {
    const response = await (globalThis as any).fetch(`${bridgeUrl}/newgate/profile`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const snapshot = await response.json() as any;
    const profile = snapshot?.profile ?? {};
    const priorities = Array.isArray(profile.priorities) ? profile.priorities : [];

    return {
      connected: true,
      bridgeUrl,
      version: String(profile.version ?? '-'),
      embeddingModel: String(profile.embedding?.primaryModel ?? '-'),
      recallStatus: String(profile.memory?.recall?.status ?? '不明'),
      storeStatus: String(profile.memory?.store?.status ?? '不明'),
      p0Count: priorities.filter((item: any) => item?.priority === 'P0').length,
      profile,
    };
  } catch (error: any) {
    const message = error?.name === 'AbortError' ? 'bridge timeout（タイムアウト）' : (error?.message ?? 'bridge error（接続失敗）');
    return {
      connected: false,
      bridgeUrl,
      version: '-',
      embeddingModel: '-',
      recallStatus: '不明',
      storeStatus: '不明',
      p0Count: 0,
      error: message,
    };
  } finally {
    clearTimeout(timeout);
  }
}

// ── Resolve Script Paths ────────────────────────────────────────────────────

function resolveCriticScript(context: vscode.ExtensionContext): string {
  const config = vscode.workspace.getConfiguration('vortex');
  const custom = config.get<string>('criticScript');
  if (custom && fs.existsSync(custom)) { return custom; }

  // Bundle package script
  const bundled = path.join(context.extensionPath, 'assets', 'critic', CRITIC_SCRIPT_NAME);
  if (fs.existsSync(bundled)) { return bundled; }
  return '';
}

// ── VORTEX Critic Runner ────────────────────────────────────────────────────

interface AuditResult {
  verdict: 'VERIFIED' | 'UNVERIFIED' | 'ERROR';
  text: string;
  preset: string;
  elapsed: number;
}

async function runVortexAudit(code: string, workspaceRoot: string, context: vscode.ExtensionContext): Promise<AuditResult> {
  const criticScript = resolveCriticScript(context);
  if (!criticScript) {
    return { verdict: 'ERROR', text: 'vortex-critic.py not found', preset: '', elapsed: 0 };
  }

  const config = vscode.workspace.getConfiguration('vortex');
  const preset = config.get<string>('preset') ?? '渦';

  const input = JSON.stringify({
    prompt: `Code to audit:\n\`\`\`\n${code}\n\`\`\`\nGive evidence-based audit. End with VERDICT: VERIFIED or VERDICT: UNVERIFIED.`,
    workspaceRoot,
    preset,
  });

  const start = Date.now();

  return new Promise<AuditResult>((resolve) => {
    const proc = cp.spawn('python3', [criticScript], {
      cwd: workspaceRoot || undefined,
    });

    let stdout = '';
    let stderr = '';
    proc.stdin.write(input);
    proc.stdin.end();
    proc.stdout.on('data', (d) => { stdout += d.toString(); });
    proc.stderr.on('data', (d) => { stderr += d.toString(); });
    proc.on('close', () => {
      const elapsed = Date.now() - start;
      let text = stdout;
      try {
        const parsed = JSON.parse(stdout);
        text = parsed?.hookSpecificOutput?.additionalContext ?? stdout;
      } catch { /* use raw */ }

      const upper = text.toUpperCase();
      const verdict = upper.includes('VERDICT: VERIFIED') ? 'VERIFIED' as const
        : upper.includes('VERDICT: UNVERIFIED') ? 'UNVERIFIED' as const
        : 'ERROR' as const;

      resolve({ verdict, text: text.trim(), preset, elapsed });
    });

    // Timeout after 25 seconds
    setTimeout(() => {
      proc.kill();
      resolve({ verdict: 'ERROR', text: 'Timeout (25s)', preset, elapsed: 25000 });
    }, 25000);
  });
}

interface PacketHarvestResult {
  stdout: string;
  stderr: string;
  summaryPath: string;
}

async function runAntigravityPacketHarvest(): Promise<PacketHarvestResult> {
  const scriptPath = getAntigravityPacketScript();
  if (!fs.existsSync(scriptPath)) {
    throw new Error(`packet script not found: ${scriptPath}`);
  }

  const summaryPath = getAntigravityPacketSummaryPath();
  fs.mkdirSync(path.dirname(summaryPath), { recursive: true });

  const args = [
    scriptPath,
    '--app-path', getAntigravityAppPath(),
    '--extensions-dir', DEFAULT_ANTIGRAVITY_EXTENSIONS_DIR,
    '--disabled-extensions-dir', DEFAULT_ANTIGRAVITY_DISABLED_EXTENSIONS_DIR,
    '--workspace-storage', DEFAULT_ANTIGRAVITY_WORKSPACE_STORAGE,
    '--settings-path', DEFAULT_ANTIGRAVITY_SETTINGS_PATH,
    '--db-path', getAntigravityPacketDbPath(),
    '--summary-path', summaryPath,
  ];

  return new Promise<PacketHarvestResult>((resolve, reject) => {
    const proc = cp.spawn('python3', args, {
      cwd: path.dirname(scriptPath),
    });

    let stdout = '';
    let stderr = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        proc.kill();
        reject(new Error('Antigravity packet harvest timed out after 120s'));
      }
    }, 120000);

    proc.stdout.on('data', (data) => { stdout += data.toString(); });
    proc.stderr.on('data', (data) => { stderr += data.toString(); });
    proc.on('error', (error) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(error);
      }
    });
    proc.on('close', (code) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      if (code === 0) {
        resolve({ stdout, stderr, summaryPath });
      } else {
        reject(new Error(stderr.trim() || stdout.trim() || `packet harvest exited with code ${code}`));
      }
    });
  });
}

async function runPipelineOneScripts(context: vscode.ExtensionContext): Promise<{ stdout: string; stderr: string; statusPath: string }> {
  const bootstrapScript = getPipelineOneBootstrapScript(context);
  const runnerScript = getPipelineOneRunnerScript(context);
  const statusPath = getPipelineOneStatusPath();
  const stateDir = getPipelineOneStateDir();
  const repoPath = getPipelineOneRepoPath();
  const packetDb = path.join(stateDir, 'oss_packets.db');
  const issueDb = path.join(stateDir, 'issue_packets.db');

  if (!fs.existsSync(bootstrapScript)) {
    throw new Error(`pipeline bootstrap script not found: ${bootstrapScript}`);
  }
  if (!fs.existsSync(runnerScript)) {
    throw new Error(`pipeline runner script not found: ${runnerScript}`);
  }

  const runProcess = (command: string, args: string[], cwd: string, timeoutMs: number, env?: NodeJS.ProcessEnv) =>
    new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
      const proc = cp.spawn(command, args, { cwd, env });
      let stdout = '';
      let stderr = '';
      let settled = false;
      const timer = setTimeout(() => {
        if (!settled) {
          settled = true;
          proc.kill();
          reject(new Error(`${path.basename(args[0] ?? command)} timed out after ${Math.round(timeoutMs / 1000)}s`));
        }
      }, timeoutMs);

      proc.stdout.on('data', (data) => { stdout += data.toString(); });
      proc.stderr.on('data', (data) => { stderr += data.toString(); });
      proc.on('error', (error) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(error);
        }
      });
      proc.on('close', (code) => {
        if (settled) {
          return;
        }
        settled = true;
        clearTimeout(timer);
        if (code === 0) {
          resolve({ stdout, stderr });
        } else {
          reject(new Error(stderr.trim() || stdout.trim() || `${command} exited with code ${code}`));
        }
      });
    });

  fs.mkdirSync(stateDir, { recursive: true });

  const pipelineEnv = {
    ...process.env,
    PIPELINE_01_STATE_DIR: stateDir,
    PIPELINE_01_STATUS_FILE: statusPath,
    PIPELINE_01_CBF_LAUNCHER: getBackgroundLauncher(),
    PIPELINE_01_CBF_TMUX_SESSION: getPipelineOneCbfTmuxSession(),
    PIPELINE_01_CONTAINER_RUNTIME: getPipelineOneContainerRuntime(),
  };

  const bootstrap = await runProcess('/bin/bash', [bootstrapScript], path.dirname(bootstrapScript), 120000, pipelineEnv);
  const runner = await runProcess(
    'python3',
    [
      runnerScript,
      '--repo-path', repoPath,
      '--repo-name', path.basename(repoPath),
      '--state-dir', stateDir,
      '--packet-db', packetDb,
      '--issue-db', issueDb,
      '--status-path', statusPath,
    ],
    path.dirname(runnerScript),
    300000,
    pipelineEnv,
  );

  return {
    stdout: [bootstrap.stdout.trim(), runner.stdout.trim()].filter(Boolean).join('\n\n'),
    stderr: [bootstrap.stderr.trim(), runner.stderr.trim()].filter(Boolean).join('\n\n'),
    statusPath,
  };
}

async function runGeminiBridgeBootstrap(context: vscode.ExtensionContext): Promise<{ stdout: string; stderr: string; statusPath: string }> {
  const bootstrapScript = getGeminiBridgeBootstrapScript(context);
  const statusPath = getGeminiBridgeStatusPath();
  const workspaceRoot = getWorkspaceRoot();
  const port = String(getGeminiBridgePort());

  if (!fs.existsSync(bootstrapScript)) {
    throw new Error(`gemini bridge bootstrap script not found: ${bootstrapScript}`);
  }

  return new Promise<{ stdout: string; stderr: string; statusPath: string }>((resolve, reject) => {
    const proc = cp.spawn('/bin/bash', [
      bootstrapScript,
      '--workspace-root', workspaceRoot,
      '--status-file', statusPath,
      '--port', port,
    ], {
      cwd: path.dirname(bootstrapScript),
      env: {
        ...process.env,
        GEMINI_A2A_LAUNCHER: getBackgroundLauncher(),
        GEMINI_A2A_TMUX_SESSION: getGeminiBridgeTmuxSession(),
      },
    });

    let stdout = '';
    let stderr = '';
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        proc.kill();
        reject(new Error('Gemini bridge bootstrap timed out after 30s'));
      }
    }, 30000);

    proc.stdout.on('data', (data) => { stdout += data.toString(); });
    proc.stderr.on('data', (data) => { stderr += data.toString(); });
    proc.on('error', (error) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(error);
      }
    });
    proc.on('close', (code) => {
      if (settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      if (code === 0) {
        resolve({ stdout, stderr, statusPath });
      } else {
        reject(new Error(stderr.trim() || stdout.trim() || `gemini bridge bootstrap exited with code ${code}`));
      }
    });
  });
}

// ── Sidebar: Webview Provider ───────────────────────────────────────────────

class VortexSidebarProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = 'vortex-sidebar-webview';
  private _view?: vscode.WebviewView;

  constructor(private readonly _extensionUri: vscode.Uri) {}

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ) {
    this._view = webviewView;
    webviewView.webview.options = { enableScripts: true };

    const updateWebview = async () => {
      const status = {
        preset: vscode.workspace.getConfiguration('vortex').get<string>('preset') ?? '渦',
        lastVerdict: lastAuditResult?.verdict || null,
        newgate: await fetchNewgateStatus(),
        bridgeRuntime: await fetchBridgeRuntimeStatus(),
        kiQueue: await fetchKiQueueStatus(),
        pipelineOne: await fetchPipelineOneStatus(),
      };
      webviewView.webview.html = getDashboardHtml(this._extensionUri, FLEET_LOG_DIR, () => status);
    };

    void updateWebview();

    webviewView.webview.onDidReceiveMessage((msg) => {
      if (msg.command === 'refresh') {
        void updateWebview();
      } else if (msg.command === 'runAudit') {
        vscode.commands.executeCommand('vortex.runAudit');
      } else if (msg.command === 'packetizeAntigravity') {
        vscode.commands.executeCommand('vortex.packetizeAntigravity');
      } else if (msg.command === 'startGeminiBridge') {
        vscode.commands.executeCommand('vortex.startGeminiBridge');
      } else if (msg.command === 'openGeminiBridge') {
        vscode.commands.executeCommand('vortex.openGeminiBridgeSnapshot');
      } else if (msg.command === 'openNewgate') {
        vscode.commands.executeCommand('vortex.openNewgateSnapshot');
      } else if (msg.command === 'openKiQueue') {
        vscode.commands.executeCommand('vortex.openKiQueueSnapshot');
      } else if (msg.command === 'openKiNotebook') {
        vscode.commands.executeCommand('vortex.openKiColabNotebook');
      } else if (msg.command === 'promoteKi') {
        vscode.commands.executeCommand('vortex.promoteKiCandidate');
      } else if (msg.command === 'runPipelineOne') {
        vscode.commands.executeCommand('vortex.runPipelineOne');
      } else if (msg.command === 'openPipelineOne') {
        vscode.commands.executeCommand('vortex.openPipelineOneSnapshot');
      }
    });
  }

  public async refresh() {
    if (this._view) {
      this._view.webview.postMessage({ command: 'refresh' });
      const status = {
        preset: vscode.workspace.getConfiguration('vortex').get<string>('preset') ?? '渦',
        lastVerdict: lastAuditResult?.verdict || null,
        newgate: await fetchNewgateStatus(),
        bridgeRuntime: await fetchBridgeRuntimeStatus(),
        kiQueue: await fetchKiQueueStatus(),
        pipelineOne: await fetchPipelineOneStatus(),
      };
      this._view.webview.html = getDashboardHtml(this._extensionUri, FLEET_LOG_DIR, () => status);
    }
  }
}

let lastAuditResult: AuditResult | null = null;

// ── Extension Activation ────────────────────────────────────────────────────

export function activate(context: vscode.ExtensionContext) {
  // Sidebar provider
  const sidebarProvider = new VortexSidebarProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(VortexSidebarProvider.viewType, sidebarProvider)
  );

  // ── Commands ────────────────────────────────────────────────────────────

  context.subscriptions.push(
    vscode.commands.registerCommand('vortex.openAsyncTaskDashboard', async () => {
      if (julesPanel) {
        julesPanel.reveal(vscode.ViewColumn.One);
        return;
      }

      const token = await getGitHubRepoToken(true);
      if (!token) {
        vscode.window.showErrorMessage('GitHub Authentication is required to access Jules operations.');
        return;
      }
      latestJulesToken = token;

      julesPanel = vscode.window.createWebviewPanel(
        'asyncTaskDashboard',
        '✨ Async Tasks (GitHub Issues)',
        vscode.ViewColumn.One,
        { enableScripts: true, retainContextWhenHidden: true }
      );
      julesPanelReady = false;

      const updateJulesView = async () => {
        if (!julesPanel) return;
        julesPanel.webview.html = getJulesHtml("Loading Jules Sessions from GitHub...", true);
        try {
          await syncJulesIssues({ createSessionIfNeeded: true, resetPanel: true });
        } catch (err: any) {
          julesPanel.webview.html = getJulesHtml(`Error: ${err.message}`, false);
        }
      };

      updateJulesView();

      julesPanel.webview.onDidReceiveMessage(async (msg) => {
        if (msg.command === 'refresh') {
          updateJulesView();
        } else if (msg.command === 'fetchComments') {
          // Fetch issue comments
          try {
            const res = await (globalThis as any).fetch(msg.commentsUrl, {
              headers: { 'Authorization': `Bearer ${token}`, 'Accept': 'application/vnd.github.v3+json' }
            });
            const comments = await res.json();
            julesPanel?.webview.postMessage({ command: 'renderComments', comments, issueId: msg.issueId });
          } catch (err: any) {
            vscode.window.showErrorMessage('Failed to fetch comments: ' + err.message);
          }
        } else if (msg.command === 'postReply') {
          try {
            const res = await (globalThis as any).fetch(msg.commentsUrl, {
              method: 'POST',
              headers: { 'Authorization': `Bearer ${token}`, 'Accept': 'application/vnd.github.v3+json', 'Content-Type': 'application/json' },
              body: JSON.stringify({ body: msg.body })
            });
            if (res.ok) {
              vscode.window.showInformationMessage('Reply sent to Jules successfully!');
              // Re-fetch comments to show the new one
              julesPanel?.webview.postMessage({ command: 'refreshComments', commentsUrl: msg.commentsUrl, issueId: msg.issueId });
            } else {
              vscode.window.showErrorMessage(`Failed to send reply: ${res.statusText}`);
            }
          } catch (err: any) {
            vscode.window.showErrorMessage('Failed to send reply: ' + err.message);
          }
        } else if (msg.command === 'openExternal' && typeof msg.url === 'string' && msg.url.trim()) {
          await openExternalUrl(msg.url);
        }
      }, undefined, context.subscriptions);

      julesPanel.onDidDispose(() => {
        julesPanel = undefined;
        julesPanelReady = false;
      }, null, context.subscriptions);
    }),

    vscode.commands.registerCommand('vortex.runAudit', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) {
        vscode.window.showWarningMessage('No active editor');
        return;
      }
      const code = editor.document.getText();
      const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: '🌀 VORTEX 監査中...' },
        async () => {
          const result = await runVortexAudit(code, wsRoot, context);
          lastAuditResult = result;
          void sidebarProvider.refresh();

          const channel = vscode.window.createOutputChannel('VORTEX Critic');
          channel.clear();
          channel.appendLine(`=== VORTEX 監査結果 ===`);
          channel.appendLine(`判定: ${result.verdict}`);
          channel.appendLine(`Preset: PCC #${result.preset}`);
          channel.appendLine(`時間: ${(result.elapsed / 1000).toFixed(1)}s`);
          channel.appendLine(`\n${result.text}`);
          channel.show();

          if (result.verdict === 'VERIFIED') {
            vscode.window.showInformationMessage(`✅ VORTEX: VERIFIED (${(result.elapsed / 1000).toFixed(1)}s)`);
          } else if (result.verdict === 'UNVERIFIED') {
            vscode.window.showWarningMessage('❌ VORTEX: UNVERIFIED — 証拠不足');
          } else {
            vscode.window.showErrorMessage(`⚠️ VORTEX: ${result.text}`);
          }
          // The sidebar refreshes automatically via sidebarProvider.refresh() above
        }
      );
    }),

    vscode.commands.registerCommand('vortex.runAuditSelection', async () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.selection.isEmpty) {
        vscode.window.showWarningMessage('No selection');
        return;
      }
      const code = editor.document.getText(editor.selection);
      const wsRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? '';

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: '🌀 VORTEX 選択範囲を監査中...' },
        async () => {
          const result = await runVortexAudit(code, wsRoot, context);
          lastAuditResult = result;
          void sidebarProvider.refresh();

          const channel = vscode.window.createOutputChannel('VORTEX Critic');
          channel.clear();
          channel.appendLine(`=== VORTEX 選択範囲監査 ===`);
          channel.appendLine(`判定: ${result.verdict}`);
          channel.appendLine(`\n${result.text}`);
          channel.show();
        }
      );
    }),

    vscode.commands.registerCommand('vortex.viewLogs', () => {
      const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      const logFile = path.join(FLEET_LOG_DIR, `fleet_${today}.jsonl`);
      if (fs.existsSync(logFile)) {
        vscode.workspace.openTextDocument(logFile).then(doc => vscode.window.showTextDocument(doc));
      } else {
        vscode.window.showInformationMessage('今日はまだフリートログがありません');
      }
    }),

    vscode.commands.registerCommand('vortex.clearLogs', async () => {
      const today = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      const logFile = path.join(FLEET_LOG_DIR, `fleet_${today}.jsonl`);
      if (fs.existsSync(logFile)) {
        const confirm = await vscode.window.showWarningMessage(
          '今日のフリートログを消去しますか？', { modal: true }, '消去'
        );
        if (confirm === '消去') {
          fs.unlinkSync(logFile);
          void sidebarProvider.refresh();
          vscode.window.showInformationMessage('フリートログを消去しました');
        }
      }
    }),

    vscode.commands.registerCommand('vortex.refreshSidebar', () => {
      void sidebarProvider.refresh();
    }),

    vscode.commands.registerCommand('vortex.startGeminiBridge', async () => {
      const channel = vscode.window.createOutputChannel('VORTEX Gemini Bridge');
      channel.clear();
      channel.appendLine('=== Gemini Code Assist A2A Bridge ===');
      channel.appendLine(`workspaceRoot: ${getWorkspaceRoot()}`);
      channel.appendLine(`statusPath: ${getGeminiBridgeStatusPath()}`);
      channel.appendLine(`bridgeUrl: http://127.0.0.1:${getGeminiBridgePort()}`);
      channel.appendLine(`launcher: ${getBackgroundLauncher()}`);
      channel.appendLine(`tmuxSession: ${getGeminiBridgeTmuxSession()}`);

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: '🚀 Gemini Code Assist Bridge を起動中...' },
        async () => {
          try {
            const result = await runGeminiBridgeBootstrap(context);
            if (result.stdout.trim()) {
              channel.appendLine(result.stdout.trim());
            }
            if (result.stderr.trim()) {
              channel.appendLine('\n--- STDERR ---');
              channel.appendLine(result.stderr.trim());
            }
            channel.show(true);
            if (fs.existsSync(result.statusPath)) {
              const doc = await vscode.workspace.openTextDocument(result.statusPath);
              await vscode.window.showTextDocument(doc, { preview: false });
            }
            void sidebarProvider.refresh();
            vscode.window.showInformationMessage('Gemini Code Assist Bridge を起動しました');
          } catch (error: any) {
            channel.appendLine('\n--- ERROR ---');
            channel.appendLine(String(error?.message ?? error));
            channel.show(true);
            void sidebarProvider.refresh();
            vscode.window.showErrorMessage(`Gemini Bridge の起動に失敗: ${error?.message ?? error}`);
          }
        }
      );
    }),

    vscode.commands.registerCommand('vortex.openGeminiBridgeSnapshot', async () => {
      const payload: any = {
        bridge: fs.existsSync(getGeminiBridgeStatusPath())
          ? JSON.parse(fs.readFileSync(getGeminiBridgeStatusPath(), 'utf-8'))
          : null,
        newgate: await fetchNewgateStatus(),
      };
      const doc = await vscode.workspace.openTextDocument({
        language: 'json',
        content: JSON.stringify(payload, null, 2),
      });
      await vscode.window.showTextDocument(doc, { preview: false });
    }),

    vscode.commands.registerCommand('vortex.openNewgateSnapshot', async () => {
      const snapshot = await fetchNewgateStatus();
      const doc = await vscode.workspace.openTextDocument({
        language: 'json',
        content: JSON.stringify(snapshot, null, 2),
      });
      await vscode.window.showTextDocument(doc, { preview: false });
    }),

    vscode.commands.registerCommand('vortex.runPipelineOne', async () => {
      const channel = vscode.window.createOutputChannel('VORTEX Pipeline 1');
      channel.clear();
      channel.appendLine('=== Pipeline 1: OSS -> Claude -> Gemini -> ECK ===');
      channel.appendLine(`repoPath: ${getPipelineOneRepoPath()}`);
      channel.appendLine(`statusPath: ${getPipelineOneStatusPath()}`);
      channel.appendLine(`containerRuntime: ${getPipelineOneContainerRuntime()}`);
      channel.appendLine(`cbfLauncher: ${getBackgroundLauncher()}`);
      channel.appendLine(`cbfTmuxSession: ${getPipelineOneCbfTmuxSession()}`);

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: '🧬 Pipeline① を起動中...' },
        async () => {
          try {
            const result = await runPipelineOneScripts(context);
            if (result.stdout.trim()) {
              channel.appendLine(result.stdout.trim());
            }
            if (result.stderr.trim()) {
              channel.appendLine('\n--- STDERR ---');
              channel.appendLine(result.stderr.trim());
            }
            channel.show(true);
            if (fs.existsSync(result.statusPath)) {
              const doc = await vscode.workspace.openTextDocument(result.statusPath);
              await vscode.window.showTextDocument(doc, { preview: false });
            }
            void sidebarProvider.refresh();
            vscode.window.showInformationMessage('Pipeline① を起動しました');
          } catch (error: any) {
            channel.appendLine('\n--- ERROR ---');
            channel.appendLine(String(error?.message ?? error));
            channel.show(true);
            void sidebarProvider.refresh();
            vscode.window.showErrorMessage(`Pipeline① の起動に失敗: ${error?.message ?? error}`);
          }
        }
      );
    }),

    vscode.commands.registerCommand('vortex.openPipelineOneSnapshot', async () => {
      const statusPath = getPipelineOneStatusPath();
      if (!fs.existsSync(statusPath)) {
        vscode.window.showWarningMessage(`Pipeline① status が見つからない: ${statusPath}`);
        return;
      }
      const doc = await vscode.workspace.openTextDocument(statusPath);
      await vscode.window.showTextDocument(doc, { preview: false });
    }),

    vscode.commands.registerCommand('vortex.packetizeAntigravity', async () => {
      const channel = vscode.window.createOutputChannel('VORTEX Antigravity Packets');
      channel.clear();
      channel.appendLine('=== Antigravity Packet Harvest ===');
      channel.appendLine(`appPath: ${getAntigravityAppPath()}`);
      channel.appendLine(`dbPath: ${getAntigravityPacketDbPath()}`);
      channel.appendLine(`summaryPath: ${getAntigravityPacketSummaryPath()}`);

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: '📦 Antigravity を Packet 抽出中...' },
        async () => {
          try {
            const result = await runAntigravityPacketHarvest();
            if (result.stdout.trim()) {
              channel.appendLine(result.stdout.trim());
            }
            if (result.stderr.trim()) {
              channel.appendLine('\n--- STDERR ---');
              channel.appendLine(result.stderr.trim());
            }
            channel.show(true);

            const doc = await vscode.workspace.openTextDocument(result.summaryPath);
            await vscode.window.showTextDocument(doc, { preview: false });
            vscode.window.showInformationMessage('Antigravity packet 抽出が完了しました');
          } catch (error: any) {
            channel.appendLine('\n--- ERROR ---');
            channel.appendLine(String(error?.message ?? error));
            channel.show(true);
            vscode.window.showErrorMessage(`Antigravity packet 抽出に失敗: ${error?.message ?? error}`);
          }
        }
      );
    }),

    vscode.commands.registerCommand('vortex.openKiQueueSnapshot', async () => {
      const entries = loadKiQueueEntries();
      const doc = await vscode.workspace.openTextDocument({
        language: 'json',
        content: JSON.stringify(
          {
            queueFile: getKiQueueFile(),
            knowledgeDir: getKiKnowledgeDir(),
            notebookPath: getKiColabNotebook(),
            entries,
          },
          null,
          2
        ),
      });
      await vscode.window.showTextDocument(doc, { preview: false });
    }),

    vscode.commands.registerCommand('vortex.openKiColabNotebook', async () => {
      const notebookPath = getKiColabNotebook();
      if (!fs.existsSync(notebookPath)) {
        vscode.window.showWarningMessage(`KI ノートブックが見つからない: ${notebookPath}`);
        return;
      }
      const uri = vscode.Uri.file(notebookPath);
      await vscode.commands.executeCommand('vscode.open', uri);
    }),

    vscode.commands.registerCommand('vortex.promoteKiCandidate', async () => {
      const pendingEntries = loadKiQueueEntries().filter((entry) => entry.status !== 'promoted');
      if (pendingEntries.length === 0) {
        vscode.window.showInformationMessage('未昇格の KI 候補はありません');
        return;
      }

      const pick = await vscode.window.showQuickPick(
        pendingEntries.map((entry) => ({
          label: entry.title || entry.id,
          description: entry.summary?.slice(0, 80) || entry.task?.slice(0, 80) || '',
          detail: entry.id,
          entry,
        })),
        {
          placeHolder: '~/.gemini/antigravity/knowledge に昇格する KI 候補を選択',
        }
      );
      if (!pick) {
        return;
      }

      const kiName = await vscode.window.showInputBox({
        prompt: 'KI ディレクトリ名',
        value: pick.entry.suggested_ki_name || slugifyKiName(pick.label),
        validateInput: (value) => (value.trim() ? undefined : 'KI ディレクトリ名は必須です'),
      });
      if (!kiName) {
        return;
      }

      const promoted = promoteKiQueueEntry(pick.entry, kiName);
      void sidebarProvider.refresh();
      vscode.window.showInformationMessage(`KI を昇格: ${promoted.kiDir}`);
      const doc = await vscode.workspace.openTextDocument(promoted.artifactPath);
      await vscode.window.showTextDocument(doc, { preview: false });
    }),

    vscode.commands.registerCommand('vortex.switchPreset', async () => {
      const presets = [
        { label: '#渦 (VORTEX)', description: 'Completion Illusion検知専用', value: '渦' },
        { label: '#監 (Auditor)', description: '証拠ベース判定', value: '監' },
        { label: '#刃 (Blade)', description: '厳格な実装レビュー', value: '刃' },
        { label: '#探 (Explorer)', description: '仮定を疑い弱点を探す', value: '探' },
        { label: '#極 (Extreme)', description: '簡潔・タスク前進のみ', value: '極' },
        { label: '#均 (Balance)', description: '長所短所のバランス', value: '均' },
      ];
      const pick = await vscode.window.showQuickPick(presets, {
        placeHolder: 'PCC Preset を選択',
      });
      if (pick) {
        await vscode.workspace.getConfiguration('vortex').update('preset', pick.value, true);
        void sidebarProvider.refresh();
        vscode.window.showInformationMessage(`PCC Preset を切替: ${pick.label}`);
      }
    }),
  );

  // ── Auto Audit on Save ────────────────────────────────────────────────────
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument(async (doc) => {
      const config = vscode.workspace.getConfiguration('vortex');
      if (config.get<boolean>('autoAuditOnSave')) {
        if (vscode.window.activeTextEditor?.document === doc) {
          vscode.commands.executeCommand('vortex.runAudit');
        }
      }
    })
  );

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration('vortex.jules')) {
        stopJulesPolling();
        startJulesPolling();
      }
    })
  );

  // Status bar (now shows fleet health)
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.text = '$(shield) VORTEX';
  statusBar.tooltip = 'Click to run VORTEX audit';
  statusBar.command = 'vortex.runAudit';
  statusBar.show();
  context.subscriptions.push(statusBar);

  // ── AI OS Subsystems ────────────────────────────────────────────────────

  // Fleet Health Check (updates status bar with live 🟢/🔴 count)
  startFleetHealthCheck(statusBar);

  // State Streaming (sends editor state to Fusion Gate)
  startStateStreaming();

  // Action Server (receives commands from AI backends)
  startActionServer();

  // Jules worker lane (delay-aware GitHub issue / PR watcher)
  startJulesPolling();

  // Sovereign Memory Recall (auto-recall on activation)
  const aiOsChannel = vscode.window.createOutputChannel('VORTEX AI OS');
  void runSovereignMemoryRecall(aiOsChannel);

  // Cleanup on dispose
  context.subscriptions.push({
    dispose: () => {
      stopFleetHealthCheck();
      stopStateStreaming();
      stopActionServer();
      stopJulesPolling();
    },
  });
}

export function deactivate() {
  stopFleetHealthCheck();
  stopStateStreaming();
  stopActionServer();
  stopJulesPolling();
}
