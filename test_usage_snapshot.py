import datetime as dt
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import usage_snapshot as usage


class UsageSnapshotTests(unittest.TestCase):
    def test_last_success_survives_failed_check_and_process_restart(self):
        account = {'email': 'bi@example.invalid', 'plan': 'max', 'home': 'X:/purple'}
        reading = usage.normalize('PURPLE', account['email'], {
            'five_hour': {'utilization': 12, 'resets_at': '2026-09-24T02:00:00+00:00'},
            'seven_day': {'utilization': 75, 'resets_at': '2026-09-25T02:00:00+00:00'},
            'limits': [{'kind': 'weekly_scoped', 'scope': {'model': {'display_name': 'Fable'}},
                        'percent': 98, 'resets_at': '2026-09-25T02:00:00+00:00'}],
        }, dt.datetime(2026, 9, 23, 18, tzinfo=dt.timezone.utc))
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'usage-last-known.json'
            with patch('usage_snapshot.sample_one', return_value=reading):
                fresh = usage.collect(['PURPLE'], {'PURPLE': account}, cache)
            self.assertFalse(fresh['accounts'][0]['stale'])
            self.assertTrue(cache.is_file())
            with patch('usage_snapshot.sample_one', side_effect=RuntimeError('http_401')):
                failed = usage.collect(['PURPLE'], {'PURPLE': account}, cache)
            self.assertEqual(failed['state'], 'PARTIAL')
            self.assertEqual(failed['errors'], {'PURPLE': 'http_401'})
            self.assertEqual(failed['accounts'][0]['five_hour']['used_percent'], 12)
            self.assertEqual(failed['accounts'][0]['observed_at'], '2026-09-23T18:00:00+00:00')
            self.assertTrue(failed['accounts'][0]['stale'])

    def test_last_success_is_not_reused_for_a_different_account_identity(self):
        old = {'email': 'green@example.invalid', 'plan': 'team', 'home': 'X:/green'}
        reading = usage.normalize('GREEN', old['email'], {'five_hour': {'utilization': 26}},
                                  dt.datetime(2026, 9, 23, 18, tzinfo=dt.timezone.utc))
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'usage-last-known.json'
            with patch('usage_snapshot.sample_one', return_value=reading):
                usage.collect(['GREEN'], {'GREEN': old}, cache)
            changed = {'email': 'someone-else@example.com', 'plan': 'team', 'home': 'X:/green'}
            with patch('usage_snapshot.sample_one', side_effect=RuntimeError('http_401')):
                failed = usage.collect(['GREEN'], {'GREEN': changed}, cache)
            self.assertEqual(failed['accounts'], [])
            self.assertEqual(failed['errors'], {'GREEN': 'http_401'})

    def test_fable_scoped_limit_and_reset_countdown_are_preserved(self):
        now = dt.datetime(2026, 9, 22, 18, 0, tzinfo=dt.timezone.utc)
        raw = {
            'five_hour': {'utilization': 26, 'resets_at': '2026-09-22T20:00:00+00:00'},
            'seven_day': {'utilization': 69, 'resets_at': '2026-09-23T18:00:00+00:00'},
            'limits': [{'kind': 'weekly_scoped', 'scope': {'model': {'display_name': 'Fable'}},
                        'percent': 89, 'resets_at': '2026-09-23T18:00:00+00:00'}],
        }
        result = usage.normalize('YELLOW', 'yellow@example.invalid', raw, now)
        self.assertEqual(result['five_hour']['used_percent'], 26)
        self.assertEqual(result['five_hour']['remaining_hhmm'], '02:00')
        self.assertEqual(result['seven_day']['remaining_hhmm'], '24:00')
        self.assertEqual(result['fable']['used_percent'], 89)
        self.assertEqual(result['fable']['remaining_hhmm'], '24:00')
        self.assertEqual(result['scope'], 'account, not conversation')

    def test_missing_meter_does_not_look_like_zero(self):
        now = dt.datetime(2026, 9, 22, 18, 0, tzinfo=dt.timezone.utc)
        result = usage.normalize('GREEN', 'green@example.invalid', {}, now)
        self.assertIsNone(result['five_hour']['used_percent'])
        self.assertIsNone(result['five_hour']['remaining_hhmm'])
        self.assertIsNone(result['fable']['used_percent'])

    def test_adapter_binds_selected_home_before_module_load_and_restores_environment(self):
        seen = []
        loader = SimpleNamespace(exec_module=lambda module: seen.append(os.environ.get('CLAUDE_CONFIG_DIR')))
        spec = SimpleNamespace(loader=loader)
        module = SimpleNamespace(fetch_raw=lambda: {'five_hour': {'utilization': 4}})
        account = {'home': 'X:/isolated/yellow', 'email': 'yellow@example.com'}
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': 'X:/other'}, clear=False), \
             patch('switcher.auth_check'), patch('usage_snapshot.Path.is_file', return_value=True), \
             patch('usage_snapshot.importlib.util.spec_from_file_location', return_value=spec), \
             patch('usage_snapshot.importlib.util.module_from_spec', return_value=module):
            result = usage.sample_one('YELLOW', account)
            self.assertEqual(os.environ['CLAUDE_CONFIG_DIR'], 'X:/other')
        self.assertEqual(seen, ['X:/isolated/yellow'])
        self.assertEqual(result['five_hour']['used_percent'], 4)


if __name__ == '__main__':
    unittest.main(verbosity=2)
