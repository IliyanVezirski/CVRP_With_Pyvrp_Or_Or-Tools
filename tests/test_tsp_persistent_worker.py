import concurrent.futures
from pathlib import Path
import unittest

from tsp_pyvrp_runtime import PersistentTspWorker
from tests.test_tsp_pyvrp import spec

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / '.venv-pyvrp-next' / 'Scripts' / 'python.exe'


@unittest.skipUnless(PYTHON.exists(), 'Isolated PyVRP runtime required')
class PersistentWorkerTests(unittest.TestCase):
    def setUp(self):
        self.worker = PersistentTspWorker()
        self.command = [str(PYTHON), str(ROOT / 'pyvrp_next_worker.py')]

    def tearDown(self):
        self.worker.close()

    def test_reuses_process_and_never_reuses_model_or_windows(self):
        self.worker.warmup(self.command)
        process = self.worker.process
        first = self.worker.solve(self.command, spec([[28800, 32400]]))
        second = self.worker.solve(self.command, spec())
        self.assertIs(process, self.worker.process)
        self.assertEqual(1, first['late_count'])
        self.assertEqual(0, second['late_count'])
        self.assertNotEqual(first['request_id'], second['request_id'])
        self.assertEqual(2, self.worker.status()['completed_requests'])
        self.worker.close()
        self.assertIsNotNone(process.poll())

    def test_concurrent_callers_get_their_own_results(self):
        self.worker.warmup(self.command)
        with concurrent.futures.ThreadPoolExecutor(2) as executor:
            first = executor.submit(self.worker.solve, self.command, spec([[28800, 32400]]))
            second = executor.submit(self.worker.solve, self.command, spec())
            self.assertEqual(1, first.result()['late_count'])
            self.assertEqual(0, second.result()['late_count'])

    def test_restarts_after_crash(self):
        self.worker.warmup(self.command)
        old = self.worker.process
        old.kill()
        old.wait(timeout=5)
        result = self.worker.solve(self.command, spec())
        self.assertEqual([1], result['order'])
        self.assertNotEqual(old.pid, self.worker.process.pid)

    def test_bad_request_does_not_poison_worker(self):
        self.worker.warmup(self.command)
        process = self.worker.process
        invalid = spec()
        invalid['distances'] = []
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            self.worker.solve(self.command, invalid)
        self.assertEqual([1], self.worker.solve(self.command, spec())['order'])
        self.assertIs(process, self.worker.process)


if __name__ == '__main__':
    unittest.main()
