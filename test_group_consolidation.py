"""Group membership must not rewrite conversation identity or project provenance."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import switcher as s
import registration as r


class CombinedGroupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / 'home'
        self.accounts = {'YELLOW': {'home': str(self.home), 'email': 'test@example.invalid',
            'plan': 'max', 'workspace_root': str(self.root), 'color': '#D4AC0D', 'theme': 'test'}}
        self.patch = patch.object(s, 'ACCOUNTS', self.accounts)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def row(self, sid, project):
        return {'uuid': sid, 'label': sid, 'config_home': str(self.home),
                'primary_history_path': str(self.home / 'projects' / 'bucket' / (sid + '.jsonl')),
                'project_directory': str(s.PROJECTS / project)}

    def test_bi_selection_includes_vat_without_changing_project(self):
        rows = [self.row('a', 'Analytics'), self.row('b', 'Compliance'),
                self.row('c', 'Finance')]
        view = {'records': {v['uuid']: v for v in rows}}
        selected = s.selected(view, 'BI')
        self.assertEqual([v['uuid'] for v in selected], ['a', 'b'])
        self.assertEqual(selected[1], rows[1])
        with self.assertRaises(s.Hold):
            s.selected(view, 'BI', ['c'])

    def test_catalog_exposes_one_bi_group_with_both_projects(self):
        rows = [self.row('a', 'Analytics'), self.row('b', 'Compliance')]
        maps = SimpleNamespace(load_verified=lambda *args: {'records': {v['uuid']: v for v in rows}})
        with patch.object(s, 'dependencies', return_value=(None, maps)):
            catalog = s.workspace_catalog()['workspaces']
        self.assertEqual([(v['group'], len(v['sessions'])) for v in catalog], [('BI', 2)])

    def test_account_workspace_accepts_both_roots_and_rejects_missing_or_extra(self):
        path = self.root / 'BI-YELLOW.code-workspace'
        folders = [{'path': str(s.PROJECTS / name)} for name in ('Analytics', 'Compliance')]
        doc = {'folders': folders, 'settings': {'window.title': 'YELLOW | BI'}}
        path.write_text(json.dumps(doc))
        self.assertEqual(s.workspace('BI', 'YELLOW'), path)
        for bad in (folders[:1], folders + [{'path': str(self.root / 'unrelated')}], folders + folders[:1]):
            path.write_text(json.dumps(dict(doc, folders=bad)))
            with self.assertRaises(s.Hold):
                s.workspace('BI', 'YELLOW')

    def test_generated_workspace_contains_both_project_roots(self):
        accounts = {'YELLOW': {k: v for k, v in self.accounts['YELLOW'].items() if k != 'workspace_root'}}
        with patch.object(s, 'ACCOUNTS', accounts), patch.object(s, 'BASE', self.root):
            path = s.workspace('BI', 'YELLOW')
        self.assertEqual([v['name'] for v in json.loads(path.read_text())['folders']],
                         ['Analytics', 'Compliance'])

    def test_discovery_and_registration_preserve_each_native_project(self):
        fixtures = [('11111111-1111-4111-8111-111111111111', 'Analytics', 'Analytics'),
                    ('22222222-2222-4222-8222-222222222222', 'Compliance', 'Compliance')]
        for sid, name, bucket in fixtures:
            path = self.home / 'projects' / ('d--NectarDockExample-Projects-' + bucket) / (sid + '.jsonl')
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'type': 'custom-title', 'sessionId': sid, 'customTitle': name}) + '\n')
        items = r.discover('BI', {'records': {}})
        self.assertEqual(len(items), 2)
        rows = r.provisional_rows('BI', 'YELLOW', items)
        self.assertEqual({v['uuid']: v['project_directory'] for v in rows},
                         {sid: str(s.PROJECTS / name) for sid, name, _ in fixtures})


if __name__ == '__main__':
    unittest.main()
