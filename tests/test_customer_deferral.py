import math
import time
import unittest
from types import SimpleNamespace

from config import CVRPConfig, MainConfig, calculate_customer_drop_penalties
from customer_deferral import (
    DEFERRAL_FIELDS, geographic_penalties, prepare_customer_depots,
    solution_selection_key, validate_deferral_config,
)
from input_handler import Customer


def customer(stock="106", volume=1):
    return Customer("c", "c", (42, 23), volume, "", source_id_skld=stock)


def config(**kwargs):
    return CVRPConfig(solver_type="pyvrp_experimental", customer_dropping_strategy="geographic", **kwargs)


class PolicyTests(unittest.TestCase):
    def test_legacy_default_and_formula_unchanged(self):
        cfg = CVRPConfig(enable_priority_dropping=False, distance_penalty_disjunction=12345)
        self.assertEqual(cfg.customer_dropping_strategy, "legacy")
        self.assertEqual(calculate_customer_drop_penalties([customer(), customer()], [(0, 0)], cfg), [12345, 12345])

    def test_own_depot_asymmetric_road_distance_and_no_volume_bias(self):
        customers = [customer(volume=1), customer(volume=100)]
        for c in customers:
            c._deferral_depots = [(1, 1)]
        matrix = SimpleNamespace(distances=[[0, 1000, 1000, 1000],
                                            [1000, 0, 80000, 80000],
                                            [1000, 120000, 0, 0],
                                            [1000, 120000, 0, 0]])
        prizes = geographic_penalties(customers, [(0, 0), (1, 1)], matrix, config())
        self.assertEqual(prizes, [1170000, 1170000])
        self.assertEqual(customers[0]._deferral_distance_km, 100)

    def test_resolve_stock_ids_and_grouped_sources(self):
        cfg = MainConfig()
        cfg.locations.depot_location = (0, 0)
        cfg.locations.vratza_depot_location = (1, 1)
        cfg.set_data.set_data_id_skld = "106"
        cfg.set_data.set_data_vratza_id_skld = "128"
        cfg.set_data.set_data_depot_id_skld_map = ""
        visits = [customer("106"), customer("128"), customer("")]
        visits[0].grouped_documents = [{"source_id_skld": "128"}]
        prepare_customer_depots(visits, cfg)
        self.assertEqual(visits[0]._deferral_depots, [(0, 0), (1, 1)])
        self.assertEqual(visits[1]._deferral_depots, [(1, 1)])
        self.assertEqual(visits[2]._deferral_depots, [(0, 0)])
        with self.assertRaisesRegex(ValueError, "999"):
            prepare_customer_depots([customer("999")], cfg)

    def test_reject_unreachable_or_missing_matrix_depot(self):
        c = customer()
        c._deferral_depots = [(0, 0)]
        for bad in (math.nan, math.inf, -1, 1e9):
            with self.assertRaises(ValueError):
                geographic_penalties([c], [(0, 0)], SimpleNamespace(distances=[[0, bad], [1, 0]]), config())
        c._deferral_depots = [(1, 1)]
        with self.assertRaisesRegex(ValueError, "липсва"):
            geographic_penalties([c], [(0, 0)], SimpleNamespace(distances=[[0, 1], [1, 0]]), config())

    def test_validation_and_consistent_time_selection(self):
        cfg = config(objective_metric="time")
        a = SimpleNamespace(dropped_customers=[1], fitness_score=100, deferral_selection_score=800)
        b = SimpleNamespace(dropped_customers=[1] * 5, fitness_score=200, deferral_selection_score=600)
        self.assertEqual(min([a, b], key=lambda s: solution_selection_key(s, cfg)), b)
        cfg.customer_dropping_strategy = "legacy"
        self.assertEqual(min([a, b], key=lambda s: solution_selection_key(s, cfg)), a)
        cfg.customer_dropping_strategy = "geographic"
        cfg.solver_type = "pyvrp"
        with self.assertRaisesRegex(ValueError, "0.14"):
            validate_deferral_config(cfg)
        for name, value in (("drop_geo_strength", float("nan")), ("drop_repair_max_ejections", 1.5),
                            ("drop_repair_budget_fraction", 0.5), ("drop_geo_reference_km", 0)):
            cfg = config()
            setattr(cfg, name, value)
            with self.assertRaises(ValueError):
                validate_deferral_config(cfg)

    def test_gui_api_fields_and_desktop_validation(self):
        import cvrp_api_server as api
        from config_gui import ConfigGUI
        from customer_deferral import DEFERRAL_LABELS
        self.assertTrue(DEFERRAL_FIELDS <= api._WEB_GUI_CVRP_FIELDS)
        self.assertTrue(DEFERRAL_FIELDS <= api._WEB_RUN_CVRP_FIELDS)
        page = api._web_gui_html(MainConfig().api, "127.0.0.1", 8085)
        self.assertIn('id="customerDroppingStrategy"', page)
        self.assertIn(DEFERRAL_LABELS["legacy"], page)
        self.assertIn(DEFERRAL_LABELS["geographic"], page)
        self.assertNotIn("Стар начин", page)
        self.assertNotIn("Нов: географски", page)
        for field in ("priorityDroppingEnabled", "dropVolumeWeight", "dropClosenessWeight",
                      "dropMinPenalty", "dropMaxPenalty", "legacyDeferralFields"):
            self.assertIn(f'id="{field}"', page)
        gui = object.__new__(ConfigGUI)
        gui.cfg = MainConfig()
        gui.controls = {}
        errors, _ = gui._validate_values({
            "cvrp.solver_type": "pyvrp_experimental",
            "cvrp.customer_dropping_strategy": DEFERRAL_LABELS["geographic"],
            "cvrp.drop_geo_reference_km": "0",
        })
        self.assertTrue(any("drop_geo_reference_km" in msg for _, msg in errors))

    def test_web_temporary_settings_and_literal_round_trip(self):
        import ast
        import cvrp_api_server as api
        cfg = MainConfig()
        changed = api._apply_web_run_settings(cfg, {"cvrp": {
            "solver_type": "pyvrp_experimental", "customer_dropping_strategy": "geographic",
            "drop_geo_reference_km": 30, "drop_repair_max_ejections": 5,
        }})
        self.assertIn("cvrp.customer_dropping_strategy", changed)
        self.assertEqual(cfg.cvrp.customer_dropping_strategy, "geographic")
        self.assertEqual(CVRPConfig().customer_dropping_strategy, "legacy")
        text = 'class CVRPConfig:\n    customer_dropping_strategy: str = "legacy"\n'
        text = api._replace_class_field_literal(text, "CVRPConfig", "customer_dropping_strategy", "geographic")
        self.assertEqual(ast.literal_eval(ast.parse(text).body[0].body[0].value), "geographic")
        from config_gui import ConfigGUI
        gui = object.__new__(ConfigGUI)
        text = gui._replace_field_value(text, "customer_dropping_strategy", "legacy", "str")
        self.assertEqual(ast.literal_eval(ast.parse(text).body[0].body[0].value), "legacy")

    def test_volume_closeness_web_settings_round_trip_and_validation(self):
        import cvrp_api_server as api
        cfg = MainConfig()
        values = {
            "customer_dropping_strategy": "legacy", "enable_priority_dropping": True,
            "drop_volume_weight": 0.2, "drop_closeness_weight": 0.8,
            "min_customer_drop_penalty": 50000, "max_customer_drop_penalty": 600000,
        }
        self.assertTrue(values.keys() <= api._WEB_GUI_CVRP_FIELDS)
        self.assertTrue(values.keys() <= api._WEB_RUN_CVRP_FIELDS)
        applied = api._apply_web_run_settings(cfg, {"cvrp": values})
        for name, value in values.items():
            self.assertEqual(getattr(cfg.cvrp, name), value)
            self.assertIn("cvrp." + name, applied)
        self.assertNotEqual(CVRPConfig().drop_volume_weight, cfg.cvrp.drop_volume_weight)
        cfg.cvrp.min_customer_drop_penalty = 700000
        with self.assertRaisesRegex(ValueError, "Максималната глоба"):
            api._validate_web_run_config(cfg)
        cfg.cvrp.min_customer_drop_penalty = 50000
        cfg.cvrp.drop_volume_weight = math.nan
        with self.assertRaisesRegex(ValueError, "drop_volume_weight"):
            api._validate_web_run_config(cfg)


