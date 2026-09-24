"""Display-only regression tests. No credentials, account commands or history writes."""
import importlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import switcher_ui as ui


class PreferenceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('switchboard_preferences'),
                             'Persistent display preferences are missing')
        self.prefs = importlib.import_module('switchboard_preferences')

    def test_missing_file_defaults_and_restart_preserves_choices(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prefs.json'
            values, warning = self.prefs.load_preferences(path)
            self.assertIsNone(warning)
            self.assertEqual(values['usage_sort'], 'fable')
            self.assertEqual(values['usage_threshold'], 95)
            values.update(usage_threshold=90, auto_fit=False, show_details=False,
                          usage_sort='five_hour', usage_descending=True)
            self.prefs.save_preferences(values, path)
            restored, warning = self.prefs.load_preferences(path)
            self.assertEqual(restored, values)
            self.assertIsNone(warning)

    def test_invalid_values_and_safety_keys_cannot_enter_preferences(self):
        for value in (-1, 101, float('nan'), True, '95'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.prefs.validate_preferences({'usage_threshold': value})
        for values in ({'auto_fit': 'false'}, {'usage_sort': 'unknown'}, {'bypass_holds': True}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.prefs.validate_preferences(values)

    def test_corrupt_file_preserved_and_reported_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prefs.json'
            path.write_text('{broken', encoding='utf-8')
            values, warning = self.prefs.load_preferences(path)
            self.assertTrue(warning)
            self.assertEqual(values['usage_sort'], 'fable')
            self.assertEqual(path.read_text(encoding='utf-8'), '{broken')


class UsageDisplayTests(unittest.TestCase):
    def test_main_toolbar_exposes_options_and_saves_preferences(self):
        root = tk.Tk(); root.withdraw()
        observed = {}
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        def inspect():
            buttons = {str(w.cget('text')):w for w in descendants(root) if w.winfo_class() == 'TButton'}
            observed['buttons'] = set(buttons)
            if 'Options' in buttons:
                buttons['Options'].invoke()
                for widget in descendants(root):
                    if widget.winfo_class() == 'TButton' and widget.cget('text') == 'Save options':
                        widget.invoke()
                        observed['saved'] = True
                        break
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'prefs.json'
            with patch.dict(os.environ, {'SWITCHBOARD_SMOKE':'1'}), \
                 patch.object(ui.tk, 'Tk', return_value=root), \
                 patch.object(ui, 'PREFERENCES_PATH', path):
                root.after(80, inspect)
                ui.main()
            self.assertTrue({'Refresh data','Usage','Reload App','Options'}.issubset(observed.get('buttons',set())))
            self.assertTrue(observed.get('saved'))
            self.assertTrue(path.is_file())

    def test_usage_sort_numeric_both_directions_missing_last_without_mutation(self):
        self.assertTrue(hasattr(ui, 'sort_usage_accounts'), 'Usage sorting is missing')
        rows = [{'color':'YELLOW', 'fable':{'used_percent':100}},
                {'color':'PURPLE', 'fable':{'used_percent':None}},
                {'color':'GREEN', 'fable':{'used_percent':9}},
                {'color':'BLUE', 'fable':{'used_percent':95}}]
        self.assertEqual([r['color'] for r in ui.sort_usage_accounts(rows)],
                         ['GREEN','BLUE','YELLOW','PURPLE'])
        self.assertEqual([r['color'] for r in ui.sort_usage_accounts(rows, 'fable', True)],
                         ['YELLOW','BLUE','GREEN','PURPLE'])
        self.assertEqual(rows[0]['color'], 'YELLOW')

    def test_strike_is_strictly_above_threshold_and_weekly_strikes_plan(self):
        self.assertTrue(hasattr(ui, 'usage_strikes'), 'Usage strike policy is missing')
        row = {'five_hour':{'used_percent':95}, 'seven_day':{'used_percent':50},
               'fable':{'used_percent':95.1}}
        self.assertEqual(ui.usage_strikes(row, 95), {'plan':False,'five_hour':False,'seven_day':False,'fable':True})
        row['seven_day']['used_percent'] = 96
        self.assertEqual(ui.usage_strikes(row, 95), {'plan':True,'five_hour':True,'seven_day':True,'fable':True})
        self.assertFalse(ui.usage_strikes({}, 95)['plan'])

    def test_fit_bounds_respect_small_and_negative_origin_monitors(self):
        self.assertTrue(hasattr(ui, 'fit_geometry'), 'Work-area bounded fit is missing')
        self.assertEqual(ui.fit_geometry(1500, 1000, (-1280,0,0,720)), (1256,656,-1268,32))
        self.assertEqual(ui.fit_geometry(640, 300, (0,0,1920,1080)), (640,300,12,32))

    def test_rendered_report_uses_real_color_stale_and_strike_tags(self):
        self.assertTrue(hasattr(ui, 'render_usage_report'), 'Styled report is missing')
        root = tk.Tk(); root.withdraw()
        try:
            report = tk.Text(root)
            meter = {'used_percent':98, 'remaining_hhmm':'02:00',
                     'resets_at':'2026-09-25T02:00:00+00:00'}
            row = {'color':'BLUE','email':'sample@example.test','stale':True,
                   'observed_at':'2026-09-23T18:00:01+00:00',
                   'five_hour':meter,'seven_day':meter,'fable':meter}
            ui.render_usage_report(report, {'accounts':[row]}, {'show_details':False})
            self.assertIn('STALE', report.get('1.0','end'))
            self.assertIn('stale', report.tag_names('1.0'))
            self.assertIn('exhausted', report.tag_names('1.0'))
            self.assertIn('BLUE', report.tag_names('1.0'))
            self.assertEqual(report.tag_cget('exhausted','overstrike'), '1')
            self.assertNotIn('observed', report.get('1.0','end'))
            self.assertNotIn('reset in', report.get('1.0','end'))
        finally:
            root.destroy()


if __name__ == '__main__':
    unittest.main()
