"""Version isolation, old-config compatibility, shared rules and real 0.14.1."""
import importlib.metadata
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from config import CVRPConfig, MainConfig, CVRP_SOLVER_TYPES, PYVRP_SOLVER_TYPES
from pyvrp_backends import STABLE, PATCH_0141, backend_for
import pyvrp_next_runtime as runtime
import pyvrp_next_worker as worker
import config_gui
import cvrp_api_server as api
from config_compat import compile_external_config
from customer_deferral import validate_deferral_config

try:
    NATIVE = importlib.metadata.version('pyvrp') == '0.14.1'
except importlib.metadata.PackageNotFoundError:
    NATIVE = False


class Patch0141IntegrationTests(unittest.TestCase):
    def test_is_separate_choice_and_keeps_old_pin(self):
        self.assertIn('pyvrp_0141', CVRP_SOLVER_TYPES)
        self.assertIn('pyvrp_0141', PYVRP_SOLVER_TYPES)
        self.assertEqual('0.14.0', STABLE.version)
        self.assertEqual(PATCH_0141, backend_for(CVRPConfig(solver_type='pyvrp_0141')))
        validate_deferral_config(CVRPConfig(solver_type='pyvrp_0141',customer_dropping_strategy='geographic'))
        self.assertEqual('PyVRP 0.14.1', config_gui.ConfigGUI.SOLVER_LABELS['pyvrp_0141'])

    def test_metadata_does_not_accept_other_versions_or_protocols(self):
        data = worker._metadata('0.14.1', PATCH_0141)
        self.assertEqual('0.14.1', runtime._validate_worker_metadata(data,context='test',backend=PATCH_0141))
        for changes in ({'solver_version':'0.14.0'}, {'solver_backend':'pyvrp_next'}):
            with self.subTest(changes=changes), self.assertRaises(runtime.PyVRPNextRuntimeError):
                runtime._validate_worker_metadata({**data,**changes},context='test',backend=PATCH_0141)

    def test_never_silently_falls_back_even_when_old_toggle_enabled(self):
        cfg=CVRPConfig(solver_type='pyvrp_0141',pyvrp_next_fallback_to_stable=True)
        with patch.object(runtime,'_solve_experimental',side_effect=runtime.PyVRPNextRuntimeError('missing')), patch.object(runtime,'_solve_with_stable_fallback') as fallback:
            with self.assertRaises(runtime.PyVRPNextRuntimeError):
                runtime.solve_cvrp_pyvrp_next(None,None,None,cfg,None,None)
        fallback.assert_not_called()

    def test_legacy_config_upgrade_keeps_values_and_is_idempotent(self):
        source='''from dataclasses import dataclass
CVRP_SOLVER_TYPES = ("pyvrp", "pyvrp_experimental")
PYVRP_SOLVER_TYPES = frozenset(CVRP_SOLVER_TYPES)
@dataclass
class CVRPConfig:
    solver_type: str = "pyvrp_experimental"
@dataclass
class APIConfig:
    tsp_search_seconds: float = 6.0
    api_port: int = 8087
'''
        ns={}
        for _ in range(2): exec(compile_external_config(source,'legacy.py'),ns)
        self.assertEqual(1,ns['CVRP_SOLVER_TYPES'].count('pyvrp_0141'))
        self.assertEqual('pyvrp_experimental',ns['CVRPConfig']().solver_type)
        self.assertEqual('',ns['CVRPConfig']().pyvrp_0141_worker_path)
        self.assertEqual(6,ns['APIConfig']().tsp_search_seconds)
        self.assertEqual(8087,ns['APIConfig']().api_port)
        gui=config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
        saved=gui._replace_field_value(source,'tsp_solver_type','pyvrp_0141','str')
        ns={}; exec(compile_external_config(saved,'saved.py'),ns)
        self.assertEqual('pyvrp_0141',ns['APIConfig']().tsp_solver_type)
        self.assertEqual(6,ns['APIConfig']().tsp_search_seconds)

    def test_api_choice_and_worker_path_protection(self):
        self.assertIn(('cvrp','pyvrp_0141_worker_path'), api._PROTECTED_REQUEST_SETTING_FIELDS)
        from tests.test_pyvrp_experimental_integration import _worker_args, _solution_with_route
        import main
        with patch.object(main,'solve_cvrp_pyvrp_next',return_value=_solution_with_route()) as solve:
            result=main.solve_cvrp_worker(_worker_args('pyvrp_0141'))
        self.assertEqual('pyvrp_0141',solve.call_args.kwargs['config'].solver_type)
        self.assertEqual('pyvrp_0141',result.solver_requested)

    def test_tsp_http_choice_reaches_health_without_changing_cvrp(self):
        cfg=MainConfig()
        original=cfg.cvrp.solver_type
        cfg.api.tsp_solver_type='pyvrp_0141'
        health=api._api_health_payload(cfg.api,'127.0.0.1',18087)
        self.assertEqual('0.14.1',health['capabilities']['tsp']['solver_version'])
        self.assertEqual(original,cfg.cvrp.solver_type)

    def test_tsp_selection_and_isolated_persistent_workers(self):
        import tsp_pyvrp
        from tsp_pyvrp_runtime import worker_for
        cfg=MainConfig()
        self.assertEqual(STABLE,tsp_pyvrp.selected_backend(cfg))
        cfg.api.tsp_solver_type='pyvrp_0141'
        self.assertEqual(PATCH_0141,tsp_pyvrp.selected_backend(cfg))
        self.assertIsNot(worker_for('0.14.0'),worker_for('0.14.1'))
        cfg.api.tsp_solver_type='arbitrary'
        with self.assertRaises(ValueError): tsp_pyvrp.selected_backend(cfg)


from tests.test_pyvrp_stable_014 import NativeStable014Tests

class NativePatch0141Tests(NativeStable014Tests):
    # Override the inherited skip flag: the 0.14.0 base is skipped in 0.14.1.
    __unittest_skip__ = not NATIVE
    __unittest_skip_why__ = 'Requires isolated PyVRP 0.14.1'
    backend_name='pyvrp_0141'
    expected_version='0.14.1'

    def test_real_tsp_worker_keeps_open_end_and_lateness_policy(self):
        from tests.test_tsp_pyvrp import spec
        import tsp_pyvrp
        from tsp_pyvrp_runtime import worker_for
        cfg=MainConfig(); cfg.api.tsp_solver_type='pyvrp_0141'
        data=spec([[28800,32400]])
        for row in data['distances']: row[-1]=0
        for row in data['durations']: row[-1]=0
        try:
            result=tsp_pyvrp.solve_order(data,cfg)
            self.assertEqual('0.14.1',result['solver_version'])
            self.assertEqual([1],result['order'])
            self.assertEqual(1000,result['distance_m'])
            self.assertEqual(1,result['late_count'])
        finally:
            worker_for('0.14.1').close()


if __name__=='__main__': unittest.main()
