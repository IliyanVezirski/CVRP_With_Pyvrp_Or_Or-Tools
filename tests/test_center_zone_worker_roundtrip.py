import unittest
from dataclasses import asdict

from config import (
    CenterZoneConfig,
    LocationConfig,
    VehicleType,
    center_zone_cost_adjustment,
    center_zone_profile_signature,
    get_center_zones,
)


class CenterZoneWorkerRoundTripTests(unittest.TestCase):
    @staticmethod
    def _vidin_zone() -> CenterZoneConfig:
        return CenterZoneConfig(
            name="Видин",
            mode="circle",
            center_coords=(43.90630307, 22.6675415),
            radius_km=35.0,
            polygon=[],
            enabled=True,
            enable_priority=True,
            enable_restrictions=True,
            priority_vehicle_types=["special_bus"],
            restricted_vehicle_types=["center_bus"],
            discount_priority_vehicle=0.8,
            priority_vehicle_outside_penalty=0.0,
            vehicle_penalties={
                "center_bus": 40000.0,
                "internal_bus": 40000.0,
                "external_bus": 40000.0,
                "vratza_bus": 40000.0,
            },
        )

    def test_asdict_worker_roundtrip_restores_dynamic_zone(self):
        original = LocationConfig(center_zones=[self._vidin_zone()])
        worker_payload = asdict(original)
        self.assertIsInstance(worker_payload["center_zones"][0], dict)

        restored = LocationConfig(**worker_payload)
        self.assertIsInstance(restored.center_zones[0], CenterZoneConfig)

        dynamic_zones = get_center_zones(restored, include_legacy=False)
        self.assertEqual(["Видин"], [zone.name for zone in dynamic_zones])

        multiplier, penalty = center_zone_cost_adjustment(
            (43.90630307, 22.6675415),
            VehicleType.SPECIAL_BUS,
            restored,
        )
        self.assertAlmostEqual(0.8, multiplier)
        self.assertEqual(0, penalty)

        signature = center_zone_profile_signature(VehicleType.SPECIAL_BUS, restored)
        self.assertEqual("center_rules", signature[0])
        self.assertIn((1, 0.8, 0.0), signature[1])

    def test_get_center_zones_defensively_accepts_dicts(self):
        location = LocationConfig()
        location.center_zones = [asdict(self._vidin_zone())]

        zones = get_center_zones(location, include_legacy=False)

        self.assertEqual(1, len(zones))
        self.assertIsInstance(zones[0], CenterZoneConfig)
        self.assertEqual("circle", zones[0].mode)
        self.assertEqual((43.90630307, 22.6675415), zones[0].center_coords)

    def test_polygon_zone_survives_worker_roundtrip_and_applies_discount(self):
        polygon_zone = CenterZoneConfig(
            name="Тестов полигон",
            mode="polygon",
            polygon=[
                (43.89, 22.64),
                (43.93, 22.64),
                (43.93, 22.70),
                (43.89, 22.70),
            ],
            priority_vehicle_types=["special_bus"],
            restricted_vehicle_types=[],
            discount_priority_vehicle=0.7,
        )
        restored = LocationConfig(**asdict(LocationConfig(center_zones=[polygon_zone])))

        multiplier, penalty = center_zone_cost_adjustment(
            (43.91, 22.67),
            VehicleType.SPECIAL_BUS,
            restored,
        )

        self.assertIsInstance(restored.center_zones[0], CenterZoneConfig)
        self.assertTrue(all(isinstance(point, tuple) for point in restored.center_zones[0].polygon))
        self.assertAlmostEqual(0.7, multiplier)
        self.assertEqual(0, penalty)


if __name__ == "__main__":
    unittest.main()
