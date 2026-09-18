"""Matrix repair uses real directional road values, never masked estimates."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from valhalla_client import ValhallaClient, ROUTE_RECOVERY_BUDGET_SECONDS
from routing_quality import quality_kind, quality_parts


A, B = (43.67586, 24.13795), (42.69454, 23.26557)


def response(distance=229.425, duration=12655.782):
    result = Mock()
    result.json.return_value = {"trip": {"status": 0, "units": "kilometers",
                                        "summary": {"length": distance, "time": duration}}}
    return result


class ValhallaRouteRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.client = ValhallaClient.__new__(ValhallaClient)
        self.client.config = SimpleNamespace(base_url="http://localhost:8002", timeout_seconds=30,
                                             costing="auto")
        self.client.routing_config = SimpleNamespace(enable_time_dependent=False,
                                                      enable_curbside_approach=False)
        self.client.session = Mock()
        self.client.session.post.return_value = response()
        self.client._reset_route_recovery()

    def matrix(self, cells, sources=None, targets=None, request=None):
        return self.client._matrix_values_with_quality(
            {"sources_to_targets": cells}, sources or [A], targets or [B],
            request or {"costing": "auto"},
        )

    def test_null_cell_recovered_and_reported_as_real_road(self):
        distance, duration, quality, remaining = self.matrix([[{"distance": None, "time": None}]])
        self.assertAlmostEqual(distance[0][0], 229425)
        self.assertEqual(duration[0][0], 12655.782)
        self.assertEqual(remaining, 0)
        self.assertEqual(quality_parts(quality[0][0]), ["valhalla", "valhalla", "matrix_route_recovery"])
        self.assertEqual(quality_kind(quality[0][0]), "road")

    def test_valid_matrix_cell_never_makes_route_request(self):
        self.matrix([[{"distance": 3, "time": 120}]])
        self.client.session.post.assert_not_called()

    def test_missing_structure_also_recovered(self):
        self.assertEqual(self.matrix([])[3], 0)

    def test_preserves_truck_options_time_and_curbside(self):
        self.client.routing_config.enable_curbside_approach = True
        self.client.routing_config.valhalla_preferred_side = "same"
        original = {"costing": "truck", "costing_options": {"truck": {"height": 3.5, "weight": 12}},
                    "date_time": {"type": 1, "value": "2026-09-13T08:00"}}
        self.matrix([[None]], request=original)
        args = self.client.session.post.call_args.kwargs
        sent = args["json"]
        self.assertEqual(sent["costing"], "truck")
        self.assertEqual(sent["costing_options"], original["costing_options"])
        self.assertEqual(sent["date_time"], original["date_time"])
        self.assertEqual([p["preferred_side"] for p in sent["locations"]], ["same", "same"])
        self.assertEqual(sent["directions_options"]["units"], "kilometers")
        self.assertLessEqual(args["timeout"], 10)

    def test_cache_is_directional_and_option_sensitive(self):
        self.matrix([[None]])
        self.matrix([[None]])
        self.assertEqual(self.client.session.post.call_count, 1)
        self.matrix([[None]], sources=[B], targets=[A])
        self.matrix([[None]], request={"costing": "truck"})
        self.assertEqual(self.client.session.post.call_count, 3)
        self.client._reset_route_recovery()
        self.matrix([[None]])
        self.assertEqual(self.client.session.post.call_count, 4)

    def test_failed_repair_retains_valid_matrix_component_and_marks_missing_one(self):
        self.client.session.post.side_effect = requests.Timeout("synthetic timeout")
        distances, durations, quality, remaining = self.matrix([[{"distance": 1.25, "time": None}]])
        self.assertEqual(distances[0][0], 1250)
        self.assertGreater(durations[0][0], 0)
        self.assertEqual(quality_parts(quality[0][0]), ["valhalla", "approx", "timeout"])
        self.assertEqual(remaining, 1)

    def test_invalid_route_values_never_become_real_road(self):
        for bad in (None, -1, float("nan"), float("inf"), 1e308, True):
            with self.subTest(value=bad):
                self.client._reset_route_recovery()
                self.client.session.post.return_value = response(distance=bad)
                self.assertEqual(self.matrix([[None]])[3], 1)
        self.client._reset_route_recovery()
        self.client.session.post.return_value.json.return_value = {"trip": {"summary": {}}}
        self.assertEqual(self.matrix([[None]])[3], 1)

    def test_http_error_and_malformed_json_stay_approximate(self):
        for error in (requests.HTTPError("400"), ValueError("invalid JSON")):
            self.client._reset_route_recovery()
            self.client.session.post.side_effect = error
            self.assertEqual(quality_kind(self.matrix([[None]])[2][0][0]), "approximate")

    def test_recovery_budget_prevents_unbounded_extra_requests(self):
        self.client._route_recovery_seconds = ROUTE_RECOVERY_BUDGET_SECONDS
        result = self.matrix([[None]])
        self.client.session.post.assert_not_called()
        self.assertEqual(quality_parts(result[2][0][0])[2], "recovery_budget")

    def test_direct_and_batch_endpoints_both_recover(self):
        matrix_response = Mock()
        matrix_response.json.return_value = {"sources_to_targets": [[None]]}
        self.client.session.post.side_effect = [matrix_response, response()]
        result = self.client._get_submatrix([A], [B])
        self.assertEqual(quality_kind(result["quality"][0][0]), "road")
        self.client._reset_route_recovery()
        self.client.session.post.side_effect = [matrix_response, response()]
        with patch("builtins.print"):
            result = self.client._get_matrix_direct([A])
        self.assertEqual(quality_kind(result.quality[0][0]), "road")


if __name__ == "__main__":
    unittest.main()
