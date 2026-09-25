"""Local, account-isolated Claude workspace switcher. No model calls or automatic closure."""
from __future__ import annotations

import argparse
import collections
import contextlib
import copy
import ctypes
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from urllib.parse import unquote, urlsplit
import uuid

BASE = Path(__file__).resolve().parent
ROOT = Path('D:/NectarDockExample/private-helpers')
HELPERS = ROOT / 'localpc'
RUNS = Path('D:/NectarDockExample/Session-Capsules/Switch-Runs')
INDEX = Path('D:/NectarDockExample/Session-Capsules/CURRENT-SESSION-HOMES.json')
MAP = ROOT / 'capsule-map.md'
MAP_HISTORY = HELPERS / 'capsule-map-history.json'
CLAUDE_CMD = Path('C:/Users/ExampleUser/AppData/Roaming/npm/claude.cmd')
CODE = Path('C:/Users/ExampleUser/AppData/Local/Programs/Microsoft VS Code/Code.exe')
SHARED_BUCKET = 'd--NectarDockExample-Projects'
PINS = {
    'history_reconcile.py': 'c6b16e928bf2d5e9639cfe5b32141fdf091ce19e770fea5cd394f7242b7a5424',
    'history_native_probe.py': '6496d6cd0423918374693525e375318c680f3e419741f5f397239d48ffe6c628',
    'render_capsule_map.py': 'a818e60b4d531e31479a3e54eb206c62c12a09caf49489aeeb4d41bd04e60ea7',
}
ACCOUNTS = {
    'ORANGE': {'home': 'D:/NectarDockExample/Accounts/Claude/orange-account/home', 'email': 'bi@example.invalid', 'plan': 'team', 'user_data': 'D:/NectarDockExample/Accounts/Codex/orange-account/vscode-user-data', 'color': '#A65412', 'theme': 'Kimbie Dark'},
    'PURPLE': {'home': 'D:/NectarDockExample/Accounts/Claude/purple-account/home', 'email': 'bi@example.invalid', 'plan': 'max', 'user_data': 'D:/NectarDockExample/Accounts/Claude/purple-account/vscode-user-data', 'fpa_user_data': 'D:/NectarDockExample/Accounts/Claude/purple-account-finance/vscode-user-data', 'color': '#5B2C83', 'theme': 'Default High Contrast'},
    'GREEN': {'home': 'D:/NectarDockExample/Accounts/Claude/green-account/home', 'email': 'green@example.invalid', 'plan': 'team', 'user_data': 'D:/NectarDockExample/Accounts/Claude/green-account/vscode-user-data', 'color': '#145A32', 'theme': 'Default High Contrast'},
    'YELLOW': {'home': 'D:/NectarDockExample/Accounts/Claude/yellow-account/home', 'email': 'yellow@example.invalid', 'plan': 'max', 'user_data': 'D:/NectarDockExample/Accounts/Claude/yellow-account/vscode-user-data', 'workspace_root': 'D:/NectarDockExample/Accounts/Claude/yellow-account/workspaces', 'color': '#D4AC0D', 'theme': 'Default High Contrast'},
}
GROUPS = {
    'FPA': 'Finance', 'GCP': 'Cloud',
    'BI': ('Analytics', 'Compliance'),
}
PROJECTS = Path('D:/NectarDockExample/Projects')
OVERRIDES = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'ANTHROPIC_AWS_API_KEY', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY', 'CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST', 'CLAUDE_CODE_PROJECT_DIR_NAME')
sys.dont_write_bytecode = True


class Hold(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise Hold(message)


def emit_progress(event):
    print('SWITCHBOARD_PROGRESS ' + ' '.join(str(event).splitlines()), file=sys.stderr, flush=True)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def dependencies():
    for name, expected in PINS.items():
        require(digest(HELPERS / name) == expected, 'Reviewed dependency changed: ' + name)
    sys.path.insert(0, str(HELPERS))
    import history_reconcile as h
    import render_capsule_map as maps
    return h, maps


def plain(path):
    """Reject redirection and cloud recall on every existing ancestor."""
    p = Path(path).absolute()
    for item in (p, *p.parents):
        if item.exists():
            attrs = getattr(item.lstat(), 'st_file_attributes', 0)
            require(not attrs & (0x400 | 0x1000 | 0x40000 | 0x400000), 'Redirected/offline path: ' + str(item))
    return p


def account_env(color):
    env = os.environ.copy()
    for name in OVERRIDES + ('VSCODE_IPC_HOOK_CLI', 'ELECTRON_RUN_AS_NODE', 'VSCODE_DEV'):
        env.pop(name, None)
    env['CLAUDE_CONFIG_DIR'] = ACCOUNTS[color]['home'].replace('/', '\\')
    env['CLAUDE_CODE_GIT_BASH_PATH'] = 'C:\\Program Files\\Git\\bin\\bash.exe'
    return env


def auth_check(color):
    # Fixed, reviewed command; credentials are never read or copied by this tool.
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        "& '" + str(CLAUDE_CMD) + "' auth status"], env=account_env(color), capture_output=True,
        encoding='utf-8', timeout=45, creationflags=0x08000000)
    require(result.returncode == 0, 'Destination login is unavailable. Use its existing account window to sign in.')
    data = json.loads(result.stdout)
    validate_auth(color, data)
    return {k: data.get(k) for k in ('loggedIn', 'email', 'orgId', 'orgName', 'subscriptionType', 'configDirectory', 'authMethod')}


def launch_login(color):
    """Open an interactive subscription login in one isolated account home."""
    require(color in ACCOUNTS, 'Unknown Claude account color')
    import desktop_launcher
    if desktop_launcher.needs_broker():
        return desktop_launcher.request('login',color=color)
    account = ACCOUNTS[color]
    command = "& '" + str(CLAUDE_CMD).replace("'", "''") + "' auth login --claudeai --email " + account['email']
    process = subprocess.Popen(
        ['powershell.exe', '-NoProfile', '-NoExit', '-Command', command],
        env=account_env(color), cwd=BASE, creationflags=0x00000010)
    return {'state': 'LOGIN_STARTED_NOT_VERIFIED', 'account': color,
            'email': account['email'], 'plan': account['plan'], 'pid': process.pid,
            'meaning': 'Complete sign-in in the new terminal, then retry this account usage snapshot.'}


def norm(path):
    return os.path.normcase(os.path.abspath(path))


def validate_auth(color, data):
    wanted = ACCOUNTS[color]
    require(data.get('loggedIn') and str(data.get('email', '')).lower() == wanted['email'].lower()
        and data.get('subscriptionType') == wanted['plan']
        and norm(data.get('configDirectory', '')) == norm(wanted['home']),
        'Destination account does not match ' + color + '. No files moved.')


def group_projects(group):
    """A launch group may contain several native projects; never rewrite their identity."""
    require(group in GROUPS, 'Unknown workspace group')
    names = GROUPS[group]
    return [PROJECTS / name for name in ((names,) if isinstance(names, str) else names)]


def selected(view, group, ids=None):
    projects = {norm(p).rstrip('\\/') for p in group_projects(group)}
    rows = [r for r in view['records'].values() if norm(r['project_directory']).rstrip('\\/') in projects]
    if ids:
        wanted = set(ids)
        rows = [r for r in rows if r['uuid'] in wanted]
        require({r['uuid'] for r in rows} == wanted, 'Selected UUID is not registered in this workspace group')
    require(rows, 'No registered conversations for this group')
    homes = {norm(a['home']) for a in ACCOUNTS.values()} | {norm('C:/Users/ExampleUser/.claude')}
    for r in rows:
        require(norm(r['config_home']) in homes, 'Unknown source account: ' + r['uuid'])
        require(Path(r['primary_history_path']).name == r['uuid'] + '.jsonl', 'UUID/path mismatch')
    return sorted(rows, key=lambda r: (r['label'], r['uuid']))


