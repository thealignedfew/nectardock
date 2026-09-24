"""Reviewed metadata-only adoption of an existing, equal or extended account history.

This is not a transfer or merge. No live transcript, companion, workspace, memory,
process or native picker is modified. Uncertain or conflicting evidence holds.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import uuid

import switcher as s

RUNS = s.BASE / 'Reconciliation-Runs'
SCHEMA = 'claude-home-reconciliation/v1'


def no_pending():
    for path in (s.RUNS/'PENDING.json', s.BASE/'Registration-Runs'/'PENDING.json'):
        s.plain(path)
        s.require(not path.exists(), 'Pending apply requires recovery review: ' + str(path))


def scope(view, group, color, ids):
    s.require(group in s.GROUPS and color in s.ACCOUNTS, 'Unknown group or account')
    s.require(ids and len(ids) == len(set(ids)), 'Explicit unique UUID selection required')
    rows = s.selected(view, group, ids)
    target = Path(s.ACCOUNTS[color]['home'])
    for row in rows:
        expected = Path(row['config_home'])/'projects'/Path(row['primary_history_path']).parent.name/(row['uuid']+'.jsonl')
        s.require(s.norm(expected) == s.norm(row['primary_history_path']), 'Registered path is outside its account')
        s.require(s.norm(row['config_home']) != s.norm(target), 'Already registered in target account')
    return rows, target


def inventory(h, paths):
    result = {}
    for rel, path in sorted(paths.items()):
        s.plain(path)
        before = h.fence(path)
        sha = s.digest(path)
        s.require(before == h.fence(path), 'History or companion changed during inventory')
        result[rel] = {'path': str(path), 'sha256': sha, 'fence': before}
    return result


def strict_members(row, home=None):
    """An inaccessible directory is unknown, never an empty companion set.

    The historical helper uses os.walk without onerror. Do not use that listing
    as completeness evidence for registration repair.
    """
    home = Path(home or row['config_home'])
    sid = row['uuid']
    main = home/'projects'/Path(row['primary_history_path']).parent.name/(sid+'.jsonl')
    found = {}
    def unreadable(exc):
        raise s.Hold('Companion tree inaccessible: ' + str(exc)) from exc
    try:
        s.plain(main)
        main.stat()  # Missing main is a hold, not an empty candidate.
        found['main'] = main
        for label, root in (('sidecars', main.with_suffix('')), ('file-history', home/'file-history'/sid)):
            try:
                root.lstat()
            except FileNotFoundError:
                continue
            s.plain(root)
            s.require(root.is_dir(), 'Companion root is not a directory')
            for base, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
                for name in dirs + files:
                    s.plain(Path(base)/name)
                for name in files:
                    path = Path(base)/name
                    found[label+'/'+path.relative_to(root).as_posix()] = path
    except OSError as exc:
        unreadable(exc)
    return found


def check_files(h, sections, target):
    for section in sections:
        row = section['record']
        for key, home in (('source', None), ('target', target)):
            current = strict_members(row, home)
            expected = section[key]
            s.require(current == {k: Path(v['path']) for k, v in expected.items()},
                      'History or companion membership changed')
            s.require(inventory(h, current) == expected, 'History or companion changed since review')
        for head in (section['source_head'], section['target_head']):
            s.plain(head['snapshot'])
            s.require(s.digest(head['snapshot']) == head['sha256'], 'Preserved checkpoint changed')


def prepare(group, color, ids):
    h, maps = s.dependencies()
    with s.mutexes(ids or []):
        no_pending()
        view = maps.load_verified(s.INDEX, s.MAP_HISTORY)
        rows, target = scope(view, group, color, ids)
        s.clear_runtime(h, rows)
        auth = s.auth_check(color)
        run = RUNS/(h.now().replace(':', '').replace('.', '')+'-'+uuid.uuid4().hex[:8])
        s.plain(run)
        run.mkdir(parents=True, exist_ok=False)
        objects = run/'objects'
        objects.mkdir()
        s.require(shutil.disk_usage(run).free > 10*1024**3, 'Less than 10 GiB free for preservation')
        m = {'schema': SCHEMA, 'state': 'PREPARED_REVIEW_REQUIRED', 'prepared_at': h.now(),
             'group': group, 'color': color, 'selected_uuids': sorted(ids), 'auth': auth,
             'engine_sha256': s.digest(__file__), 'switcher_sha256': s.digest(s.__file__),
             'index_sha256': view['index_sha256'], 'sessions': [],
             'meaning': 'Adopt existing saved history by metadata only; no runtime or completeness claim.'}
        try:
            for n, row in enumerate(rows, 1):
                s.emit_progress(f'RECONCILE inspect {n}/{len(rows)}: {row["label"]}')
                source_files, target_files = strict_members(row), strict_members(row, target)
                s.require('main' in source_files and 'main' in target_files, 'Source or target main history missing')
                source, dest = inventory(h, source_files), inventory(h, target_files)
                for rel, item in source.items():
                    if rel != 'main':
                        s.require(rel in dest and dest[rel]['sha256'] == item['sha256'],
                                  'Missing or conflicting target companion: ' + rel)
                s.require(source['main']['fence']['size'] > 0 and dest['main']['fence']['size'] > 0,
                          'Source or target main history is empty')
                old = s.capture(h, source_files['main'], objects, row['uuid'], True, row['checkpoint'])
                head = s.capture(h, target_files['main'], objects, row['uuid'], True, row['checkpoint'])
                s.require(s.prefix(old['snapshot'], head['snapshot']), 'Target must equal or byte-extend source; diverged history requires separate review')
                available = {Path(k).name for k in target_files if k.startswith('file-history/')}
                tools = {Path(k).name for k in target_files if k.startswith('sidecars/tool-results/')}
                holds = s.inherited_reference_holds(row, head, available, tools)
                m['sessions'].append({'record': row, 'source': source, 'target': dest,
                    'source_head': old, 'target_head': head, 'inherited_reference_holds': holds,
                    'relation': 'IDENTICAL' if old['sha256'] == head['sha256'] else 'TARGET_EXTENDS_SOURCE'})
            check_files(h, m['sessions'], target)
            s.clear_runtime(h, rows)
            maps.assert_unchanged(view)
            no_pending()
            path = run/'manifest.json'
            h.write_new(path, m, readonly=True)
            return {'state': m['state'], 'manifest': str(path), 'sha256': s.digest(path),
                    'sessions': len(rows), 'history_modified': False}
        except Exception as exc:
            h.write_new(run/'HELD.json', {'state': 'PREPARE_HELD_NO_LIVE_FILES_CHANGED', 'error': str(exc)})
            raise


def apply(path, sha):
    h, maps = s.dependencies()
    path = s.plain(path).resolve()
    s.require(path.is_relative_to(RUNS.resolve()) and path.name == 'manifest.json', 'Not a reconciliation manifest')
    s.require(s.digest(path) == sha, 'Reviewed manifest changed')
    m = json.loads(path.read_text(encoding='utf-8'))
    s.require(m['schema'] == SCHEMA and m['state'] == 'PREPARED_REVIEW_REQUIRED', 'Wrong manifest type')
    s.require(m['engine_sha256'] == s.digest(__file__) and m['switcher_sha256'] == s.digest(s.__file__),
              'Engine changed; prepare again')
    with s.mutexes(m['selected_uuids']):
        no_pending()
        view = maps.load_verified(s.INDEX, s.MAP_HISTORY)
        s.require(view['index_sha256'] == m['index_sha256'], 'Current-home register changed; prepare again')
        rows, target = scope(view, m['group'], m['color'], m['selected_uuids'])
        s.require(rows == [x['record'] for x in m['sessions']], 'Registered selection changed')
        auth = s.auth_check(m['color'])
        s.require(all(auth[k] == m['auth'][k] for k in ('email', 'orgId', 'subscriptionType')),
                  'Account identity changed since review')
        s.clear_runtime(h, rows)
        check_files(h, m['sessions'], target)
        maps.assert_unchanged(view)
        run = path.parent
        backup = run/'routing-before'
        backup.mkdir(exist_ok=False)
        # Byte-exact backups precede the first registry mutation. Leave evidence
        # and the global marker intact after any partial publication failure.
        for src, dst in [(s.INDEX, backup/s.INDEX.name)] + [
                (Path(view['index']['entries'][r['uuid']]['path']), backup/(r['uuid']+'.json')) for r in rows]:
            s.plain(src)
            shutil.copyfile(src, dst)
            s.require(s.digest(src) == s.digest(dst), 'Registry backup readback failed')
        check_files(h, m['sessions'], target)
        s.clear_runtime(h, rows)
        maps.assert_unchanged(view)
        no_pending()
        marker = {'state': 'RECONCILING_METADATA_REVIEW_REQUIRED_IF_INTERRUPTED',
                  'manifest': str(path), 'sha256': sha, 'seat': 'nectardock-maintainer'}
        pending = s.RUNS/'PENDING.json'
        h.write_new(pending, marker)
        try:
            index = copy.deepcopy(view['index'])
            expected_docs = {}
            workspace = Path(s.ACCOUNTS[m['color']].get('workspace_root', s.BASE/'workspaces'))/(m['group']+'-'+m['color']+'.code-workspace')
            for section in m['sessions']:
                old, head = section['record'], section['target_head']
                sid = old['uuid']
                doc = copy.deepcopy(old)
                older = {s.norm(x['path']): x for x in old.get('older_sources', [])
                         if s.norm(x['path']) != s.norm(head['path'])}
                older[s.norm(old['primary_history_path'])] = {'path': old['primary_history_path'],
                    'role': 'preserved_previous_account_copy', 'normal_resume_authorized': False,
                    'removed_from_native_picker': False}
                doc.update(updated_utc=h.now(), config_home=str(target), primary_history_path=head['path'],
                    workspace_path=str(workspace), runtime_observation=None,
                    status='RECONCILED_SAVED_HOME_NOT_REOPENED', older_sources=list(older.values()),
                    previous_checkpoint=old['checkpoint'],
                    companions={'session_directory': str(Path(head['path']).with_suffix('')),
                                'file_history_directory': str(target/'file-history'/sid)},
                    account_identity={'email': auth['email'], 'org_id': auth['orgId'],
                        'subscription_type': auth['subscriptionType'], 'evidence': {'path': str(path), 'sha256': sha}},
                    checkpoint={'at': h.now(), 'main_sha256': head['sha256'], 'snapshot_path': head['snapshot'],
                        'manifest': {'path': str(path), 'sha256': sha},
                        'meaning': 'Existing account history preserved and registered; runtime adoption not claimed.'})
                dest = s.plain(index['entries'][sid]['path'])
                s.require(s.digest(dest) == index['entries'][sid]['sha256'], 'Concurrent record change')
                h.publish(dest, doc)
                s.require(json.loads(dest.read_text(encoding='utf-8')) == doc, 'Record publication readback failed')
                index['entries'][sid] = {'path': str(dest), 'sha256': s.digest(dest)}
                expected_docs[sid] = doc
            check_files(h, m['sessions'], target)
            s.clear_runtime(h, rows)
            s.require(s.digest(s.INDEX) == m['index_sha256'], 'Concurrent index update')
            index['updated_utc'] = h.now()
            h.publish(s.INDEX, index)
            verified = maps.load_verified(s.INDEX, s.MAP_HISTORY)
            s.require(all(verified['records'][sid] == doc for sid, doc in expected_docs.items()), 'Final registry verification failed')
            map_result = maps.refresh(s.INDEX, s.MAP, s.MAP_HISTORY)
            receipt = {'state': 'RECONCILED_SAVED_HOMES_NOT_REOPENED', 'at': h.now(),
                'group': m['group'], 'destination': m['color'], 'uuids': m['selected_uuids'],
                'manifest_sha256': sha, 'map': map_result, 'seat': 'nectardock-maintainer',
                'history_modified': False, 'old_copies_preserved': True, 'runtime_adoption': 'NOT_CLAIMED',
                'background_tasks': 'NOT_REARMED', 'memory': 'NOT_CHANGED_OR_RECONCILED'}
            h.write_new(run/'receipt.json', receipt, readonly=True)
            s.require(json.loads(pending.read_text()) == marker, 'Pending marker ownership changed')
            pending.unlink()
            return receipt
        except Exception as exc:
            h.write_new(run/('HELD-APPLY-'+uuid.uuid4().hex[:8]+'.json'),
                        {'state': 'PARTIAL_OR_NO_RECONCILIATION_REQUIRES_REVIEW', 'error': str(exc)})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    p.add_argument('--group', required=True, choices=sorted(s.GROUPS))
    p.add_argument('--account', required=True, choices=sorted(s.ACCOUNTS))
    p.add_argument('--uuid', action='append', required=True)
    p = commands.add_parser('apply')
    p.add_argument('--manifest', required=True)
    p.add_argument('--sha256', required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.group, args.account, args.uuid) if args.command == 'prepare' else apply(args.manifest, args.sha256)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({'state': 'HELD', 'error': str(exc)}, indent=2))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
