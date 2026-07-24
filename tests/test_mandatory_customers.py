import math
import unittest

import pandas as pd

from config import CVRPConfig, MainConfig, VehicleConfig, VehicleType
from input_handler import InputHandler, MandatoryCustomerError, MandatoryFlagError
from vrp_rust_prototype import VRPRustPrototype
from warehouse_manager import WarehouseManager

from tests.test_vroom_solver import (
    DEPOT_A,
    DEPOT_B,
    make_config as make_vroom_config,
    make_customers as make_vroom_customers,
    make_location_config,
    make_matrix,
    make_vehicles,
)


class MandatoryInputTests(unittest.TestCase):
    def setUp(self):
        self.config = MainConfig()
        self.handler = InputHandler(main_config=self.config)

    @staticmethod
    def record(customer_id, **overrides):
        record = {
            "IdCust": customer_id,
            "CustName": f"Client {customer_id}",
            "GPS": "42.6977,23.3219",
            "Volume": 1,
            "IdDoc": f"doc-{customer_id}",
        }
        record.update(overrides)
        return record

    def test_json_accepts_supported_mandatory_values_and_aliases(self):
        data = self.handler.load_data_from_json_records(
            [
                self.record("one", Mandatory=True),
                self.record("two", Required="да"),
                self.record("three", MustServe=1),
                self.record("four", Mandatory="не"),
            ]
        )

        self.assertEqual([True, True, True, False], [customer.mandatory for customer in data.customers])

    def test_grouped_visit_is_mandatory_when_any_document_is_mandatory(self):
        records = [
            self.record("same", IdDoc="doc-1", Mandatory=False),
            self.record("same", IdDoc="doc-2", Mandatory=True),
        ]

        data = self.handler.load_data_from_json_records(records)

        self.assertEqual(1, len(data.customers))
        self.assertTrue(data.customers[0].mandatory)
        self.assertEqual([False, True], [item["mandatory"] for item in data.customers[0].grouped_documents])

    def test_invalid_explicit_marker_fails_instead_of_silently_dropping_record(self):
        with self.assertRaises(MandatoryFlagError):
            self.handler.load_data_from_json_records([self.record("bad", Mandatory="maybe")])

    def test_json_mandatory_record_with_invalid_field_fails_instead_of_being_skipped(self):
        with self.assertRaisesRegex(
            MandatoryCustomerError,
            r"задължителен клиент.*JSON запис 0, клиент broken-json.*Невалиден обем",
        ):
            self.handler.load_data_from_json_records(
                [self.record("broken-json", Mandatory=True, Volume="not-a-volume")]
            )

    def test_json_optional_record_with_invalid_field_keeps_legacy_skip_behavior(self):
        data = self.handler.load_data_from_json_records(
            [
                self.record("broken-optional", Mandatory=False, Volume="not-a-volume"),
                self.record("valid-optional", Mandatory=False),
            ]
        )

        self.assertEqual(["valid-optional"], [customer.id for customer in data.customers])

    def test_mandatory_customer_without_gps_fails_before_solver(self):
        with self.assertRaisesRegex(ValueError, "няма валидни GPS"):
            self.handler.load_data_from_json_records([self.record("no-gps", GPS="", Mandatory=True)])

    def test_excel_column_marks_mandatory_customer(self):
        dataframe = pd.DataFrame(
            [
                {
                    self.config.input.client_id_column: "excel-one",
                    self.config.input.client_name_column: "Excel One",
                    self.config.input.gps_column: "42.6977,23.3219",
                    self.config.input.volume_column: 1,
                    self.config.input.document_column: "doc-excel",
                    self.config.input.mandatory_column: "да",
                }
            ]
        )

        customers = self.handler._process_dataframe(dataframe)

        self.assertTrue(customers[0].mandatory)

    def test_excel_mandatory_row_with_invalid_field_fails_instead_of_being_skipped(self):
        dataframe = pd.DataFrame(
            [
                {
                    self.config.input.client_id_column: "broken-excel",
                    self.config.input.client_name_column: "Broken Excel",
                    self.config.input.gps_column: "42.6977,23.3219",
                    self.config.input.volume_column: "not-a-volume",
                    self.config.input.document_column: "doc-broken-excel",
                    self.config.input.mandatory_column: True,
                }
            ]
        )

        with self.assertRaisesRegex(
            MandatoryCustomerError,
            r"задължителен клиент.*Excel ред 0, клиент broken-excel.*Невалиден обем",
        ):
            self.handler._process_dataframe(dataframe)

    def test_excel_optional_row_with_invalid_field_keeps_legacy_skip_behavior(self):
        dataframe = pd.DataFrame(
            [
                {
                    self.config.input.client_id_column: "broken-optional",
                    self.config.input.client_name_column: "Broken Optional",
                    self.config.input.gps_column: "42.6977,23.3219",
                    self.config.input.volume_column: "not-a-volume",
                    self.config.input.document_column: "doc-broken-optional",
                    self.config.input.mandatory_column: False,
                },
                {
                    self.config.input.client_id_column: "valid-optional",
                    self.config.input.client_name_column: "Valid Optional",
                    self.config.input.gps_column: "42.6977,23.3219",
                    self.config.input.volume_column: 1,
                    self.config.input.document_column: "doc-valid-optional",
                    self.config.input.mandatory_column: False,
                },
            ]
        )

        customers = self.handler._process_dataframe(dataframe)

        self.assertEqual(["valid-optional"], [customer.id for customer in customers])


