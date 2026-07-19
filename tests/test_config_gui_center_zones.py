import ast
import unittest

import config
from config_gui import ConfigGUI


class CenterZoneEditorDataTests(unittest.TestCase):
    def setUp(self):
        # The methods under test are deliberately data-only and do not need a
        # Tk root, which keeps these tests usable in headless build agents.
        self.gui = ConfigGUI.__new__(ConfigGUI)

    def test_json_round_trip_preserves_multiple_zone_structures(self):
        zones = [
            config.CenterZoneConfig(
                name='Център: "А" | сутрин',
                mode="circle",
                center_coords=(42.6977, 23.3219),
                radius_km=1.75,
                polygon=[],
                enabled=False,
                enable_priority=False,
                enable_restrictions=True,
                priority_vehicle_types=["center_bus"],
                restricted_vehicle_types=["internal_bus", "external_bus"],
                discount_priority_vehicle=0.85,
                priority_vehicle_outside_penalty=123.5,
                vehicle_penalties={
                    "internal_bus": 41000.0,
                    "external_bus": 42000.0,
                    "future_bus": 43000.0,
                },
            ),
            config.CenterZoneConfig(
                name="Полигон Запад",
                mode="polygon",
                center_coords=(42.70, 23.30),
                radius_km=2.25,
                polygon=[(42.70, 23.30), (42.71, 23.31), (42.69, 23.32)],
                enabled=True,
                enable_priority=True,
                enable_restrictions=False,
                priority_vehicle_types=["vratza_bus"],
                restricted_vehicle_types=[],
                discount_priority_vehicle=0.75,
                priority_vehicle_outside_penalty=0.0,
                vehicle_penalties={},
            ),
        ]

        encoded = self.gui._format_center_zones_text(zones)
        decoded = self.gui._parse_center_zones_text(encoded, strict=True)

        self.assertEqual(2, len(decoded))
        for expected, actual in zip(zones, decoded):
            self.assertEqual(expected.name, actual.name)
            self.assertEqual(expected.mode, actual.mode)
            self.assertEqual(expected.center_coords, actual.center_coords)
            self.assertEqual(expected.radius_km, actual.radius_km)
            self.assertEqual(expected.polygon, actual.polygon)
            self.assertEqual(expected.enabled, actual.enabled)
            self.assertEqual(expected.enable_priority, actual.enable_priority)
            self.assertEqual(expected.enable_restrictions, actual.enable_restrictions)
            self.assertEqual(expected.priority_vehicle_types, actual.priority_vehicle_types)
            self.assertEqual(expected.restricted_vehicle_types, actual.restricted_vehicle_types)
            self.assertEqual(expected.discount_priority_vehicle, actual.discount_priority_vehicle)
            self.assertEqual(
                expected.priority_vehicle_outside_penalty,
                actual.priority_vehicle_outside_penalty,
            )
            self.assertEqual(expected.vehicle_penalties, actual.vehicle_penalties)

    def test_circle_and_polygon_geometry_validation(self):
        center, radius, polygon = self.gui._parse_center_zone_geometry(
            "circle", "42.6977, 23.3219", "1.5"
        )
        self.assertEqual((42.6977, 23.3219), center)
        self.assertEqual(1.5, radius)
        self.assertEqual([], polygon)

        polygon_text = "42.70, 23.30\n42.71, 23.31\n42.69, 23.32"
        center, radius, polygon = self.gui._parse_center_zone_geometry(
            "polygon", polygon_text, "999"
        )
        self.assertEqual(polygon[0], center)
        self.assertEqual(0.0, radius)
        self.assertEqual(3, len(polygon))

        invalid_values = [
            ("circle", "42.7, 23.3", "0"),
            ("circle", "91, 23.3", "1"),
            ("polygon", "42.7, 23.3\n42.8, 23.4", "1"),
            ("polygon", "42.7, 23.3\ninvalid\n42.8, 23.4\n42.9, 23.5", "1"),
            ("polygon", "42.7, 23.3\n42.7, 23.3\n42.8, 23.4", "1"),
        ]
        for mode, coords, raw_radius in invalid_values:
            with self.subTest(mode=mode, coords=coords, radius=raw_radius):
                with self.assertRaises(ValueError):
                    self.gui._parse_center_zone_geometry(mode, coords, raw_radius)

    def test_legacy_text_is_still_readable(self):
        raw = (
            "Кръг: circle | 42.7, 23.3 | 1.5 | center_bus | internal_bus | "
            "true | 0.9 | 0 | internal_bus=40000\n"
            "Полигон: polygon | 42.70,23.30;42.71,23.31;42.69,23.32 | 0 | "
            "vratza_bus | external_bus | false | 0.8 | 100 | external_bus=50000"
        )

        zones = self.gui._parse_center_zones_text(raw, strict=True)

        self.assertEqual(["Кръг", "Полигон"], [zone.name for zone in zones])
        self.assertEqual("circle", zones[0].mode)
        self.assertEqual(1.5, zones[0].radius_km)
        self.assertEqual("polygon", zones[1].mode)
        self.assertEqual(3, len(zones[1].polygon))
        self.assertFalse(zones[1].enabled)

    def test_config_source_replacement_is_valid_and_preserves_flags(self):
        zone = config.CenterZoneConfig(
            name='Зона "Тест"',
            mode="polygon",
            center_coords=(42.70, 23.30),
            radius_km=0.0,
            polygon=[(42.70, 23.30), (42.71, 23.31), (42.69, 23.32)],
            enabled=False,
            enable_priority=False,
            enable_restrictions=True,
            priority_vehicle_types=["center_bus"],
            restricted_vehicle_types=["internal_bus"],
            discount_priority_vehicle=0.8,
            priority_vehicle_outside_penalty=50.0,
            vehicle_penalties={"internal_bus": 45678.0},
        )
        raw = self.gui._format_center_zones_text([zone])
        source = (
            "from dataclasses import dataclass, field\n"
            "from typing import List\n"
            "from config import CenterZoneConfig\n\n"
            "@dataclass\n"
            "class LocationConfig:\n"
            "    center_zones: List[CenterZoneConfig] = field(default_factory=lambda: [])\n"
        )

        replaced = self.gui._replace_center_zones_value(source, raw)
        ast.parse(replaced)
        namespace = {}
        exec(replaced, namespace)
        saved = namespace["LocationConfig"]().center_zones[0]

        self.assertEqual(zone.name, saved.name)
        self.assertFalse(saved.enabled)
        self.assertFalse(saved.enable_priority)
        self.assertTrue(saved.enable_restrictions)
        self.assertEqual(zone.polygon, saved.polygon)
        self.assertEqual(zone.vehicle_penalties, saved.vehicle_penalties)

        circle = config.CenterZoneConfig(
            name="Втори кръг",
            mode="circle",
            center_coords=(42.72, 23.34),
            radius_km=1.2,
            enabled=True,
            enable_priority=True,
            enable_restrictions=False,
            priority_vehicle_types=["center_bus"],
            restricted_vehicle_types=[],
            vehicle_penalties={},
        )
        replaced_again = self.gui._replace_center_zones_value(
            replaced,
            self.gui._format_center_zones_text([zone, circle]),
        )
        ast.parse(replaced_again)
        namespace = {}
        exec(replaced_again, namespace)
        saved_zones = namespace["LocationConfig"]().center_zones
        self.assertEqual([zone.name, circle.name], [item.name for item in saved_zones])

    def test_strict_internal_json_rejects_invalid_zones(self):
        invalid = '[{"name":"Bad","mode":"circle","center_coords":[42.7,23.3],"radius_km":0}]'
        self.assertEqual([], self.gui._parse_center_zones_text(invalid))
        with self.assertRaises(ValueError):
            self.gui._parse_center_zones_text(invalid, strict=True)

if __name__ == "__main__":
    unittest.main()