def support_reason(item, evidence):
    """Exact non-conversation modes only, fenced by native process start and home."""
    if (not evidence or not item.get('process_start_filetime') or
            str(evidence.get('start')) != item['process_start_filetime'] or
            item.get('uuid') or item.get('registry_verified') or
            item.get('error') not in (None, 'missing/conflicting UUID or process-start binding')):
        return None
    command = (evidence.get('command') or '').strip()
    # Do not use shell parsing, substring matching, or arbitrary executable names.
    parsed = re.fullmatch(r'(?:"([^"\r\n]+)"|([^\s"]+))[ \t]+([^\r\n]+)', command)
    if not parsed:
        return None
    binary, arguments = parsed.group(1) or parsed.group(2), parsed.group(3)
    if norm(binary) != norm(evidence.get('exe') or ''):
        return None
    cli = CLAUDE_CMD.parent / 'node_modules/@anthropic-ai/claude-code/bin/claude.exe'
    if norm(binary) == norm(cli):
        accounts = [a for a in ACCOUNTS.values()
                    if norm(item.get('account_home') or '') == norm(a['home'])]
        if accounts and arguments == 'auth status':
            return 'EXACT_ACCOUNT_AUTH_STATUS_NOT_CONVERSATION'
        if any(arguments == 'auth login --claudeai --email ' + a['email'] for a in accounts):
            return 'EXACT_ACCOUNT_AUTH_LOGIN_NOT_CONVERSATION'
        return None
    expected = Path('C:/Users/ExampleUser/.vscode/extensions/anthropic.claude-code-2.1.280-win32-x64/resources/native-binary/claude.exe')
    if norm(binary) == norm(expected) and arguments == '--claude-in-chrome-mcp':
        return 'EXACT_INSTALLED_CHROME_MCP_MODE'
    return None


def support_only(item, evidence):
    return support_reason(item, evidence) is not None


def classify_support_processes(census):
    """Keep uncertain writers blocked; retain evidence of narrowly recognized helpers."""
    if not census['unknown_live']:
        return census
    import base64
    ids = [item['pid'] for item in census['unknown_live']]
    require(all(type(pid) is int and pid > 0 for pid in ids), 'Invalid process identity')
    filt = ' OR '.join('ProcessId = ' + str(pid) for pid in ids)
    command = "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter '" + filt + "' | ForEach-Object { [pscustomobject]@{pid=$_.ProcessId;exe=$_.ExecutablePath;command=$_.CommandLine;start=$_.CreationDate.ToUniversalTime().ToFileTimeUtc().ToString()} }) | ConvertTo-Json -Compress"
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',base64.b64encode(command.encode('utf-16-le')).decode()],capture_output=True,encoding='utf-8',timeout=30,creationflags=0x08000000)
    require(result.returncode == 0, 'Support process inspection failed')
    details = json.loads(result.stdout or '[]')
    if isinstance(details, dict):
        details = [details]
    # CIM CreationDate is rounded to microseconds. Fence with the exact native
    # FILETIME used by the original census, rather than treating rounding as reuse.
    from history_native_probe import k, ct
    for detail in details:
        handle = k.OpenProcess(0x1000, False, detail['pid'])
        creation = ct.c_ulonglong(); rest = [ct.c_ulonglong() for _ in range(3)]
        detail['start'] = None
        if handle:
            try:
                if k.GetProcessTimes(handle,ct.byref(creation),*[ct.byref(x) for x in rest]):
                    detail['start'] = str(creation.value)
            finally:
                k.CloseHandle(handle)
    by_pid = {p['pid']: p for p in details}
    census = copy.deepcopy(census)
    census['verified_support_only'] = [dict(item, reason=support_reason(item, by_pid.get(item['pid']))) for item in census['unknown_live'] if support_only(item, by_pid.get(item['pid']))]
    census['unknown_live'] = [item for item in census['unknown_live'] if not support_only(item, by_pid.get(item['pid']))]
    return census


def runtime_diagnostics(census=None):
    """Read-only diagnosis. Automatically clear only proven non-writers, never kill."""
    if census is None:
        h, _ = dependencies()
        census = classify_support_processes(h.processes(
            [a['home'] for a in ACCOUNTS.values()] + ['C:/Users/ExampleUser/.claude'], set()))
    cleared = [{'pid': p['pid'], 'account_home': p.get('account_home'), 'reason': p['reason']}
               for p in census.get('verified_support_only', [])]
    blocked = [{'pid': p['pid'], 'account_home': p.get('account_home'), 'error': p.get('error')}
               for p in census['unknown_live']]
    return {'state': 'HELD' if blocked or census['registry_errors'] else 'PROCESS_IDENTITIES_CLEAR',
            'automatically_cleared_nonwriters': cleared, 'unresolved_processes': blocked,
            'registry_errors': census['registry_errors'], 'live_conversations': census['bindings'],
            'meaning': 'Identity diagnosis only, not transfer readiness. No processes stopped; no histories changed.'}


def runtime_gate(h, rows):
    ids = {r['uuid'] for r in rows}
    census = classify_support_processes(h.processes([a['home'] for a in ACCOUNTS.values()] + ['C:/Users/ExampleUser/.claude'], ids))
    diagnostic = runtime_diagnostics(census)
    for item in diagnostic['automatically_cleared_nonwriters']:
        emit_progress(f"RUNTIME auto-cleared non-writer PID {item['pid']}: {item['reason']} (process left running)")
    details = ', '.join(f"PID {p['pid']}: {p.get('error')}" for p in diagnostic['unresolved_processes'])
    if census['registry_errors']:
        details += f"; {len(census['registry_errors'])} registry error(s)"
    require(diagnostic['state'] != 'HELD',
            'Live process identity is uncertain; no switch. ' + details + '. Use Runtime diagnostics for details.')
    live = [r for r in census['bindings'] if r['uuid'] in ids]
    return census, live


def clear_runtime(h, rows):
    census, live = runtime_gate(h, rows)
    names = {r['uuid']: r['label'] for r in rows}
    require(not live, 'Close these Claude conversation tabs normally first: ' + ', '.join(names[r['uuid']] for r in live))
    return census


def prefix(older, newer):
    if Path(older).stat().st_size > Path(newer).stat().st_size:
        return False
    with Path(older).open('rb') as a, Path(newer).open('rb') as b:
        while True:
            block = a.read(1024 * 1024)
            if not block:
                return True
            if b.read(len(block)) != block:
                return False


def classify(source, target, main=False, choose_source=False):
    if target is None:
        return 'CREATE'
    if source['sha256'] == target['sha256']:
        return 'KEEP'
    if main and prefix(target['snapshot'], source['snapshot']):
        return 'ADVANCE_BYTE_PREFIX'
    if main and native_prefix(target['snapshot'], source['snapshot']):
        return 'ADVANCE_NATIVE_PREFIX'
    if not main:
        return 'REVIEW_CURRENT_COMPANION'
    if choose_source:
        return 'CHOOSE_SOURCE_MAIN_SURVIVOR'
    raise Hold('Distinct saved branches or companion versions require review: ' + target['path'])


def validate_survivor_scope(rows, destination_color, ids, source_color):
    if source_color is None:
        return False
    require(source_color in ACCOUNTS and destination_color in ACCOUNTS,
            'Unknown survivor account')
    require(source_color != destination_color, 'Survivor source and destination must differ')
    require(ids is not None and len(ids) == 1 and len(rows) == 1
            and ids[0] == rows[0]['uuid'], 'Survivor choice requires one exact registered UUID')
    require(norm(rows[0]['config_home']) == norm(ACCOUNTS[source_color]['home']),
            'Registered source account is not the selected survivor')
    return True


