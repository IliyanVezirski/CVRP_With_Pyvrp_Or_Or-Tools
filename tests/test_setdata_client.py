import unittest
from types import SimpleNamespace
from unittest.mock import patch

from setdata_client import (
    build_unserved_done_flag_rows,
    build_unserved_set_data_rows,
    upload_solution_set_data,
)


def _set_data_config():
    return SimpleNamespace(
        enable_set_data_upload=True,
        enable_unserved_set_data_upload=True,
        enable_make_group=True,
        set_data_command="setData",
        set_data_make_group_command="makeGroup",
        set_data_done_flag="1973",
        set_data_unserved_done_flag="0",
        enable_unserved_final_done_flag_update=True,
        set_data_unserved_final_done_flag="1973",
        set_data_id_skld="106",
        set_data_unserved_id_grafik="1004501000",
        set_data_unserved_id_grafik_template="{id_grafik}",
        set_data_unserved_bukva_template="HOF-{stop_number}",
    )


class SetDataOrderingTests(unittest.TestCase):
    def test_unserved_final_row_keeps_all_fields_and_only_changes_done_flag(self):
        set_data = _set_data_config()
        config = SimpleNamespace(set_data=set_data)
        customer = SimpleNamespace(
            id="customer-1",
            document="doc-1",
            plas_doc="plas-1",
            source_id_skld="128",
            volume=10,
            grouped_documents=[],
        )

        initial_rows = build_unserved_set_data_rows([customer], config)
        final_rows = build_unserved_done_flag_rows(initial_rows, set_data)

        self.assertEqual(initial_rows[0]["DoneFlag"], "0")
        self.assertEqual(initial_rows[0]["IdSkld"], "128")
        self.assertEqual(
            final_rows,
            [{
                "cmd": "setData",
                "IdPlasDoc": "plas-1",
                "DoneFlag": "1973",
                "IdSkld": "128",
                "Bukva": "HOF-1",
                "IdGrafik": "1004501000",
            }],
        )

    def test_upload_sends_all_rows_then_make_group_then_unserved_final_flag(self):
        set_data = _set_data_config()
        config = SimpleNamespace(set_data=set_data)
        solution = SimpleNamespace(dropped_customers=[object()])
        served = {
            "cmd": "setData", "IdPlasDoc": "served-1", "DoneFlag": "1973",
            "IdSkld": "106", "Bukva": "A", "IdGrafik": "G1",
        }
        unserved = {
            "cmd": "setData", "IdPlasDoc": "unserved-1", "DoneFlag": "0",
            "IdSkld": "128", "Bukva": "HOF", "IdGrafik": "G0",
        }
        calls = []

        def capture(params, _config):
            calls.append(dict(params))
            return {"status_code": 200}

        with (
            patch("setdata_client.build_set_data_rows", return_value=[served]),
            patch("setdata_client.build_unserved_set_data_rows", return_value=[unserved]),
            patch("setdata_client.send_set_data_row", side_effect=capture),
        ):
            result = upload_solution_set_data(solution, config)

        self.assertEqual(
            calls,
            [
                served,
                unserved,
                {"cmd": "makeGroup", "IdSkld": "106"},
                {"cmd": "makeGroup", "IdSkld": "128"},
                {
                    "cmd": "setData", "IdPlasDoc": "unserved-1", "DoneFlag": "1973",
                    "IdSkld": "128", "Bukva": "HOF", "IdGrafik": "G0",
                },
            ],
        )
        self.assertEqual(result["unserved_done_flag"]["succeeded"], 1)

    def test_final_unserved_flag_can_be_disabled(self):
        set_data = _set_data_config()
        set_data.enable_unserved_final_done_flag_update = False
        rows = [{"cmd": "setData", "IdPlasDoc": "unserved-1", "DoneFlag": "0"}]

        self.assertEqual(build_unserved_done_flag_rows(rows, set_data), [])

    def test_final_unserved_flag_is_independent_from_common_flag(self):
        set_data = _set_data_config()
        set_data.set_data_unserved_final_done_flag = "2999"
        rows = [{"cmd": "setData", "IdPlasDoc": "unserved-1", "DoneFlag": "0"}]

        self.assertEqual(
            build_unserved_done_flag_rows(rows, set_data),
            [{"cmd": "setData", "IdPlasDoc": "unserved-1", "DoneFlag": "2999"}],
        )

    def test_final_unserved_flag_does_not_mutate_initial_row(self):
        set_data = _set_data_config()
        initial = {
            "cmd": "setData", "IdPlasDoc": "unserved-1", "DoneFlag": "0",
            "IdSkld": "128", "Bukva": "HOF", "IdGrafik": "G0",
        }

        final_rows = build_unserved_done_flag_rows([initial], set_data)

        self.assertEqual(initial["DoneFlag"], "0")
        self.assertEqual(final_rows[0]["DoneFlag"], "1973")
        self.assertEqual(final_rows[0]["IdSkld"], "128")
        self.assertEqual(final_rows[0]["Bukva"], "HOF")
        self.assertEqual(final_rows[0]["IdGrafik"], "G0")

    def test_failed_make_group_does_not_apply_final_unserved_flag(self):
        set_data = _set_data_config()
        config = SimpleNamespace(set_data=set_data)
        solution = SimpleNamespace(dropped_customers=[object()])
        unserved = {
            "cmd": "setData", "IdPlasDoc": "unserved-1", "DoneFlag": "0",
            "IdSkld": "128", "Bukva": "HOF", "IdGrafik": "G0",
        }
        calls = []

        def fail_make_group(params, _config):
            calls.append(dict(params))
            if params.get("cmd") == "makeGroup":
                raise RuntimeError("makeGroup failed")
            return {"status_code": 200}

        with (
            patch("setdata_client.build_set_data_rows", return_value=[]),
            patch("setdata_client.build_unserved_set_data_rows", return_value=[unserved]),
            patch("setdata_client.send_set_data_row", side_effect=fail_make_group),
        ):
            result = upload_solution_set_data(solution, config)

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1], {"cmd": "makeGroup", "IdSkld": "128"})
        self.assertEqual(result["unserved_done_flag"]["attempted"], 0)


if __name__ == "__main__":
    unittest.main()