class MandatoryWarehouseTests(unittest.TestCase):
    def make_manager(self, capacity=10):
        config = MainConfig(
            vehicles=[
                VehicleConfig(
                    vehicle_type=VehicleType.INTERNAL_BUS,
                    capacity=capacity,
                    count=1,
                    enabled=True,
                    max_time_hours=8,
                    service_time_minutes=5,
                    start_location=DEPOT_A,
                )
            ],
            cvrp=CVRPConfig(enable_multiple_trips=False),
        )
        return WarehouseManager(main_config=config)

    def test_mandatory_customer_reserves_capacity_before_optional_customer(self):
        customers = make_vroom_customers()
        customers[0].volume = 6
        customers[0].mandatory = False
        customers[1].volume = 6
        customers[1].mandatory = True
        input_data = type("Input", (), {"customers": customers})()

        allocation = self.make_manager(capacity=10).allocate_customers(input_data)

        self.assertEqual(["client-b"], [customer.id for customer in allocation.vehicle_customers])
        self.assertEqual(["client-a"], [customer.id for customer in allocation.warehouse_customers])

    def test_impossible_mandatory_volume_is_a_clear_error(self):
        customer = make_vroom_customers()[0]
        customer.volume = 11
        customer.mandatory = True
        input_data = type("Input", (), {"customers": [customer]})()

        with self.assertRaisesRegex(ValueError, "Задължителният клиент"):
            self.make_manager(capacity=10).allocate_customers(input_data)


class MandatoryVRPRustPayloadTests(unittest.TestCase):
    def test_mandatory_job_has_no_skip_value(self):
        customers = make_vroom_customers()
        customers[0].mandatory = True
        vehicles = make_vehicles()
        for vehicle in vehicles:
            vehicle.max_distance_km = None
        solver = VRPRustPrototype(
            make_vroom_config(solver_type="vrp", objective_metric="distance"),
            vehicles,
            customers,
            make_matrix(customers),
            [DEPOT_A, DEPOT_B],
            [customers[0]],
            make_location_config(customers),
        )

        request = solver.build_request()
        jobs = request["problem"]["plan"]["jobs"]

        self.assertNotIn("value", jobs[0])
        self.assertGreater(jobs[1]["value"], 0)


if __name__ == "__main__":
    unittest.main()
