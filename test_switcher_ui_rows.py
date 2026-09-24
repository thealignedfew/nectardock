import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import switcher_ui


class SwitchboardRowTests(unittest.TestCase):
    def test_default_sort_uses_modified_descending_and_unavailable_last(self):
        self.assertTrue(hasattr(switcher_ui, 'sort_table_rows'), 'Column sorter is missing')
        rows = [
            ('old', ('Alpha','YELLOW','READY','abc','2026-09-21 12:00:00','2.1.99','p','old')),
            ('unknown', ('Beta','GREEN','READY','abc','unavailable','2.1.280','p','unknown')),
            ('new', ('Zulu','YELLOW','READY','abc','2026-09-22 18:00:00','2.1.280','p','new')),
        ]
        self.assertEqual([key for key, _ in switcher_ui.sort_table_rows(rows)],
                         ['new', 'old', 'unknown'])

    def test_click_sort_can_reverse_name_and_orders_versions_numerically(self):
        self.assertTrue(hasattr(switcher_ui, 'sort_table_rows'), 'Column sorter is missing')
        rows = [
            ('z', ('Zulu','YELLOW','READY','abc','2026-09-22 18:00:00','2.1.99','p','z')),
            ('a', ('alpha','GREEN','READY','abc','2026-09-21 12:00:00','2.1.280','p','a')),
        ]
        self.assertEqual([key for key, _ in switcher_ui.sort_table_rows(rows, 'seat', False)], ['a', 'z'])
        self.assertEqual([key for key, _ in switcher_ui.sort_table_rows(rows, 'seat', True)], ['z', 'a'])
        self.assertEqual([key for key, _ in switcher_ui.sort_table_rows(rows, 'version', False)], ['z', 'a'])

    def test_auxiliary_merge_work_prevents_window_replacement(self):
        self.assertTrue(hasattr(switcher_ui, 'safe_to_close'), 'Close gate is missing')
        self.assertFalse(switcher_ui.safe_to_close(False, 1))
        self.assertFalse(switcher_ui.safe_to_close(True, 0))
        self.assertTrue(switcher_ui.safe_to_close(False, 0))

    def test_row_shows_registered_history_file_modified_time(self):
        self.assertTrue(hasattr(switcher_ui, 'table_row_values'), 'Table row formatter is missing')
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / '11111111.jsonl'
            history.write_text('{}\n', encoding='utf-8')
            modified = dt.datetime(2026, 9, 22, 18, 27, tzinfo=dt.timezone.utc).timestamp()
            os.utime(history, (modified, modified))
            row = {'label': 'DEMO-ANALYST', 'uuid': '11111111', 'config_home': 'YELLOW_HOME',
                   'status': 'REGISTERED', 'checkpoint': {'main_sha256': 'a' * 64},
                   'primary_history_path': str(history)}
            values = switcher_ui.table_row_values(row, {'YELLOW_HOME': 'YELLOW'})
            expected = dt.datetime.fromtimestamp(modified).strftime('%Y-%m-%d %H:%M:%S')
            self.assertEqual(values[0:4], ('DEMO-ANALYST', 'YELLOW', 'REGISTERED', 'a' * 12))
            self.assertEqual(values[4], expected)
            self.assertEqual(values[5], 'unavailable')
            self.assertEqual(values[6:], (str(history), '11111111'))

    def test_missing_history_has_unavailable_timestamp(self):
        self.assertTrue(hasattr(switcher_ui, 'table_row_values'), 'Table row formatter is missing')
        with tempfile.TemporaryDirectory() as directory:
            row = {'label': 'DEMO-ANALYST', 'uuid': '11111111', 'config_home': 'YELLOW_HOME',
                   'status': 'REGISTERED', 'checkpoint': {},
                   'primary_history_path': str(Path(directory) / 'missing.jsonl')}
            values = switcher_ui.table_row_values(row, {'YELLOW_HOME': 'YELLOW'})
            self.assertEqual(values[4], 'unavailable')
            self.assertEqual(values[5], 'unavailable')

    def test_row_reports_latest_top_level_recorded_version(self):
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory) / 'history.jsonl'
            records = [{'type': 'user', 'version': '2.1.273'},
                       {'type': 'assistant', 'version': '2.1.280'},
                       {'type': 'cost-state'}]
            history.write_text(''.join(json.dumps(record) + '\n' for record in records), encoding='utf-8')
            row = {'label': 'DEMO-ANALYST', 'uuid': '11111111', 'config_home': 'YELLOW_HOME',
                   'status': 'REGISTERED', 'checkpoint': {}, 'primary_history_path': str(history)}
            values = switcher_ui.table_row_values(row, {'YELLOW_HOME': 'YELLOW'})
            self.assertEqual(values[5], '2.1.280')


