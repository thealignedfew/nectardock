"""Local-only branch evidence and explicit single-source survivor preparation."""
import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import switcher as s


def conflict_uuid(result, registered_ids):
    """Offer review only for an exact known main-history conflict, never arbitrary paths."""
    if result.get('state') != 'HELD':
        return None
    match = re.fullmatch(r'Distinct saved branches or companion versions require review: .*[/\\]([0-9a-fA-F-]{36})\.jsonl',
                         result.get('error', ''))
    sid = match.group(1) if match else None
    return sid if sid in registered_ids else None


def evidence_token(evidence):
    return hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def require_fresh(token, evidence):
    s.require(token == evidence_token(evidence), 'Compared histories, companions or register changed; compare again')


class OversizedRecord(s.Hold):
    pass


def _scan(path, sid, record_limit=32*1024*1024):
    """Keep hashes and bounded text previews, never reasoning or tool payloads."""
    rows = []
    with Path(path).open('rb') as stream:
        while True:
            raw = stream.readline(record_limit + 1)
            if not raw:
                break
            if len(raw)>record_limit:raise OversizedRecord('Oversized history record; separate review required')
            s.require(raw.endswith(b'\n'), 'Incomplete history record; separate review required')
            if not raw.strip():
                continue
            value = json.loads(raw)
            s.require(isinstance(value, dict) and value.get('sessionId', sid) == sid,
                      'History identity mismatch')
            kind = value.get('type')
            if kind not in ('user', 'assistant', 'system'):
                continue
            message = value.get('message') or {}
            content = message.get('content', []) if isinstance(message, dict) else []
            blocks = [{'type':'text','text':content}] if isinstance(content, str) else content
            blocks = blocks if isinstance(blocks, list) else []
            text = '\n'.join(b['text'] for b in blocks if isinstance(b, dict)
                             and b.get('type') == 'text' and isinstance(b.get('text'), str))
            stamp = value.get('timestamp')
            try:
                parsed = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    stamp = None
            except (AttributeError, TypeError, ValueError):
                stamp = None
            rows.append({'hash':evidence_token(value), 'type':kind, 'timestamp':stamp,
                         'text':text[:800], 'text_truncated':len(text)>800,
                         'tool_calls':sum(isinstance(b,dict) and b.get('type')=='tool_use' for b in blocks),
                         'tool_results':sum(isinstance(b,dict) and b.get('type')=='tool_result' for b in blocks)})
            s.require(len(rows) <= 250000, 'History comparison exceeds bounded record limit')
    return rows


def compare_histories(source, target, sid, checkpoint=None, helper=None):
    def scan(path):
        try:return _scan(path,sid)
        except OversizedRecord:
            if not checkpoint or helper is None:raise
            item={'snapshot':str(path),'sha256':s.digest(path),'bytes':Path(path).stat().st_size}
            s.validate_large_checkpoint(helper,item,sid,checkpoint)
            return _scan(path,sid,record_limit=s.LARGE_RECORD_BYTES)
    left=scan(source)
    right=left if Path(source)==Path(target) else scan(target)
    common = 0
    for a, b in zip(left, right):
        if a['hash'] != b['hash']:
            break
        common += 1
    def summary(rows):
        branch = rows[common:]
        previews = [dict(type=r['type'], timestamp=r['timestamp'], text=r['text'],
                         truncated=r['text_truncated']) for r in branch
                    if r['text'] and r['type'] in ('user', 'assistant')]
        def latest(kind):
            dates = [r['timestamp'] for r in rows if r['type']==kind and r['text'] and r['timestamp']]
            return max(dates, key=lambda v:dt.datetime.fromisoformat(v.replace('Z','+00:00'))) if dates else None
        return {'records':len(rows), 'branch_records':len(branch),
                'user_text_records':sum(r['type']=='user' and bool(r['text']) for r in rows),
                'assistant_text_records':sum(r['type']=='assistant' and bool(r['text']) for r in rows),
                'tool_calls':sum(r['tool_calls'] for r in rows),
                'tool_results':sum(r['tool_results'] for r in rows),
                'last_prompt':latest('user'), 'last_output':latest('assistant'),
                'previews':previews[-8:], 'preview_total':len(previews)}
    return {'common_records':common, 'source':summary(left), 'target':summary(right)}


