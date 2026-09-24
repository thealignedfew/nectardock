"""Batch workflow regression fixtures never touch account histories."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import switcher as s
import test_reconcile_home as fixture

SID2='22222222-2222-4222-8222-222222222222'
SID3='33333333-3333-4333-8333-333333333333'


class BatchIntegration(unittest.TestCase):
    make_writable=fixture.ReconcileHomeTests.make_writable

    def setUp(self):
        fixture.ReconcileHomeTests.setUp(self)
        runtime=patch.object(s,'runtime_gate',return_value=({},[]))
        runtime.start();self.addCleanup(runtime.stop)
        for account in s.ACCOUNTS.values():account['email']='owner@example.invalid'
        self.y.write_bytes(self.first.replace(b'fixture',b'destination branch'))

    def batch(self):
        self.assertIsNotNone(importlib.util.find_spec('batch_transition'),'Batch transition is not implemented')
        import batch_transition
        return batch_transition

    def add_session(self,sid,at_destination=False):
        g=self.g.with_name(sid+'.jsonl');y=self.y.with_name(sid+'.jsonl')
        g.write_bytes(self.first.replace(fixture.SID.encode(),sid.encode()))
        y.write_bytes(g.read_bytes().replace(b'fixture',b'other branch'))
        doc=copy.deepcopy(self.doc);doc.update(uuid=sid,label='Example '+sid[:4],primary_history_path=str(y if at_destination else g),config_home=str(self.yellow if at_destination else self.green))
        doc['checkpoint']={'main_sha256':s.digest(y if at_destination else g),'snapshot_path':str(y if at_destination else g)}
        p=self.index.parent/sid/'CURRENT-HOME.json';p.parent.mkdir();p.write_text(json.dumps(doc))
        index=json.loads(self.index.read_text());index['entries'][sid]={'path':str(p),'sha256':s.digest(p)};self.index.write_text(json.dumps(index))
        return g,y,p

    def test_same_account_is_noop_before_auth_or_capture(self):
        with patch.object(s,'auth_check',side_effect=AssertionError('No auth for a no-op')):
            result=s.prepare('BI','GREEN',[fixture.SID])
        self.assertEqual(result['state'],'NO_TRANSFER_NEEDED')
        self.assertFalse(s.RUNS.exists())

    def test_scan_lists_all_conflicts_without_backup_capture(self):
        b=self.batch();self.add_session(SID2);self.add_session(SID3,True)
        result=b.scan('BI','YELLOW',[fixture.SID,SID2,SID3])
        self.assertEqual([r['state'] for r in result['rows']],['CONFLICT','CONFLICT','ALREADY_DESTINATION'])
        self.assertFalse(s.RUNS.exists())
        self.assertTrue(Path(result['review']).exists())

    def test_known_live_row_can_be_excluded_without_hiding_other_conflicts(self):
        b=self.batch();self.add_session(SID2)
        with patch.object(s,'runtime_gate',return_value=({},[{'uuid':fixture.SID,'pid':123}])):
            scan=b.scan('BI','YELLOW',[fixture.SID,SID2])
        self.assertEqual([r['state'] for r in scan['rows']],['HELD','CONFLICT'])
        plan=b.prepare(scan['review'],scan['sha256'],{fixture.SID:'skip',SID2:'source'})
        self.assertEqual(plan['sessions'],1)

    def test_unknown_runtime_identity_keeps_rows_visible_but_held(self):
        b=self.batch();self.add_session(SID2)
        with patch.object(s,'runtime_gate',side_effect=s.Hold('Live process identity is uncertain')):
            scan=b.scan('BI','YELLOW',[fixture.SID,SID2])
        self.assertEqual([r['state'] for r in scan['rows']],['HELD','HELD'])
        self.assertNotIn(fixture.SID,scan['evidence']['sessions'])

    def test_registered_large_attachment_scans_but_changed_checkpoint_is_held(self):
        b=self.batch()
        record={'type':'user','sessionId':fixture.SID,'uuid':'large',
                'message':{'content':[{'type':'image','source':{'data':'x'*(33*1024**2)}}]}}
        self.g.write_text(json.dumps(record)+'\n',encoding='utf-8')
        self.doc['checkpoint']['main_sha256']=s.digest(self.g)
        self.record_path.write_text(json.dumps(self.doc))
        index=json.loads(self.index.read_text());index['entries'][fixture.SID]['sha256']=s.digest(self.record_path)
        self.index.write_text(json.dumps(index))
        with patch.object(s,'available_memory',return_value=8*1024**3):
            scan=b.scan('BI','YELLOW',[fixture.SID])
            self.assertEqual(scan['rows'][0]['state'],'CONFLICT')
            self.g.write_bytes(self.g.read_bytes()+b'{}\n')
            scan=b.scan('BI','YELLOW',[fixture.SID])
        self.assertEqual(scan['rows'][0]['state'],'HELD')
        self.assertIn('checkpoint',scan['rows'][0]['reason'])

    def test_batch_requires_every_conflict_choice_and_applies_both_once(self):
        b=self.batch();g2,y2,_=self.add_session(SID2)
        scan=b.scan('BI','YELLOW',[fixture.SID,SID2])
        with self.assertRaisesRegex(s.Hold,'choice'):
            b.prepare(scan['review'],scan['sha256'],{fixture.SID:'source'})
        plan=b.prepare(scan['review'],scan['sha256'],{fixture.SID:'source',SID2:'source'})
        self.assertEqual(plan['sessions'],2)
        before=self.y.read_bytes();other=y2.read_bytes()
        self.assertNotEqual(before,self.g.read_bytes());self.assertNotEqual(other,g2.read_bytes())
        result=s.apply(plan['manifest'],plan['sha256'],accept_survivor=True)
        self.assertEqual(result['sessions'],2)
        self.assertEqual(self.y.read_bytes(),self.g.read_bytes());self.assertEqual(y2.read_bytes(),g2.read_bytes())
        self.assertFalse((s.RUNS/'PENDING.json').exists())

    def test_skip_and_already_destination_are_not_in_manifest(self):
        b=self.batch();_,y2,p2=self.add_session(SID2);self.add_session(SID3,True)
        scan=b.scan('BI','YELLOW',[fixture.SID,SID2,SID3]);before=(y2.read_bytes(),p2.read_bytes())
        plan=b.prepare(scan['review'],scan['sha256'],{fixture.SID:'source',SID2:'skip'})
        self.assertEqual(plan['sessions'],1)
        s.apply(plan['manifest'],plan['sha256'],accept_survivor=True)
        self.assertEqual((y2.read_bytes(),p2.read_bytes()),before)

    def test_changed_history_after_scan_cannot_prepare(self):
        b=self.batch();scan=b.scan('BI','YELLOW',[fixture.SID]);before=self.index.read_bytes()
        self.y.write_bytes(self.y.read_bytes()+b'{}\n')
        with self.assertRaisesRegex(s.Hold,'changed|differs'):
            b.prepare(scan['review'],scan['sha256'],{fixture.SID:'source'})
        self.assertEqual(self.index.read_bytes(),before)

    def test_identical_companions_reuse_verified_snapshot(self):
        h,_=s.dependencies();objects=self.root/'objects';objects.mkdir()
        a=self.root/'a';b=self.root/'b';a.write_bytes(b'identical');b.write_bytes(b'identical')
        cache={}
        first=s.capture(h,a,objects,fixture.SID,cache=cache)
        second=s.capture(h,b,objects,fixture.SID,cache=cache)
        self.assertTrue(second.get('reused_snapshot'))
        self.assertEqual(first['snapshot'],second['snapshot'])
        self.assertEqual(second['path'],str(b))
        s.stable(h,second)
        b.write_bytes(b'changed')
        with self.assertRaises(s.Hold):s.stable(h,second)

    def test_corrupt_cached_snapshot_is_never_reused(self):
        h,_=s.dependencies();objects=self.root/'objects';objects.mkdir()
        a=self.root/'a';b=self.root/'b';a.write_bytes(b'identical');b.write_bytes(b'identical')
        cache={};first=s.capture(h,a,objects,fixture.SID,cache=cache)
        snapshot=Path(first['snapshot']);snapshot.chmod(0o666);snapshot.write_bytes(b'corrupt')
        with self.assertRaisesRegex(s.Hold,'snapshot changed'):s.capture(h,b,objects,fixture.SID,cache=cache)

    def test_stale_second_row_blocks_entire_apply_before_any_writes(self):
        b=self.batch();_,y2,_=self.add_session(SID2)
        scan=b.scan('BI','YELLOW',[fixture.SID,SID2])
        plan=b.prepare(scan['review'],scan['sha256'],{fixture.SID:'source',SID2:'source'})
        before=(self.y.read_bytes(),self.index.read_bytes());y2.write_bytes(y2.read_bytes()+b'{}\n')
        with self.assertRaisesRegex(s.Hold,'changed'):s.apply(plan['manifest'],plan['sha256'],accept_survivor=True)
        self.assertEqual((self.y.read_bytes(),self.index.read_bytes()),before)
        self.assertFalse((s.RUNS/'PENDING.json').exists())

    def test_interrupted_batch_leaves_recovery_marker_and_original_backups(self):
        b=self.batch();_,y2,_=self.add_session(SID2)
        scan=b.scan('BI','YELLOW',[fixture.SID,SID2])
        plan=b.prepare(scan['review'],scan['sha256'],{fixture.SID:'source',SID2:'source'})
        before=(self.y.read_bytes(),y2.read_bytes());original=s.mutate
        def fail_second(h,op,run,number):
            if number==1:raise OSError('fixture interruption')
            return original(h,op,run,number)
        with patch.object(s,'mutate',side_effect=fail_second):
            with self.assertRaisesRegex(OSError,'fixture interruption'):
                s.apply(plan['manifest'],plan['sha256'],accept_survivor=True)
        self.assertTrue((s.RUNS/'PENDING.json').exists())
        manifest=json.loads(Path(plan['manifest']).read_text())
        self.assertEqual(tuple(Path(section['operations'][0]['before']['snapshot']).read_bytes() for section in manifest['sessions']),before)

    def test_ready_session_and_conflict_share_one_batch(self):
        b=self.batch();g2,y2,_=self.add_session(SID2);y2.write_bytes(g2.read_bytes())
        scan=b.scan('BI','YELLOW',[fixture.SID,SID2])
        self.assertEqual([r['state'] for r in scan['rows']],['CONFLICT','READY'])
        plan=b.prepare(scan['review'],scan['sha256'],{fixture.SID:'source'})
        result=s.apply(plan['manifest'],plan['sha256'],accept_survivor=True)
        self.assertEqual(result['sessions'],2)
        journal=[json.loads(line) for line in (Path(plan['manifest']).parent/'journal.jsonl').read_text().splitlines()]
        self.assertEqual([entry['action'] for entry in journal if entry['state']=='BEFORE'],['CHOOSE_SOURCE_MAIN_SURVIVOR'])
        self.assertEqual(y2.read_bytes(),g2.read_bytes())

    def test_keep_destination_changed_mid_apply_holds_before_registration(self):
        b=self.batch();g2,y2,_=self.add_session(SID2);y2.write_bytes(g2.read_bytes())
        scan=b.scan('BI','YELLOW',[fixture.SID,SID2])
        plan=b.prepare(scan['review'],scan['sha256'],{fixture.SID:'source'})
        original=s.mutate;before=self.index.read_bytes()
        def concurrent_change(h,op,run,number):
            original(h,op,run,number)
            y2.write_bytes(y2.read_bytes()+b'{}\n')
        with patch.object(s,'mutate',side_effect=concurrent_change):
            with self.assertRaises(s.Hold):s.apply(plan['manifest'],plan['sha256'],accept_survivor=True)
        self.assertEqual(self.index.read_bytes(),before)
        self.assertTrue((s.RUNS/'PENDING.json').exists())
        self.assertFalse((Path(plan['manifest']).parent/'receipt.json').exists())


if __name__=='__main__':unittest.main()