def survivor_choices(rows, destination_color, ids, legacy_source=None, choices=None):
    require(not (legacy_source and choices), 'Use one survivor selection format')
    result = dict(choices or {})
    if legacy_source:
        validate_survivor_scope(rows,destination_color,ids,legacy_source)
        result[rows[0]['uuid']] = legacy_source
    require(ids is not None or not result, 'Survivor choices require exact UUIDs')
    by_id = {r['uuid']:r for r in rows}
    require(set(result)<=set(by_id), 'Survivor choice outside selected conversations')
    for sid,color in result.items():
        validate_survivor_scope([by_id[sid]],destination_color,[sid],color)
    return result


def validate_survivor_manifest(manifest, rows, accepted):
    choices = survivor_choices(rows,manifest['color'],manifest.get('selected_uuids'),
                              manifest.get('survivor_source'),manifest.get('survivor_sources'))
    choose_source = bool(choices)
    validate_survivor_scope(
        rows, manifest['color'], manifest.get('selected_uuids'), manifest.get('survivor_source'))
    count = sum(op['action'] == 'CHOOSE_SOURCE_MAIN_SURVIVOR'
                for section in manifest['sessions'] for op in section['operations'])
    require(manifest.get('survivor_main_count', 0) == count,
            'Survivor action count does not match prepared operations')
    require(not count or choose_source, 'Survivor action lacks an exact source selection')
    for section in manifest['sessions']:
        require(not any(o['action']=='CHOOSE_SOURCE_MAIN_SURVIVOR' for o in section['operations'])
                or section['record']['uuid'] in choices, 'Survivor action lacks a per-conversation choice')
    require(not choose_source or not manifest.get('memory'),
            'A one-conversation survivor choice cannot change shared memory')
    require(not count or accepted,
            'Review the divergent main histories and explicitly accept the named source survivor')
    return choices


def native_records(path):
    with Path(path).open('rb') as stream:
        while True:
            raw = stream.readline(32 * 1024 * 1024 + 1)
            if not raw:
                return
            require(len(raw) <= 32 * 1024 * 1024 and raw.endswith(b'\n'), 'Oversized or incomplete native record')
            if not raw.strip():
                continue
            record = json.loads(raw)
            if record.get('type') in ('user', 'assistant', 'system'):
                yield record


def native_prefix(older, newer):
    a, b = native_records(older), native_records(newer)
    count = 0
    for record in a:
        if next(b, None) != record:
            return False
        count += 1
    for _ in b:
        pass
    return count > 0


def stable(h, item):
    plain(item['path'])
    require(h.fence(item['path']) == item['after'] and digest(item['path']) == item['sha256'], 'File changed since review: ' + item['path'])
    require(digest(item['snapshot']) == item['sha256'], 'Preserved snapshot changed')


def available_memory():
    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ('total_physical', 'available_physical',
            'total_pagefile', 'available_pagefile', 'total_virtual', 'available_virtual', 'extended')]
    status = MemoryStatus(); status.length = ctypes.sizeof(status)
    require(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)), 'Cannot check parser memory budget')
    return status.available_physical


LARGE_RECORD_BYTES = 256 * 1024**2
LARGE_HISTORY_BYTES = 512 * 1024**2


def validate_large_checkpoint(h, item, sid, checkpoint):
    """Validate a bounded attachment record only in an unchanged registered checkpoint.

    The shared parser keeps its 32 MiB cap. No source bytes or prior evidence are edited.
    A changed history, insufficient memory, malformed record or foreign UUID stays held.
    """
    require(checkpoint and item['sha256'] == checkpoint['main_sha256'],
            'Oversized history differs from its registered checkpoint; separate review required')
    plain(checkpoint['snapshot_path'])
    require(digest(checkpoint['snapshot_path']) == checkpoint['main_sha256'], 'Registered checkpoint snapshot changed')
    require(item['bytes'] <= LARGE_HISTORY_BYTES, 'History exceeds bounded attachment review size')
    require(available_memory() >= 4 * 1024**3, 'Less than 4 GiB available for attachment record validation')
    types = collections.Counter(); ids = set(); parents = set(); backups = set(); tools = set()
    count = 0; compactions = 0; maximum = 0; hashed = hashlib.sha256()
    def reject_constant(value):
        raise ValueError('Non-JSON numeric constant')
    with Path(item['snapshot']).open('rb') as stream:
        while True:
            raw = stream.readline(LARGE_RECORD_BYTES + 1)
            if not raw:
                break
            require(len(raw) <= LARGE_RECORD_BYTES, 'Record exceeds bounded attachment review size')
            require(raw.endswith(b'\n'), 'History needs review: unterminated_tail_bytes')
            hashed.update(raw); maximum = max(maximum, len(raw))
            if not raw.strip():
                continue
            try:
                value = json.loads(raw.decode('utf-8'), parse_constant=reject_constant)
            except (ValueError, UnicodeError) as exc:
                raise Hold('History needs review: malformed_complete_lines') from exc
            require(isinstance(value, dict), 'History record is not an object')
            require(not value.get('sessionId') or value['sessionId'] == sid,
                    'History needs review: foreign_session_ids')
            typ = value.get('type'); types[str(typ)] += 1; count += 1
            if value.get('uuid'): ids.add(str(value['uuid']))
            if value.get('parentUuid'): parents.add(str(value['parentUuid']))
            if typ == 'system' and value.get('subtype') == 'compact_boundary': compactions += 1
            if typ == 'file-history-snapshot':
                tracked = (value.get('snapshot') or {}).get('trackedFileBackups') or {}
                require(isinstance(tracked, dict), 'Malformed file-history reference map')
                backups.update(str(v['backupFileName']) for v in tracked.values()
                               if isinstance(v, dict) and v.get('backupFileName'))
            tools.update(h.extract_tool_refs(raw, sid))
            del value
    require(hashed.hexdigest() == item['sha256'], 'Attachment validation snapshot changed')
    item.update(complete_lines=count, malformed_complete_lines=0, oversized_records=0,
                unterminated_tail_bytes=0, record_types=dict(types), foreign_session_ids=[],
                compaction_boundaries=compactions, unresolved_parent_ids=sorted(parents - ids),
                backup_refs=sorted(backups), tool_result_refs=sorted(tools),
                large_record_validation={'method': 'strict_utf8_json_unchanged_registered_checkpoint',
                    'checkpoint_sha256': checkpoint['main_sha256'], 'standard_record_limit': h.MAX_RECORD_BYTES,
                    'bounded_record_limit': LARGE_RECORD_BYTES, 'largest_record_bytes': maximum})


def capture(h, path, objects, sid, parse=False, checkpoint=None, cache=None):
    plain(path)
    key = None
    if cache is not None:
        before = h.fence(path); sha = digest(path)
        require(h.fence(path)==before, 'Changing source file')
        key = (sha, bool(parse), sid if parse else None)
        previous = cache.get(key)
        if previous:
            plain(previous['snapshot'])
            require(digest(previous['snapshot'])==sha, 'Preserved snapshot changed')
            after = h.fence(path)
            require(after==before, 'Changing source file')
            return dict(copy.deepcopy(previous),path=str(path),before=before,after=after,
                        stable_during_read=True,reused_snapshot=True)
    item = h.capture(path, objects, sid, parse=parse)
    require(item['stable_during_read'], 'Changing source file')
    if parse:
        if item.get('oversized_records') and checkpoint:
            item['standard_parser_oversized_records'] = item['oversized_records']
            validate_large_checkpoint(h, item, sid, checkpoint)
        for field in ('oversized_records', 'malformed_complete_lines', 'unterminated_tail_bytes', 'foreign_session_ids'):
            require(not item.get(field), 'History needs review: ' + field + ': ' + str(path))
    result = {k: v for k, v in item.items() if not k.startswith('_')}
    if cache is not None:
        require(result['sha256']==key[0], 'File changed during preservation')
        cache[key]=result
    return result