class UsageControlsTests(unittest.TestCase):
    def test_usage_observation_is_converted_from_utc_to_local_clock(self):
        observed = '2026-09-23T01:17:13.192451+00:00'
        expected = dt.datetime.fromisoformat(observed).astimezone().strftime('%Y-%m-%d %H:%M')
        self.assertEqual(switcher_ui.format_observed_local(observed), expected)
        self.assertEqual(switcher_ui.format_observed_local('bad timestamp'), 'unavailable')

    def test_login_control_is_offered_for_recoverable_auth_errors(self):
        self.assertTrue(switcher_ui.needs_usage_login('Usage endpoint unavailable: http_401'))
        self.assertTrue(switcher_ui.needs_usage_login('Destination account does not match PURPLE. No files moved.'))
        self.assertTrue(switcher_ui.needs_usage_login('Destination login is unavailable. Use its existing account window to sign in.'))
        self.assertFalse(switcher_ui.needs_usage_login('Usage endpoint unavailable: http_403'))
        self.assertFalse(switcher_ui.needs_usage_login('Unrelated transfer error'))

    def test_stale_meter_uses_local_absolute_reset_not_old_countdown(self):
        meter = {'used_percent': 89, 'remaining_hhmm': '24:00',
                 'resets_at': '2026-09-23T18:00:00+00:00'}
        before = dt.datetime(2026, 9, 23, 17, tzinfo=dt.timezone.utc)
        after = dt.datetime(2026, 9, 24, 17, tzinfo=dt.timezone.utc)
        local_reset = dt.datetime.fromisoformat(meter['resets_at']).astimezone().strftime('%a %b %d, %H:%M local')
        self.assertEqual(switcher_ui.format_usage_meter(meter, True, before),
                         f'89% used; reset on {local_reset}')
        self.assertEqual(switcher_ui.format_usage_meter(meter, True, after),
                         f'89% used; last reported reset on {local_reset} (passed)')
        self.assertEqual(switcher_ui.format_usage_meter(meter, False, before),
                         '89% used; reset in 24:00')

    def test_usage_report_puts_each_meter_on_its_own_visible_line(self):
        meter = {'used_percent': 89, 'remaining_hhmm': '02:00',
                 'resets_at': '2026-09-25T18:00:00+00:00'}
        data = {'accounts': [{'color': 'PURPLE', 'email': 'bi@example.invalid',
                              'observed_at': '2026-09-23T18:00:00+00:00', 'stale': True,
                              'five_hour': meter, 'seven_day': meter, 'fable': meter}],
                'errors': {'PURPLE': 'Destination account does not match PURPLE. No files moved.'}}
        lines = switcher_ui.usage_report_lines(data)
        self.assertTrue(lines[0].startswith('PURPLE  bi@example.invalid  ·  STALE'))
        self.assertTrue(lines[1].startswith('  5-hour: 89% used; reset on '))
        self.assertTrue(lines[2].startswith('  Weekly: 89% used; reset on '))
        self.assertTrue(lines[3].startswith('  Fable: 89% used; reset on '))
        self.assertIn('fresh check failed', lines[5])

    def test_sign_in_guidance_distinguishes_google_from_email_and_plan(self):
        self.assertIn('Continue with Google', switcher_ui.login_guidance('GREEN'))
        self.assertIn('green@example.invalid', switcher_ui.login_guidance('GREEN'))
        self.assertIn('Continue with email', switcher_ui.login_guidance('PURPLE'))
        self.assertIn('bi@example.invalid', switcher_ui.login_guidance('PURPLE'))
        self.assertIn('Max', switcher_ui.login_guidance('PURPLE'))

    def test_reload_releases_single_instance_before_starting_fresh_process(self):
        events = []
        gate = Mock()
        gate.release.side_effect = lambda: events.append('released')
        def launch(argv, **kwargs):
            events.append('launched')
            self.assertEqual(argv, ['C:/Python/pythonw.exe', 'E:/App/switcher_ui.py'])
            self.assertEqual(kwargs['cwd'], str(switcher_ui.engine.BASE))
        switcher_ui.restart_application(gate, launch, 'C:/Python/pythonw.exe',
                                        'E:/App/switcher_ui.py', frozen=False)
        self.assertEqual(events, ['released', 'launched'])

    def test_retry_one_account_preserves_other_snapshot_rows_and_errors(self):
        previous = {'accounts': [{'color': 'GREEN', 'five_hour': {'used_percent': 50}}],
                    'errors': {'ORANGE': 'http_401', 'PURPLE': 'http_401'}}
        update = {'accounts': [{'color': 'PURPLE', 'five_hour': {'used_percent': 10}}], 'errors': {}}
        merged = switcher_ui.merge_usage_snapshot(previous, update, 'PURPLE')
        self.assertEqual([row['color'] for row in merged['accounts']], ['GREEN', 'PURPLE'])
        self.assertEqual(merged['errors'], {'ORANGE': 'http_401'})
        self.assertEqual(merged['state'], 'PARTIAL')

    def test_retry_failure_keeps_other_accounts_and_marks_selected_unavailable(self):
        previous = {'accounts': [{'color': 'GREEN'}], 'errors': {'PURPLE': 'http_401'}}
        merged = switcher_ui.merge_usage_snapshot(previous, {'state': 'HELD', 'error': 'Timeout'}, 'PURPLE')
        self.assertEqual(merged['accounts'], [{'color': 'GREEN'}])
        self.assertEqual(merged['errors'], {'PURPLE': 'Timeout'})


if __name__ == '__main__':
    unittest.main(verbosity=2)
