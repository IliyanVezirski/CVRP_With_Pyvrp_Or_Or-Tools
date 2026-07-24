import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import (
    CenterZoneConfig,
    LocationConfig,
    OutputConfig,
    TrafficZoneConfig,
    get_center_zones,
    get_traffic_zones,
)
from input_handler import Customer
from output_handler import (
    InteractiveMapGenerator,
    _group_routes_for_vehicle_maps,
    _vehicle_route_map_output_key,
    _visible_center_zones,
    _visible_traffic_zones,
)


class OutputMapZoneVisibilityTests(unittest.TestCase):
    def test_center_visibility_does_not_remove_zone_from_solver_configuration(self):
        visible = CenterZoneConfig(
            name="Visible",
            center_coords=(43.0, 23.0),
            radius_km=2,
            show_on_map=True,
        )
        hidden = CenterZoneConfig(
            name="Hidden",
            center_coords=(44.0, 24.0),
            radius_km=2,
            show_on_map=False,
        )
        locations = LocationConfig(
            show_center_zone_on_map=False,
            center_zones=[visible, hidden],
        )

        self.assertEqual(
            ["Visible"],
            [zone.name for zone in _visible_center_zones(locations)],
        )
        self.assertIn("Hidden", [zone.name for zone in get_center_zones(locations)])

        locations.show_center_zone_on_map = True
        visible_names = [zone.name for zone in _visible_center_zones(locations)]
        self.assertIn("Основна център зона", visible_names)
        self.assertIn("Visible", visible_names)
        self.assertNotIn("Hidden", visible_names)

    def test_traffic_zones_are_opt_in_and_solver_rules_remain_active(self):
        visible = TrafficZoneConfig(
            name="Visible traffic",
            center_coords=(43.0, 23.0),
            radius_km=2,
            duration_multiplier=1.4,
            show_on_map=True,
        )
        hidden = TrafficZoneConfig(
            name="Hidden traffic",
            center_coords=(44.0, 24.0),
            radius_km=2,
            duration_multiplier=1.5,
            show_on_map=False,
        )
        locations = LocationConfig(
            enable_city_traffic_adjustment=True,
            show_city_traffic_zone_on_map=False,
            traffic_zones=[visible, hidden],
        )

        self.assertEqual(
            ["Visible traffic"],
            [zone.name for zone in _visible_traffic_zones(locations)],
        )
        solver_zone_names = [zone.name for zone in get_traffic_zones(locations)]
        self.assertIn("Visible traffic", solver_zone_names)
        self.assertIn("Hidden traffic", solver_zone_names)

        locations.show_city_traffic_zone_on_map = True
        visible_names = [zone.name for zone in _visible_traffic_zones(locations)]
        self.assertIn("Основна зона", visible_names)
        self.assertIn("Visible traffic", visible_names)