def members(h, record, home=None):
    sid = record['uuid']
    home = Path(home or record['config_home'])
    p = home / 'projects' / Path(record['primary_history_path']).parent.name / (sid + '.jsonl')
    plain(p)
    files, errors = h.companion_listing({'path': str(p), 'home': str(home), 'role': 'home primary'}, sid)
    require(not errors, 'Companion tree contains inaccessible/redirected files')
    found = {v['relative']: Path(k) for k, v in files.items()}
    if p.exists():
        found['main'] = p
    return found


def inherited_reference_holds(row, main, available, tools):
    """Carry exact prior exceptions only; a count alone never authorizes a new gap."""
    missing = {'backup': sorted(set(main['backup_refs']) - available), 'tool_result': sorted(set(main['tool_result_refs']) - tools)}
    checkpoint = row['checkpoint']
    require(digest(checkpoint['snapshot_path']) == checkpoint['main_sha256'], 'Registered checkpoint snapshot changed')
    require(prefix(checkpoint['snapshot_path'], main['snapshot']) or native_prefix(checkpoint['snapshot_path'], main['snapshot']), 'Current source diverged from its registered checkpoint')
    if not missing['backup'] and not missing['tool_result']:
        return missing
    ref = checkpoint['manifest']
    require(digest(ref['path']) == ref['sha256'], 'Historical hold evidence changed')
    previous = json.loads(Path(ref['path']).read_text())
    old = next((s for s in previous['sessions'] if s.get('uuid', s.get('record', {}).get('uuid')) == row['uuid']), None)
    require(old is not None, 'Previous session proof absent')
    if 'selected_head' in old:
        head = old['selected_head']
        require(head['sha256'] == checkpoint['main_sha256'], 'Historical exception is for another head')
        allowed = {'backup': set(head.get('missing_backup_refs', [])), 'tool_result': set(head.get('missing_tool_result_refs', []))}
    else:
        held = old.get('inherited_reference_holds', {})
        allowed = {k: set(held.get(k, [])) for k in missing}
    require(all(set(v) <= allowed[k] for k, v in missing.items()), 'New missing referenced companion: ' + row['label'])
    return dict(missing, meaning='Previously documented gaps retained unchanged; no recovery or completeness claim')


def destination(home, bucket, sid, relative):
    rel = Path(relative)
    require(not rel.is_absolute() and '..' not in rel.parts and ':' not in relative, 'Unsafe companion path')
    if relative == 'main':
        return home / 'projects' / bucket / (sid + '.jsonl')
    require(len(rel.parts) > 1, 'Invalid companion path')
    if rel.parts[0] == 'sidecars':
        return home / 'projects' / bucket / sid / Path(*rel.parts[1:])
    require(rel.parts[0] == 'file-history', 'Unsupported companion type')
    return home / 'file-history' / sid / Path(*rel.parts[1:])


def status(group, color, ids=None):
    h, maps = dependencies()
    view = maps.load_verified(INDEX, MAP_HISTORY)
    rows = selected(view, group, ids)
    emit_progress(f'READINESS checking {len(rows)} selected conversations')
    census, live = runtime_gate(h, rows)
    auth = auth_check(color)
    pending = RUNS / 'PENDING.json'
    names = {r['uuid']: r['label'] for r in rows}
    return {'state': 'RECOVERY_REQUIRED' if pending.exists() else ('CLOSE_SESSIONS_FIRST' if live else 'READY_TO_PREPARE'),
        'group': group, 'destination': color, 'auth': auth, 'registered_sessions': len(rows),
        'source_homes': dict(collections.Counter(r['config_home'] for r in rows)),
        'live_sessions': [{'label': names[r['uuid']], 'uuid': r['uuid'], 'pid': r['pid'], 'home': r['account_home']} for r in live],
        'pending_recovery': str(pending) if pending.exists() else None,
        'runtime_diagnostics': runtime_diagnostics(census),
        'meaning': 'Readiness only. Current histories are freshly preserved and compared during Prepare; no files moved.'}


