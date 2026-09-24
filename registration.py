"""Review and register existing Claude histories without moving or opening them."""
from __future__ import annotations

import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import uuid

import switcher as s


RUNS = s.BASE / 'Registration-Runs'
BUCKETS = {
    'FPA': 'd--NectarDockExample-Projects-Finance',
    'GCP': 'd--NectarDockExample-Projects-Cloud',
    'BI': 'd--NectarDockExample-Projects-Analytics',
    'VAT': 'd--NectarDockExample-Projects-Compliance',
}
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')


def verified_view():
    _, maps = s.dependencies()
    return maps.load_verified(s.INDEX, s.MAP_HISTORY)


def account_identity(color):
    return s.auth_check(color)


def clear_runtime(rows):
    h, _ = s.dependencies()
    return s.clear_runtime(h, rows)


def native_title(path, sid):
    """Read only native title records; do not expose message text as a title."""
    title = None
    with Path(path).open('rb') as stream:
        for line in stream:
            if len(line) > 256 * 1024 * 1024:
                raise s.Hold('Oversized native record: ' + sid)
            if b'"custom-title"' not in line and b'"agent-name"' not in line:
                continue
            try:
                item = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if item.get('sessionId') != sid:
                continue
            if item.get('type') == 'custom-title':
                candidate = item.get('customTitle')
            elif item.get('type') == 'agent-name':
                candidate = item.get('agentName')
            else:
                continue
            if isinstance(candidate, str) and candidate.strip():
                title = candidate.strip()
    return title


def discover(group, view=None):
    s.require(group in BUCKETS, 'Unknown workspace group')
    view = view or verified_view()
    found = {}
    for color, account in s.ACCOUNTS.items():
        folder = s.plain(Path(account['home']) / 'projects' / BUCKETS[group])
        if not folder.is_dir():
            continue
        for path in folder.glob('*.jsonl'):
            sid = path.stem
            if not UUID.fullmatch(sid) or sid in view['records']:
                continue
            s.plain(path)
            stat = path.stat()
            found.setdefault(sid, []).append({'color': color, 'path': str(path),
                'bytes': stat.st_size, 'modified_ns': stat.st_mtime_ns})
    result = []
    for sid, copies in found.items():
        copies.sort(key=lambda item:item['color'])
        labels = {native_title(item['path'], sid) for item in copies}
        result.append({'uuid': sid, 'label': next(iter(labels)) if len(labels) == 1 else None,
                       'homes': [item['color'] for item in copies], 'copies': copies,
                       'registration': 'UNREGISTERED_REVIEW_REQUIRED'})
    return sorted(result, key=lambda item:(item['label'] or '', item['uuid']))


def selection(group, color, ids, view):
    s.require(color in s.ACCOUNTS and group in BUCKETS, 'Unknown account or group')
    s.require(ids and len(ids) == len(set(ids)) and all(UUID.fullmatch(sid) for sid in ids),
              'Select unique exact UUIDs')
    items = {item['uuid']:item for item in discover(group, view)}
    out = []
    for sid in ids:
        s.require(sid in items, 'UUID is registered or absent from discovery: ' + sid)
        item = items[sid]
        s.require(item['homes'] == [color], 'History exists in multiple account homes or not in ' + color + ': ' + sid)
        s.require(item['label'], 'No unique native title for ' + sid)
        out.append(item)
    return out


def provisional_rows(group, color, items):
    return [{'uuid':item['uuid'], 'label':item['label'], 'config_home':s.ACCOUNTS[color]['home'],
             'primary_history_path':item['copies'][0]['path'],
             'project_directory':str(s.PROJECTS / s.GROUPS[group])} for item in items]


