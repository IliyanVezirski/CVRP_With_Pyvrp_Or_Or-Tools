import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import textwrap
import unittest

import vroom_runtime as runtime


def _config(worker_path: str, **overrides):
    values = {
        "vroom_worker_path": worker_path,
        "vroom_worker_timeout_seconds": 5,
        "vroom_threads": 1,
        "vroom_exploration_level": 5,
        "time_limit_seconds": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class VROOMRuntimeTests(unittest.TestCase):
    def setUp(self):
        runtime._SELF_CHECK_CACHE.clear()
        self.temp = tempfile.TemporaryDirectory(prefix="vroom_runtime_test_")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()
        runtime._SELF_CHECK_CACHE.clear()

    def _fake_worker(self, body: str = "") -> Path:
        path = self.root / "fake_vroom_worker.py"
        path.write_text(
            textwrap.dedent(
                f"""
                import argparse, json
                from pathlib import Path

                parser=argparse.ArgumentParser()
                parser.add_argument('--self-check', action='store_true')
                parser.add_argument('--input')
                parser.add_argument('--output')
                parser.add_argument('--exploration')
                parser.add_argument('--threads')
                parser.add_argument('--limit')
                args=parser.parse_args()
                if args.self_check:
                    print(json.dumps({{'ok':True,'kind':'self_check','protocol_version':1,'solver_backend':'vroom','solver_version':'1.15.2'}}))
                else:
                    {body or "Path(args.output).write_text(json.dumps({'protocol_version':1,'solver_backend':'vroom','solver_version':'1.15.2','result':{'code':0,'summary':{'cost':7},'routes':[],'unassigned':[]}}), encoding='utf-8')"}
                """
            ),
            encoding="utf-8",
        )
        return path

    def test_run_uses_explicit_worker_and_validates_protocol(self):
        worker = self._fake_worker()
        result, version = runtime.run_vroom_problem(
            {"jobs": [], "vehicles": [], "matrices": {}},
            _config(str(worker)),
        )
        self.assertEqual("1.15.2", version)
        self.assertEqual(0, result["code"])
        self.assertEqual(7, result["summary"]["cost"])

    def test_invalid_quality_settings_fail_before_solve(self):
        worker = self._fake_worker()
        with self.assertRaisesRegex(runtime.VROOMRuntimeError, "between 0 and 5"):
            runtime.run_vroom_problem(
                {"jobs": [], "vehicles": [], "matrices": {}},
                _config(str(worker), vroom_exploration_level=6),
            )

    def test_worker_error_is_not_silently_replaced(self):
        worker = self._fake_worker("raise SystemExit('deliberate failure')")
        with self.assertRaisesRegex(runtime.VROOMRuntimeError, "worker failed"):
            runtime.run_vroom_problem(
                {"jobs": [], "vehicles": [], "matrices": {}},
                _config(str(worker)),
            )

    def test_protected_explicit_path_must_exist(self):
        with self.assertRaisesRegex(runtime.VROOMRuntimeError, "does not exist"):
            runtime._resolve_worker_command(
                _config(str(self.root / "missing-worker.exe"))
            )


if __name__ == "__main__":
    unittest.main()