def prepare(group, color, ids=None, survivor_source=None, survivor_sources=None, expected_evidence=None):
    h, maps = dependencies()
    require(not (RUNS / 'PENDING.json').exists(), 'A previous incomplete apply needs review; see Switch-Runs/PENDING.json')
    view = maps.load_verified(INDEX, MAP_HISTORY)
    rows = selected(view, group, ids)
    choices = survivor_choices(rows,color,ids,survivor_source,survivor_sources)
    skipped = [r['uuid'] for r in rows if norm(r['config_home'])==norm(ACCOUNTS[color]['home'])]
    rows = [r for r in rows if r['uuid'] not in skipped]
    if not rows:
        return {'state':'NO_TRANSFER_NEEDED','sessions':0,'skipped_uuids':skipped}
    ids = [r['uuid'] for r in rows]
    if expected_evidence is not None:
        require(digest(INDEX)==expected_evidence['index'], 'Current-home register changed since batch scan')
        require(set(expected_evidence['sessions'])==set(ids), 'Batch evidence scope differs')
    emit_progress(f'PREPARE checking {len(rows)} selected conversations')
    choose_source = bool(choices)
    capture_cache = {}
    clear_runtime(h, rows)
    auth = auth_check(color)
    run = RUNS / (h.now().replace(':', '').replace('.', '') + '-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    objects = run / 'objects'; objects.mkdir()
    require(shutil.disk_usage(run).free > 10 * 1024**3, 'Less than 10 GiB free for preservation')
    target_home = Path(ACCOUNTS[color]['home'])
    manifest = {'schema': 'claude-self-service-switch/v1', 'prepared_at': h.now(), 'group': group, 'color': color,
        'engine_sha256': digest(__file__), 'index_sha256': digest(INDEX), 'auth': auth, 'sessions': [], 'memory': [],
        'selected_uuids': sorted(ids), 'survivor_source': survivor_source,
        'survivor_sources':dict(choices) if not survivor_source else {},
        'batch_reviewed':expected_evidence is not None, 'skipped_uuids':skipped}
    try:
        for position, row in enumerate(rows, 1):
            emit_progress(f'PREPARE capturing conversation {position}/{len(rows)}: {row["label"]}')
            sid = row['uuid']; source_files = members(h, row); target_files = members(h, row, target_home)
            require('main' in source_files, 'Registered source history missing: ' + sid)
            ops = []
            for file_number,(rel, src) in enumerate(sorted(source_files.items(), key=lambda x: (x[0] == 'main', x[0])),1):
                if file_number==1 or file_number%100==0 or file_number==len(source_files):
                    emit_progress(f'PREPARE {row["label"]}: file {file_number}/{len(source_files)}')
                source = capture(h, src, objects, sid, rel == 'main', row['checkpoint'],capture_cache)
                dst = destination(target_home, Path(row['primary_history_path']).parent.name, sid, rel)
                before = capture(h, dst, objects, sid, rel == 'main', row['checkpoint'],capture_cache) if dst.exists() else None
                ops.append({'relative': rel, 'source': source, 'before': before, 'destination': str(dst),
                            'action': classify(source, before, rel == 'main', sid in choices and rel == 'main')})
            main = next(o['source'] for o in ops if o['relative'] == 'main')
            available = {Path(k).name for k in source_files if k.startswith('file-history/')}
            tools = {Path(k).name for k in source_files if k.startswith('sidecars/tool-results/')}
            held_refs = inherited_reference_holds(row, main, available, tools)
            manifest['sessions'].append({'record': row, 'source_members': {k: str(v) for k, v in source_files.items()}, 'target_members': {k: str(v) for k, v in target_files.items()}, 'operations': ops, 'inherited_reference_holds': held_refs})
            if choose_source or expected_evidence is not None:
                manifest['sessions'][-1]['survivor_target_hashes'] = {k:digest(v) for k,v in target_files.items()}
            if expected_evidence is not None:
                expected = expected_evidence['sessions'][sid]
                require({o['relative']:o['source']['sha256'] for o in ops}==expected['source'],
                        'Source history or companions changed since batch scan: '+row['label'])
                require(manifest['sessions'][-1]['survivor_target_hashes']==expected['target'],
                        'Destination history or companions changed since batch scan: '+row['label'])
                require(all(expected['target'].get(o['relative'])==o['before']['sha256'] for o in ops if o['before']),
                        'Destination capture differs from batch scan: '+row['label'])
        emit_progress('PREPARE inspecting shared project memory')
        # A single-session survivor choice must not alter shared project memory.
        if not choose_source and expected_evidence is None:
            seen = set()
            source_homes = {r['config_home'] for r in rows}
            buckets = {Path(r['primary_history_path']).parent.name for r in rows} | {SHARED_BUCKET}
            for source_home in sorted(source_homes):
                for bucket in sorted(buckets):
                    folder = Path(source_home) / 'projects' / bucket / 'memory'
                    plain(folder)
                    if not folder.exists():
                        continue
                    for base, dirs, files in os.walk(folder, followlinks=False):
                        for name in dirs:
                            plain(Path(base) / name)
                        for name in files:
                            src = Path(base) / name; rel = src.relative_to(folder)
                            dst = target_home / 'projects' / bucket / 'memory' / rel
                            source = capture(h, src, objects, 'memory',cache=capture_cache)
                            before = capture(h, dst, objects, 'memory',cache=capture_cache) if dst.exists() else None
                            key = norm(dst)
                            previous = next((o for o in manifest['memory'] if norm(o['destination']) == key), None)
                            if previous:
                                require(previous['source']['sha256'] == source['sha256'] or before is not None, 'Several source memory variants for an absent target: ' + str(dst))
                            action = 'KEEP' if before and before['sha256'] == source['sha256'] else ('KEEP_TARGET_MEMORY_VARIANT' if before else 'CREATE')
                            manifest['memory'].append({'destination': str(dst), 'source': source, 'before': before, 'action': action, 'duplicate_target': key in seen})
                            seen.add(key)
        clear_runtime(h, rows)
        emit_progress('PREPARE verifying captured source and destination snapshots')
        maps.assert_unchanged(view)
        for section in manifest['sessions']:
            require(members(h, section['record']) == {k: Path(v) for k, v in section['source_members'].items()}, 'Source companion membership changed')
            require(members(h, section['record'], target_home) == {k: Path(v) for k, v in section['target_members'].items()}, 'Target companion membership changed')
            if choose_source or expected_evidence is not None:
                require({k:digest(v) for k,v in section['target_members'].items()} == section['survivor_target_hashes'],
                        'Destination companion content changed during preparation')
        for op in [o for s in manifest['sessions'] for o in s['operations']] + manifest['memory']:
            stable(h, op['source'])
            if op['before']:
                stable(h, op['before'])
        manifest['memory_variants'] = sum(o['action'] == 'KEEP_TARGET_MEMORY_VARIANT' for o in manifest['memory'])
        manifest['companion_variants'] = sum(o['action'] == 'REVIEW_CURRENT_COMPANION' for section in manifest['sessions'] for o in section['operations'])
        manifest['survivor_main_count'] = sum(o['action'] == 'CHOOSE_SOURCE_MAIN_SURVIVOR' for section in manifest['sessions'] for o in section['operations'])
        manifest['state'] = 'PREPARED_REVIEW_REQUIRED'
        path = run / 'manifest.json'; h.write_new(path, manifest, readonly=True)
        report = ['Claude workspace switch prepared', group + ' -> ' + color, str(len(rows)) + ' registered conversations.',
            'Destination: ' + auth['email'] + ' / ' + auth['subscriptionType'],
            'Conflicting memory paths preserved; destination versions retained: ' + str(manifest['memory_variants']),
            'Undo/tool companion variants requiring current-source selection: ' + str(manifest['companion_variants']),
            'Explicit main-history survivor replacements: ' + str(manifest['survivor_main_count'])
                + (' | source ' + survivor_source + ' -> destination ' + color if survivor_source else ''),
            'No histories changed. Close all source conversation tabs and keep them closed until completion.',
            'Source account data remains preserved. Background tasks and wakeups do not transfer.', '', 'Conversations:']
        report += [s['record']['label'] + ' | ' + s['record']['uuid'] for s in manifest['sessions']]
        report += ['', 'Main history survivor selection (both originals retained in run objects):'] + [
            o['destination'] + ' | ' + choices[section['record']['uuid']] + '/source sha256 ' + o['source']['sha256']
            + ' | displaced ' + color + ' sha256 ' + o['before']['sha256']
            for section in manifest['sessions'] for o in section['operations']
            if o['action'] == 'CHOOSE_SOURCE_MAIN_SURVIVOR']
        report += ['', 'Memory variants kept in the destination:'] + [o['destination'] for o in manifest['memory'] if o['action'] == 'KEEP_TARGET_MEMORY_VARIANT']
        report += ['', 'Companion variants: current registered source selected ONLY if explicitly accepted; displaced destination bytes retained:'] + [o['destination'] + ' | source ' + o['source']['sha256'] + ' | old target ' + o['before']['sha256'] for section in manifest['sessions'] for o in section['operations'] if o['action'] == 'REVIEW_CURRENT_COMPANION']
        if expected_evidence is not None:
            report += ['', 'Combined batch review. Shared project memory is unchanged. No automatic workspace launch.',
                       'Exact survivor choices: '+json.dumps(choices,sort_keys=True)]
        (run / 'REVIEW.txt').write_text('\n'.join(report), encoding='utf-8')
        emit_progress('PREPARE review ready; no live histories changed')
        return {'state': manifest['state'], 'manifest': str(path), 'sha256': digest(path),
                'review': str(run / 'REVIEW.txt'), 'sessions': len(rows),
                'memory_variants': manifest['memory_variants'],
                'companion_variants': manifest['companion_variants'],
                'survivor_main_count': manifest['survivor_main_count'],
                'reused_snapshots':sum(bool(item.get('reused_snapshot')) for section in manifest['sessions']
                                      for op in section['operations'] for item in (op['source'],op['before']) if item),
                'skipped_uuids':skipped}
    except Exception as exc:
        h.write_new(run / 'HELD.json', {'state': 'PREPARE_HELD_NO_LIVE_FILES_CHANGED', 'error': str(exc)})
        raise


@contextlib.contextmanager
def mutexes(ids):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]; kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.ReleaseMutex.argtypes = [ctypes.c_void_p]; kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    acquired = []
    try:
        for name in ['ENV-Session-Home-Index'] + ['ENV-History-Reconcile-' + sid for sid in sorted(ids)]:
            handle = kernel.CreateMutexW(None, False, 'Local\\' + name)
            require(handle, 'Cannot acquire switch mutex')
            result = kernel.WaitForSingleObject(handle, 0)
            if result != 0:
                if result == 0x80:
                    kernel.ReleaseMutex(handle)
                kernel.CloseHandle(handle)
                raise Hold('Busy or abandoned switch lock; review required')
            acquired.append(handle)
        yield
    finally:
        for handle in reversed(acquired):
            kernel.ReleaseMutex(handle); kernel.CloseHandle(handle)


