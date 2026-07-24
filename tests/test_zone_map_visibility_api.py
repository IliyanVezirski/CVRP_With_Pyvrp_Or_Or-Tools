import unittest

import cvrp_api_server as api


class ZoneMapVisibilityAPITests(unittest.TestCase):
    def test_center_zone_payload_preserves_map_visibility(self):
        zone = api._center_zone_from_payload({
            "name": "Hidden center zone",
            "mode": "circle",
            "center_coords": [42.7, 23.3],
            "radius_km": 1.5,
            "show_on_map": False,
        })

        self.assertFalse(zone.show_on_map)

    def test_center_zone_payload_defaults_to_visible(self):
        zone = api._center_zone_from_payload({
            "name": "Visible by default",
            "mode": "circle",
            "center_coords": [42.7, 23.3],
            "radius_km": 1.5,
        })

        self.assertTrue(zone.show_on_map)

    def test_traffic_zone_payload_preserves_map_visibility(self):
        zone = api._traffic_zone_from_payload({
            "name": "Visible traffic zone",
            "center_coords": [42.7, 23.3],
            "radius_km": 4,
            "duration_multiplier": 1.4,
            "show_on_map": True,
        })

        self.assertTrue(zone.show_on_map)

    def test_traffic_zone_payload_defaults_to_hidden(self):
        zone = api._traffic_zone_from_payload({
            "name": "Hidden by default",
            "center_coords": [42.7, 23.3],
            "radius_km": 4,
            "duration_multiplier": 1.4,
        })

        self.assertFalse(zone.show_on_map)


if __name__ == "__main__":
    unittest.main()
