import unittest
from types import SimpleNamespace

from input_handler import InputHandler


def make_handler(**overrides):
    input_values = {
        "json_gps_field": "GPS",
        "json_client_id_field": "IdCust",
        "json_client_name_field": "CustName",
        "json_volume_field": "Volume",
        "json_document_field": "IdDoc",
        "json_plas_doc_field": "IdPlasDoc",
        "json_id_skld_field": "IdSkld",
        "json_time_window_field": "WorkTime",
        "json_delivery_comment_field": "DeliveryComment",
        "json_service_time_field": "ServiceTimeMinutes",
        "enable_customer_document_grouping": True,
    }
    input_values.update(overrides)
    main_config = SimpleNamespace(
        input=SimpleNamespace(**input_values),
        locations=SimpleNamespace(depot_location=(42.6977, 23.3219)),
    )
    return InputHandler(main_config)


def make_record(document, **overrides):
    record = {
        "GPS": "42.7000,23.3000",
        "IdCust": "client-1",
        "CustName": "Client 1",
        "Volume": 1,
        "IdDoc": document,
        "IdPlasDoc": document,
        "IdSkld": "106",
    }
    record.update(overrides)
    return record


class InputHandlerServiceTimeTests(unittest.TestCase):
    def test_reads_configured_field_and_common_aliases(self):
        configured = make_handler(json_service_time_field="VisitDuration")
        configured_customer = configured._process_json_records(
            [make_record("doc-1", VisitDuration="00:12")]
        )[0]
        self.assertEqual(12.0, configured_customer.service_time_minutes)

        aliased = make_handler(json_service_time_field="UnusedField")
        aliased_customer = aliased._process_json_records(
            [make_record("doc-2", service_time_minutes="7,5 min")]
        )[0]
        self.assertEqual(7.5, aliased_customer.service_time_minutes)

    def test_missing_or_invalid_value_keeps_customer_for_vehicle_fallback(self):
        customers = make_handler()._process_json_records(
            [
                make_record("doc-1"),
                make_record(
                    "doc-2",
                    IdCust="client-2",
                    GPS="42.7100,23.3100",
                    ServiceTimeMinutes="invalid",
                ),
            ]
        )

        self.assertEqual(2, len(customers))
        self.assertIsNone(customers[0].service_time_minutes)
        self.assertIsNone(customers[1].service_time_minutes)

    def test_grouping_uses_largest_available_stop_time_without_summing_documents(self):
        customer = make_handler()._process_json_records(
            [
                make_record("doc-1", ServiceTimeMinutes=7),
                make_record("doc-2", ServiceTimeMinutes=12),
                make_record("doc-3", ServiceTimeMinutes=12),
            ]
        )[0]

        self.assertEqual(3.0, customer.volume)
        self.assertEqual(12.0, customer.service_time_minutes)
        self.assertEqual([7.0, 12.0, 12.0], [
            item["service_time_minutes"] for item in customer.grouped_documents
        ])

    def test_grouping_adopts_later_value_when_first_document_has_none(self):
        customer = make_handler()._process_json_records(
            [
                make_record("doc-1"),
                make_record("doc-2", ServiceTimeMinutes=9),
            ]
        )[0]

        self.assertEqual(9.0, customer.service_time_minutes)


if __name__ == "__main__":
    unittest.main()
