import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import consolidate_groups as c
import switcher as s
import test_reconcile_home as fixtures


class WorkspaceConsolidationTests(unittest.TestCase):
    def test_workspace_change_preserves_settings_and_requires_expected_old_root(self):
        doc = {'folders': [{'path': str(s.PROJECTS / 'Analytics')}],
               'settings': {'window.title': 'YELLOW | BI', 'custom.setting': 7}, 'extensions': {}}
        before = copy.deepcopy(doc)
        result = c.combined_workspace(doc)
        self.assertEqual([p['name'] for p in result['folders']], ['Analytics', 'Compliance'])
        self.assertEqual(result['settings'], doc['settings'])
        self.assertEqual(doc, before)
        self.assertEqual(c.combined_workspace(result), result)
        doc['folders'] = [{'path': str(s.PROJECTS / 'Finance')}]
        with self.assertRaises(s.Hold):
            c.combined_workspace(doc)

    def test_workspace_route_change_does_not_relabel_history_or_account(self):
        old = {'workspace_path': 'old', 'updated_utc': 'old-time', 'uuid': 'example',
               'project_directory': 'Compliance', 'config_home': 'same-home',
               'primary_history_path': 'same-history', 'checkpoint': {'keep': 'exact'},
               'account_identity': {'keep': 'exact'}}
        result = c.rerouted_record(old, 'new', 'new-time')
        self.assertEqual(result['workspace_path'], 'new')
        self.assertEqual({k: v for k, v in result.items() if k not in ('workspace_path', 'updated_utc')},
                         {k: v for k, v in old.items() if k not in ('workspace_path', 'updated_utc')})
        self.assertEqual(old['workspace_path'], 'old')


class WorkspaceTransactionTests(unittest.TestCase):
    def setUp(self):
        fixtures.ReconcileHomeTests.setUp(self)
        accounts = {'GREEN': {'home': str(self.green), 'workspace_root': str(self.root),
                              'user_data': str(self.root / 'userdata')}}
        for p in (patch.object(s, 'ACCOUNTS', accounts),
                  patch.object(s, 'GROUPS', {'BI': ('Analytics', 'Compliance')})):
            p.start(); self.addCleanup(p.stop)
        self.bi = self.root / 'BI-GREEN.code-workspace'
        self.vat = self.root / 'VAT-GREEN.code-workspace'
        self.bi.write_text(json.dumps({'folders': [{'path': str(self.root / 'Analytics')}]}))
        self.vat.write_text(json.dumps({'folders': [{'path': str(self.root / 'Compliance')}]}))
        self.doc.update(project_directory=str(self.root / 'Compliance'), workspace_path=str(self.vat))
        self.record_path.write_text(json.dumps(self.doc))
        idx = json.loads(self.index.read_text())
        idx['entries'][fixtures.SID]['sha256'] = s.digest(self.record_path)
        self.index.write_text(json.dumps(idx))

    def make_writable(self):
        fixtures.ReconcileHomeTests.make_writable(self)

    def test_apply_preserves_history_and_account_and_archives_only_workspace(self):
        before = (self.g.read_bytes(), self.vat.read_bytes(), self.bi.read_bytes(), self.record_path.read_bytes())
        result = c.consolidate(True)
        run = Path(result['run'])
        self.assertEqual(self.g.read_bytes(), before[0])
        doc = json.loads(self.record_path.read_text())
        self.assertEqual(doc['config_home'], str(self.green))
        self.assertEqual(doc['primary_history_path'], str(self.g))
        self.assertEqual(doc['workspace_path'], str(self.bi))
        self.assertFalse(self.vat.exists())
        self.assertEqual((run / 'VAT-GREEN.code-workspace.retired').read_bytes(), before[1])
        manifest = json.loads((run / 'manifest.json').read_text())
        self.assertEqual(Path(manifest['before'][str(self.bi)]['backup']).read_bytes(), before[2])
        self.assertFalse((s.RUNS / 'PENDING.json').exists())
        self.assertEqual(c.consolidate(True)['update_paths'], [])

    def test_partial_publication_retains_pending_and_recovery_bytes(self):
        h, _ = s.dependencies()
        original = h.publish
        def fail_record(path, doc):
            if Path(path) == self.record_path:
                raise OSError('injected record failure')
            return original(path, doc)
        before = self.record_path.read_bytes()
        with patch.object(h, 'publish', side_effect=fail_record):
            with self.assertRaisesRegex(OSError, 'injected record'):
                c.consolidate(True)
        pending = s.RUNS / 'PENDING.json'
        self.assertTrue(pending.exists())
        self.assertEqual(self.record_path.read_bytes(), before)
        self.assertTrue(self.vat.exists())
        with self.assertRaisesRegex(s.Hold, 'Pending'):
            c.consolidate(True)

    def test_saved_vat_tabs_hold_before_any_publication(self):
        before = self.bi.read_bytes()
        with patch.object(s, 'restored_workspace_tabs', return_value=[{'sessionId': fixtures.SID}]):
            with self.assertRaisesRegex(s.Hold, 'saved tabs'):
                c.consolidate(True)
        self.assertEqual(self.bi.read_bytes(), before)
        self.assertFalse((s.RUNS / 'PENDING.json').exists())

    def test_workspace_edit_after_planning_is_not_overwritten(self):
        h, _ = s.dependencies()
        original = h.now
        def edit_after_read():
            doc = json.loads(self.bi.read_text())
            doc['settings'] = {'user.concurrent.edit': True}
            self.bi.write_text(json.dumps(doc))
            return original()
        with patch.object(h, 'now', side_effect=edit_after_read):
            with self.assertRaisesRegex(s.Hold, 'changed'):
                c.consolidate(True)
        self.assertTrue(json.loads(self.bi.read_text())['settings']['user.concurrent.edit'])
        self.assertFalse((s.RUNS / 'PENDING.json').exists())

    def test_tabs_appearing_after_plan_hold_before_publication(self):
        before = self.bi.read_bytes()
        with patch.object(s, 'restored_workspace_tabs', side_effect=[[], [{'sessionId': fixtures.SID}]]):
            with self.assertRaisesRegex(s.Hold, 'saved tabs'):
                c.consolidate(True)
        self.assertEqual(self.bi.read_bytes(), before)
        self.assertTrue(self.vat.exists())
        self.assertFalse((s.RUNS / 'PENDING.json').exists())

    def test_already_combined_destination_is_revalidated_before_rerouting(self):
        self.bi.write_text(json.dumps(c.combined_workspace(json.loads(self.bi.read_text()))))
        self.test_workspace_edit_after_planning_is_not_overwritten()


if __name__ == '__main__':
    unittest.main()
