import importlib.util
import threading
import time
import unittest
import uuid


class SwitchboardInstanceTests(unittest.TestCase):
    def gate_type(self):
        self.assertIsNotNone(importlib.util.find_spec('switchboard_instance'),
                             'Single-instance coordinator is missing')
        from switchboard_instance import InstanceGate
        return InstanceGate

    def test_new_launch_requests_normal_close_before_it_takes_ownership(self):
        Gate = self.gate_type()
        name = 'Local\\ClaudeSwitchboardTest_' + uuid.uuid4().hex
        first = Gate(name)
        self.assertTrue(first.enter())
        outcome = []
        def second_launch():
            second = Gate(name)
            try:
                outcome.append(second.enter(timeout_ms=2500))
            finally:
                second.release()
        worker = threading.Thread(target=second_launch)
        try:
            worker.start()
            deadline = time.monotonic() + 2
            while not first.replacement_requested() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(first.replacement_requested())
        finally:
            first.release()
            worker.join(timeout=3)
        self.assertEqual(outcome, [True])

    def test_busy_previous_instance_is_not_force_closed(self):
        Gate = self.gate_type()
        name = 'Local\\ClaudeSwitchboardTest_' + uuid.uuid4().hex
        first = Gate(name)
        self.assertTrue(first.enter())
        outcome = []
        def second_launch():
            second = Gate(name)
            try:
                outcome.append(second.enter(timeout_ms=150))
            finally:
                second.release()
        worker = threading.Thread(target=second_launch)
        try:
            worker.start()
            worker.join(timeout=2)
            self.assertEqual(outcome, [False])
        finally:
            first.release()


if __name__ == '__main__':
    unittest.main(verbosity=2)
