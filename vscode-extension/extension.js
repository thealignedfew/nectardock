'use strict';
const vscode = require('vscode');
const path = require('node:path');
const {runBackend, openAccountWorkspace} = require('./launcher');

function activate(context) {
  const output = vscode.window.createOutputChannel('Account Switchboard');
  context.subscriptions.push(output);
  let busy = false;
  const run = async args => {
    const config = vscode.workspace.getConfiguration('accountSwitchboard');
    const python = config.get('pythonPath');
    const backend = config.get('backendPath');
    if (!path.isAbsolute(python) || !path.isAbsolute(backend)) throw new Error('Configure absolute Python and switchboard paths in User Settings.');
    output.appendLine(`[${new Date().toLocaleString()}] switcher.py ${args.join(' ')}`);
    const result = await runBackend(python, [backend, ...args], line => output.appendLine(line));
    output.appendLine(JSON.stringify(result, null, 2));
    return result;
  };
  const guarded = fn => async () => {
    if (busy) return vscode.window.showInformationMessage('An Account Switchboard operation is already running.');
    busy = true;
    try { await fn(); }
    catch (error) { output.appendLine(`HELD: ${error.message}`); output.show(true);
      await vscode.window.showErrorMessage(`Account Switchboard: ${error.message}`); }
    finally { busy = false; }
  };
  context.subscriptions.push(vscode.commands.registerCommand('accountSwitchboard.openWorkspace',
    guarded(() => openAccountWorkspace(vscode.window, run))));
  context.subscriptions.push(vscode.commands.registerCommand('accountSwitchboard.runtimeDiagnostics',
    guarded(async () => { await run(['diagnose']); output.show(true); })));
}
module.exports = {activate};