class VehicleMapGroupingTests(unittest.TestCase):
    @staticmethod
    def _route(trip_number, route_id, customers=None):
        customers = list(customers or [])
        schedule_entries = []
        for index, customer in enumerate(customers, start=1):
            schedule_entries.append({
                "index": index,
                "customer": customer,
                "start_time_minutes": 480,
                "arrival_time_minutes": 500 + index * 10,
                "total_time_with_start": 505 + index * 10,
                "time_window_text": "08:00-12:00" if customer.time_windows else "",
                "time_window_status": "",
                "delivery_comment": customer.delivery_comment,
            })
        return SimpleNamespace(
            vehicle_key="physical-A",
            trip_number=trip_number,
            trip_count=2,
            route_id=route_id,
            vehicle_type=SimpleNamespace(value="internal_bus"),
            vehicle_id=0,
            vehicle_name="Bus A",
            customers=customers,
            schedule_entries=schedule_entries,
            depot_location=(42.7, 23.3),
            end_location=(42.7, 23.3),
            total_distance_km=10.0,
            total_time_minutes=20.0,
            total_volume=sum(customer.volume for customer in customers) or 5.0,
        )

    @staticmethod
    def _customer(customer_id, name, lat, comment):
        customer = Customer(
            id=customer_id,
            name=name,
            coordinates=(lat, 23.3),
            volume=2.5,
            original_gps_data=f"{lat},23.3",
            time_windows=[(480, 720)],
            delivery_comment=comment,
        )
        customer.quantity = 3
        customer.turnover = 42.5
        return customer

    @staticmethod
    def _map_generator(provider):
        generator = InteractiveMapGenerator.__new__(InteractiveMapGenerator)
        generator.config = OutputConfig(
            map_provider=provider,
            google_maps_api_key="test-key",
            map_zoom_level=10,
            folium_tiles="OpenStreetMap",
        )
        generator.use_routing = False
        generator.routing_engine = None
        return generator

    @staticmethod
    def _config_without_zones():
        return SimpleNamespace(
            locations=LocationConfig(
                enable_center_zone_priority=False,
                enable_center_zone_restrictions=False,
                enable_city_traffic_adjustment=False,
            ),
            vehicles=[],
            cvrp=SimpleNamespace(enable_customer_time_windows=False),
        )

    def test_courses_share_one_vehicle_map_group_without_losing_global_indexes(self):
        routes = [
            SimpleNamespace(vehicle_key="A"),
            SimpleNamespace(vehicle_key="A"),
            SimpleNamespace(vehicle_key="B"),
        ]

        groups = _group_routes_for_vehicle_maps(routes)

        self.assertEqual([[0, 1], [2]], [[idx for idx, _ in group] for group in groups])
        self.assertEqual(routes[:2], [route for _, route in groups[0]])
        self.assertEqual(
            ["route_map_vehicle_1", "route_map_vehicle_2"],
            [_vehicle_route_map_output_key(index) for index, _ in enumerate(groups, start=1)],
        )

    def test_grouped_google_map_has_one_document_and_distinct_course_metadata(self):
        first_customer = self._customer("C-1", "Client One", 42.71, "First comment")
        second_customer = self._customer("C-2", "Client Two", 42.72, "Second comment")
        routes = [
            self._route(1, "A-1", [first_customer]),
            self._route(2, "A-2", [second_customer]),
        ]
        generator = self._map_generator("google")

        with patch("output_handler.get_config", return_value=self._config_without_zones()):
            document = generator.create_vehicle_route_map(
                routes, [1, 2], (42.7, 23.3), routes
            )

        match = re.search(
            r"const MAP_DATA = (.*?);\s*let infoWindow;",
            document.html_content,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match)
        map_data = json.loads(match.group(1))
        self.assertTrue(map_data["courseFilter"])
        self.assertEqual(2, len(map_data["routes"]))
        self.assertEqual(["A-1", "A-2"], [item["routeId"] for item in map_data["routes"]])
        self.assertEqual(2, len({item["outputNumber"] for item in map_data["routes"]}))
        self.assertEqual(2, len({item["id"] for item in map_data["routes"]}))
        self.assertTrue(all(f"№ {item['outputNumber']}" in item["name"] for item in map_data["routes"]))
        self.assertEqual([1, 2], [item["routeNumber"] for item in map_data["routes"]])
        self.assertEqual(["Client One", "Client Two"], [item["markers"][0]["customerName"] for item in map_data["routes"]])
        for item in map_data["routes"]:
            marker = item["markers"][0]
            self.assertEqual("08:00-12:00", marker["timeWindowText"])
            self.assertTrue(marker["deliveryComment"].endswith("comment"))
            self.assertEqual(3.0, marker["quantity"])
            self.assertEqual(42.5, marker["turnover"])
        self.assertIn("renderCourseCustomerPanel", document.html_content)
        self.assertIn('name="active-course"', document.html_content)
        self.assertIn("objects.startMarker.setMap", document.html_content)
        self.assertEqual(
            2,
            document._cvrp_top_html_comment.count("CVRP_ROUTE_DELIVERY_ORDER"),
        )

    def test_grouped_folium_map_has_exclusive_course_layers_and_rich_customer_panel(self):
        routes = [
            self._route(1, "A-1", [self._customer("C-1", "Client One", 42.71, "First comment")]),
            self._route(2, "A-2", [self._customer("C-2", "Client Two", 42.72, "Second comment")]),
        ]
        generator = self._map_generator("osm")

        with patch("output_handler.get_config", return_value=self._config_without_zones()):
            route_map = generator.create_vehicle_route_map(
                routes, [1, 2], (42.7, 23.3), routes
            )

        course_layers = [
            child
            for child in route_map._children.values()
            if child.__class__.__name__ == "FeatureGroup"
            and "Курс" in str(getattr(child, "layer_name", ""))
        ]
        self.assertEqual(2, len(course_layers))
        layer_names = "\n".join(str(layer.layer_name) for layer in course_layers)
        self.assertIn("Курс 1", layer_names)
        self.assertIn("Курс 2", layer_names)
        self.assertNotIn("A-1", layer_names)
        self.assertEqual(
            0,
            sum(
                child.__class__.__name__ == "LayerControl"
                for child in route_map._children.values()
            ),
        )
        self.assertEqual(
            1,
            sum(
                child.__class__.__name__ == "MultiCourseCustomerPanel"
                for child in route_map._children.values()
            ),
        )
        rendered = route_map.get_root().render()
        for expected in (
            "Client One",
            "Client Two",
            "08:00-12:00",
            "Тръгване",
            "First comment",
            "Second comment",
            "Навигация",
            "Street View",
            "leaflet-active-course",
        ):
            self.assertIn(expected, rendered)

    def test_overview_google_labels_hide_raw_route_ids(self):
        routes = [
            self._route(1, "A-1", [self._customer("C-1", "Client One", 42.71, "")]),
            self._route(2, "A-2"),
        ]
        generator = self._map_generator("google")

        payload = generator._build_google_routes(
            routes,
            all_routes=routes,
            show_bus_ids=False,
        )

        self.assertIn("Курс 1", payload[0]["name"])
        self.assertNotIn("A-1", payload[0]["name"])
        self.assertNotIn("ID ", payload[0]["name"])
        self.assertNotIn("routeId", payload[0])
        self.assertNotIn("vehicleKey", payload[0])
        self.assertEqual("route-1", payload[0]["id"])
        self.assertNotIn("A-1", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("Маршрут 1", payload[0]["popup"])
        self.assertNotIn("Автобус 1", payload[0]["popup"])
        self.assertNotIn("Маршрут 1", payload[0]["markers"][0]["popup"])

    def test_overview_folium_keeps_layer_control_and_clean_course_labels(self):
        routes = [
            self._route(1, "A-1", [self._customer("C-1", "Client One", 42.71, "")]),
            self._route(2, "A-2"),
        ]
        generator = self._map_generator("osm")
        route_map = generator._create_folium_map((42.7, 23.3))

        generator._add_routes_to_map(route_map, routes, all_routes=routes)

        self.assertEqual(
            1,
            sum(
                child.__class__.__name__ == "LayerControl"
                for child in route_map._children.values()
            ),
        )
        layer_names = "\n".join(
            str(child.layer_name)
            for child in route_map._children.values()
            if child.__class__.__name__ == "FeatureGroup"
        )
        self.assertIn("Курс 1", layer_names)
        self.assertNotIn("A-1", layer_names)
        self.assertNotIn("A-1", route_map.get_root().render())
        self.assertNotIn("Маршрут 1", route_map.get_root().render())

    def test_individual_maps_keep_route_numbers_for_course_filtering(self):
        routes = [
            self._route(1, "A-1", [self._customer("C-1", "Client One", 42.71, "")]),
            self._route(2, "A-2"),
        ]
        google = self._map_generator("google")
        folium_generator = self._map_generator("osm")

        google_payload = google._build_google_routes(
            routes,
            all_routes=routes,
            show_bus_ids=True,
            route_numbers=[1, 2],
        )
        self.assertIn("Маршрут 1", google_payload[0]["markers"][0]["popup"])
        self.assertEqual("A-1", google_payload[0]["routeId"])

        folium_map = folium_generator._create_folium_map((42.7, 23.3))
        course_metadata = folium_generator._add_routes_to_map(
            folium_map,
            routes,
            route_numbers=[1, 2],
            all_routes=routes,
            add_layer_control=False,
        )
        rendered = folium_map.get_root().render()
        self.assertIn("Маршрут 1", rendered)
        self.assertEqual("A-1", course_metadata[0]["route_id"])

    def test_grouped_maps_handle_course_with_only_missing_gps(self):
        missing = Customer(
            id="NO-GPS",
            name="Missing GPS",
            coordinates=None,
            volume=1.0,
            original_gps_data="",
            delivery_comment="Call first",
        )
        routes = [self._route(1, "A-1", [missing]), self._route(2, "A-2")]
        google = self._map_generator("google")
        folium_generator = self._map_generator("osm")

        with patch("output_handler.get_config", return_value=self._config_without_zones()):
            google_document = google.create_vehicle_route_map(routes, [1, 2], (42.7, 23.3), routes)
            folium_map = folium_generator.create_vehicle_route_map(routes, [1, 2], (42.7, 23.3), routes)

        match = re.search(r"const MAP_DATA = (.*?);\s*let infoWindow;", google_document.html_content, flags=re.DOTALL)
        map_data = json.loads(match.group(1))
        self.assertEqual(1, map_data["routes"][0]["customerCount"])
        self.assertEqual(1, map_data["routes"][0]["googleMissingCoords"])
        self.assertEqual([], map_data["routes"][0]["markers"])
        rendered = folium_map.get_root().render()
        self.assertIn("1 клиент(а) без GPS", rendered)
        self.assertIn("Няма клиенти с валидни GPS координати", rendered)

if __name__ == "__main__":
    unittest.main()
