import unittest
from types import SimpleNamespace
from unittest.mock import patch
import switcher as s


class RuntimeDiagnostics(unittest.TestCase):
    def fixture(self):
        exe = str(s.CLAUDE_CMD.parent / 'node_modules/@anthropic-ai/claude-code/bin/claude.exe')
        item = {'pid': 42, 'process_start_filetime': '123',
                'account_home': s.ACCOUNTS['ORANGE']['home'], 'registry_verified': False,
                'error': 'missing/conflicting UUID or process-start binding'}
        evidence = {'exe': exe, 'start': '123',
                    'command': f'"{exe}" auth login --claudeai --email bi@example.invalid'}
        return item, evidence

    def test_exact_login_not_a_conversation_writer(self):
        item, evidence = self.fixture()
        self.assertTrue(s.support_only(item, evidence))

    def test_windows_cmd_shim_spacing_is_accepted(self):
        item, evidence = self.fixture()
        evidence['command'] = evidence['command'].replace('" auth', '"    auth')
        self.assertTrue(s.support_only(item, evidence))

    def test_login_not_exempt_when_identity_or_mode_differs(self):
        for key, value in [('start', '124'), ('exe', 'C:/other/claude.exe'),
                           ('command', 'claude auth login --resume abc')]:
            item, evidence = self.fixture()
            evidence[key] = value
            self.assertFalse(s.support_only(item, evidence))
        for key, value in [('account_home', 'C:/other'), ('registry_verified', True),
                           ('uuid', 'conversation'), ('error', 'access denied')]:
            item, evidence = self.fixture()
            item[key] = value
            self.assertFalse(s.support_only(item, evidence))

    def test_login_rejects_extra_arguments_and_wrong_email(self):
        for suffix in [' --print', ' --resume=abc', ' & echo surprise']:
            item, evidence = self.fixture()
            evidence['command'] += suffix
            self.assertFalse(s.support_only(item, evidence))
        item, evidence = self.fixture()
        evidence['command'] = evidence['command'].replace('bi@', 'other@')
        self.assertFalse(s.support_only(item, evidence))

    def test_exact_auth_status_not_a_writer(self):
        item, evidence = self.fixture()
        evidence['command'] = f'"{evidence["exe"]}" auth status'
        self.assertTrue(s.support_only(item, evidence))

    def test_unknown_hold_reports_pid_without_raw_command(self):
        census = {'bindings': [], 'unknown_live': [{'pid': 42, 'error': 'unresolved'}],
                  'registry_errors': [], 'verified_support_only': []}
        helper = SimpleNamespace(processes=lambda *args: census)
        with patch.object(s, 'classify_support_processes', side_effect=lambda c: c):
            with self.assertRaisesRegex(s.Hold, 'PID 42'):
                s.runtime_gate(helper, [])

    def test_catalog_groups_only_current_registered_account_histories(self):
        rows = {'a': {'uuid': 'a', 'label': 'Current', 'config_home': s.ACCOUNTS['GREEN']['home'],
                      'project_directory': str(s.PROJECTS / s.GROUPS['FPA'])},
                'b': {'uuid': 'b', 'label': 'Elsewhere', 'config_home': s.ACCOUNTS['ORANGE']['home'],
                      'project_directory': str(s.PROJECTS / s.GROUPS['FPA'])}}
        maps = SimpleNamespace(load_verified=lambda *args: {'records': rows})
        with patch.object(s, 'dependencies', return_value=(None, maps)):
            data = s.workspace_catalog()
        green = next(w for w in data['workspaces'] if w['account'] == 'GREEN' and w['group'] == 'FPA')
        self.assertEqual(green['sessions'], [{'uuid': 'a', 'label': 'Current'}])

    def test_duplicate_vscode_profile_is_detected_across_path_spellings(self):
        processes = [{'pid': 1, 'user_data': 'E:/profiles/yellow'},
                     {'pid': 2, 'user_data': 'e:\\profiles\\YELLOW'},
                     {'pid': 3, 'user_data': 'E:/profiles/green'}]
        result = s.vscode_instance_diagnostics(processes)
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual([p['pid'] for p in result['conflicts'][0]['processes']], [1, 2])

    def test_duplicate_guard_blocks_only_affected_profile(self):
        data = {'conflicts': [{'user_data': 'E:/profiles/yellow', 'processes': [{'pid': 1}, {'pid': 2}]}]}
        with patch.object(s, 'vscode_instance_diagnostics', return_value=data):
            with self.assertRaisesRegex(s.Hold, 'multiple VS Code main processes'):
                s.assert_single_vscode_instance('E:/profiles/yellow')
            s.assert_single_vscode_instance('E:/profiles/green')


if __name__ == '__main__':
    unittest.main()