def prepare(group, color, ids):
    h, _ = s.dependencies()
    s.require(not (RUNS / 'PENDING.json').exists() and not (s.RUNS / 'PENDING.json').exists(),
              'Pending registration or transfer requires recovery review')
    view = verified_view()
    items = selection(group, color, ids, view)
    rows = provisional_rows(group, color, items)
    clear_runtime(rows)
    auth = account_identity(color)
    run = RUNS / (h.now().replace(':','').replace('.','') + '-' + uuid.uuid4().hex[:8])
    run.mkdir(parents=True, exist_ok=False)
    objects = run / 'objects'; objects.mkdir()
    s.require(shutil.disk_usage(run).free > sum(i['copies'][0]['bytes'] for i in items) + 2 * 1024**3,
              'Not enough free space to preserve a full registration checkpoint')
    manifest = {'schema':'claude-registration/v1','state':'PREPARED_REVIEW_REQUIRED',
        'group':group,'color':color,'prepared_at':h.now(),'engine_sha256':s.digest(__file__),
        'index_sha256':s.digest(s.INDEX),'auth':auth,'sessions':[]}
    try:
        for row in rows:
            sid = row['uuid']
            members = s.members(h,row)
            s.require('main' in members, 'History disappeared: ' + sid)
            main = s.capture(h,members['main'],objects,sid,parse=True)
            available = {Path(k).name for k in members if k.startswith('file-history/')}
            tools = {Path(k).name for k in members if k.startswith('sidecars/tool-results/')}
            missing_backup = sorted(set(main['backup_refs']) - available)
            missing_tools = sorted(set(main['tool_result_refs']) - tools)
            manifest['sessions'].append({'uuid':sid,'label':row['label'],'path':row['primary_history_path'],
                'members':{k:str(v) for k,v in members.items()},'selected_head':{
                    'sha256':main['sha256'],'snapshot_path':main['snapshot'],'bytes':main['bytes'],
                    'missing_backup_refs':missing_backup,'missing_tool_result_refs':missing_tools}})
        clear_runtime(rows)
        s.require(s.digest(s.INDEX) == manifest['index_sha256'], 'Register changed during preparation')
        for section in manifest['sessions']:
            s.require(s.digest(section['path']) == section['selected_head']['sha256'], 'History changed during preparation')
        path = run / 'manifest.json'; h.write_new(path,manifest,readonly=True)
        review = run / 'REVIEW.txt'
        lines = ['Unregistered Claude histories — registration review',group + ' / ' + color,
                 'This registers saved home authority only. It does not transfer history, open tabs, or prove loaded context.',
                 'Source and destination account identity: ' + auth['email'] + ' / ' + auth['subscriptionType'],'']
        for item in manifest['sessions']:
            head = item['selected_head']
            lines += [item['label'] + ' | ' + item['uuid'],item['path'],
                      'SHA256 ' + head['sha256'] + ' | bytes ' + str(head['bytes']),
                      'Missing referenced backups: ' + str(len(head['missing_backup_refs'])),
                      'Missing referenced tool results: ' + str(len(head['missing_tool_result_refs'])),'']
        review.write_text('\n'.join(lines),encoding='utf-8')
        return {'state':'PREPARED_REVIEW_REQUIRED','manifest':str(path),'sha256':s.digest(path),
                'review':str(review),'sessions':len(rows)}
    except Exception as exc:
        h.write_new(run/'HELD.json',{'state':'PREPARE_HELD_NO_REGISTER_CHANGE','error':str(exc)})
        raise


