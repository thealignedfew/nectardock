"""Collect all transfer conflicts before preservation; apply only a reviewed exact batch."""
import argparse
import copy
import json
from pathlib import Path
import uuid

import switcher as s
import survivor_review as r


def decisions(rows, choices):
    s.require(isinstance(choices,dict), 'Batch choices must be a mapping')
    s.require(set(choices)<={x['uuid'] for x in rows}, 'Unknown conversation choice')
    included=[];survivors={}
    for row in rows:
        sid=row['uuid'];state=row['state'];choice=choices.get(sid)
        if choice=='skip':continue
        if state=='ALREADY_DESTINATION':
            s.require(choice is None, 'Already-destination conversation cannot be transferred again')
            continue
        s.require(state!='HELD', 'Resolve or explicitly exclude held conversation: '+row['label'])
        if state=='CONFLICT':
            s.require(choice=='source', 'Explicit survivor choice or exclusion required: '+row['label'])
            survivors[sid]=row['source_color']
        else:
            s.require(state=='READY' and choice in (None,'move'), 'Invalid batch choice: '+row['label'])
        included.append(sid)
    return included,survivors


def scan(group,destination,ids):
    s.require(ids and len(set(ids))==len(ids), 'Select exact, unique conversations')
    s.require(destination in s.ACCOUNTS, 'Unknown destination')
    h,maps=s.dependencies();view=maps.load_verified(s.INDEX,s.MAP_HISTORY)
    records=s.selected(view,group,ids);index_sha=s.digest(s.INDEX)
    home=Path(s.ACCOUNTS[destination]['home'])
    pending=[row for row in records if s.norm(row['config_home'])!=s.norm(home)]
    live_ids=set();runtime_error=None
    if pending:
        try:
            _,live=s.runtime_gate(h,pending)
            live_ids={binding['uuid'] for binding in live}
        except Exception as exc:runtime_error=str(exc)
    rows=[];evidence={}
    for position,row in enumerate(records,1):
        sid=row['uuid'];colors=[c for c,a in s.ACCOUNTS.items() if s.norm(a['home'])==s.norm(row['config_home'])]
        item={'uuid':sid,'label':row['label'],'destination':destination,'source_color':colors[0] if len(colors)==1 else 'UNKNOWN'}
        s.emit_progress(f'SCAN {position}/{len(records)}: {row["label"]}')
        try:
            s.require(len(colors)==1, 'Registered source account is ambiguous')
            if s.norm(row['config_home'])==s.norm(home):
                item.update(state='ALREADY_DESTINATION',reason='Already registered here; no transfer or backup needed')
            else:
                s.require(runtime_error is None,runtime_error or '')
                s.require(sid not in live_ids,'Close this live conversation normally, then rescan, or exclude it from this batch')
                paths={'source':s.members(h,row),'target':s.members(h,row,home)}
                s.require('main' in paths['source'], 'Registered source history missing')
                def fingerprints():return {side:{k:s.digest(p) for k,p in members.items()} for side,members in paths.items()}
                before=fingerprints()
                target=paths['target'].get('main');source=paths['source']['main']
                identical=target and before['source']['main']==before['target']['main']
                data=r.compare_histories(source,source if identical or not target else target,sid,
                                         checkpoint=row.get('checkpoint'),helper=h)
                s.require(data['source']['records']>0, 'No native conversation records in source')
                if not target:
                    data['target']={k:None for k in ('last_prompt','last_output')}
                    data['target'].update(records=0,branch_records=0,previews=[],preview_total=0)
                    data['common_records']=0;data['source']['branch_records']=data['source']['records']
                else:s.require(data['target']['records']>0, 'No native conversation records in destination')
                data['companions']=[{'relative':key,'state':'different' if key in before['source'] and key in before['target'] else 'source only' if key in before['source'] else 'destination only',
                                    'source_sha256':before['source'].get(key),'target_sha256':before['target'].get(key)}
                                   for key in sorted((set(before['source'])|set(before['target']))-{'main'})
                                   if before['source'].get(key)!=before['target'].get(key)]
                for side,color in [('source',colors[0]),('target',destination)]:
                    data[side].update(color=color,email=s.ACCOUNTS[color]['email'],registered=side=='source',
                                      path=str(paths[side].get('main','absent')),sha256=before[side].get('main','absent'))
                ready=not target or identical or data['common_records']==data['target']['records']
                s.require(paths['source']==s.members(h,row) and paths['target']==s.members(h,row,home), 'Companion membership changed during scan')
                s.require(before==fingerprints(), 'History or companions changed during scan')
                item.update(data,state='READY' if ready else 'CONFLICT',
                            reason='New, identical, or prefix-compatible destination' if ready else 'Destination has a distinct continuation; choose source or exclude')
                evidence[sid]=before
        except Exception as exc:
            item.update(state='HELD',reason=str(exc))
        rows.append(item)
    maps.assert_unchanged(view);s.require(s.digest(s.INDEX)==index_sha,'Register changed during scan')
    folder=s.BASE/'Batch-Reviews'/uuid.uuid4().hex;folder.mkdir(parents=True)
    path=folder/'scan.json'
    data={'schema':'nectardock-batch-scan/v1','group':group,'destination':destination,'ids':list(ids),
          'rows':rows,'evidence':{'index':index_sha,'sessions':evidence}}
    h.write_new(path,data,readonly=True)
    return dict(data,state='BATCH_SCAN_READY',review=str(path),sha256=s.digest(path))


def prepare(path,sha,choices):
    path=Path(path).resolve();root=(s.BASE/'Batch-Reviews').resolve()
    s.require(path.is_relative_to(root) and path.name=='scan.json', 'Not a batch scan')
    s.plain(path);s.require(s.digest(path)==sha,'Batch scan changed')
    data=json.loads(path.read_text(encoding='utf-8'))
    s.require(data.get('schema')=='nectardock-batch-scan/v1', 'Unknown batch scan format')
    included,survivors=decisions(data['rows'],choices)
    if not included:return {'state':'NO_TRANSFER_NEEDED','sessions':0}
    evidence=copy.deepcopy(data['evidence'])
    evidence['sessions']={sid:evidence['sessions'][sid] for sid in included}
    result=s.prepare(data['group'],data['destination'],included,survivor_sources=survivors,expected_evidence=evidence)
    return dict(result,excluded_uuids=[x['uuid'] for x in data['rows'] if x['uuid'] not in included],
                source_choices=survivors)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['scan','prepare']);p.add_argument('--group',choices=s.GROUPS)
    p.add_argument('--destination',choices=s.ACCOUNTS);p.add_argument('--uuid',action='append')
    p.add_argument('--review');p.add_argument('--sha256');p.add_argument('--choices')
    args=p.parse_args()
    try:
        result=(scan(args.group,args.destination,args.uuid) if args.mode=='scan'
                else prepare(args.review,args.sha256,json.loads(args.choices or '{}')))
        print(json.dumps(result,ensure_ascii=True));return 0
    except Exception as exc:
        print(json.dumps({'state':'HELD','error':str(exc)}));return 2


if __name__=='__main__':raise SystemExit(main())
