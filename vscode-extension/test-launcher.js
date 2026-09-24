'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { openAccountWorkspace, runBackend } = require('./launcher');

const catalog = { state: 'CATALOG', workspaces: [{account: 'GREEN', group: 'FPA',
  email: 'max@example.com', plan: 'team', sessions: [{uuid: 'a', label: 'Current'}]}] };

test('explicit selection launches exact account and UUID, never a transfer', async () => {
  const calls = [], messages = [];
  const ui = {showQuickPick: async (items, opts) => opts.canPickMany ? items : items[0],
    showWarningMessage: async () => 'Open workspace', showInformationMessage: async m => messages.push(m)};
  await openAccountWorkspace(ui, async args => {calls.push(args); return args[0] === 'catalog'
    ? catalog : {state:'WORKSPACE_LAUNCH_REQUESTED'};});
  assert.deepEqual(calls, [['catalog'], ['open', '--group', 'FPA', '--destination', 'GREEN', '--uuid', 'a']]);
  assert.match(messages[0], /requested/i);
});

test('cancel and empty selection never launch a window', async () => {
  for (const result of [undefined, []]) {
    const calls = [];
    const ui = {showQuickPick: async (items, opts) => opts.canPickMany ? result : items[0]};
    await openAccountWorkspace(ui, async args => {calls.push(args); return catalog;});
    assert.deepEqual(calls, [['catalog']]);
  }
});

test('backend hold is surfaced, not announced as a successful launch', async () => {
  const ui = {showQuickPick: async (items, opts) => opts.canPickMany ? items : items[0],
    showWarningMessage: async () => 'Open workspace',
    showInformationMessage: () => assert.fail('must not report success')};
  await assert.rejects(openAccountWorkspace(ui, async args => args[0] === 'catalog'
    ? catalog : {state:'HELD', error:'Saved tabs outside selection'}), /Saved tabs outside selection/);
});

test('subprocess uses literal arguments without shell and rejects malformed output', async () => {
  const stdout = await runBackend(process.execPath, ['-e', 'console.log(JSON.stringify({state:"OK"}))']);
  assert.equal(stdout.state, 'OK');
  await assert.rejects(runBackend(process.execPath, ['-e', 'console.log("not json")']), /structured JSON/);
  await assert.rejects(runBackend(process.execPath,
    ['-e', 'console.log(JSON.stringify({state:"HELD",error:"unsafe"}));process.exit(2)']), /unsafe/);
});