def copy_exclusive(src, dst, sha):
    plain(dst); dst.parent.mkdir(parents=True, exist_ok=True); plain(dst)
    with Path(src).open('rb') as source, dst.open('xb') as target:
        shutil.copyfileobj(source, target, 1024 * 1024); target.flush(); os.fsync(target.fileno())
    require(digest(dst) == sha, 'Copied bytes failed verification')


def replace_with_backup(dest, temporary, backup):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ReplaceFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_void_p]
    kernel.ReplaceFileW.restype = ctypes.c_int
    require(not backup.exists(), 'Backup already exists')
    require(kernel.ReplaceFileW(str(dest), str(temporary), str(backup), 0, None, None), 'Atomic replacement failed: ' + str(ctypes.get_last_error()))


def mutate(h, op, run, number):
    stable(h, op['source']); dest = Path(op['destination']); before = op['before']
    if before:
        stable(h, before)
    else:
        require(not dest.exists(), 'Destination appeared after review')
    if op['action'] in ('KEEP', 'KEEP_TARGET_MEMORY_VARIANT'):
        return
    if op['action'] == 'CREATE':
        copy_exclusive(op['source']['snapshot'], dest, op['source']['sha256'])
    else:
        require(before is not None, 'Replacement without preserved prior bytes')
        if op['action'] == 'ADVANCE_BYTE_PREFIX':
            require(prefix(before['snapshot'], op['source']['snapshot']), 'Unproved byte prefix')
        elif op['action'] == 'ADVANCE_NATIVE_PREFIX':
            require(native_prefix(before['snapshot'], op['source']['snapshot']), 'Unproved native prefix')
        else:
            require(op['action'] in ('REVIEW_CURRENT_COMPANION', 'CHOOSE_SOURCE_MAIN_SURVIVOR'),
                    'Unreviewed replacement action')
        temporary = dest.with_name(dest.name + '.switch-' + uuid.uuid4().hex + '.tmp')
        backup = run / ('displaced-' + str(number) + '.before')
        copy_exclusive(op['source']['snapshot'], temporary, op['source']['sha256'])
        stable(h, before)
        replace_with_backup(dest, temporary, backup)
        require(digest(backup) == before['sha256'], 'Displaced bytes did not match the preserved target')
        backup.chmod(0o444)
    require(digest(dest) == op['source']['sha256'], 'Destination readback failed')


def workspace(group, color):
    """Workspace appearance is descriptive; process environment selects the account."""
    account = ACCOUNTS[color]
    if account.get('workspace_root'):
        path = plain(Path(account['workspace_root']) / (group + '-' + color + '.code-workspace'))
        doc = json.loads(path.read_text(encoding='utf-8'))
        folders = doc.get('folders', [])
        require([norm(f.get('path', '')).rstrip('\\/') for f in folders] ==
                [norm(p).rstrip('\\/') for p in group_projects(group)],
                'Account workspace points to an unexpected project')
        require(doc.get('settings', {}).get('window.title', '').startswith(color + ' |'),
                'Account workspace title does not identify its color')
        return path
    path = BASE / 'workspaces' / (group + '-' + color + '.code-workspace')
    path.parent.mkdir(exist_ok=True)
    expected = {'folders': [{'name': p.name, 'path': str(p)} for p in group_projects(group)], 'settings': {
        'window.title': color + ' | ' + group + ' | ${activeEditorShort}', 'workbench.colorTheme': ACCOUNTS[color]['theme'],
        'workbench.colorCustomizations': {'titleBar.activeBackground': ACCOUNTS[color]['color'], 'titleBar.activeForeground': '#FFFFFF', 'statusBar.background': ACCOUNTS[color]['color'], 'statusBar.foreground': '#FFFFFF'},
        'git.openRepositoryInParentFolders': 'never', 'search.followSymlinks': False, 'python.analysis.indexing': False,
        'files.watcherExclude': {'**/.claude/**': True, '**/.git/objects/**': True, '**/node_modules/**': True}},
        'extensions': {'recommendations': ['anthropic.claude-code']}}
    if path.exists():
        require(json.loads(path.read_text()) == expected, 'Generated workspace changed; review it before launch')
    else:
        with path.open('x', encoding='utf-8') as f:
            json.dump(expected, f, indent=2)
    return path


def restored_workspace_tabs(user_data, workspace_path):
    """Read Claude tab IDs in VS Code's saved workspace state; never mutate it."""
    storage = Path(user_data) / 'User' / 'workspaceStorage'
    if not storage.exists():
        return []
    found = {}

    def walk(value, title='', depth=0):
        if depth > 12:
            return
        if isinstance(value, dict):
            title = value.get('title', title)
            sid = value.get('sessionId') or value.get('sessionID')
            if isinstance(sid, str) and sid:
                found.setdefault(sid, {'sessionId': sid, 'title': title or sid})
            for child in value.values():
                walk(child, title, depth + 1)
        elif isinstance(value, list):
            for child in value:
                walk(child, title, depth + 1)
        elif isinstance(value, str) and value[:1] in ('{', '[') and len(value) < 10_000_000:
            try:
                walk(json.loads(value), title, depth + 1)
            except json.JSONDecodeError:
                pass

    for marker in storage.glob('*/workspace.json'):
        try:
            uri = json.loads(marker.read_text(encoding='utf-8')).get('workspace')
            if not isinstance(uri, str):
                continue
            parsed = urlsplit(uri)
            if parsed.scheme != 'file' or norm(unquote(parsed.path).lstrip('/')) != norm(workspace_path):
                continue
            db = marker.parent / 'state.vscdb'
            if not db.exists():
                continue
            with contextlib.closing(sqlite3.connect('file:' + str(db) + '?mode=ro', uri=True)) as connection:
                for _, value in connection.execute(
                        'SELECT key,value FROM ItemTable WHERE key IN (?,?)',
                        ('Anthropic.claude-code', 'memento/workbench.parts.editor')):
                    walk(json.loads(value))
        except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
            raise Hold('Cannot inspect saved VS Code workspace tabs: ' + str(exc)) from exc
    return list(found.values())


def launch(group, color):
    import desktop_launcher
    require(not desktop_launcher.needs_broker(),
            'Use the independent Open destination workspace launcher with exact selected conversations')
    account = ACCOUNTS[color]
    user_data = account.get('fpa_user_data', account['user_data']) if group == 'FPA' else account['user_data']
    existing=assert_single_vscode_instance(user_data)
    settings = json.loads((Path(user_data) / 'User/settings.json').read_text(encoding='utf-8-sig'))
    envs = {v['name']: v['value'] for v in settings.get('claudeCode.environmentVariables', [])}
    require(norm(envs.get('CLAUDE_CONFIG_DIR', '')) == norm(account['home']), 'VS Code profile home mismatch')
    require(not any(envs.get(k) for k in OVERRIDES), 'VS Code profile has a provider override')
    # A new console/process group does not escape a host's Windows job.
    process = subprocess.Popen([str(CODE), '--new-window', '--user-data-dir', str(Path(user_data).resolve()), str(workspace(group, color))],
        env=account_env(color), creationflags=0x00000200, close_fds=True)
    result={'pid':process.pid,'lifetime':'INDEPENDENT_LAUNCH_REQUESTED'}
    try:
        if not existing and process.poll() is None:
            desktop_launcher.record_code_child(process.pid,user_data)
    except Exception as exc:
        result.update(lifetime='VERIFICATION_UNAVAILABLE_AFTER_LAUNCH',
            warning=f'Code launch was already requested (PID {process.pid}), but lifetime certification failed: {exc}. '
                    'Do not retry blindly. Inspect the opened window and runtime diagnostics first.')
    return result