try:
    from pyvrp import Activity, ActivityType, CostEvaluator, Model, Route, Solution
    NATIVE_014 = hasattr(Model, "add_location")
except ImportError:
    NATIVE_014 = False


@unittest.skipUnless(NATIVE_014, "Requires isolated PyVRP 0.14 environment")
class NativeRepairTests(unittest.TestCase):
    def build_case(self, far_prize=500000, required_near=False, grouped=False, max_distance=1000000,
                   impossible_window=False):
        model = Model()
        positions = [0, 20000, 20000, 20000, 20000, 20000, 100000]
        locations = [model.add_location(x=x, y=0) for x in positions]
        model.add_depot(location=locations[0])
        for idx in range(1, 6):
            model.add_client(location=locations[idx], delivery=1, prize=50000, required=required_near)
        group = model.add_client_group(required=False) if grouped else None
        model.add_client(location=locations[6], delivery=5, prize=far_prize, required=False, group=group,
                         tw_late=10 if impossible_window else 1000000)
        original = list(range(6))
        if grouped:
            model.add_client(location=locations[6], delivery=5, prize=far_prize, required=False, group=group)
            original.append(5)
        model.add_vehicle_type(capacity=5, max_distance=max_distance)
        for i, frm in enumerate(locations):
            for j, to in enumerate(locations):
                model.add_edge(frm, to, distance=abs(positions[i] - positions[j]),
                               duration=abs(positions[i] - positions[j]) // 10)
        data = model.data()
        solution = Solution(data, [Route(data, [Activity(ActivityType.CLIENT, i) for i in range(5)], 0)])
        evaluator = CostEvaluator([1000000], 1000000, 1000000)
        from pyvrp_deferral_search import GeographicRepair
        return data, solution, evaluator, GeographicRepair(data, original)

    def test_five_for_one_is_found_inside_repair(self):
        _, old, evaluator, repair = self.build_case()
        with self.assertLogs("pyvrp_deferral_search", level="DEBUG") as captured:
            new = repair.improve(old, evaluator, time.monotonic() + 2)
        moves = [record for record in captured.records if "Geographic repair accepted:" in record.getMessage()]
        self.assertTrue(moves)
        self.assertTrue(all(record.levelname == "DEBUG" for record in moves))
        self.assertTrue(new.is_feasible())
        self.assertEqual(repair.served(new), {5})
        self.assertLess(evaluator.cost(new), evaluator.cost(old))
        self.assertEqual(repair.stats["max_ejected"], 5)

    def test_group_windows_do_not_duplicate(self):
        _, old, evaluator, repair = self.build_case(grouped=True)
        new = repair.improve(old, evaluator, time.monotonic() + 2)
        self.assertTrue(new.is_feasible())
        self.assertEqual(new.num_clients(), 1)
        self.assertEqual(repair.served(new), {5})

    def test_mandatory_distance_and_nonimproving_moves_preserved(self):
        for params in ({"required_near": True}, {"far_prize": 45000}, {"max_distance": 150000},
                       {"impossible_window": True}):
            with self.subTest(params=params):
                _, old, evaluator, repair = self.build_case(**params)
                self.assertIs(repair.improve(old, evaluator, time.monotonic() + 2), old)

    def test_external_audit_veto_and_expired_budget(self):
        _, old, evaluator, repair = self.build_case()
        repair.audit = lambda candidate: False
        self.assertIs(repair.improve(old, evaluator, time.monotonic() + 2), old)
        self.assertGreater(repair.stats["audit_rejected"], 0)
        before = repair.stats["evaluated"]
        self.assertIs(repair.improve(old, evaluator, time.monotonic() - 1), old)
        self.assertEqual(before, repair.stats["evaluated"])

    def test_adapter_synthetic_end_to_end(self):
        from tests.test_pyvrp_multitrip import make_customers, make_matrix, make_vehicle, make_config, DEPOT
        from pyvrp_solver import PyVRPSolver
        from config import LocationConfig
        customers = make_customers(4, volume=4)
        cfg = make_config(solver_type="pyvrp_experimental", customer_dropping_strategy="geographic",
                          allow_customer_skipping=True, objective_metric="time", time_limit_seconds=1)
        vehicle = make_vehicle(capacity=8, max_time_hours=2)
        solution = PyVRPSolver(cfg, [vehicle], customers, make_matrix(customers), [DEPOT],
                               location_config=LocationConfig()).solve()
        self.assertTrue(solution.is_feasible)
        self.assertEqual(solution.deferral_strategy, "geographic")
        self.assertTrue(math.isfinite(solution.deferral_selection_score))
        self.assertEqual(sum(len(r.customers) for r in solution.routes), 4)

    def test_adapter_rejects_fallback_instead_of_ignoring_policy(self):
        from tests.test_pyvrp_multitrip import make_customers, make_matrix, make_vehicle, make_config, DEPOT
        from pyvrp_solver import PyVRPSolver
        customers = make_customers(4)
        cfg = make_config(solver_type="pyvrp_experimental", customer_dropping_strategy="geographic")
        solver = PyVRPSolver(cfg, [make_vehicle(max_customers_per_day=2)], customers,
                             make_matrix(customers), [DEPOT])
        with self.assertRaisesRegex(ValueError, "fallback"):
            solver.solve()


if __name__ == "__main__":
    unittest.main()
