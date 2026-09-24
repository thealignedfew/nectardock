import json
from pathlib import Path
import tempfile
import unittest

import account_inventory as inv


class CrossAccountInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.runs = self.root / 'runs'
        for home in (self.source, self.target):
            (home / 'skills').mkdir(parents=True)
        (self.source / 'skills' / 'new-skill').mkdir()
        (self.source / 'skills' / 'new-skill' / 'SKILL.md').write_text('source skill\n', encoding='utf-8')
        (self.target / 'skills' / 'target-only').mkdir()
        (self.target / 'skills' / 'target-only' / 'SKILL.md').write_text('keep me\n', encoding='utf-8')
        self.source_settings = {
            'effortLevel': 'xhigh', 'model': 'source-only-model',
            'permissions': {'allow': ['Read', 'Bash(git status:*)'], 'deny': ['Bash(rm:*)'],
                            'ask': [], 'defaultMode': 'default', 'additionalDirectories': []},
        }
        self.target_settings = {
            'effortLevel': 'high', 'model': 'target-only-model',
            'permissions': {'allow': ['Read'], 'deny': ['Bash(rm:*)'],
                            'ask': [], 'defaultMode': 'plan', 'additionalDirectories': []},
        }
        self.source_claude = {'mcpServers': {
            'safe-server': {'type': 'stdio', 'command': 'python', 'args': ['server.py']},
            'credentialed-server': {'type': 'stdio', 'command': 'python',
                                    'args': ['private.py'], 'env': {'TOKEN': 'never-expose'}},
        }, 'accountState': {'keep': 'source'}}
        self.target_claude = {'mcpServers': {}, 'accountState': {'keep': 'target'}}
        self.write(self.source / 'settings.json', self.source_settings)
        self.write(self.target / 'settings.json', self.target_settings)
        self.write(self.source / '.claude.json', self.source_claude)
        self.write(self.target / '.claude.json', self.target_claude)
        self.accounts = {'GREEN': {'home': str(self.source), 'email': 'source@example.com', 'plan': 'team'},
                         'YELLOW': {'home': str(self.target), 'email': 'target@example.com', 'plan': 'max'}}
        self.shared = {'effortLevel'}

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')

    def test_compare_reports_differences_without_secret_values(self):
        result = inv.compare(self.accounts, 'GREEN', 'YELLOW', self.shared)
        serialized = json.dumps(result)
        self.assertIn('credentialed-server', serialized)
        self.assertNotIn('never-expose', serialized)
        self.assertEqual(result['settings']['different'], ['effortLevel', 'model', 'permissions'])
        self.assertEqual(result['skills']['missing_in_target'], ['new-skill/SKILL.md'])
        self.assertEqual(result['mcps']['unsafe_to_copy'], ['credentialed-server'])

    def test_prepare_and_apply_merges_reviewed_safe_items_preserving_target_only_state(self):
        prepared = inv.prepare(self.accounts, 'GREEN', 'YELLOW',
                               {'settings', 'permissions', 'skills', 'mcps'}, self.shared, self.runs)
        self.assertEqual(prepared['state'], 'PREPARED_REVIEW_REQUIRED')
        self.assertEqual(self.target_settings,
                         json.loads((self.target / 'settings.json').read_text()))
        result = inv.apply(self.accounts, Path(prepared['manifest']), prepared['sha256'],
                           self.shared, self.runs)
        self.assertEqual(result['state'], 'MERGE_COMPLETE')
        settings = json.loads((self.target / 'settings.json').read_text())
        self.assertEqual(settings['effortLevel'], 'xhigh')
        self.assertEqual(settings['model'], 'target-only-model')
        self.assertEqual(settings['permissions']['allow'], ['Read', 'Bash(git status:*)'])
        self.assertEqual(settings['permissions']['defaultMode'], 'plan')
        claude = json.loads((self.target / '.claude.json').read_text())
        self.assertEqual(claude['accountState'], {'keep': 'target'})
        self.assertIn('safe-server', claude['mcpServers'])
        self.assertNotIn('credentialed-server', claude['mcpServers'])
        self.assertEqual((self.target / 'skills' / 'new-skill' / 'SKILL.md').read_text(), 'source skill\n')
        self.assertEqual((self.target / 'skills' / 'target-only' / 'SKILL.md').read_text(), 'keep me\n')
        self.assertFalse((self.runs / 'PENDING.json').exists())

    def test_changed_target_blocks_apply_and_keeps_recovery_marker(self):
        prepared = inv.prepare(self.accounts, 'GREEN', 'YELLOW', {'settings'}, self.shared, self.runs)
        self.write(self.target / 'settings.json', dict(self.target_settings, newKey='newer'))
        with self.assertRaisesRegex(inv.Hold, 'Target changed after review'):
            inv.apply(self.accounts, Path(prepared['manifest']), prepared['sha256'], self.shared, self.runs)
        self.assertTrue((self.runs / 'PENDING.json').exists())
        self.assertEqual(json.loads((self.target / 'settings.json').read_text())['newKey'], 'newer')

    def test_permission_conflict_does_not_add_allow_over_target_deny(self):
        self.target_settings['permissions']['deny'].append('Bash(git status:*)')
        self.write(self.target / 'settings.json', self.target_settings)
        prepared = inv.prepare(self.accounts, 'GREEN', 'YELLOW', {'permissions'}, self.shared, self.runs)
        self.assertIn('Bash(git status:*)', prepared['conflicts']['permissions'])
        inv.apply(self.accounts, Path(prepared['manifest']), prepared['sha256'], self.shared, self.runs)
        result = json.loads((self.target / 'settings.json').read_text())
        self.assertNotIn('Bash(git status:*)', result['permissions']['allow'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
