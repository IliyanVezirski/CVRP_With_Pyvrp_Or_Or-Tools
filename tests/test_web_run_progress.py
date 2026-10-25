import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from web_run_progress import RunProgressReader, elapsed_seconds


def marker(text, logger="main"):
    return f"2026-09-07 09:00:00,000 - {logger} - INFO - {text}\n".encode("utf-8")


class RunProgressTests(unittest.TestCase):
    def test_search_estimate_uses_slowest_parallel_search_and_never_finishes_a_run(self):
        reader = RunProgressReader()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'run.log'
            path.write_bytes(marker('СТЪПКА 2: РЕШАВАНЕ НА CVRP ПРОБЛЕМА') + (
                '2026-09-07 09:00:10,000 - worker-1 - pyvrp_next_runtime - INFO - [PyVRP 0.14] Solving with PyVRP (time limit: 100s, seed: 1)...\n'
                '2026-09-07 09:00:20,000 - worker-2 - pyvrp_next_runtime - INFO - [PyVRP 0.14] Solving with PyVRP (time limit: 100s, seed: 2)...\n'
            ).encode())
            status = {'running':True, 'source':'web_gui', 'search_time_limit_seconds':9999}
            observation = reader.read('a', path, status, datetime(2026,9,7,9,1,0))
            self.assertEqual(44, observation['percent'])
            self.assertTrue(observation['estimated'])
            self.assertEqual(94, reader.read('a', path, status, datetime(2026,9,7,9,9,0))['percent'])
            with path.open('ab') as log:
                log.write(marker('СТЪПКА 3: ОБРАБОТКА НА РЕЗУЛТАТИТЕ') + marker('CVRP ОПТИМИЗАЦИЯ ЗАВЪРШЕНА УСПЕШНО'))
            self.assertEqual(95, reader.read('a', path, status)['percent'])
            completed = reader.read('a', path, {'running':False,'status':'completed'})
            self.assertEqual(100, completed['percent'])
            self.assertFalse(completed['estimated'])

    def test_fallback_budget_is_run_specific_errors_freeze_and_new_runs_reset(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'run.log'
            path.write_bytes(marker('СТЪПКА 2: РЕШАВАНЕ НА CVRP ПРОБЛЕМА'))
            now = datetime(2026,9,7,9,0,50)
            reader = RunProgressReader()
            status = {'running':True,'source':'web_gui','search_time_limit_seconds':100}
            self.assertEqual(52, reader.read('a', path, status, now)['percent'])
            status.update(running=False,status='failed',finished_at='2026-09-07T09:00:50')
            self.assertEqual(52, reader.read('a', path, status, datetime(2026,9,8))['percent'])
            for value in (0,-1,float('nan'),float('inf'),'bad'):
                invalid = dict(status, running=True, search_time_limit_seconds=value)
                self.assertEqual(10, RunProgressReader().read('b', path, invalid, now)['percent'])
            # An API run must not reuse a budget left in shared status by a Web run.
            self.assertEqual(10, RunProgressReader().read('c', path, dict(status,source='api'), now)['percent'])
            path.write_bytes(marker('СТЪПКА 1: ПОДГОТОВКА НА ДАННИ'))
            self.assertEqual(0, reader.read('d', path, {'running':True}, now)['percent'])
            self.assertIsNone(reader.read('d', path.with_name('missing'), {'running':True})['percent'])

    def test_phases_survive_log_tail_and_reset_for_new_run(self):
        reader = RunProgressReader()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run.log"
            path.write_bytes(marker("СТЪПКА 2: РЕШАВАНЕ НА CVRP ПРОБЛЕМА"))
            self.assertEqual("solve", reader.read("a", path)["phase"])
            with path.open("ab") as log:
                log.write(b"worker progress\n" * 2000)
            self.assertEqual("solve", reader.read("a", path)["phase"])
            path.write_bytes(marker("СТЪПКА 1: ПОДГОТОВКА НА ДАННИ"))
            self.assertEqual("input", reader.read("b", path)["phase"])

    def test_partial_utf8_lines_truncation_and_missing_log(self):
        reader = RunProgressReader()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run.log"
            full = marker("СТЪПКА 1.5: ИЗЧИСЛЯВАНЕ НА МАТРИЦА С РАЗСТОЯНИЯ")
            path.write_bytes(full[:63])
            self.assertIsNone(reader.read("a", path)["phase"])
            with path.open("ab") as log:
                log.write(full[63:])
            self.assertEqual("matrix", reader.read("a", path)["phase"])
            path.write_bytes(b"new file\n")
            self.assertIsNone(reader.read("a", path)["phase"])
            self.assertIsNone(reader.read("a", path.with_name("missing"))["phase"])

    def test_success_banner_is_not_completion_and_customer_text_is_not_a_phase(self):
        reader = RunProgressReader()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "run.log"
            path.write_bytes(marker("СТЪПКА 3: ОБРАБОТКА НА РЕЗУЛТАТИТЕ")
                             + marker("CVRP ОПТИМИЗАЦИЯ ЗАВЪРШЕНА УСПЕШНО")
                             + marker("Клиент: СТЪПКА 1: ПОДГОТОВКА НА ДАННИ")
                             + marker("СТЪПКА 1: ПОДГОТОВКА НА ДАННИ", "worker"))
            self.assertEqual("output", reader.read("a", path)["phase"])
            with path.open("ab") as log:
                log.write(marker("Изпращам 120 setData заявки към Bizant", "setdata_client"))
            self.assertEqual("sending", reader.read("a", path)["phase"])

    def test_elapsed_uses_server_time_and_stops_at_finish(self):
        status = {"started_at": "2026-09-07T09:00:00", "running": True}
        self.assertEqual(125, elapsed_seconds(status, datetime(2026, 9, 7, 9, 2, 5)))
        status.update(running=False, finished_at="2026-09-07T09:01:00")
        self.assertEqual(60, elapsed_seconds(status, datetime(2026, 9, 8)))
        self.assertIsNone(elapsed_seconds({"started_at": "invalid"}))
        self.assertIsNone(elapsed_seconds({}))