def compare(group, destination, sid):
    h, maps = s.dependencies()
    view = maps.load_verified(s.INDEX, s.MAP_HISTORY)
    rows = s.selected(view, group, [sid])
    row = rows[0]
    colors = [c for c,a in s.ACCOUNTS.items() if s.norm(a['home'])==s.norm(row['config_home'])]
    s.require(len(colors)==1, 'Registered source account is ambiguous')
    source = colors[0]
    s.validate_survivor_scope(rows, destination, [sid], source)
    s.clear_runtime(h, rows)
    target_home = Path(s.ACCOUNTS[destination]['home'])
    paths = {'source':s.members(h,row), 'target':s.members(h,row,target_home)}
    s.require(all('main' in p for p in paths.values()), 'Both main histories must exist for a branch comparison')
    def evidence():
        return {'group':group, 'uuid':sid, 'source_color':source, 'destination':destination,
                'index':s.digest(s.INDEX),
                **{side:{key:s.digest(path) for key,path in members.items()} for side,members in paths.items()}}
    before = evidence()
    data = compare_histories(paths['source']['main'], paths['target']['main'], sid)
    s.require(paths['source']==s.members(h,row) and paths['target']==s.members(h,row,target_home),
              'Companion membership changed during comparison')
    require_fresh(evidence_token(before), evidence())
    maps.assert_unchanged(view)
    companions = []
    for key in sorted((set(before['source']) | set(before['target'])) - {'main'}):
        a,b=before['source'].get(key),before['target'].get(key)
        if a != b:
            companions.append({'relative':key, 'state':'different' if a and b else 'source only' if a else 'destination only',
                               'source_sha256':a, 'target_sha256':b})
    for side,color in [('source',source),('target',destination)]:
        data[side].update(color=color, path=str(paths[side]['main']), sha256=before[side]['main'],
                          email=s.ACCOUNTS[color]['email'], registered=side=='source')
    return dict(data, state='BRANCH_COMPARISON_READY', uuid=sid, label=row['label'], group=group,
                destination=destination, source_color=source, token=evidence_token(before),
                companions=companions, evidence=before,
                meaning='Read-only comparison. Record counts are not prompt counts. No automatic winner or merge.')


def prepare(group, destination, sid, token):
    data = compare(group, destination, sid)
    require_fresh(token, data['evidence'])
    result = s.prepare(group, destination, [sid], survivor_source=data['source_color'])
    # Compare again after preservation. Any new data invalidates the UI choice.
    require_fresh(token, compare(group,destination,sid)['evidence'])
    manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
    section = manifest['sessions'][0]
    captured_source = {op['relative']:op['source']['sha256'] for op in section['operations']}
    captured_target = {op['relative']:op['before']['sha256'] for op in section['operations'] if op['before']}
    s.require(captured_source==data['evidence']['source'], 'Source capture differs from comparison; compare again')
    s.require(all(data['evidence']['target'].get(k)==v for k,v in captured_target.items()),
              'Destination capture differs from comparison; compare again')
    s.require(section.get('survivor_target_hashes')==data['evidence']['target'],
              'Destination companion evidence differs from comparison; compare again')
    return dict(result, source_color=data['source_color'], destination=destination, uuid=sid,
                label=data['label'], backups=str(Path(result['manifest']).parent/'objects'))


def apply_arguments(item, choice, reviewed, companions):
    s.require(choice=='source', 'Explicit registered-source survivor choice required')
    s.require(reviewed, 'Open and acknowledge the prepared review first')
    s.require(not item.get('companion_variants') or companions, 'Explicit companion replacement review required')
    args=['apply','--manifest',item['manifest'],'--sha256',item['sha256'],'--accept-source-main-survivor']
    if companions:
        args.append('--accept-companion-variants')
    return args


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['compare','prepare'])
    parser.add_argument('--group',choices=s.GROUPS,required=True)
    parser.add_argument('--destination',choices=s.ACCOUNTS,required=True)
    parser.add_argument('--uuid',required=True)
    parser.add_argument('--token')
    args=parser.parse_args()
    try:
        result=(compare(args.group,args.destination,args.uuid) if args.mode=='compare'
                else prepare(args.group,args.destination,args.uuid,args.token))
        print(json.dumps(result,ensure_ascii=True));return 0
    except Exception as exc:
        print(json.dumps({'state':'HELD','error':str(exc)}));return 2


if __name__=='__main__':raise SystemExit(main())
