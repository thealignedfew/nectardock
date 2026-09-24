import json
from pathlib import Path
import sys
import tempfile
import unittest

import switchboard_activity as activity


class ActivityTests(unittest.TestCase):
    def test_runner_streams_only_marked_progress_and_returns_final_json(self):
        code = ('import json,sys; '
                'print("SWITCHBOARD_PROGRESS PREPARE session 1/2", file=sys.stderr, flush=True); '
                'print("private diagnostic", file=sys.stderr, flush=True); '
                'print(json.dumps({"state":"PREPARED_REVIEW_REQUIRED"}))')
        events = []
        result, exit_code, elapsed = activity.run_json_command([sys.executable, '-c', code], events.append)
        self.assertEqual(events, ['PREPARE session 1/2'])
        self.assertEqual(result['state'], 'PREPARED_REVIEW_REQUIRED')
        self.assertEqual(exit_code, 0)
        self.assertGreaterEqual(elapsed, 0)

    def test_recorder_creates_durable_file_only_on_first_event(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = activity.ActivityRecorder(Path(directory))
            self.assertIsNone(recorder.path)
            line = recorder.record('RUN switcher.py prepare --group FPA')
            self.assertTrue(recorder.path.is_file())
            self.assertIn('RUN switcher.py prepare --group FPA', line)
            self.assertEqual(recorder.path.read_text(encoding='utf-8').strip(), line)


if __name__ == '__main__':
    unittest.main(verbosity=2)
