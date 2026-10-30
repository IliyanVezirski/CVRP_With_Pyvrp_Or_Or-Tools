import importlib.metadata
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tsp_pyvrp import evaluate_order, search_seconds, solve_native, solve_order

try:
    NATIVE = importlib.metadata.version("pyvrp") == "0.14.0"
except importlib.metadata.PackageNotFoundError:
    NATIVE = False


def spec(windows=None):
    return dict(locations=[[42.7,23.3],[42.71,23.31],[42.7,23.3]],
                distances=[[0,1000,0],[1000,0,2000],[0,2000,0]],
                durations=[[0,300,0],[300,0,600],[0,600,0]],
                clients=[dict(service_seconds=120,windows=windows or [])],
                start_seconds=36000,metric="time",wait_weight=1,late_weight=20,search_seconds=.1)


class TspScoringTests(unittest.TestCase):
    def test_subprocess_timeout_accounts_for_query_search_budget(self):
        import json
        import tempfile
        from pathlib import Path
        import cvrp_api_server as api
        from config import MainConfig
        config = MainConfig()
        config.api.tsp_search_seconds = 10
        config.api.tsp_worker_timeout_seconds = 30

        def worker(command, **kwargs):
            self.assertEqual(240, kwargs['timeout'])
            Path(command[-1]).write_text(json.dumps({'status': 'ok'}), encoding='utf-8')
            return SimpleNamespace(returncode=0)

        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'get_config', return_value=config), \
                patch.object(api, '_api_logs_dir', return_value=directory), \
                patch.object(api.subprocess, 'run', side_effect=worker):
            result = api._run_tsp_in_subprocess({}, {'tsp_search_seconds': ['120']})
        self.assertEqual('ok', result['status'])
        self.assertEqual(10, config.api.tsp_search_seconds)

    def test_legacy_config_schema_and_gui_save_preserve_existing_values(self):
        from config_compat import compile_external_config
        from config_gui import ConfigGUI
        source = 'from dataclasses import dataclass\n@dataclass\nclass APIConfig:\n    api_port: int = 8087\n'
        namespace = {}
        exec(compile_external_config(source, 'test-config.py'), namespace)
        self.assertEqual(10, namespace['APIConfig']().tsp_search_seconds)
        gui = ConfigGUI.__new__(ConfigGUI)
        saved = gui._replace_field_value(source, 'tsp_search_seconds', 15, 'float')
        self.assertIn('tsp_search_seconds: float = 15.0', saved)
        self.assertIn('api_port: int = 8087', saved)

    def test_actual_lateness_does_not_use_time_warped_clock(self):
        result=evaluate_order(spec([[28800,32400]]),[1])
        self.assertEqual(3900,result['late_seconds'])
        self.assertEqual(1020,result['duration_seconds'])
        self.assertEqual(79020,result['penalised_cost'])

    def test_wait_until_second_window(self):
        result=evaluate_order(spec([[28800,32400],[39600,43200]]),[1])
        self.assertEqual(3300,result['wait_seconds'])
        self.assertEqual(0,result['late_count'])

    def test_search_budget_validation(self):
        cfg=SimpleNamespace(api=SimpleNamespace())
        self.assertEqual(10,search_seconds({},cfg))
        for value in (-1,0,121,float('nan'),float('inf'),'oops'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                search_seconds({'tsp_search_seconds':value},cfg)

    def test_worker_failure_does_not_use_local_tsp(self):
        cfg=SimpleNamespace(cvrp=SimpleNamespace())
        with patch('pyvrp_next_runtime._resolve_worker_command',side_effect=RuntimeError('missing worker')):
            with self.assertRaisesRegex(RuntimeError,'missing worker'):
                solve_order(spec(),cfg)


@unittest.skipUnless(NATIVE,'Run native tests in .venv-pyvrp-next (0.14.0)')
class NativeTspTests(unittest.TestCase):
    def test_open_route_optimises_last_client_without_return_cost(self):
        data = spec()
        data.update(locations=[[42.7,23.3]]*4,
                    distances=[[0,100,200,0],[100,0,100,0],[1000,100,0,0],[0,0,0,0]],
                    durations=[[0,10,20,0],[10,0,10,0],[100,10,0,0],[0,0,0,0]],
                    clients=[dict(service_seconds=0,windows=[]) for _ in range(2)],
                    metric='distance')
        result = solve_native(data)
        self.assertEqual([1,2], result['order'])
        self.assertEqual(200, result['distance_m'])
        self.assertEqual(20, result['duration_seconds'])
        data['distances'][1][-1] = 100
        data['distances'][2][-1] = 1000
        data['durations'][1][-1] = 10
        data['durations'][2][-1] = 100
        closed = solve_native(data)
        self.assertEqual([2,1], closed['order'])
        self.assertEqual(400, closed['distance_m'])

    def test_fixed_start_end_and_return_leg(self):
        result=solve_native(spec())
        self.assertEqual([1],result['order'])
        self.assertEqual(3000,result['distance_m'])
        self.assertEqual(1020,result['duration_seconds'])

    def test_impossible_window_returns_complete_warned_route(self):
        result=solve_native(spec([[28800,32400]]))
        self.assertEqual([1],result['order'])
        self.assertFalse(result['time_windows_satisfied'])
        self.assertEqual(3900,result['late_seconds'])

    def test_multiple_windows_visit_client_once(self):
        result=solve_native(spec([[28800,32400],[39600,43200]]))
        self.assertEqual([1],result['order'])
        self.assertTrue(result['time_windows_satisfied'])
        self.assertEqual(3300,result['wait_seconds'])

    def test_malformed_matrix_rejected(self):
        data=spec(); data['distances'][1][0]=float('nan')
        with self.assertRaises(ValueError):solve_native(data)

    def test_every_visit_survives_impossible_multiple_windows(self):
        data=spec()
        data.update(locations=[[42.7,23.3]]*5,
                    distances=[[0 if i==j else 100 for j in range(5)] for i in range(5)],
                    durations=[[0 if i==j else 60 for j in range(5)] for i in range(5)],
                    clients=[dict(service_seconds=120,windows=[[100,200],[300,400]]) for _ in range(3)])
        result=solve_native(data)
        self.assertEqual([1,2,3],sorted(result['order']))
        self.assertEqual(3,result['late_count'])


if __name__=='__main__':
    unittest.main()
