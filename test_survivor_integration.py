"""Real temporary histories and manifests; only runtime/auth external boundaries stubbed."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import survivor_review as r
import test_reconcile_home as fixture


class SurvivorIntegration(unittest.TestCase):
    make_writable=fixture.ReconcileHomeTests.make_writable

    def setUp(self):
        fixture.ReconcileHomeTests.setUp(self)
        for account in r.s.ACCOUNTS.values():account['email']='owner@example.invalid'
        self.y.write_bytes(self.first.replace(b'fixture',b'destination branch'))

    def compare(self):return r.compare('BI','YELLOW',fixture.SID)

    def prepare(self):
        data=self.compare()
        return r.prepare('BI','YELLOW',fixture.SID,data['token'])

    def test_real_prepare_and_apply_preserve_both_originals(self):
        source,target=self.g.read_bytes(),self.y.read_bytes()
        plan=self.prepare()
        self.assertEqual(self.y.read_bytes(),target)
        manifest=json.loads(Path(plan['manifest']).read_text())
        main=next(op for op in manifest['sessions'][0]['operations'] if op['relative']=='main')
        self.assertEqual(Path(main['source']['snapshot']).read_bytes(),source)
        self.assertEqual(Path(main['before']['snapshot']).read_bytes(),target)
        with patch.object(r.s,'launch') as launch:
            result=r.s.apply(plan['manifest'],plan['sha256'],accept_survivor=True)
        self.assertEqual(result['state'],'SWITCH_COMPLETE_SAVED_HISTORIES_READY')
        self.assertEqual(self.y.read_bytes(),source);self.assertEqual(self.g.read_bytes(),source)
        self.assertFalse(launch.called)

    def test_changed_comparison_cannot_prepare(self):
        token=self.compare()['token'];before=self.index.read_bytes()
        self.y.write_bytes(self.y.read_bytes()+b'{}\n')
        with self.assertRaisesRegex(r.s.Hold,'changed'):r.prepare('BI','YELLOW',fixture.SID,token)
        self.assertEqual(self.index.read_bytes(),before)

    def test_target_only_companion_change_after_review_holds(self):
        extra=self.yellow/'file-history'/fixture.SID/'example@v1';extra.parent.mkdir(parents=True)
        extra.write_bytes(b'old')
        plan=self.prepare();before=(self.y.read_bytes(),self.index.read_bytes())
        extra.write_bytes(b'new')
        with self.assertRaisesRegex(r.s.Hold,'companion.*changed|changed.*companion'):
            r.s.apply(plan['manifest'],plan['sha256'],accept_survivor=True)
        self.assertEqual((self.y.read_bytes(),self.index.read_bytes()),before)

    def test_live_writer_blocks_comparison(self):
        with patch.object(r.s,'clear_runtime',side_effect=r.s.Hold('Live writer')):
            with self.assertRaisesRegex(r.s.Hold,'Live writer'):self.compare()


if __name__=='__main__':unittest.main()
