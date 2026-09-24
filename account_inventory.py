"""Read-only inventory and reviewed source-to-target merge for isolated Claude homes."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

BASE = Path(__file__).resolve().parent
RUNS = BASE / 'CrossAccount-Runs'
SCHEMA = 'claude-cross-account-merge/v1'
CATEGORIES = {'settings', 'permissions', 'skills', 'mcps'}
PERMISSION_LISTS = ('allow', 'deny', 'ask', 'additionalDirectories')
SENSITIVE_FILE = re.compile(r'(^\.env($|\.)|credentials|secret|token|private.?key|\.(pem|p12|pfx|key)$)', re.I)
SENSITIVE_ARG = re.compile(r'(bearer|token|secret|api[_-]?key|password|://|\$\{|%[A-Z_]+%)', re.I)


class Hold(RuntimeError):
    pass


def require(ok, message):
    if not ok:
        raise Hold(message)


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Hold(f'Cannot read JSON: {path}: {exc}') from exc
    require(isinstance(value, dict), f'Expected JSON object: {path}')
    return value


def inside(path, root):
    try:
        return os.path.commonpath((str(Path(path).resolve()), str(Path(root).resolve()))) == str(Path(root).resolve())
    except ValueError:
        return False


def homes(accounts, source, target):
    require(source in accounts and target in accounts and source != target, 'Choose distinct known account colors')
    left, right = (Path(accounts[c]['home']).resolve() for c in (source, target))
    require(left.is_dir() and right.is_dir(), 'Account home is unavailable')
    return left, right


def skill_files(home):
    root = home / 'skills'
    result = {}
    if not root.is_dir():
        return result
    for path in root.rglob('*'):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in {'.git', '.trash', '__pycache__'} for part in relative.parts) or path.suffix == '.pyc':
            continue
        require(not path.is_symlink() and inside(path, root), f'Skill path escaped its account: {path}')
        result[relative.as_posix()] = (path, digest(path))
    return result


def mcp_servers(home):
    path = home / '.claude.json'
    if not path.is_file():
        return {}
    result = read_json(path).get('mcpServers') or {}
    require(isinstance(result, dict), f'Invalid MCP map: {path}')
    return result


def safe_mcp(entry):
    if not isinstance(entry, dict) or set(entry) - {'type', 'command', 'args'}:
        return False
    args = entry.get('args', [])
    if entry.get('type') != 'stdio' or not isinstance(entry.get('command'), str):
        return False
    if not isinstance(args, list) or not all(isinstance(v, str) for v in args):
        return False
    return not any(SENSITIVE_ARG.search(v) for v in [entry['command'], *args])


def compare(accounts, source, target, shared):
    left, right = homes(accounts, source, target)
    ls, rs = read_json(left / 'settings.json'), read_json(right / 'settings.json')
    lp, rp = ls.get('permissions') or {}, rs.get('permissions') or {}
    require(isinstance(lp, dict) and isinstance(rp, dict), 'Invalid permission map')
    lf, rf = skill_files(left), skill_files(right)
    lm, rm = mcp_servers(left), mcp_servers(right)
    lists = {}
    for key in PERMISSION_LISTS:
        a, b = lp.get(key) or [], rp.get(key) or []
        require(isinstance(a, list) and isinstance(b, list), f'Invalid permission list: {key}')
        lists[key] = {'source_only': [v for v in a if v not in b],
                      'target_only': [v for v in b if v not in a]}
    return {
        'state': 'ACCOUNT_COMPARISON', 'source': source, 'target': target,
        'settings': {'different': sorted(k for k in set(ls) | set(rs) if ls.get(k) != rs.get(k)),
                     'missing_in_target': sorted(set(ls) - set(rs)), 'shared_keys': sorted(shared),
                     'unclassified_or_protected': sorted((set(ls) | set(rs)) - set(shared) - {'permissions'})},
        'permissions': {'lists': lists, 'default_mode_differs': lp.get('defaultMode') != rp.get('defaultMode')},
        'skills': {'source_files': len(lf), 'target_files': len(rf),
                   'missing_in_target': sorted(set(lf) - set(rf)),
                   'different': sorted(k for k in lf.keys() & rf.keys() if lf[k][1] != rf[k][1]),
                   'target_only': sorted(set(rf) - set(lf))},
        'mcps': {'source_names': sorted(lm), 'target_names': sorted(rm),
                 'missing_in_target': sorted(set(lm) - set(rm)),
                 'different': sorted(k for k in lm.keys() & rm.keys() if lm[k] != rm[k]),
                 'unsafe_to_copy': sorted(k for k, v in lm.items() if not safe_mcp(v)),
                 'target_only': sorted(set(rm) - set(lm))},
        'meaning': 'No credential values are emitted. Source and target account scope is explicit.',
    }


def inventory(accounts, shared):
    rows = []
    for color, account in accounts.items():
        home = Path(account['home']).resolve()
        require(home.is_dir(), f'Account home unavailable: {color}')
        settings = read_json(home / 'settings.json')
        permissions = settings.get('permissions') or {}
        require(isinstance(permissions, dict), f'Invalid permission map: {color}')
        skills, mcps = skill_files(home), mcp_servers(home)
        rows.append({'color': color, 'email': account.get('email'), 'plan': account.get('plan'),
                     'settings_keys': sorted(settings), 'shared_settings_present': sorted(set(settings) & set(shared)),
                     'permission_counts': {k: len(permissions.get(k) or []) for k in PERMISSION_LISTS},
                     'permission_default_mode': permissions.get('defaultMode'),
                     'skills': {'directories': sorted({p.split('/')[0] for p in skills}), 'files': len(skills)},
                     'mcps': {'names': sorted(mcps),
                              'credentialed_or_unsupported': sorted(k for k, v in mcps.items() if not safe_mcp(v))}})
    return {'state': 'ACCOUNT_INVENTORY', 'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'accounts': rows, 'meaning': 'Names and counts only; no credential values are emitted.'}


def merge_settings(source, target, categories, shared):
    after = copy.deepcopy(target)
    keys, additions, conflicts = [], {}, []
    if 'settings' in categories:
        for key in sorted(shared):
            if key in source and source[key] != after.get(key):
                after[key] = copy.deepcopy(source[key])
                keys.append(key)
    if 'permissions' in categories:
        left, right = source.get('permissions') or {}, copy.deepcopy(after.get('permissions') or {})
        require(isinstance(left, dict) and isinstance(right, dict), 'Invalid permission map')
        for field in PERMISSION_LISTS:
            incoming, current = left.get(field) or [], right.get(field) or []
            require(isinstance(incoming, list) and isinstance(current, list), f'Invalid permission list: {field}')
            added = []
            for value in incoming:
                require(isinstance(value, str), f'Non-string permission: {field}')
                if value in current:
                    continue
                if field in ('allow', 'deny', 'ask') and any(value in (right.get(other) or [])
                                                           for other in ('allow', 'deny', 'ask') if other != field):
                    conflicts.append(value)
                    continue
                current.append(value)
                added.append(value)
            if added:
                right[field] = current
                additions[field] = added
        if right != (after.get('permissions') or {}):
            after['permissions'] = right
    return after, keys, additions, sorted(set(conflicts))


def merge_mcps(source, target):
    after = copy.deepcopy(target)
    left, right = source.get('mcpServers') or {}, copy.deepcopy(after.get('mcpServers') or {})
    require(isinstance(left, dict) and isinstance(right, dict), 'Invalid MCP map')
    names, held = [], []
    for name, entry in sorted(left.items()):
        if right.get(name) == entry:
            continue
        if not safe_mcp(entry):
            held.append(name)
            continue
        right[name] = copy.deepcopy(entry)
        names.append(name)
    if names:
        after['mcpServers'] = right
    return after, names, held


def write_atomic(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.cross-account-{uuid.uuid4().hex}.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def prepare(accounts, source, target, categories, shared, runs_root=RUNS):
    left, right = homes(accounts, source, target)
    categories = set(categories)
    require(categories and categories <= CATEGORIES, 'Select supported merge categories')
    runs_root = Path(runs_root).resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    require(not (runs_root / 'PENDING.json').exists(), 'Cross-account recovery is required before preparing again')
    run = runs_root / (dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H%M%S%f+0000') + '-' + uuid.uuid4().hex[:8])
    run.mkdir()
    ops = []
    conflicts = {'permissions': [], 'mcps': [], 'skills': []}
    if categories & {'settings', 'permissions'}:
        sp, tp = left / 'settings.json', right / 'settings.json'
        before = read_json(tp)
        after, keys, additions, conflicts['permissions'] = merge_settings(read_json(sp), before, categories, shared)
        if after != before:
            ops.append({'kind': 'settings', 'source_path': str(sp), 'destination': str(tp),
                        'source_sha256': digest(sp), 'before_sha256': digest(tp),
                        'after_sha256': sha(json_bytes(after)), 'changed_keys': keys,
                        'permission_additions': additions})
    if 'mcps' in categories:
        sp, tp = left / '.claude.json', right / '.claude.json'
        require(sp.is_file() and tp.is_file(), 'Account MCP config unavailable')
        before = read_json(tp)
        after, names, conflicts['mcps'] = merge_mcps(read_json(sp), before)
        if after != before:
            ops.append({'kind': 'mcps', 'source_path': str(sp), 'destination': str(tp),
                        'source_sha256': digest(sp), 'before_sha256': digest(tp),
                        'after_sha256': sha(json_bytes(after)), 'mcp_names': names})
    if 'skills' in categories:
        for relative, (sp, source_sha) in sorted(skill_files(left).items()):
            if SENSITIVE_FILE.search(Path(relative).name):
                conflicts['skills'].append(relative)
                continue
            tp = right / 'skills' / Path(relative)
            before_sha = digest(tp) if tp.is_file() else None
            if before_sha != source_sha:
                ops.append({'kind': 'skill', 'relative': relative, 'source_path': str(sp),
                            'destination': str(tp), 'source_sha256': source_sha,
                            'before_sha256': before_sha, 'after_sha256': source_sha})
    manifest = {'schema': SCHEMA, 'state': 'PREPARED_REVIEW_REQUIRED',
                'source': source, 'target': target, 'categories': sorted(categories),
                'shared_keys_sha256': sha(json_bytes(sorted(shared))),
                'operations': ops, 'conflicts': conflicts,
                'created_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    manifest_path = run / 'manifest.json'
    manifest_path.write_bytes(json_bytes(manifest))
    lines = [f'Claude cross-account merge: {source} -> {target}',
             f'Categories: {", ".join(sorted(categories))}', f'Changed files: {len(ops)}',
             'No account files have been changed.',
             'Shared settings use the reviewed allowlist. Permissions add only nonconflicting entries.',
             'Credentialed MCP definitions are held. Models, hooks, auth, default permission mode, and target-only objects stay in place.',
             'Apply rechecks hashes, writes backups, and leaves PENDING.json if interrupted.', '']
    for op in ops:
        names = op.get('changed_keys') or op.get('mcp_names') or [op.get('relative', '')]
        lines.append(f"{op['kind']} | {', '.join(names)} | {op['destination']} | {op['before_sha256'] or 'MISSING'} -> {op['after_sha256']}")
        for field, added in op.get('permission_additions', {}).items():
            lines.append(f'  permissions.{field} additions: {json.dumps(added, ensure_ascii=False)}')
    for category, values in conflicts.items():
        if values:
            lines.append(f'{category} held for manual review: {", ".join(values)}')
    review = run / 'REVIEW.txt'
    review.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'state': 'PREPARED_REVIEW_REQUIRED', 'source': source, 'target': target,
            'categories': sorted(categories), 'changed_files': len(ops), 'conflicts': conflicts,
            'manifest': str(manifest_path), 'sha256': digest(manifest_path), 'review': str(review)}


def apply(accounts, manifest_path, expected_sha, shared, runs_root=RUNS):
    runs_root = Path(runs_root).resolve()
    manifest_path = Path(manifest_path).resolve()
    require(manifest_path.name == 'manifest.json' and inside(manifest_path, runs_root)
            and manifest_path.parent != runs_root, 'Manifest outside cross-account runs')
    require(re.fullmatch(r'[a-f0-9]{64}', expected_sha or '') is not None, 'Invalid review hash')
    require(digest(manifest_path) == expected_sha, 'Manifest changed after review')
    manifest = read_json(manifest_path)
    require(manifest.get('schema') == SCHEMA and manifest.get('state') == 'PREPARED_REVIEW_REQUIRED',
            'Unsupported merge manifest')
    source, target = manifest.get('source'), manifest.get('target')
    left, right = homes(accounts, source, target)
    categories = set(manifest.get('categories') or [])
    require(categories and categories <= CATEGORIES, 'Invalid merge categories')
    require(manifest.get('shared_keys_sha256') == sha(json_bytes(sorted(shared))), 'Shared-settings policy changed')
    operations = manifest.get('operations')
    require(isinstance(operations, list), 'Invalid operation list')
    pending = runs_root / 'PENDING.json'
    require(not pending.exists(), 'A prior cross-account run requires recovery')
    write_atomic(pending, json_bytes({'manifest': str(manifest_path), 'sha256': expected_sha,
                                      'state': 'APPLY_STARTED', 'at': dt.datetime.now(dt.timezone.utc).isoformat()}))
    staged, seen = [], set()
    for op in operations:
        kind = op.get('kind')
        src, dst = Path(op.get('source_path', '')).resolve(), Path(op.get('destination', '')).resolve()
        if kind == 'settings':
            require(src == left / 'settings.json' and dst == right / 'settings.json'
                    and categories & {'settings', 'permissions'}, 'Unapproved settings path')
        elif kind == 'mcps':
            require(src == left / '.claude.json' and dst == right / '.claude.json'
                    and 'mcps' in categories, 'Unapproved MCP path')
        elif kind == 'skill':
            relative = op.get('relative', '')
            require('skills' in categories and relative and not Path(relative).is_absolute()
                    and '..' not in Path(relative).parts and not SENSITIVE_FILE.search(Path(relative).name),
                    'Unapproved skill path')
            require(src == (left / 'skills' / relative).resolve()
                    and dst == (right / 'skills' / relative).resolve()
                    and inside(src, left / 'skills') and inside(dst, right / 'skills'), 'Skill escaped account')
        else:
            raise Hold('Unknown merge operation')
        require(str(dst) not in seen, 'Duplicate destination operation')
        seen.add(str(dst))
        require(src.is_file() and not src.is_symlink() and not dst.is_symlink(), 'Source missing or symlink found')
        require(digest(src) == op.get('source_sha256'), 'Source changed after review')
        before = digest(dst) if dst.is_file() else None
        require(before == op.get('before_sha256'), 'Target changed after review')
        if kind == 'settings':
            value, keys, additions, _ = merge_settings(read_json(src), read_json(dst), categories, shared)
            require(keys == op.get('changed_keys') and additions == op.get('permission_additions'),
                    'Settings merge changed after review')
            raw = json_bytes(value)
        elif kind == 'mcps':
            value, names, _ = merge_mcps(read_json(src), read_json(dst))
            require(names == op.get('mcp_names'), 'MCP merge changed after review')
            raw = json_bytes(value)
        else:
            raw = src.read_bytes()
        require(sha(raw) == op.get('after_sha256'), 'Prepared result changed after review')
        staged.append((dst, raw, before))
    backups = manifest_path.parent / 'backups'
    backups.mkdir(exist_ok=True)
    receipt = {'state': 'MERGE_COMPLETE', 'source': source, 'target': target,
               'changed_files': len(staged), 'files': [],
               'runtime_adoption': 'Unverified; running Claude sessions may retain cached configuration.',
               'completed_at': dt.datetime.now(dt.timezone.utc).isoformat()}
    for index, (dst, raw, before) in enumerate(staged):
        backup = None
        if before is not None:
            backup = backups / f'{index:04d}-{dst.name}.bak'
            write_atomic(backup, dst.read_bytes())
            require(digest(backup) == before, 'Backup verification failed')
        write_atomic(dst, raw)
        require(digest(dst) == sha(raw), 'Target verification failed')
        receipt['files'].append({'path': str(dst), 'before_sha256': before,
                                 'after_sha256': sha(raw), 'backup': str(backup) if backup else None})
        write_atomic(manifest_path.parent / 'APPLY_PROGRESS.json', json_bytes(receipt))
    receipt_path = manifest_path.parent / 'RESULT.json'
    write_atomic(receipt_path, json_bytes(receipt))
    pending.unlink()
    return {'state': 'MERGE_COMPLETE', 'changed_files': len(staged), 'receipt': str(receipt_path),
            'runtime_adoption': receipt['runtime_adoption']}


def main():
    import regularize
    import switcher
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('inventory')
    compare_cmd = sub.add_parser('compare')
    prepare_cmd = sub.add_parser('prepare')
    for cmd in (compare_cmd, prepare_cmd):
        cmd.add_argument('--source', required=True, choices=sorted(switcher.ACCOUNTS))
        cmd.add_argument('--target', required=True, choices=sorted(switcher.ACCOUNTS))
    prepare_cmd.add_argument('--category', action='append', required=True, choices=sorted(CATEGORIES))
    apply_cmd = sub.add_parser('apply')
    apply_cmd.add_argument('--manifest', required=True)
    apply_cmd.add_argument('--sha256', required=True)
    args = parser.parse_args()
    try:
        policy, _ = regularize.load_policy()
        for color, account in switcher.ACCOUNTS.items():
            require(color in policy['accounts'] and
                    Path(policy['accounts'][color]['home']).resolve() == Path(account['home']).resolve(),
                    f'Account policy/home mismatch: {color}')
        shared = set(policy['shared_settings'])
        if args.command == 'inventory':
            result = inventory(switcher.ACCOUNTS, shared)
        elif args.command == 'compare':
            result = compare(switcher.ACCOUNTS, args.source, args.target, shared)
        elif args.command == 'prepare':
            result = prepare(switcher.ACCOUNTS, args.source, args.target, set(args.category), shared)
        else:
            result = apply(switcher.ACCOUNTS, args.manifest, args.sha256, shared)
    except (Hold, OSError, ValueError, KeyError) as exc:
        result = {'state': 'HELD', 'error': str(exc)}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
