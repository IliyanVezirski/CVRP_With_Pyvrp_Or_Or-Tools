import unittest

import config
import cvrp_api_server as api
from config_gui import ConfigGUI


class _Panel:
    def __init__(self):
        self.hidden = False
        self.pack_calls = []

    def pack_forget(self):
        self.hidden = True

    def pack(self, **kwargs):
        self.hidden = False
        self.pack_calls.append(kwargs)


class _GridPanel:
    def __init__(self):
        self.visible = True

    def grid(self):
        self.visible = True

    def grid_remove(self):
        self.visible = False


class SolverGuiPanelsTests(unittest.TestCase):
    def test_desktop_panel_switch_uses_display_label_and_keeps_solver_specific_rows(self):
        gui = object.__new__(ConfigGUI)
        quality = _Panel()
        penalty = _Panel()
        next_worker = _Panel()
        ortools = _Panel()
        vroom = _Panel()
        vrp = _Panel()
        parallel = _Panel()
        anchor = object()
        gui.solver_fine_anchor = anchor
        gui.pyvrp_parallel_specific = _GridPanel()
        gui.ortools_parallel_specific = _GridPanel()
        gui.solver_fine_panels = {
            "pyvrp": (quality, penalty, parallel),
            "pyvrp_experimental": (quality, penalty, next_worker, parallel),
            "or_tools": (ortools, parallel),
            "vroom": (vroom,),
            "vrp": (vrp,),
        }

        gui._show_solver_fine_settings("PyVRP 0.14")

        self.assertFalse(quality.hidden)
        self.assertFalse(penalty.hidden)
        self.assertFalse(next_worker.hidden)
        self.assertFalse(parallel.hidden)
        self.assertTrue(ortools.hidden)
        self.assertTrue(vroom.hidden)
        self.assertTrue(vrp.hidden)
        self.assertTrue(gui.pyvrp_parallel_specific.visible)
        self.assertFalse(gui.ortools_parallel_specific.visible)
        self.assertTrue(all(call["before"] is anchor for call in quality.pack_calls + parallel.pack_calls))

        gui._show_solver_fine_settings("OR-Tools")

        self.assertFalse(ortools.hidden)
        self.assertFalse(parallel.hidden)
        self.assertTrue(quality.hidden)
        self.assertFalse(gui.pyvrp_parallel_specific.visible)
        self.assertTrue(gui.ortools_parallel_specific.visible)

    def test_every_solver_has_a_web_panel_and_safe_runtime_fields(self):
        page = api._web_gui_html(config.get_config().api, "127.0.0.1", 8085)
        for solver in config.CVRP_SOLVER_TYPES:
            self.assertIn(solver, page)
        for field_id in (
            "pyvrpNumNeighbours",
            "pyvrpNextTimeout",
            "orFirstSolution",
            "orParallelFirstStrategies",
            "orParallelMetaheuristics",
            "vroomExploration",
            "vrpMaxGenerations",
        ):
            self.assertIn(f'id="{field_id}"', page)
        self.assertIn('document.querySelectorAll("[data-solvers]")', page)
        self.assertIn('split(/[,;\\r\\n]+/)', page)
        self.assertIn("pyvrp_num_neighbours", api._WEB_RUN_CVRP_FIELDS)
        self.assertIn("first_solution_strategy", api._WEB_RUN_CVRP_FIELDS)
        self.assertIn("parallel_first_solution_strategies", api._WEB_RUN_CVRP_FIELDS)
        self.assertIn("parallel_local_search_metaheuristics", api._WEB_RUN_CVRP_FIELDS)
        self.assertIn("vroom_worker_timeout_seconds", api._WEB_RUN_CVRP_FIELDS)
        self.assertIn("vrp_max_generations", api._WEB_RUN_CVRP_FIELDS)

    def test_pyvrp_fine_fields_are_real_config_fields(self):
        cvrp = config.CVRPConfig()
        for field_name in (
            "pyvrp_weight_wait_time",
            "pyvrp_symmetric_proximity",
            "pyvrp_display_interval_seconds",
            "pyvrp_use_library_penalty_defaults",
            "pyvrp_penalty_solutions_between_updates",
            "pyvrp_penalty_max",
        ):
            self.assertTrue(hasattr(cvrp, field_name), field_name)


if __name__ == "__main__":
    unittest.main()