def vscode_instance_diagnostics(processes=None):
    """Detect competing main processes for the same account profile, without stopping any."""
    if processes is None:
        import base64
        script = r'''$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter "Name='Code.exe'" | ForEach-Object {
          $c=$_.CommandLine
          if($c -and $c -notmatch '(?:^|\s)--type[= ]') {
            $m=[regex]::Match($c,'--user-data-dir[ =]+(?:"([^"]+)"|([^ ]+))')
            if($m.Success) {
              $d=if($m.Groups[1].Success){$m.Groups[1].Value}else{$m.Groups[2].Value}
              [pscustomobject]@{pid=$_.ProcessId;user_data=$d;started_local=$_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss zzz')}
            }
          }
        }) | ConvertTo-Json -Compress'''
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
            base64.b64encode(script.encode('utf-16-le')).decode()], capture_output=True,
            encoding='utf-8', timeout=30, creationflags=0x08000000)
        require(result.returncode == 0, 'VS Code instance diagnostic unavailable; no launch')
        processes = json.loads(result.stdout or '[]')
        if isinstance(processes, dict):
            processes = [processes]
    groups = collections.defaultdict(list)
    for process in processes:
        groups[norm(process['user_data'])].append(process)
    conflicts = [{'user_data': home, 'processes': members} for home, members in groups.items() if len(members) > 1]
    return {'state': 'CONFLICT' if conflicts else 'NO_DUPLICATE_ACCOUNT_INSTANCES_OBSERVED',
            'conflicts': conflicts,
            'instances': processes,
            'meaning': 'Read-only snapshot of explicit account profiles. No process closed; not a webview health check.'}


def assert_single_vscode_instance(user_data):
    data = vscode_instance_diagnostics()
    conflicts = [c for c in data['conflicts'] if norm(c['user_data']) == norm(user_data)]
    require(not conflicts, 'This account has multiple VS Code main processes for one user-data directory. '
            'No additional launch requested. Use Runtime diagnostics and reconcile those instances before reopening.')
    import desktop_launcher
    for process in data.get('instances',[]):
        if norm(process['user_data'])==norm(user_data):
            desktop_launcher.assert_existing_code_safe(process['pid'],user_data)
    return [p for p in data.get('instances',[]) if norm(p['user_data'])==norm(user_data)]


def workspace_catalog():
    """Account-aware launcher inventory; never launch, authenticate, or move history."""
    _, maps = dependencies()
    view = maps.load_verified(INDEX, MAP_HISTORY)
    workspaces = []
    for color, account in ACCOUNTS.items():
        for group in GROUPS:
            projects = group_projects(group)
            project_keys = {norm(p).rstrip('\\/') for p in projects}
            sessions = sorted([{'uuid': r['uuid'], 'label': r['label']}
                for r in view['records'].values()
                if norm(r['config_home']) == norm(account['home']) and
                norm(r['project_directory']).rstrip('\\/') in project_keys],
                key=lambda r: (r['label'], r['uuid']))
            if sessions:
                workspaces.append({'account': color, 'group': group, 'email': account['email'],
                                   'plan': account['plan'], 'project': ' + '.join(p.name for p in projects), 'sessions': sessions})
    return {'state': 'CATALOG', 'workspaces': workspaces}


def open_workspace(group, color, ids=None):
    require(group in GROUPS and color in ACCOUNTS, 'Choose a known workspace and destination account')
    require(ids, 'Select registered conversations before opening a destination workspace; opening does not transfer history')
    import desktop_launcher
    if desktop_launcher.needs_broker():
        return desktop_launcher.request('workspace',group=group,color=color,ids=list(ids))
    _, maps = dependencies()
    view = maps.load_verified(INDEX, MAP_HISTORY)
    rows = selected(view, group, ids)
    require(all(norm(row['config_home']) == norm(ACCOUNTS[color]['home']) for row in rows),
            'Open destination workspace does not transfer history. Apply the reviewed switch first; selected histories are still registered elsewhere.')
    account = ACCOUNTS[color]
    user_data = account.get('fpa_user_data', account['user_data']) if group == 'FPA' else account['user_data']
    tabs = restored_workspace_tabs(user_data, workspace(group, color))
    stale = [tab for tab in tabs if tab['sessionId'] not in view['records'] or
             norm(view['records'][tab['sessionId']]['config_home']) != norm(account['home'])]
    require(not stale, color + ' saved VS Code tabs include histories registered elsewhere: ' +
            ', '.join(tab['title'] + ' (' + tab['sessionId'] + ')' for tab in stale) +
            '. Close only those stale tabs in the existing window; no histories were moved or windows launched.')
    outside = [tab for tab in tabs if tab['sessionId'] not in set(ids)]
    require(not outside, color + ' saved VS Code tabs outside the selection would also reopen: ' +
            ', '.join(tab['title'] + ' (' + tab['sessionId'] + ')' for tab in outside) +
            '. Select those registered destination sessions too, or close only their tabs in the existing window.')
    auth = auth_check(color)
    launch_details=launch(group, color)
    return {'state': 'WORKSPACE_LAUNCH_REQUESTED', 'group': group, 'destination': color,
            'auth': auth, 'workspace_launch': 'REQUESTED_NOT_GUI_VERIFIED',
            'launch_details':launch_details,
            'selected_history_uuids_verified': [row['uuid'] for row in rows],
            'saved_workspace_tabs_observed': tabs,
            'runtime_adoption': 'NOT_CLAIMED'}


