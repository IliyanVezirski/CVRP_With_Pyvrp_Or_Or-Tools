import unittest
from unittest.mock import patch

from config import MainConfig
from current_tsp import solve_current_tsp_route
from osrm_client import DistanceMatrix


class TspDefaultDepotTests(unittest.TestCase):
    def setUp(self):
        self.config = MainConfig()
        self.config.locations.depot_location = (42.695785029219415, 23.23165887245312)
        self.payload = {
            "driver_id": "TEST_ONLY",
            "generate_map": False,
            "upload_map": False,
            "use_time_windows": False,
            "service_time_minutes": 0,
            "customers": [{"id": "A", "name": "A", "gps": "42.70,23.30", "quantity": 1}],
        }

    def solve(self, payload):
        def matrix(config, locations, truck):
            self.locations = locations
            size = len(locations)
            return DistanceMatrix(
                distances=[r[:size] for r in [[0, 1000, 0], [1000, 0, 2000], [0, 2000, 0]][:size]],
                durations=[r[:size] for r in [[0, 60, 0], [60, 0, 120], [0, 120, 0]][:size]],
                locations=locations, sources=list(range(size)), destinations=list(range(size)),
            )
        with patch("current_tsp._build_distance_matrix", side_effect=matrix), patch(
            "current_tsp._pyvrp_solve_order", return_value={"order": [1], "solver_version": "0.14.0"}
        ) as solver:
            result = solve_current_tsp_route(payload, self.config)
            self.solver_spec = solver.call_args.args[0]
            return result

    def test_missing_null_and_blank_endpoints_start_at_sofia_and_end_at_customer(self):
        for fields in ({}, {"driver_location": None, "end_location": None},
                       {"driver_location": " ", "end_location": ""}):
            with self.subTest(fields=fields):
                result = self.solve({**self.payload, **fields})
                self.assertEqual(self.config.locations.depot_location, self.locations[0])
                self.assertEqual((42.70, 23.30), self.locations[-1])
                self.assertFalse(result["has_fixed_end_location"])
                self.assertIsNone(result["end_location"])
                self.assertEqual(1, result["total_distance_km"])
                self.assertEqual(1, result["total_time_minutes"])
                self.assertEqual(3, len(self.solver_spec['locations']))
                self.assertTrue(all(row[-1] == 0 for row in self.solver_spec['distances']))
                self.assertTrue(all(row[-1] == 0 for row in self.solver_spec['durations']))
                self.assertEqual(1, result["delivery_order"][-1]["cumulative_distance_km"])

    def test_explicit_endpoints_and_independent_defaults(self):
        start, end = (42.71, 23.31), (42.72, 23.32)
        for fields, expected_start, expected_end in (
            ({"driver_location": start}, start, (42.70, 23.30)),
            ({"end_location": end}, self.config.locations.depot_location, end),
            ({"driver_location": start, "end_location": end}, start, end),
            ({"currentLocation": start, "endLocation": end}, start, end),
        ):
            with self.subTest(fields=fields):
                self.solve({**self.payload, **fields})
                self.assertEqual(expected_start, self.locations[0])
                self.assertEqual(expected_end, self.locations[-1])

    def test_explicit_end_still_includes_final_leg(self):
        result = self.solve({**self.payload, 'end_location': [42.72, 23.32]})
        self.assertTrue(result['has_fixed_end_location'])
        self.assertEqual(3, result['total_distance_km'])
        self.assertEqual(3, result['total_time_minutes'])

    def test_map_ends_at_last_client_not_virtual_depot(self):
        with patch('current_tsp._create_tsp_map_document') as create_map, patch('current_tsp._render_tsp_map_html', return_value='map'):
            create_map.return_value = (None, None)
            self.solve({**self.payload, '_return_html_response': True})
            route = create_map.call_args.args[1]
            self.assertEqual((42.70, 23.30), route.end_location)

    def test_custom_field_mapping_keeps_priority(self):
        self.config.api.tsp_driver_location_field = "vehicle_gps"
        self.config.api.tsp_end_location_field = "finish_gps"
        self.solve({**self.payload, "vehicle_gps": [42.73, 23.33], "finish_gps": [42.74, 23.34]})
        self.assertEqual((42.73, 23.33), self.locations[0])
        self.assertEqual((42.74, 23.34), self.locations[-1])

    def test_nonblank_invalid_endpoints_are_not_silently_replaced(self):
        for field in ("driver_location", "end_location"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                self.solve({**self.payload, field: "invalid GPS"})

    def test_late_visit_is_successful_and_visible_in_map_warning(self):
        from output_handler import InteractiveMapGenerator
        from cvrp_solver import Route
        payload={**self.payload, 'start_time':'10:00', 'use_time_windows':True,
                 'customers':[{'id':'A','name':'Late <client>','gps':'42.70,23.30','work_time':'08:00-09:00'}]}
        result=self.solve(payload)
        self.assertEqual('ok',result['status'])
        self.assertEqual(61,result['delivery_order'][0]['late_minutes'])
        self.assertEqual(1,result['solver']['late_customers'])
        self.assertIn('закъснение',result['delivery_order'][0]['time_window_status'])
        generator=InteractiveMapGenerator.__new__(InteractiveMapGenerator)
        from types import SimpleNamespace
        route=SimpleNamespace(schedule_entries=[{'customer':SimpleNamespace(name='Late <client>'),'late_minutes':61}])
        warning=generator._tsp_warning_html(route)
        self.assertIn('Извън работното време',warning)
        self.assertIn('Late &lt;client&gt;',warning)


if __name__ == "__main__":
    unittest.main()
