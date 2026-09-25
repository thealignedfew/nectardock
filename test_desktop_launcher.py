import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid
import subprocess


class DesktopLauncherTests(unittest.TestCase):
    def module(self):
        self.assertIsNotNone(importlib.util.find_spec('desktop_launcher'),'Independent launcher is missing')
        import desktop_launcher
        return desktop_launcher

    def test_requests_cannot_supply_commands_paths_or_environment(self):
        d=self.module()
        for value in ({'kind':'shell'},{'kind':'ui','argv':['calc']},
                      {'kind':'workspace','group':'FPA','color':'ORANGE','ids':['../escape']},
                      {'kind':'login','color':'ORANGE','env':{'API_KEY':'secret'}}):
            with self.subTest(value=value),self.assertRaises(RuntimeError):d.validate_request(value)

    def test_validated_uuid_scope_is_preserved(self):
        d=self.module();sid=str(uuid.uuid4())
        value={'kind':'workspace','group':'FPA','color':'ORANGE','ids':[sid]}
        self.assertEqual(d.validate_request(value),value)

    def test_job_bound_worker_never_launches(self):
        d=self.module()
        with patch.object(d,'in_job',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'job'):d.perform({'kind':'ui'})

    def test_probe_receipt_and_duplicate_claim(self):
        d=self.module()
        with tempfile.TemporaryDirectory() as tmp,patch.object(d,'QUEUE',Path(tmp)),patch.object(d,'in_job',return_value=False):
            rid=str(uuid.uuid4());folder=Path(tmp)/rid;folder.mkdir()
            (folder/'request.json').write_text(json.dumps({'kind':'probe'}))
            d.serve(rid)
            result=json.loads((folder/'result.json').read_text())
            self.assertEqual(result['state'],'INDEPENDENT_LAUNCHER_VERIFIED')
            self.assertFalse(result['in_job'])
            with self.assertRaises(FileExistsError):d.serve(rid)

    def test_request_id_cannot_escape_queue(self):
        d=self.module()
        with self.assertRaises(RuntimeError):d.serve('../outside')

    def test_only_scheduler_parent_with_no_kill_job_can_certify_worker(self):
        d=self.module()
        self.assertTrue(hasattr(d,'verify_worker_boundary'),'Missing scheduler boundary verification')
        for parent,service,flags,accepted in [(30,30,0,True),(30,31,0,False),
                (30,30,0x2000,False),(30,30,0x2800,False),(30,0,0,False),
                (30,30,None,False)]:
            with self.subTest(parent=parent,service=service,flags=flags):
                if accepted:d.verify_worker_boundary(parent,service,flags)
                else:
                    with self.assertRaises(RuntimeError):d.verify_worker_boundary(parent,service,flags)

    def test_bound_open_and_login_use_fixed_broker_actions(self):
        d=self.module()
        import switcher as s
        sid=str(uuid.uuid4())
        def external_request(kind,**fields):
            d.validate_request(dict(kind=kind,**fields))
            return {'state':'BROKERED','kind':kind,'scope':fields}
        with patch.object(d,'needs_broker',return_value=True),patch.object(d,'request',side_effect=external_request):
            result=s.open_workspace('FPA','ORANGE',[sid])
            self.assertEqual(result,{'state':'BROKERED','kind':'workspace',
                'scope':{'group':'FPA','color':'ORANGE','ids':[sid]}})
            self.assertEqual(s.launch_login('YELLOW')['kind'],'login')
            with self.assertRaisesRegex(RuntimeError,'independent'):s.launch('FPA','ORANGE')

    def test_existing_job_bound_code_requires_matching_creation_and_profile_receipt(self):
        d=self.module()
        self.assertTrue(hasattr(d,'assert_existing_code_safe'),'Missing VS Code reuse guard')
        with tempfile.TemporaryDirectory() as tmp,patch.object(d,'QUEUE',Path(tmp)),\
                patch.object(d,'in_job',return_value=True),\
                patch.object(d,'process_created',return_value='123'),\
                patch.object(d,'_verified_worker',True):
            with self.assertRaisesRegex(RuntimeError,'existing VS Code'):d.assert_existing_code_safe(42,'E:/profile')
            d.record_code_child(42,'E:/profile')
            d.assert_existing_code_safe(42,'E:/profile')
            with self.assertRaises(RuntimeError):d.assert_existing_code_safe(42,'E:/other')
            with patch.object(d,'process_created',return_value='124'):
                with self.assertRaises(RuntimeError):d.assert_existing_code_safe(42,'E:/profile')

    def test_dispatch_timeout_retains_request_location_and_reconciles_receipt(self):
        d=self.module()
        for completed in (False,True):
            with tempfile.TemporaryDirectory() as tmp,patch.object(d,'QUEUE',Path(tmp)):
                def timed_out(*args,**kwargs):
                    folder=next(Path(tmp).iterdir())
                    if completed:
                        (folder/'result.json').write_text(json.dumps({'state':'PROBE_RESULT','request_id':folder.name}))
                    raise subprocess.TimeoutExpired('powershell.exe',20)
                with patch.object(d.subprocess,'run',side_effect=timed_out):
                    if completed:self.assertEqual(d.request('probe')['state'],'PROBE_RESULT')
                    else:
                        with self.assertRaisesRegex(RuntimeError,'uncertain') as caught:
                            d.request('probe')
                        self.assertIn(str(Path(tmp)),str(caught.exception))
                        self.assertIn(next(Path(tmp).iterdir()).name,str(caught.exception))

    def test_expired_request_is_held_without_execution(self):
        d=self.module()
        import os,time
        with tempfile.TemporaryDirectory() as tmp,patch.object(d,'QUEUE',Path(tmp)):
            rid=str(uuid.uuid4());folder=Path(tmp)/rid;folder.mkdir()
            path=folder/'request.json';path.write_text(json.dumps({'kind':'probe'}))
            os.utime(path,(time.time()-180,time.time()-180))
            result=d.serve(rid)
            self.assertEqual(result['state'],'HELD')
            self.assertIn('Expired',result['error'])

    def test_post_launch_receipt_failure_is_not_reported_as_no_launch(self):
        d=self.module()
        import switcher as s
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'User').mkdir()
            (root/'User/settings.json').write_text(json.dumps({'claudeCode.environmentVariables':
                [{'name':'CLAUDE_CONFIG_DIR','value':tmp}]}))
            with patch.dict(s.ACCOUNTS,{'TEST':{'user_data':tmp,'home':tmp}}),\
                    patch.object(d,'needs_broker',return_value=False),\
                    patch.object(s,'assert_single_vscode_instance',return_value=[]),\
                    patch.object(s,'workspace',return_value=root/'test.code-workspace'),\
                    patch.object(s,'account_env',return_value={}),\
                    patch.object(s.subprocess,'Popen',return_value=SimpleNamespace(pid=42,poll=lambda:None)),\
                    patch.object(d,'record_code_child',side_effect=OSError('receipt unavailable')):
                result=s.launch('FPA','TEST')
            self.assertEqual(result['pid'],42)
            self.assertEqual(result['lifetime'],'VERIFICATION_UNAVAILABLE_AFTER_LAUNCH')
            self.assertIn('Do not retry',result['warning'])


if __name__=='__main__':unittest.main()
