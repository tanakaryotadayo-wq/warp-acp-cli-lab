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
const DEFAULT_PIPELINE_ONE_STATUS_PATH = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'fusion-gate/data/pipeline_01/status.json'
);
const DEFAULT_PIPELINE_ONE_BOOTSTRAP_SCRIPT = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'fusion-gate/scripts/bootstrap_pipeline_01.sh'
);
const DEFAULT_PIPELINE_ONE_RUNNER_SCRIPT = path.join(
  process.env.HOME ?? '/Users/ryyota',
  'fusion-gate/scripts/pipeline_01_runner.py'
);
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
  packetCount: number;
  issueCount: number;
  eckRuns: number;
  error?: string;
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
  return vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneStatusPath')?.trim() || DEFAULT_PIPELINE_ONE_STATUS_PATH;
}

function getPipelineOneBootstrapScript(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneBootstrapScript')?.trim() || DEFAULT_PIPELINE_ONE_BOOTSTRAP_SCRIPT;
}

function getPipelineOneRunnerScript(): string {
  return vscode.workspace.getConfiguration('vortex').get<string>('pipelineOneRunnerScript')?.trim() || DEFAULT_PIPELINE_ONE_RUNNER_SCRIPT;
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
      packetCount: Number(raw.packet_summary?.count ?? raw.packetCount ?? 0),
      issueCount: Number(raw.issue_count ?? raw.issueCount ?? 0),
      eckRuns: Number(raw.eck?.bridge?.runs ?? raw.eckRuns ?? 0),
      error: raw.error ? String(raw.error) : undefined,
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
      packetCount: 0,
      issueCount: 0,
      eckRuns: 0,
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

async function runPipelineOneScripts(): Promise<{ stdout: string; stderr: string; statusPath: string }> {
  const bootstrapScript = getPipelineOneBootstrapScript();
  const runnerScript = getPipelineOneRunnerScript();
  const statusPath = getPipelineOneStatusPath();
  const repoPath = getPipelineOneRepoPath();

  if (!fs.existsSync(bootstrapScript)) {
    throw new Error(`pipeline bootstrap script not found: ${bootstrapScript}`);
  }
  if (!fs.existsSync(runnerScript)) {
    throw new Error(`pipeline runner script not found: ${runnerScript}`);
  }

  const runProcess = (command: string, args: string[], cwd: string, timeoutMs: number) =>
    new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
      const proc = cp.spawn(command, args, { cwd });
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

  const bootstrap = await runProcess('/bin/bash', [bootstrapScript], path.dirname(bootstrapScript), 120000);
  const runner = await runProcess(
    'python3',
    [
      runnerScript,
      '--repo-path', repoPath,
      '--repo-name', path.basename(repoPath),
      '--status-path', statusPath,
    ],
    path.dirname(runnerScript),
    300000,
  );

  return {
    stdout: [bootstrap.stdout.trim(), runner.stdout.trim()].filter(Boolean).join('\n\n'),
    stderr: [bootstrap.stderr.trim(), runner.stderr.trim()].filter(Boolean).join('\n\n'),
    statusPath,
  };
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
        kiQueue: await fetchKiQueueStatus(),
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

  let julesPanel: vscode.WebviewPanel | undefined;

  context.subscriptions.push(
    vscode.commands.registerCommand('vortex.openAsyncTaskDashboard', async () => {
      if (julesPanel) {
        julesPanel.reveal(vscode.ViewColumn.One);
        return;
      }
      
      let token = '';
      try {
        const session = await vscode.authentication.getSession('github', ['repo'], { createIfNone: true });
        token = session.accessToken;
      } catch (err) {
        vscode.window.showErrorMessage('GitHub Authentication is required to access Jules operations.');
        return;
      }

      julesPanel = vscode.window.createWebviewPanel(
        'asyncTaskDashboard',
        '✨ Async Tasks (GitHub Issues)',
        vscode.ViewColumn.One,
        { enableScripts: true, retainContextWhenHidden: true }
      );

      const updateJulesView = async () => {
        if (!julesPanel) return;
        julesPanel.webview.html = getJulesHtml("Loading Jules Sessions from GitHub...", true);
        try {
          // Fetch open issues mentioning jules involving the current user
          const res = await (globalThis as any).fetch(`https://api.github.com/search/issues?q=involves:@me+"jules"+is:open+is:issue`, {
            headers: { 'Authorization': `Bearer ${token}`, 'Accept': 'application/vnd.github.v3+json' }
          });
          const data = await res.json() as any;
          if (data.items) {
             // Pass the raw JSON array to the dashboard parser instead of a CLI string
             julesPanel.webview.html = getJulesHtml(JSON.stringify(data.items), false);
          } else {
             julesPanel.webview.html = getJulesHtml(`Error: ${JSON.stringify(data)}`, false);
          }
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
        }
      }, undefined, context.subscriptions);

      julesPanel.onDidDispose(() => {
        julesPanel = undefined;
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

      await vscode.window.withProgress(
        { location: vscode.ProgressLocation.Notification, title: '🧬 Pipeline① を起動中...' },
        async () => {
          try {
            const result = await runPipelineOneScripts();
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

  // Status bar
  const statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusBar.text = '$(shield) VORTEX';
  statusBar.tooltip = 'Click to run VORTEX audit';
  statusBar.command = 'vortex.runAudit';
  statusBar.show();
  context.subscriptions.push(statusBar);
}

export function deactivate() {}
