import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import switcher as s


class TransferSafety(unittest.TestCase):
    def setUp(self):
        # Existing backend tests model an independent desktop caller. Broker
        # routing is exercised separately with job-bound process fixtures.
        lifetime=patch('desktop_launcher.needs_broker',return_value=False)
        lifetime.start();self.addCleanup(lifetime.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.h, self.maps = s.dependencies()
        self.obj = self.root/'objects'; self.obj.mkdir()

    def tearDown(self):
        # Read-only snapshots are owned fixture output only.
        for p in self.root.rglob('*'):
            if p.is_file(): p.chmod(0o666)
        self.tmp.cleanup()

    def item(self, name, data):
        p = self.root/name; p.write_bytes(data)
        return s.capture(self.h, p, self.obj, 'fixture')

    def test_exact(self):
        self.assertEqual(s.classify(self.item('a',b'ab\n'),self.item('b',b'ab\n'),True),'KEEP')

    def test_prefix(self):
        self.assertEqual(s.classify(self.item('a',b'ab\ncd\n'),self.item('b',b'ab\n'),True),'ADVANCE_BYTE_PREFIX')

    def test_divergence_held(self):
        a=b'{"type":"user","uuid":"a","text":"different"}\n';b=b'{"type":"user","uuid":"a","text":"original"}\n'
        with self.assertRaises(s.Hold): s.classify(self.item('a',a),self.item('b',b),True)

    def test_explicit_source_survivor_preserves_divergent_target(self):
        source=self.item('source',b'{"type":"user","uuid":"a","text":"yellow"}\n')
        target=self.item('target',b'{"type":"user","uuid":"a","text":"green"}\n')
        action=s.classify(source,target,True,choose_source=True)
        self.assertEqual(action,'CHOOSE_SOURCE_MAIN_SURVIVOR')
        s.mutate(self.h,{'source':source,'before':target,'destination':target['path'],'action':action},self.root,0)
        self.assertEqual(Path(target['path']).read_bytes(),b'{"type":"user","uuid":"a","text":"yellow"}\n')
        self.assertEqual((self.root/'displaced-0.before').read_bytes(),b'{"type":"user","uuid":"a","text":"green"}\n')

    def test_survivor_scope_requires_exactly_one_source_and_other_destination(self):
        row={'uuid':'a','config_home':s.ACCOUNTS['YELLOW']['home']}
        self.assertTrue(s.validate_survivor_scope([row],'GREEN',['a'],'YELLOW'))
        for rows, destination, ids, source in (
            ([row],'GREEN',None,'YELLOW'),
            ([row,row],'GREEN',['a'],'YELLOW'),
            ([row],'YELLOW',['a'],'YELLOW'),
            ([row],'GREEN',['a'],'ORANGE'),
        ):
            with self.subTest(destination=destination,ids=ids,source=source,rows=len(rows)):
                with self.assertRaises(s.Hold):
                    s.validate_survivor_scope(rows,destination,ids,source)

    def test_survivor_manifest_requires_matching_count_and_explicit_acceptance(self):
        row={'uuid':'a','config_home':s.ACCOUNTS['YELLOW']['home']}
        manifest={'color':'GREEN','survivor_source':'YELLOW','selected_uuids':['a'],
                  'survivor_main_count':1,
                  'sessions':[{'record':row,'operations':[{'relative':'main','action':'CHOOSE_SOURCE_MAIN_SURVIVOR'}]}]}
        with self.assertRaises(s.Hold):
            s.validate_survivor_manifest(manifest,[row],False)
        self.assertTrue(s.validate_survivor_manifest(manifest,[row],True))
        manifest['survivor_main_count']=0
        with self.assertRaises(s.Hold):
            s.validate_survivor_manifest(manifest,[row],True)

    def test_source_survivor_refuses_shared_memory_operations(self):
        row={'uuid':'a','config_home':s.ACCOUNTS['YELLOW']['home']}
        manifest={'color':'GREEN','survivor_source':'YELLOW','selected_uuids':['a'],
                  'survivor_main_count':1,
                  'sessions':[{'record':row,'operations':[{'relative':'main','action':'CHOOSE_SOURCE_MAIN_SURVIVOR'}]}],
                  'memory':[{'action':'CREATE','destination':'shared-memory'}]}
        with self.assertRaises(s.Hold):
            s.validate_survivor_manifest(manifest,[row],True)

    def test_target_newer_held(self):
        a=b'{"type":"user","uuid":"a"}\n';b=a+b'{"type":"assistant","uuid":"b"}\n'
        with self.assertRaises(s.Hold): s.classify(self.item('a',a),self.item('b',b),True)

    def test_companion_different_requires_explicit_source_selection(self):
        self.assertEqual(s.classify(self.item('a',b'abcd'),self.item('b',b'ab')),'REVIEW_CURRENT_COMPANION')

    def test_metadata_difference_keeps_exact_native_prefix(self):
        a=b'{"type":"custom-title","customTitle":"new"}\n{"type":"user","uuid":"a","text":"same"}\n{"type":"assistant","uuid":"b"}\n'
        b=b'{"type":"custom-title","customTitle":"old"}\n{"type":"user","uuid":"a","text":"same"}\n'
        self.assertEqual(s.classify(self.item('a',a),self.item('b',b),True),'ADVANCE_NATIVE_PREFIX')

    def test_metadata_only_is_not_native_prefix(self):
        a=b'{"type":"custom-title","customTitle":"new"}\n';b=b'{"type":"custom-title","customTitle":"old"}\n'
        with self.assertRaises(s.Hold): s.classify(self.item('a',a),self.item('b',b),True)

    def test_create_exclusive_does_not_overwrite(self):
        src=self.item('src',b'new'); dst=self.root/'target';dst.write_bytes(b'keep')
        with self.assertRaises(FileExistsError): s.copy_exclusive(src['snapshot'],dst,src['sha256'])
        self.assertEqual(dst.read_bytes(),b'keep')

    def test_atomic_prefix_replace_preserves_displaced(self):
        src=self.item('src',b'old\nnew\n');old=self.item('target',b'old\n')
        op={'source':src,'before':old,'destination':old['path'],'action':'ADVANCE_BYTE_PREFIX'}
        s.mutate(self.h,op,self.root,0)
        self.assertEqual(Path(old['path']).read_bytes(),b'old\nnew\n')
        self.assertEqual((self.root/'displaced-0.before').read_bytes(),b'old\n')
        self.assertEqual(Path(src['path']).read_bytes(),b'old\nnew\n')

    def test_mutated_source_stops_before_target_write(self):
        src=self.item('src',b'new');old=self.item('target',b'old')
        Path(src['path']).write_bytes(b'changed')
        with self.assertRaises(s.Hold): s.mutate(self.h,{'source':src,'before':old,'destination':old['path'],'action':'ADVANCE_BYTE_PREFIX'},self.root,0)
        self.assertEqual(Path(old['path']).read_bytes(),b'old')

    def test_memory_conflict_preserves_target(self):
        src=self.item('src',b'new');old=self.item('target',b'old')
        s.mutate(self.h,{'source':src,'before':old,'destination':old['path'],'action':'KEEP_TARGET_MEMORY_VARIANT'},self.root,0)
        self.assertEqual(Path(old['path']).read_bytes(),b'old')
        self.assertEqual(Path(src['snapshot']).read_bytes(),b'new')

    def test_wrong_destination_plan_held(self):
        with self.assertRaises(s.Hold): s.validate_auth('ORANGE',{'loggedIn':True,'email':'bi@example.invalid','subscriptionType':'max','configDirectory':s.ACCOUNTS['ORANGE']['home']})

    def test_wrong_email_held(self):
        with self.assertRaises(s.Hold): s.validate_auth('GREEN',{'loggedIn':True,'email':'bi@example.invalid','subscriptionType':'team','configDirectory':s.ACCOUNTS['GREEN']['home']})

    def test_yellow_exact_account_passes(self):
        s.validate_auth('YELLOW', {'loggedIn': True, 'email': 'yellow@example.invalid',
                                 'subscriptionType': 'max', 'configDirectory': s.ACCOUNTS['YELLOW']['home']})

    def test_yellow_other_max_account_held(self):
        with self.assertRaises(s.Hold):
            s.validate_auth('YELLOW', {'loggedIn': True, 'email': 'bi@example.invalid',
                                      'subscriptionType': 'max', 'configDirectory': s.ACCOUNTS['YELLOW']['home']})

    def test_yellow_wrong_home_held(self):
        with self.assertRaises(s.Hold):
            s.validate_auth('YELLOW', {'loggedIn': True, 'email': 'yellow@example.invalid',
                                      'subscriptionType': 'max', 'configDirectory': s.ACCOUNTS['ORANGE']['home']})

    def test_login_launch_uses_only_selected_profile_and_requires_later_verification(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'must-not-propagate'}), \
             patch.object(s.subprocess, 'Popen') as opened:
            opened.return_value.pid = 42
            result = s.launch_login('PURPLE')
        argv = opened.call_args.args[0]
        env = opened.call_args.kwargs['env']
        self.assertIn('auth login --claudeai --email bi@example.invalid', argv[-1])
        self.assertEqual(env['CLAUDE_CONFIG_DIR'], s.ACCOUNTS['PURPLE']['home'].replace('/', '\\'))
        self.assertNotIn('ANTHROPIC_API_KEY', env)
        self.assertEqual(result['state'], 'LOGIN_STARTED_NOT_VERIFIED')
        self.assertEqual(result['account'], 'PURPLE')
        self.assertEqual(result['plan'], 'max')

    def test_progress_event_uses_machine_separable_stderr_marker(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            s.emit_progress('PREPARE session 1/2')
        self.assertEqual(output.getvalue(), 'SWITCHBOARD_PROGRESS PREPARE session 1/2\n')

    def test_account_workspace_wrong_project_held(self):
        path = self.root/'FPA-YELLOW.code-workspace'
        path.write_text(json.dumps({'folders': [{'path': str(s.PROJECTS/s.GROUPS['GCP'])}],
                                    'settings': {'window.title': 'YELLOW | FPA'}}))
        account = dict(s.ACCOUNTS['YELLOW'], workspace_root=str(self.root))
        with patch.dict(s.ACCOUNTS, {'YELLOW': account}):
            with self.assertRaises(s.Hold): s.workspace('FPA', 'YELLOW')

    def test_account_workspace_exact_project_passes(self):
        path = self.root/'FPA-YELLOW.code-workspace'
        path.write_text(json.dumps({'folders': [{'path': str(s.PROJECTS/s.GROUPS['FPA'])}],
                                    'settings': {'window.title': 'YELLOW | FPA'}}))
        account = dict(s.ACCOUNTS['YELLOW'], workspace_root=str(self.root))
        with patch.dict(s.ACCOUNTS, {'YELLOW': account}):
            self.assertEqual(s.workspace('FPA', 'YELLOW'), path)

    def test_path_escape_held(self):
        with self.assertRaises(s.Hold): s.destination(self.root,'bucket','id','sidecars/../../elsewhere')

    def test_live_writer_held(self):
        row={'uuid':'id','label':'Working'}
        with patch.object(s,'runtime_gate',return_value=({},[{'uuid':'id'}])):
            with self.assertRaises(s.Hold): s.clear_runtime(self.h,[row])

    def support_fixture(self):
        exe='C:/Users/ExampleUser/.vscode/extensions/anthropic.claude-code-2.1.280-win32-x64/resources/native-binary/claude.exe'
        return {'pid':42,'process_start_filetime':'123'}, {'exe':exe,'command':'"'+exe+'" --claude-in-chrome-mcp','start':'123'}

    def test_exact_chrome_helper_is_not_history_writer(self):
        item,evidence=self.support_fixture()
        self.assertTrue(s.support_only(item,evidence))

    def test_helper_pid_reuse_stays_unknown(self):
        item,evidence=self.support_fixture();evidence['start']='124'
        self.assertFalse(s.support_only(item,evidence))

    def test_helper_extra_arguments_stay_unknown(self):
        item,evidence=self.support_fixture();evidence['command']+=' --resume=id'
        self.assertFalse(s.support_only(item,evidence))

    def test_unknown_claude_mode_is_not_excluded(self):
        item,evidence=self.support_fixture();evidence['command']=evidence['command'].replace('--claude-in-chrome-mcp','--output-format stream-json')
        self.assertFalse(s.support_only(item,evidence))

    def test_unreviewed_executable_is_not_excluded(self):
        item,evidence=self.support_fixture();evidence['exe']='C:/other/claude.exe'
        self.assertFalse(s.support_only(item,evidence))

    def test_unreviewed_version_is_not_excluded(self):
        item,evidence=self.support_fixture()
        evidence['exe']=evidence['exe'].replace('2.1.280','2.1.281')
        evidence['command']=evidence['command'].replace('2.1.280','2.1.281')
        self.assertFalse(s.support_only(item,evidence))

    def test_closed_unverified_helper_stays_unknown(self):
        item,evidence=self.support_fixture()
        self.assertFalse(s.support_only(item,None))

    def test_native_foreign_uuid_held(self):
        p=self.root/'foreign.jsonl';p.write_text(json.dumps({'type':'user','sessionId':'different'})+'\n')
        with self.assertRaises(s.Hold): s.capture(self.h,p,self.obj,'expected',True)

    def test_incomplete_native_tail_held(self):
        p=self.root/'incomplete.jsonl';p.write_text('{"type":"user"}')
        with self.assertRaises(s.Hold): s.capture(self.h,p,self.obj,'expected',True)

    def attachment_fixture(self, suffix=b''):
        p=self.root/'attachment.jsonl'
        records=[{'type':'user','uuid':'a','sessionId':'fixture',
                  'message':{'content':'z'*4096+' fixture/tool-results/known.txt'}},
                 {'type':'file-history-snapshot','snapshot':{'trackedFileBackups':{'x':{'backupFileName':'undo-v1'}}}}]
        data=b''.join(json.dumps(v).encode()+b'\n' for v in records)+suffix
        p.write_bytes(data);old=self.root/'registered.jsonl';old.write_bytes(data)
        return p, {'main_sha256':s.digest(old),'snapshot_path':str(old)}

    def test_large_registered_attachment_preserves_bytes_and_references(self):
        p,cp=self.attachment_fixture();original=p.read_bytes()
        with patch.object(self.h,'MAX_RECORD_BYTES',128), patch.object(s,'available_memory',return_value=8*1024**3):
            item=s.capture(self.h,p,self.obj,'fixture',True,cp)
            self.assertEqual(self.h.MAX_RECORD_BYTES,128)
        self.assertEqual(p.read_bytes(),original)
        self.assertEqual(Path(item['snapshot']).read_bytes(),original)
        self.assertEqual(item['tool_result_refs'],['known.txt'])
        self.assertEqual(item['backup_refs'],['undo-v1'])
        self.assertEqual(item['complete_lines'],2)
        self.assertEqual(item['standard_parser_oversized_records'],1)
        self.assertEqual(item['large_record_validation']['checkpoint_sha256'],cp['main_sha256'])

    def test_large_record_without_registered_checkpoint_stays_held(self):
        p,cp=self.attachment_fixture()
        with patch.object(self.h,'MAX_RECORD_BYTES',128):
            with self.assertRaisesRegex(s.Hold,'oversized_records'):
                s.capture(self.h,p,self.obj,'fixture',True)

    def test_changed_large_history_stays_held(self):
        p,cp=self.attachment_fixture();p.write_bytes(p.read_bytes()+b'{"type":"user"}\n')
        with patch.object(self.h,'MAX_RECORD_BYTES',128):
            with self.assertRaisesRegex(s.Hold,'differs from its registered checkpoint'):
                s.capture(self.h,p,self.obj,'fixture',True,cp)

    def test_large_history_malformed_record_stays_held(self):
        p,cp=self.attachment_fixture(b'{not-json}\n')
        with patch.object(self.h,'MAX_RECORD_BYTES',128), patch.object(s,'available_memory',return_value=8*1024**3):
            with self.assertRaisesRegex(s.Hold,'malformed_complete_lines'):
                s.capture(self.h,p,self.obj,'fixture',True,cp)

    def test_large_history_foreign_uuid_stays_held(self):
        p,cp=self.attachment_fixture(b'{"sessionId":"other"}\n')
        with patch.object(self.h,'MAX_RECORD_BYTES',128), patch.object(s,'available_memory',return_value=8*1024**3):
            with self.assertRaisesRegex(s.Hold,'foreign_session_ids'):
                s.capture(self.h,p,self.obj,'fixture',True,cp)

    def test_large_history_partial_tail_stays_held(self):
        p,cp=self.attachment_fixture(b'{"type":"user"}')
        with patch.object(self.h,'MAX_RECORD_BYTES',128), patch.object(s,'available_memory',return_value=8*1024**3):
            with self.assertRaisesRegex(s.Hold,'unterminated_tail_bytes'):
                s.capture(self.h,p,self.obj,'fixture',True,cp)

    def test_large_record_memory_budget_is_enforced(self):
        p,cp=self.attachment_fixture()
        with patch.object(self.h,'MAX_RECORD_BYTES',128), patch.object(s,'available_memory',return_value=1024**3):
            with self.assertRaisesRegex(s.Hold,'4 GiB'):
                s.capture(self.h,p,self.obj,'fixture',True,cp)

    def test_large_record_byte_bound_is_enforced(self):
        p,cp=self.attachment_fixture()
        with patch.object(self.h,'MAX_RECORD_BYTES',128), patch.object(s,'available_memory',return_value=8*1024**3), patch.object(s,'LARGE_RECORD_BYTES',2048):
            with self.assertRaisesRegex(s.Hold,'Record exceeds'):
                s.capture(self.h,p,self.obj,'fixture',True,cp)

    def test_large_history_byte_bound_is_enforced(self):
        p,cp=self.attachment_fixture()
        with patch.object(self.h,'MAX_RECORD_BYTES',128), patch.object(s,'LARGE_HISTORY_BYTES',2048):
            with self.assertRaisesRegex(s.Hold,'History exceeds'):
                s.capture(self.h,p,self.obj,'fixture',True,cp)

    def test_selected_ids_cannot_cross_workspace(self):
        row={'uuid':'a','label':'a','config_home':s.ACCOUNTS['ORANGE']['home'],'primary_history_path':'E:/x/a.jsonl','project_directory':str(s.PROJECTS/s.GROUPS['FPA'])}
        with self.assertRaises(s.Hold): s.selected({'records':{'a':row}},'FPA',['b'])

    def test_open_workspace_validates_account_before_detached_launch(self):
        auth={'loggedIn':True,'email':'green@example.invalid','subscriptionType':'team',
              'configDirectory':s.ACCOUNTS['GREEN']['home']}
        row={'uuid':'a','label':'BI-A','config_home':s.ACCOUNTS['GREEN']['home']}
        maps=SimpleNamespace(load_verified=lambda *_: {})
        with patch.object(s,'dependencies',return_value=(None,maps)), \
             patch.object(s,'BASE',self.root), \
             patch.object(s,'selected',return_value=[row]), \
             patch.object(s,'restored_workspace_tabs',return_value=[]), \
             patch.object(s,'auth_check',return_value=auth) as checked, patch.object(s,'launch') as launched:
            result=s.open_workspace('BI','GREEN',['a'])
        checked.assert_called_once_with('GREEN')
        launched.assert_called_once_with('BI','GREEN')
        self.assertEqual(result['state'],'WORKSPACE_LAUNCH_REQUESTED')
        self.assertEqual(result['runtime_adoption'],'NOT_CLAIMED')

    def test_open_destination_workspace_refuses_unmoved_selected_history(self):
        row={'uuid':'a','label':'DEMO-ANALYST','config_home':s.ACCOUNTS['GREEN']['home']}
        maps=SimpleNamespace(load_verified=lambda *_: {})
        with patch.object(s,'dependencies',return_value=(None,maps)), \
             patch.object(s,'selected',return_value=[row]), \
             patch.object(s,'restored_workspace_tabs',return_value=[]), \
             patch.object(s,'auth_check') as checked, patch.object(s,'launch') as launched:
            with self.assertRaisesRegex(s.Hold,'does not transfer history'):
                s.open_workspace('FPA','ORANGE',['a'])
        checked.assert_not_called()
        launched.assert_not_called()

    def test_restored_workspace_tabs_reads_claude_panel_state_only_for_exact_workspace(self):
        profile=self.root/'profile'; storage=profile/'User'/'workspaceStorage'/'abc';storage.mkdir(parents=True)
        unrelated=storage.parent/'folder';unrelated.mkdir()
        (unrelated/'workspace.json').write_text(json.dumps({'folder':'file:///other'}))
        workspace=self.root/'FPA-ORANGE.code-workspace';workspace.write_text('{}')
        (storage/'workspace.json').write_text(json.dumps({'workspace':workspace.as_uri()}))
        db=sqlite3.connect(storage/'state.vscdb')
        db.execute('create table ItemTable (key text primary key,value blob)')
        db.execute('insert into ItemTable values (?,?)',('Anthropic.claude-code',json.dumps({
            'panelTabSessions':[{'sessionId':'a','title':'DEMO-ANALYST'}, {'sessionId':'b','title':'DEMO-REVIEWER'}]})))
        db.commit();db.close()
        self.assertEqual([r['sessionId'] for r in s.restored_workspace_tabs(profile,workspace)],['a','b'])
        self.assertEqual(s.restored_workspace_tabs(profile,self.root/'other.code-workspace'),[])

    def test_open_refuses_stale_restored_claude_tabs_even_when_selected_history_moved(self):
        row={'uuid':'a','label':'DEMO-ANALYST','config_home':s.ACCOUNTS['ORANGE']['home']}
        stale={'uuid':'b','label':'DEMO-REVIEWER','config_home':s.ACCOUNTS['YELLOW']['home']}
        maps=SimpleNamespace(load_verified=lambda *_: {'records':{'a':row,'b':stale}})
        with patch.object(s,'dependencies',return_value=(None,maps)), \
             patch.object(s,'selected',return_value=[row]), \
             patch.object(s,'restored_workspace_tabs',return_value=[{'sessionId':'a','title':'DEMO-ANALYST'}, {'sessionId':'b','title':'DEMO-REVIEWER'}]), \
             patch.object(s,'auth_check') as checked, patch.object(s,'launch') as launched:
            with self.assertRaisesRegex(s.Hold,'saved VS Code tabs.*DEMO-REVIEWER'):
                s.open_workspace('FPA','ORANGE',['a'])
        checked.assert_not_called()
        launched.assert_not_called()

    def test_open_refuses_restored_tabs_outside_exact_selection(self):
        row={'uuid':'a','label':'DEMO-ANALYST','config_home':s.ACCOUNTS['ORANGE']['home']}
        other={'uuid':'b','label':'DEMO-PLANNER','config_home':s.ACCOUNTS['ORANGE']['home']}
        maps=SimpleNamespace(load_verified=lambda *_: {'records':{'a':row,'b':other}})
        with patch.object(s,'dependencies',return_value=(None,maps)), \
             patch.object(s,'selected',return_value=[row]), \
             patch.object(s,'restored_workspace_tabs',return_value=[{'sessionId':'b','title':'DEMO-PLANNER'}]), \
             patch.object(s,'auth_check') as checked, patch.object(s,'launch') as launched:
            with self.assertRaisesRegex(s.Hold,'saved VS Code tabs outside the selection.*DEMO-PLANNER'):
                s.open_workspace('FPA','ORANGE',['a'])
        checked.assert_not_called()
        launched.assert_not_called()

    def reference_fixture(self):
        source=self.item('history',b'{"type":"user","uuid":"a"}\n')
        source.update(backup_refs=[],tool_result_refs=['known.txt'])
        manifest=self.root/'previous.json'
        manifest.write_text(json.dumps({'sessions':[{'uuid':'a','selected_head':{'sha256':source['sha256'],'missing_backup_refs':[],'missing_tool_result_refs':['known.txt']}}]}))
        row={'uuid':'a','label':'fixture','checkpoint':{'snapshot_path':source['snapshot'],'main_sha256':source['sha256'],'manifest':{'path':str(manifest),'sha256':s.digest(manifest)}}}
        return row,source

    def test_exact_historical_missing_reference_carried(self):
        row,source=self.reference_fixture()
        self.assertEqual(s.inherited_reference_holds(row,source,set(),set())['tool_result'],['known.txt'])

    def test_new_missing_reference_is_not_covered_by_old_hold(self):
        row,source=self.reference_fixture();source['tool_result_refs'].append('new.txt')
        with self.assertRaises(s.Hold): s.inherited_reference_holds(row,source,set(),set())


if __name__=='__main__': unittest.main(verbosity=2)
