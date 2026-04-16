import * as assert from 'assert';
import * as vscode from 'vscode';

suite('VORTEX Extension Test Suite', () => {
    vscode.window.showInformationMessage('Start all tests.');

    test('Extension should be present', () => {
        assert.ok(vscode.extensions.getExtension('ryota-core.vortex-critic'));
    });

    test('Should activate without errors', async () => {
        const ext = vscode.extensions.getExtension('ryota-core.vortex-critic');
        if (ext) {
            await ext.activate();
            assert.strictEqual(ext.isActive, true);
        } else {
            assert.fail('Extension not found');
        }
    });

    test('Should register vortex.runAudit command', async () => {
        const commands = await vscode.commands.getCommands(true);
        assert.ok(commands.includes('vortex.runAudit'), 'vortex.runAudit command should be registered');
    });

    test('Should register vortex.openAsyncTaskDashboard command', async () => {
        const commands = await vscode.commands.getCommands(true);
        assert.ok(commands.includes('vortex.openAsyncTaskDashboard'), 'vortex.openAsyncTaskDashboard should be registered');
    });
});