def apply(path, sha):
    h, maps = s.dependencies()
    path = Path(path).resolve()
    s.require(path.is_relative_to(RUNS.resolve()) and path.name == 'manifest.json', 'Not a registration manifest')
    s.require(s.digest(path) == sha, 'Reviewed manifest changed')
    m = json.loads(path.read_text(encoding='utf-8'))
    s.require(m.get('schema') == 'claude-registration/v1' and m.get('state') == 'PREPARED_REVIEW_REQUIRED',
              'Wrong registration manifest')
    s.require(m['engine_sha256'] == s.digest(__file__), 'Registration code changed; prepare again')
    group,color = m['group'],m['color']
    ids = [item['uuid'] for item in m['sessions']]
    pending = RUNS / 'PENDING.json'
    with s.mutexes(ids):
        s.require(not pending.exists() and not (s.RUNS/'PENDING.json').exists(), 'Pending apply requires review')
        view = verified_view()
        s.require(s.digest(s.INDEX) == m['index_sha256'], 'Current-home register changed; prepare again')
        items = selection(group,color,ids,view)
        rows = provisional_rows(group,color,items)
        s.require([(r['uuid'],r['label'],r['primary_history_path']) for r in rows] ==
                  [(i['uuid'],i['label'],i['path']) for i in m['sessions']], 'Discovered identity changed')
        clear_runtime(rows)
        auth = account_identity(color)
        s.require(auth['email'].lower() == m['auth']['email'].lower() and
                  auth['orgId'] == m['auth']['orgId'] and auth['subscriptionType'] == m['auth']['subscriptionType'],
                  'Account identity changed since review')
        for row,section in zip(rows,m['sessions']):
            s.require(s.members(h,row) == {k:Path(v) for k,v in section['members'].items()},
                      'Companion membership changed')
            head = section['selected_head']
            s.require(s.digest(section['path']) == head['sha256'], 'History changed since review')
            s.require(s.digest(head['snapshot_path']) == head['sha256'], 'Preserved checkpoint changed')
        s.require(s.digest(s.INDEX) == m['index_sha256'], 'Current-home register changed')
        run = path.parent
        (run/'routing-before.json').write_bytes(s.INDEX.read_bytes())
        h.write_new(pending,{'state':'REGISTERING_REVIEW_REQUIRED_IF_INTERRUPTED','manifest':str(path),'sha256':sha})
        try:
            index = copy.deepcopy(view['index'])
            for row,section in zip(rows,m['sessions']):
                sid = row['uuid']; head = section['selected_head']
                doc = {'schema':'session-current-home/v1','provider':'claude-code','uuid':sid,
                    'label':row['label'],'updated_utc':h.now(),'machine':os.environ.get('COMPUTERNAME','UNVERIFIED'),
                    'status':'REGISTERED_SAVED_HOME_NOT_REOPENED','config_home':row['config_home'],
                    'primary_history_path':row['primary_history_path'],
                    'companions':{'session_directory':str(Path(row['primary_history_path']).with_suffix('')),
                                  'file_history_directory':str(Path(row['config_home'])/'file-history'/sid)},
                    'project_directory':row['project_directory'],
                    'workspace_path':str(s.workspace(group,color)),
                    'account_identity':{'email':auth['email'],'org_id':auth['orgId'],
                        'subscription_type':auth['subscriptionType'],'evidence':{'path':str(path),'sha256':sha}},
                    'runtime_observation':None,
                    'checkpoint':{'at':h.now(),'main_sha256':head['sha256'],
                        'snapshot_path':head['snapshot_path'],'manifest':{'path':str(path),'sha256':sha},
                        'meaning':'Full saved history captured for reviewed in-place registration; not runtime adoption.'},
                    'older_sources':[],'enforcement':{'central_history_redirection':False,
                        'all_launch_paths_guarded':False,'old_copies_retired_from_native_pickers':False},
                    'scope_note':'New saved history registered in its existing account; no account transfer or graphical reopen.',
                    'preserved_limitations':{'loaded_model_context':'Not observed',
                                             'companions':'Inventoried at registration; not copied.'}}
                record = s.INDEX.parent / sid / 'CURRENT-HOME.json'
                s.require(not record.exists(), 'Record appeared during registration: ' + sid)
                h.write_new(record,doc)
                index['entries'][sid] = {'path':str(record),'sha256':s.digest(record)}
            s.require(s.digest(s.INDEX) == m['index_sha256'], 'Concurrent register change')
            index['updated_utc'] = h.now()
            h.publish(s.INDEX,index)
            maps.refresh(s.INDEX,s.MAP,s.MAP_HISTORY)
            receipt = {'state':'REGISTERED_SAVED_HOMES_NOT_REOPENED','group':group,'color':color,
                       'uuids':ids,'manifest_sha256':sha,'at':h.now(),'history_moved':False}
            h.write_new(run/'receipt.json',receipt,readonly=True)
            pending.unlink()
            return receipt
        except Exception as exc:
            h.write_new(run/('HELD-APPLY-'+uuid.uuid4().hex[:8]+'.json'),
                        {'state':'PARTIAL_OR_NO_REGISTRATION_REQUIRES_REVIEW','error':str(exc)})
            raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['discover','prepare','apply'])
    parser.add_argument('--group',choices=BUCKETS)
    parser.add_argument('--account',choices=s.ACCOUNTS)
    parser.add_argument('--uuid',action='append')
    parser.add_argument('--manifest');parser.add_argument('--sha256')
    args = parser.parse_args()
    try:
        if args.mode == 'discover':
            s.require(args.group,'Choose a workspace group')
            result = {'state':'UNREGISTERED_INVENTORY','group':args.group,'items':discover(args.group)}
        elif args.mode == 'prepare':
            s.require(args.group and args.account,'Choose group and source account')
            result = prepare(args.group,args.account,args.uuid)
        else:
            s.require(args.manifest and args.sha256,'Reviewed manifest and SHA256 required')
            result = apply(args.manifest,args.sha256)
        print(json.dumps(result,indent=2))
    except Exception as exc:
        print(json.dumps({'state':'HELD','error':str(exc)},indent=2));return 2
    return 0


if __name__ == '__main__':raise SystemExit(main())
