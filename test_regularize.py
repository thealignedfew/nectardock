import json
from pathlib import Path
import tempfile
import unittest

import regularize as r


class RegularizationSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.kitchen = self.root / 'kitchen'
        self.home = self.root / 'green-home'
        self.runs = self.root / 'runs'
        (self.kitchen / 'demo').mkdir(parents=True)
        (self.home / 'skills' / 'demo').mkdir(parents=True)
        (self.kitchen / 'demo' / 'SKILL.md').write_text('canonical\n', encoding='utf-8')
        (self.home / 'skills' / 'demo' / 'SKILL.md').write_text('installed\n', encoding='utf-8')
        self.settings = {
            'effortLevel': 'high',
            'theme': 'dark',
            'permissions': {'allow': ['account-only']},
            'hooks': {'SessionStart': [{'hooks': [{'command': 'account-hook'}]}]},
            'model': 'account-model',
        }
        (self.home / 'settings.json').write_text(
            json.dumps(self.settings, indent=2) + '\n', encoding='utf-8'
        )
        self.policy = {
            'schema': 'claude-regularization-policy/v1',
            'skills_root': str(self.kitchen),
            'accounts': {'GREEN': {'home': str(self.home)}},
            'shared_settings': {'effortLevel': 'xhigh', 'theme': 'dark'},
            'protected_settings': ['permissions', 'hooks', 'model'],
            'shared_skills': ['demo'],
        }
        self.policy_path = self.root / 'policy.json'
        self.policy_path.write_text(json.dumps(self.policy, indent=2) + '\n', encoding='utf-8')

    def prepare(self, kind):
        return r.prepare(kind, ['GREEN'], self.policy_path, self.runs)

    def apply(self, prepared):
        return r.apply(Path(prepared['manifest']), prepared['sha256'], self.policy_path, self.runs)

    def test_audit_reports_shared_drift_and_labels_protected_exceptions(self):
        audit = r.audit(['GREEN'], self.policy_path, self.runs)
        row = audit['accounts'][0]
        self.assertEqual(row['settings']['drift_keys'], ['effortLevel'])
        self.assertEqual(row['settings']['protected_keys'], ['hooks', 'model', 'permissions'])
        self.assertEqual(row['skills']['changed_files'], ['demo/SKILL.md'])

    def test_settings_apply_changes_only_shared_keys_and_creates_verified_backup(self):
        prepared = self.prepare('settings')
        result = self.apply(prepared)
        current = json.loads((self.home / 'settings.json').read_text(encoding='utf-8'))
        self.assertEqual(current['effortLevel'], 'xhigh')
        self.assertEqual(current['permissions'], self.settings['permissions'])
        self.assertEqual(current['hooks'], self.settings['hooks'])
        self.assertEqual(current['model'], self.settings['model'])
        self.assertEqual(result['state'], 'REGULARIZATION_COMPLETE')
        self.assertEqual(result['changed_files'], 1)
        receipt = json.loads(Path(result['receipt']).read_text(encoding='utf-8'))
        op = receipt['operations'][0]
        self.assertEqual(r.digest(Path(op['backup'])), op['before_sha256'])
        self.assertEqual(r.digest(Path(op['destination'])), op['after_sha256'])
        self.assertFalse((self.runs / 'PENDING.json').exists())

    def test_semantically_current_settings_do_not_create_format_only_change(self):
        current = dict(self.settings, effortLevel='xhigh')
        (self.home / 'settings.json').write_text(json.dumps(current, separators=(',', ':')), encoding='utf-8')
        prepared = self.prepare('settings')
        self.assertEqual(prepared['changed_files'], 0)

    def test_skill_apply_updates_canonical_files_and_preserves_target_only_resource(self):
        extra = self.home / 'skills' / 'demo' / 'provider-only.py'
        extra.write_text('keep\n', encoding='utf-8')
        prepared = self.prepare('skills')
        result = self.apply(prepared)
        self.assertEqual((self.home / 'skills' / 'demo' / 'SKILL.md').read_text(), 'canonical\n')
        self.assertEqual(extra.read_text(), 'keep\n')
        self.assertEqual(result['changed_files'], 1)

    def test_changed_target_aborts_without_overwrite_and_leaves_recovery_marker(self):
        prepared = self.prepare('settings')
        target = self.home / 'settings.json'
        target.write_text('{"newer": true}\n', encoding='utf-8')
        with self.assertRaisesRegex(r.Hold, 'Target changed after review'):
            self.apply(prepared)
        self.assertEqual(target.read_text(), '{"newer": true}\n')
        pending = json.loads((self.runs / 'PENDING.json').read_text(encoding='utf-8'))
        self.assertEqual(pending['manifest'], prepared['manifest'])

    def test_changed_skill_source_aborts_without_overwrite(self):
        prepared = self.prepare('skills')
        target = self.home / 'skills' / 'demo' / 'SKILL.md'
        (self.kitchen / 'demo' / 'SKILL.md').write_text('changed after review\n', encoding='utf-8')
        with self.assertRaisesRegex(r.Hold, 'Source changed after review'):
            self.apply(prepared)
        self.assertEqual(target.read_text(), 'installed\n')

    def test_policy_path_escape_is_refused(self):
        self.policy['accounts']['GREEN']['home'] = str(self.root)
        self.policy['shared_skills'] = ['../escape']
        self.policy_path.write_text(json.dumps(self.policy), encoding='utf-8')
        with self.assertRaisesRegex(r.Hold, 'skill name'):
            r.audit(['GREEN'], self.policy_path, self.runs)

    def test_existing_recovery_marker_blocks_new_prepare(self):
        self.runs.mkdir()
        (self.runs / 'PENDING.json').write_text('{}\n', encoding='utf-8')
        with self.assertRaisesRegex(r.Hold, 'recovery'):
            self.prepare('skills')


if __name__ == '__main__':
    unittest.main(verbosity=2)