def apply(path, sha, accept_memory=False, open_window=False, accept_companions=False,
          accept_survivor=False):
    emit_progress('APPLY validating reviewed manifest and current account state')
    h, maps = dependencies(); path = Path(path).resolve()
    require(path.is_relative_to(RUNS.resolve()) and path.name == 'manifest.json', 'Not a switch manifest')
    require(digest(path) == sha, 'Reviewed manifest changed')
    m = json.loads(path.read_text()); run = path.parent
    require(m['schema'] == 'claude-self-service-switch/v1' and m['state'] == 'PREPARED_REVIEW_REQUIRED', 'Wrong manifest type')
    require(m['engine_sha256'] == digest(__file__), 'Switcher changed since preparation; prepare again')
    require(not m['memory_variants'] or accept_memory, 'Review the memory variants and explicitly choose to keep destination versions')
    require(not m.get('companion_variants') or accept_companions, 'Review the differing undo/tool companions and explicitly select the current registered source set')
    color, group = m['color'], m['group']; target = Path(ACCOUNTS[color]['home'])
    rows = [s['record'] for s in m['sessions']]
    choices = validate_survivor_manifest(m, rows, accept_survivor)
    pending = RUNS / 'PENDING.json'
    with mutexes([r['uuid'] for r in rows]):
        require(not pending.exists(), 'An incomplete apply needs review; see ' + str(pending))
        view = maps.load_verified(INDEX, MAP_HISTORY)
        require(digest(INDEX) == m['index_sha256'], 'Current-home register changed; prepare again')
        require(selected(view, group, m.get('selected_uuids')) == rows, 'Registered conversation scope changed')
        auth = auth_check(color); clear_runtime(h, rows)
        require(auth['email'].lower() == m['auth']['email'].lower() and auth['orgId'] == m['auth']['orgId'], 'Account changed after review')
        for s in m['sessions']:
            require(members(h, s['record']) == {k: Path(v) for k, v in s['source_members'].items()}, 'Source companions changed')
            require(members(h, s['record'], target) == {k: Path(v) for k, v in s['target_members'].items()}, 'Target companions changed')
            if choices or m.get('batch_reviewed'):
                require({k:digest(v) for k,v in s['target_members'].items()} == s.get('survivor_target_hashes'),
                        'Destination companion content changed or survivor review is outdated; prepare again')
            for op in s['operations']:
                want = destination(target, Path(s['record']['primary_history_path']).parent.name, s['record']['uuid'], op['relative'])
                require(norm(want) == norm(op['destination']), 'Manifest target escaped its conversation')
                require(classify(op['source'], op['before'], op['relative'] == 'main',
                                 s['record']['uuid'] in choices and op['relative'] == 'main') == op['action'],
                        'Transfer action changed')
        operations = [o for s in m['sessions'] for o in s['operations']] + [o for o in m['memory'] if not o['duplicate_target']]
        emit_progress(f'APPLY verifying {len(operations)} captured operations')
        for op in operations:
            stable(h, op['source'])
            if op['before']:
                stable(h, op['before'])
            else:
                require(not Path(op['destination']).exists(), 'New destination appeared')
        maps.assert_unchanged(view)
        backup = run / 'routing-before'; backup.mkdir(exist_ok=False)
        shutil.copyfile(INDEX, backup / INDEX.name)
        for row in rows:
            shutil.copyfile(view['index']['entries'][row['uuid']]['path'], backup / (row['uuid'] + '.json'))
        h.write_new(pending, {'run': str(run), 'manifest_sha256': sha, 'state': 'APPLYING_DO_NOT_OPEN_EITHER_COPY'})
        try:
            with (run / 'journal.jsonl').open('x', encoding='utf-8') as journal:
                def log(value):
                    journal.write(json.dumps(value) + '\n'); journal.flush(); os.fsync(journal.fileno())
                writes=[(i,op) for i,op in enumerate(operations)
                        if op['action'] not in ('KEEP','KEEP_TARGET_MEMORY_VARIANT')]
                for position,(i,op) in enumerate(writes,1):
                    if position == 1 or position % 25 == 0 or position == len(writes):
                        emit_progress(f'APPLY writing operation {position}/{len(writes)}')
                    if op.get('relative') == 'main':
                        clear_runtime(h, rows)
                    log({'i': i, 'state': 'BEFORE', 'path': op['destination'], 'action': op['action']})
                    mutate(h, op, run, i)
                    log({'i': i, 'state': 'VERIFIED'})
            clear_runtime(h, rows)
            # Unchanged files still have freshness gates; they need no mutation journal.
            for op in operations:
                if op['action'] in ('KEEP','KEEP_TARGET_MEMORY_VARIANT'):
                    stable(h,op['source'])
                    if op['before']:stable(h,op['before'])
            emit_progress('APPLY verifying final histories and publishing current-home register')
            maps.assert_unchanged(view)
            index = copy.deepcopy(view['index'])
            wp = workspace(group, color)
            for s in m['sessions']:
                old = s['record']; sid = old['uuid']; doc = copy.deepcopy(old)
                main = next(o for o in s['operations'] if o['relative'] == 'main')
                require(digest(main['destination']) == main['source']['sha256'], 'Final history readback failed')
                older = {norm(x['path']): x for x in old.get('older_sources', []) if norm(x['path']) != norm(main['destination'])}
                if norm(old['config_home']) != norm(target):
                    older[norm(old['primary_history_path'])] = {'path': old['primary_history_path'], 'role': 'preserved_previous_account_copy', 'normal_resume_authorized': False, 'removed_from_native_picker': False}
                doc.update(updated_utc=h.now(), config_home=str(target), primary_history_path=main['destination'],
                    workspace_path=str(wp), runtime_observation=None, status='SAVED_HOME_TRANSFER_VERIFIED_NOT_REOPENED', older_sources=list(older.values()),
                    companions={'session_directory': str(Path(main['destination']).with_suffix('')), 'file_history_directory': str(target / 'file-history' / sid)},
                    account_identity={'email': auth['email'], 'org_id': auth['orgId'], 'subscription_type': auth['subscriptionType'], 'evidence': {'path': str(path), 'sha256': sha}})
                doc['previous_checkpoint'] = old['checkpoint']
                doc['checkpoint'] = {'at': h.now(), 'main_sha256': main['source']['sha256'], 'snapshot_path': main['source']['snapshot'], 'manifest': {'path': str(path), 'sha256': sha}, 'meaning': 'Preserved self-service switch checkpoint; runtime adoption remains unverified.'}
                dest = Path(index['entries'][sid]['path']); h.publish(dest, doc)
                index['entries'][sid] = {'path': str(dest), 'sha256': digest(dest)}
            require(digest(INDEX) == m['index_sha256'], 'Concurrent routing update; review required')
            index['updated_utc'] = h.now(); h.publish(INDEX, index)
            map_result = maps.refresh(INDEX, MAP, MAP_HISTORY)
            receipt = {'state': 'SWITCH_COMPLETE_SAVED_HISTORIES_READY', 'at': h.now(), 'group': group, 'destination': color,
                'sessions': len(rows), 'map': map_result, 'manifest_sha256': sha, 'model_prompts': 0,
                'old_copies_preserved': True, 'runtime_adoption': 'NOT_CLAIMED', 'background_tasks': 'NOT_REARMED'}
            h.write_new(run / 'receipt.json', receipt, readonly=True)
            pending.unlink()  # Single owned transaction marker, after verified completion only.
            emit_progress('APPLY receipt verified and pending marker cleared')
        except Exception as exc:
            h.write_new(run / ('HELD-APPLY-' + uuid.uuid4().hex[:8] + '.json'), {'state': 'PARTIAL_OR_NO_APPLY_REQUIRES_REVIEW', 'error': str(exc)})
            raise
    if open_window:
        try:
            receipt['workspace_launch_details']=open_workspace(group, color, [row['uuid'] for row in rows])
            receipt['workspace_launch'] = 'REQUESTED_NOT_GUI_VERIFIED'
        except Exception as exc:
            receipt['workspace_launch'] = 'HELD_OR_OUTCOME_UNCERTAIN: ' + str(exc)
            receipt['workspace_launch_guidance'] = 'No automatic retry. Inspect the diagnostic and any request receipt before reopening.'
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['status', 'prepare', 'apply', 'open', 'catalog', 'diagnose'])
    parser.add_argument('--group', choices=GROUPS)
    parser.add_argument('--destination', choices=ACCOUNTS)
    parser.add_argument('--manifest'); parser.add_argument('--sha256')
    parser.add_argument('--uuid', action='append', help='Optional exact registered conversation; repeat to select a cohort')
    parser.add_argument('--accept-memory-variants', action='store_true'); parser.add_argument('--open', action='store_true')
    parser.add_argument('--accept-companion-variants', action='store_true')
    parser.add_argument('--survivor-source', choices=ACCOUNTS,
                        help='Prepare one exact UUID with this registered account as the source survivor')
    parser.add_argument('--accept-source-main-survivor', action='store_true')
    args = parser.parse_args()
    try:
        if args.mode == 'catalog':
            result = workspace_catalog()
        elif args.mode == 'diagnose':
            result = {'state': 'DIAGNOSTIC_COMPLETE', 'process_identity': runtime_diagnostics(),
                      'vscode_instances': vscode_instance_diagnostics()}
        elif args.mode == 'apply':
            require(args.manifest and args.sha256, 'Manifest and review hash required')
            result = apply(args.manifest, args.sha256, args.accept_memory_variants, args.open,
                           args.accept_companion_variants, args.accept_source_main_survivor)
        else:
            require(args.group and args.destination, 'Choose a workspace group and destination')
            if args.mode == 'open':
                result = open_workspace(args.group, args.destination, args.uuid)
            else:
                result = (status(args.group, args.destination, args.uuid) if args.mode == 'status'
                          else prepare(args.group, args.destination, args.uuid, args.survivor_source))
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({'state': 'HELD', 'error': str(exc)}, indent=2)); return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
