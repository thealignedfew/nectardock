import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import registration as r


SID = '33333333-3333-4333-8333-333333333333'


class RegistrationSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.green = self.root / 'green'
        self.orange = self.root / 'orange'
        self.bucket = 'd--NectarDockExample-Projects-Finance'
        self.main = self.green / 'projects' / self.bucket / (SID + '.jsonl')
        self.main.parent.mkdir(parents=True)
        self.main.write_text(json.dumps({'type':'custom-title','sessionId':SID,
                                         'customTitle':'DEMO-ANALYST'}) + '\n')
        self.accounts = {'GREEN':{'home':str(self.green),'email':'green@example.invalid','plan':'team'},
                         'ORANGE':{'home':str(self.orange),'email':'bi@example.invalid','plan':'team'}}
        self.index = self.root / 'CURRENT-SESSION-HOMES.json'
        self.index.write_text(json.dumps({'schema':'session-current-homes-index/v1',
            'updated_utc':'2026-09-22T00:00:00+00:00','entries':{}}))
        self.patches = [patch.object(r,'RUNS',self.root/'registration-runs'),
                        patch.object(r.s,'ACCOUNTS',self.accounts),patch.object(r.s,'INDEX',self.index),
                        patch.object(r.s,'RUNS',self.root/'runs'),patch.object(r.s,'MAP',self.root/'map.md'),
                        patch.object(r.s,'MAP_HISTORY',self.root/'history.json')]
        for p in self.patches:p.start()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        for path in self.root.rglob('*'):
            if path.is_file():path.chmod(0o666)
        self.temp.cleanup()

    def test_discovery_reports_only_unregistered_exact_home_and_native_title(self):
        self.orange_main = self.orange / 'projects' / self.bucket / (SID + '.jsonl')
        self.orange_main.parent.mkdir(parents=True)
        self.orange_main.write_bytes(self.main.read_bytes())
        items = r.discover('FPA', {'records':{}})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['uuid'], SID)
        self.assertEqual(items[0]['label'], 'DEMO-ANALYST')
        self.assertEqual(items[0]['homes'], ['GREEN','ORANGE'])
        self.assertEqual(r.discover('FPA', {'records':{SID:{}}}), [])

    def test_prepare_holds_duplicate_account_copy_instead_of_guessing_owner(self):
        other = self.orange / 'projects' / self.bucket / (SID + '.jsonl')
        other.parent.mkdir(parents=True)
        other.write_bytes(self.main.read_bytes())
        with patch.object(r,'verified_view',return_value={'index':json.loads(self.index.read_text()),
                'records':{},'index_sha256':r.s.digest(self.index)}):
            with self.assertRaisesRegex(r.s.Hold, 'multiple account homes'):
                r.prepare('FPA','GREEN',[SID])

    def test_apply_holds_if_source_appended_after_review(self):
        with patch.object(r,'verified_view',return_value={'index':json.loads(self.index.read_text()),'records':{},'index_sha256':r.s.digest(self.index)}), \
             patch.object(r,'clear_runtime',return_value=None), \
             patch.object(r,'account_identity',return_value={'email':'green@example.invalid','orgId':'org','subscriptionType':'team'}):
            prepared = r.prepare('FPA','GREEN',[SID])
            self.main.write_text(self.main.read_text() + json.dumps({'type':'user','sessionId':SID})+'\n')
            with self.assertRaisesRegex(r.s.Hold, 'changed'):
                r.apply(prepared['manifest'],prepared['sha256'])
        self.assertEqual(json.loads(self.index.read_text())['entries'], {})

    def test_reviewed_registration_publishes_exact_green_record_without_moving_history(self):
        view={'index':json.loads(self.index.read_text()),'records':{},'index_sha256':r.s.digest(self.index)}
        _,maps=r.s.dependencies()
        auth={'email':'green@example.invalid','orgId':'org','subscriptionType':'team'}
        with patch.object(r,'verified_view',return_value=view), \
             patch.object(r,'clear_runtime',return_value=None), \
             patch.object(r,'account_identity',return_value=auth), \
             patch.object(r.s,'workspace',return_value=self.root/'FPA-GREEN.code-workspace'), \
             patch.object(maps,'refresh',return_value={'status':'CURRENT'}):
            prepared=r.prepare('FPA','GREEN',[SID])
            result=r.apply(prepared['manifest'],prepared['sha256'])
        self.assertEqual(result['state'],'REGISTERED_SAVED_HOMES_NOT_REOPENED')
        entry=json.loads(self.index.read_text())['entries'][SID]
        record=json.loads(Path(entry['path']).read_text())
        self.assertEqual(record['config_home'],str(self.green))
        self.assertEqual(record['primary_history_path'],str(self.main))
        self.assertEqual(record['checkpoint']['main_sha256'],r.s.digest(self.main))
        self.assertEqual(record['label'],'DEMO-ANALYST')
        self.assertFalse((self.root/'runs'/'PENDING.json').exists())
        self.assertFalse((self.orange/'projects'/self.bucket/(SID+'.jsonl')).exists())


if __name__ == '__main__': unittest.main()
