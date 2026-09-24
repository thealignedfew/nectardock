import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import switcher as s
try:
    import reconcile_home as r
except ImportError:
    r = None


SID = '11111111-1111-4111-8111-111111111111'


class ReconcileHomeTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(r, 'Guarded metadata reconciliation is not implemented')
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.green, self.yellow = self.root/'green', self.root/'yellow'
        self.index = self.root/'capsules'/'CURRENT-SESSION-HOMES.json'
        self.index.parent.mkdir()
        self.record_path = self.index.parent/SID/'CURRENT-HOME.json'
        self.record_path.parent.mkdir()
        self.bucket = 'project'
        self.g = self.green/'projects'/self.bucket/(SID+'.jsonl')
        self.y = self.yellow/'projects'/self.bucket/(SID+'.jsonl')
        for p in (self.g, self.y): p.parent.mkdir(parents=True)
        self.first = (json.dumps({'type':'user','sessionId':SID,'uuid':'one',
                                 'message':{'content':'fixture'}})+'\n').encode()
        self.g.write_bytes(self.first)
        self.y.write_bytes(self.first + (json.dumps({'type':'assistant','sessionId':SID,
            'uuid':'two','message':{'content':'new'}})+'\n').encode())
        self.doc = {'schema':'session-current-home/v1','provider':'claude-code','uuid':SID,
            'label':'Example','updated_utc':'2026-09-23T00:00:00+00:00',
            'config_home':str(self.green),'primary_history_path':str(self.g),
            'project_directory':str(self.root/'project'), 'runtime_observation':None,
            'older_sources':[], 'checkpoint':{'main_sha256':s.digest(self.g),
                                             'snapshot_path':str(self.g)}}
        self.record_path.write_text(json.dumps(self.doc))
        self.index.write_text(json.dumps({'schema':'session-current-homes-index/v1',
            'updated_utc':'2026-09-23T00:00:00+00:00',
            'entries':{SID:{'path':str(self.record_path),'sha256':s.digest(self.record_path)}}}))
        self.history = self.root/'map-history.json'
        archive = self.root/'archive.md'; archive.write_text('Historical fixture\n')
        self.history.write_text(json.dumps({'schema':'capsule-map-history/v1',
            'historical_map_path':str(archive),'historical_map_sha256':s.digest(archive),
            'historical_inventory_uuid_count':1,'historical_inventory_path':'fixture',
            'historical_inventory_date':'2026-09-23'}))
        self.auth = {'email':'owner@example.invalid','orgId':'fixture-org','subscriptionType':'max'}
        self.patches = [patch.object(s,'INDEX',self.index),
            patch.object(s,'MAP_HISTORY',self.history),patch.object(s,'MAP',self.root/'map.md'),
            patch.object(s,'RUNS',self.root/'transfers'),patch.object(r,'RUNS',self.root/'reconciliations'),
            patch.object(s,'BASE',self.root),patch.object(s,'PROJECTS',self.root),
            patch.object(s,'GROUPS',{'BI':'project'}),
            patch.object(s,'ACCOUNTS',{'GREEN':{'home':str(self.green)},'YELLOW':{'home':str(self.yellow)}}),
            patch.object(s,'auth_check',return_value=self.auth),
            patch.object(s,'clear_runtime',return_value={}),
            patch.object(s,'workspace',return_value=self.root/'BI-YELLOW.code-workspace')]
        for p in self.patches: p.start(); self.addCleanup(p.stop)
        self.addCleanup(self.make_writable)

    def make_writable(self):
        for p in self.root.rglob('*'):
            if p.is_file(): p.chmod(0o666)

    def prepare(self):
        return r.prepare('BI','YELLOW',[SID])

    def test_metadata_repair_preserves_transcripts_and_old_registry(self):
        before = (self.g.read_bytes(),self.y.read_bytes(),self.record_path.read_bytes(),self.index.read_bytes())
        plan = self.prepare()
        self.assertEqual(self.record_path.read_bytes(),before[2])
        result = r.apply(plan['manifest'],plan['sha256'])
        self.assertEqual(result['state'],'RECONCILED_SAVED_HOMES_NOT_REOPENED')
        doc = json.loads(self.record_path.read_text())
        self.assertEqual(doc['config_home'],str(self.yellow))
        self.assertEqual(doc['primary_history_path'],str(self.y))
        self.assertEqual(doc['checkpoint']['main_sha256'],s.digest(self.y))
        self.assertIsNone(doc['runtime_observation'])
        self.assertEqual((self.g.read_bytes(),self.y.read_bytes()),before[:2])
        run = Path(plan['manifest']).parent
        self.assertEqual((run/'routing-before'/self.index.name).read_bytes(),before[3])
        self.assertEqual((run/'routing-before'/(SID+'.json')).read_bytes(),before[2])
        self.assertFalse((s.RUNS/'PENDING.json').exists())

    def test_repair_rejects_shorter_or_divergent_target(self):
        for raw in (b'',self.first.replace(b'fixture',b'diverged')):
            self.y.write_bytes(raw)
            with self.assertRaisesRegex(s.Hold,'extend|empty|diverg'):
                self.prepare()
        self.assertEqual(json.loads(self.record_path.read_text())['config_home'],str(self.green))

    def test_changed_target_after_review_does_not_publish_registry(self):
        plan=self.prepare(); before=self.index.read_bytes()
        self.y.write_bytes(self.y.read_bytes()+b'{}\n')
        with self.assertRaisesRegex(s.Hold,'changed|Changed'):
            r.apply(plan['manifest'],plan['sha256'])
        self.assertEqual(self.index.read_bytes(),before)

    def test_missing_or_conflicting_companion_blocks_repair(self):
        extra=self.green/'file-history'/SID/'example@v1';extra.parent.mkdir(parents=True)
        extra.write_bytes(b'original')
        with self.assertRaisesRegex(s.Hold,'companion'):
            self.prepare()
        other=self.yellow/'file-history'/SID/extra.name;other.parent.mkdir(parents=True)
        other.write_bytes(b'conflict')
        with self.assertRaisesRegex(s.Hold,'companion'):
            self.prepare()

    def test_writer_blocks_apply_even_after_clean_prepare(self):
        plan=self.prepare();before=self.index.read_bytes()
        with patch.object(s,'clear_runtime',side_effect=s.Hold('Live writer')):
            with self.assertRaisesRegex(s.Hold,'Live writer'):
                r.apply(plan['manifest'],plan['sha256'])
        self.assertEqual(self.index.read_bytes(),before)

    def test_partial_publish_leaves_pending_marker_and_complete_backup(self):
        plan=self.prepare();h,_=s.dependencies();original=h.publish
        def fail_index(path,value):
            if Path(path)==self.index: raise OSError('fixture publish failure')
            return original(path,value)
        with patch.object(h,'publish',side_effect=fail_index):
            with self.assertRaisesRegex(OSError,'fixture publish failure'):
                r.apply(plan['manifest'],plan['sha256'])
        self.assertTrue((s.RUNS/'PENDING.json').exists())
        self.assertTrue((Path(plan['manifest']).parent/'routing-before'/(SID+'.json')).exists())

    def test_changed_source_after_review_holds(self):
        plan=self.prepare();before=self.index.read_bytes()
        self.g.write_bytes(self.g.read_bytes()+b'{}\n')
        with self.assertRaisesRegex(s.Hold,'changed'):
            r.apply(plan['manifest'],plan['sha256'])
        self.assertEqual(self.index.read_bytes(),before)

    def test_target_only_companion_is_preserved_and_fenced(self):
        extra=self.yellow/'file-history'/SID/'new@v1';extra.parent.mkdir(parents=True)
        extra.write_bytes(b'new companion')
        plan=self.prepare();before=self.index.read_bytes()
        extra.write_bytes(b'changed companion')
        with self.assertRaisesRegex(s.Hold,'changed'):
            r.apply(plan['manifest'],plan['sha256'])
        self.assertEqual(self.index.read_bytes(),before)

    def test_new_companion_after_review_holds(self):
        plan=self.prepare()
        extra=self.yellow/'file-history'/SID/'new@v1';extra.parent.mkdir(parents=True)
        extra.write_bytes(b'new')
        with self.assertRaisesRegex(s.Hold,'membership changed'):
            r.apply(plan['manifest'],plan['sha256'])

    def test_malformed_or_foreign_target_history_holds(self):
        for suffix in (b'{bad}\n', b'{}',
                       b'{"type":"user","sessionId":"22222222-2222-4222-8222-222222222222"}\n'):
            self.y.write_bytes(self.first+suffix)
            with self.assertRaisesRegex(s.Hold,'History needs review'):
                self.prepare()

    def test_target_with_new_missing_reference_holds(self):
        self.historical_exceptions([])
        event={'type':'file-history-snapshot','sessionId':SID,
               'snapshot':{'trackedFileBackups':{'example':{'backupFileName':'absent@v1'}}}}
        self.y.write_bytes(self.first+(json.dumps(event)+'\n').encode())
        with self.assertRaisesRegex(s.Hold,'New missing referenced companion'):
            self.prepare()

    def historical_exceptions(self, missing):
        evidence=self.root/'historical-manifest.json'
        evidence.write_text(json.dumps({'sessions':[{'uuid':SID,'selected_head':{
            'sha256':s.digest(self.g),'missing_backup_refs':missing,'missing_tool_result_refs':[]}}]}))
        self.doc['checkpoint']['main_sha256']=s.digest(self.g)
        self.doc['checkpoint']['manifest']={'path':str(evidence),'sha256':s.digest(evidence)}
        self.record_path.write_text(json.dumps(self.doc))
        index=json.loads(self.index.read_text())
        index['entries'][SID]['sha256']=s.digest(self.record_path)
        self.index.write_text(json.dumps(index))

    def test_known_missing_reference_remains_explicit_after_repair(self):
        event={'type':'file-history-snapshot','sessionId':SID,
               'snapshot':{'trackedFileBackups':{'example':{'backupFileName':'known@v1'}}}}
        raw=self.first+(json.dumps(event)+'\n').encode()
        self.g.write_bytes(raw);self.y.write_bytes(raw)
        self.historical_exceptions(['known@v1'])
        plan=self.prepare()
        manifest=json.loads(Path(plan['manifest']).read_text())
        self.assertEqual(manifest['sessions'][0]['inherited_reference_holds']['backup'],['known@v1'])
        r.apply(plan['manifest'],plan['sha256'])
        self.assertEqual((self.g.read_bytes(),self.y.read_bytes()),(raw,raw))

    def test_identical_history_can_be_registered_without_copy(self):
        self.y.write_bytes(self.first)
        plan=self.prepare()
        manifest=json.loads(Path(plan['manifest']).read_text())
        self.assertEqual(manifest['sessions'][0]['relation'],'IDENTICAL')
        r.apply(plan['manifest'],plan['sha256'])
        self.assertEqual(self.y.read_bytes(),self.first)

    def test_map_refresh_failure_retains_pending_after_registry_publication(self):
        plan=self.prepare();_,maps=s.dependencies()
        with patch.object(maps,'refresh',side_effect=OSError('map fixture failure')):
            with self.assertRaisesRegex(OSError,'map fixture failure'):
                r.apply(plan['manifest'],plan['sha256'])
        self.assertTrue((s.RUNS/'PENDING.json').exists())
        self.assertFalse((Path(plan['manifest']).parent/'receipt.json').exists())
        self.assertEqual(json.loads(self.record_path.read_text())['config_home'],str(self.yellow))

    def test_wrong_account_after_review_holds(self):
        plan=self.prepare()
        with patch.object(s,'auth_check',return_value={**self.auth,'orgId':'other'}):
            with self.assertRaisesRegex(s.Hold,'Account identity changed'):
                r.apply(plan['manifest'],plan['sha256'])

    def test_empty_duplicate_unknown_selection_holds(self):
        for ids in ([],[SID,SID],['22222222-2222-4222-8222-222222222222']):
            with self.assertRaises(s.Hold):
                r.prepare('BI','YELLOW',ids)

    def test_registration_pending_blocks_prepare(self):
        marker=self.root/'Registration-Runs'/'PENDING.json'
        marker.parent.mkdir();marker.write_text('{}')
        with self.assertRaisesRegex(s.Hold,'Pending'):
            self.prepare()

    def test_transfer_pending_blocks_apply(self):
        plan=self.prepare()
        marker=s.RUNS/'PENDING.json';marker.parent.mkdir();marker.write_text('{}')
        with self.assertRaisesRegex(s.Hold,'Pending'):
            r.apply(plan['manifest'],plan['sha256'])

    def test_workspace_and_shared_memory_not_modified(self):
        workspace=self.root/'workspaces'/'BI-YELLOW.code-workspace'
        workspace.parent.mkdir();workspace.write_bytes(b'workspace unchanged')
        memory=self.yellow/'projects'/self.bucket/'memory'/'MEMORY.md'
        memory.parent.mkdir();memory.write_bytes(b'memory unchanged')
        plan=self.prepare()
        r.apply(plan['manifest'],plan['sha256'])
        self.assertEqual(workspace.read_bytes(),b'workspace unchanged')
        self.assertEqual(memory.read_bytes(),b'memory unchanged')

    def test_unreadable_companion_directory_must_not_look_empty(self):
        folder=self.green/'projects'/self.bucket/SID
        folder.mkdir()
        original=os.scandir
        def denied(path):
            if Path(path)==folder:
                raise PermissionError('fixture unreadable companion directory')
            return original(path)
        with patch('os.scandir',side_effect=denied):
            with self.assertRaisesRegex(s.Hold,'inaccessible|unreadable'):
                self.prepare()


if __name__=='__main__': unittest.main()
