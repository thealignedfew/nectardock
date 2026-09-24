'use strict';
const { execFile } = require('node:child_process');

function runBackend(executable, args, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    execFile(executable, args, {windowsHide: true, shell: false, timeout: 90000,
      maxBuffer: 4 * 1024 * 1024, encoding: 'utf8'}, (error, stdout, stderr) => {
      for (const line of (stderr || '').split(/\r?\n/)) {
        if (line.startsWith('SWITCHBOARD_PROGRESS ')) onProgress(line);
      }
      let result;
      try { result = JSON.parse(stdout); }
      catch { return reject(new Error('Switchboard returned no structured JSON. Check backend/Python paths.')); }
      if (error || result.state === 'HELD') {
        return reject(new Error(result.error || `Switchboard held the operation (${error?.code || result.state}).`));
      }
      resolve(result);
    });
  });
}

async function openAccountWorkspace(ui, run) {
  const catalog = await run(['catalog']);
  if (!Array.isArray(catalog.workspaces)) throw new Error('Invalid workspace catalog.');
  const choices = catalog.workspaces.map(w => ({label: `${w.account} | ${w.group}`,
    description: `${w.email} (${w.plan})`, detail: `${w.sessions.length} registered conversations`, workspace: w}));
  if (!choices.length) throw new Error('No registered account workspaces. Use the switchboard to register sessions.');
  const choice = await ui.showQuickPick(choices, {title:'Open Account Workspace',
    placeHolder:'Choose the account and workspace, not just its title color'});
  if (!choice) return;
  const w = choice.workspace;
  const selected = await ui.showQuickPick(w.sessions.map(s => ({label:s.label, description:s.uuid,
    uuid:s.uuid, picked:true})), {canPickMany:true, title:`${w.account} | ${w.group}: allowed conversations`,
    placeHolder:'Saved tabs outside this selection will block opening. This does not transfer histories.'});
  if (!selected?.length) return;
  const confirm = await ui.showWarningMessage(
    `Open ${w.account} | ${w.group} (${w.email})? The whole workspace opens. Missing tabs are not auto-resumed; saved tabs and account identity will be checked.`,
    {modal:true}, 'Open workspace');
  if (confirm !== 'Open workspace') return;
  const args = ['open', '--group', w.group, '--destination', w.account];
  for (const s of selected) args.push('--uuid', s.uuid);
  const result = await run(args);
  if (result.state !== 'WORKSPACE_LAUNCH_REQUESTED') throw new Error(result.error || 'Workspace launch was not requested.');
  await ui.showInformationMessage(`${w.account} | ${w.group}: launch requested. Verify the latest messages in the destination window; rendering is not verified.`);
}

module.exports = {runBackend, openAccountWorkspace};
