'use strict';
const assert = require('node:assert/strict');
const vscode = require('vscode');
exports.run = async function () {
  const extension = vscode.extensions.getExtension('local-switchboard.account-switchboard');
  assert.ok(extension, 'extension is discoverable by VS Code');
  await extension.activate();
  const commands = await vscode.commands.getCommands(true);
  assert.ok(commands.includes('accountSwitchboard.openWorkspace'));
  assert.ok(commands.includes('accountSwitchboard.runtimeDiagnostics'));
  const config = vscode.workspace.getConfiguration('accountSwitchboard');
  assert.ok(config.get('backendPath').endsWith('switcher.py'));
  // Registration-only smoke check. The UI catches backend errors, so awaiting a
  // command cannot prove that diagnostics succeeded. Do not invoke a backend here.
  console.log('ACCOUNT_SWITCHBOARD_HOST_REGISTRATION_PASS: extension activated; commands registered; backend not invoked');
};
