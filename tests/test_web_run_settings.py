import json
import os
import time
import types
import unittest
from copy import deepcopy
from email.message import Message
from unittest.mock import patch

import cvrp_api_server as api
from config import get_config


class WebRunSettingsTests(unittest.TestCase):
    def test_builder_is_isolated_and_accepts_cvrp_output_and_set_data(self):
        base = get_config()
        original_solver = base.cvrp.solver_type
        requested_solver = "or_tools" if original_solver != "or_tools" else "pyvrp"
        original_excel = base.output.enable_excel_output
        original_set_data = base.set_data.enable_set_data_upload
        payload = {
            "run_settings": {
                "cvrp": {"solver_type": requested_solver},
                "output": {"enable_excel_output": not original_excel},
                "set_data": {"enable_set_data_upload": not original_set_data},
            }
        }

        with patch.object(base.api, "web_gui_run_defaults_json", ""):
            override, applied, ignored = api._build_web_run_config_override(payload)

        self.assertIsNot(override, base)
        self.assertEqual(override.cvrp.solver_type, requested_solver)
        self.assertEqual(override.output.enable_excel_output, not original_excel)
        self.assertEqual(override.set_data.enable_set_data_upload, not original_set_data)
        self.assertEqual(base.cvrp.solver_type, original_solver)
        self.assertEqual(base.output.enable_excel_output, original_excel)
        self.assertEqual(base.set_data.enable_set_data_upload, original_set_data)
        self.assertIn("cvrp.solver_type", applied)
        self.assertIn("output.enable_excel_output", applied)
        self.assertIn("set_data.enable_set_data_upload", applied)
        self.assertEqual([], ignored)

        with self.assertRaises(ValueError):
            api._build_web_run_config_override({"run_settings": {"cvrp": {"unknown": True}}})
        with self.assertRaises(ValueError):
            api._build_web_run_config_override({"run_settings": {"output": {"unknown": True}}})

    def test_or_tools_parallel_lists_round_trip_from_web_textareas(self):
        base = get_config()
        payload = {
            "run_settings": {
                "cvrp": {
                    "solver_type": "or_tools",
                    "parallel_first_solution_strategies": ["SAVINGS", "PATH_CHEAPEST_ARC"],
                    "parallel_local_search_metaheuristics": ["GUIDED_LOCAL_SEARCH", "TABU_SEARCH"],
                }
            }
        }

        with patch.object(base.api, "web_gui_run_defaults_json", ""):
            override, applied, ignored = api._build_web_run_config_override(payload)

        self.assertEqual(
            ["SAVINGS", "PATH_CHEAPEST_ARC"],
            override.cvrp.parallel_first_solution_strategies,
        )
        self.assertEqual(
            ["GUIDED_LOCAL_SEARCH", "TABU_SEARCH"],
            override.cvrp.parallel_local_search_metaheuristics,
        )
        self.assertIn("cvrp.parallel_first_solution_strategies", applied)
        self.assertEqual([], ignored)

        with patch.object(base.api, "web_gui_run_defaults_json", ""):
            with self.assertRaisesRegex(ValueError, "must contain at least one strategy"):
                api._build_web_run_config_override({
                    "run_settings": {
                        "cvrp": {"parallel_first_solution_strategies": []}
                    }
                })

    def test_legacy_saved_web_preset_is_ignored_by_web_and_normal_api_runs(self):
        base = get_config()
        saved = {
            "output": {"enable_csv_output": True},
            "set_data": {"enable_set_data_upload": False},
        }
        with (
            patch.object(base.output, "enable_csv_output", False),
            patch.object(base.api, "web_gui_run_defaults_json", json.dumps(saved)),
        ):
            web_override, web_applied, web_ignored = api._build_web_run_config_override({"run_settings": {}})
            normal_override, normal_applied, normal_ignored = api._build_request_config_override(None, None)

        self.assertFalse(web_override.output.enable_csv_output)
        self.assertEqual([], web_applied)
        self.assertEqual([], web_ignored)
        self.assertIsNone(normal_override)
        self.assertEqual([], normal_applied)
        self.assertEqual([], normal_ignored)

    def test_web_defaults_save_stores_web_only_json(self):
        base = deepcopy(get_config())
        original_output = base.output.enable_excel_output
        fake_manager = types.SimpleNamespace(config=base)
        payload = {
            "run_settings": {
                "output": {"enable_excel_output": not original_output},
                "set_data": {"enable_set_data_upload": False},
            }
        }
        with (
            patch.object(api, "get_config", return_value=base),
            patch.object(api, "_persist_web_gui_config", return_value="backup.py"),
            patch.object(api.config_module, "config_manager", fake_manager),
        ):
            result = api._save_web_run_defaults(payload)

        stored = json.loads(fake_manager.config.api.web_gui_run_defaults_json)
        self.assertEqual(stored["output"]["enable_excel_output"], not original_output)
        self.assertEqual(base.output.enable_excel_output, original_output)
        self.assertEqual(result["scope"], "web_gui_runs_only")

    def test_secrets_are_redacted_and_html_has_distinct_actions(self):
        settings = {
            "output": {"route_maps_upload_token": "do-not-return", "enable_excel_output": True},
            "set_data": {"enable_set_data_upload": False},
        }
        redacted = api._redact_web_run_settings(settings)
        self.assertNotIn("route_maps_upload_token", redacted["output"])
        self.assertTrue(redacted["output"]["route_maps_upload_token_configured"])

        cfg = get_config()
        page = api._web_gui_html(cfg.api, "127.0.0.1", 8084)
        self.assertIn('id="runSettingsSection"', page)
        self.assertIn("Стартирай без запис", page)
        self.assertIn("Запази глобално в config.py", page)
        self.assertIn("Запази само бусовете глобално", page)
        self.assertNotIn("Запази като Web defaults", page)
        self.assertIn("cvrp:collectCvrpSettings()", page)
        self.assertIn("const runSettings = collectRunSettings()", page)
        self.assertIn("run_settings:runSettings", page)
        self.assertNotIn("482253", page)

    def test_web_run_rejects_wrong_json_types(self):
        base = get_config()
        with patch.object(base.api, "web_gui_run_defaults_json", ""):
            with self.assertRaisesRegex(ValueError, "JSON string"):
                api._build_web_run_config_override({
                    "run_settings": {"output": {"map_output_file": {"bad": "path"}}}
                })
            with self.assertRaisesRegex(ValueError, "JSON integer"):
                api._build_web_run_config_override({
                    "run_settings": {"set_data": {"set_data_timeout_seconds": True}}
                })

    def test_inline_json_and_web_endpoint_are_safe(self):
        api_config = deepcopy(get_config().api)
        malicious = "</script><script>window.pwned=true</script>"
        api_config.web_gui_title = malicious
        main_page = api._web_gui_html(api_config, "127.0.0.1", 8084)
        login_page = api._web_gui_login_html(api_config)

        self.assertNotIn(malicious, main_page)
        self.assertNotIn(malicious, login_page)
        self.assertIn("\\u003c/script\\u003e", main_page)

        api_config.web_gui_endpoint = api_config.trigger_endpoint
        self.assertNotEqual(api._web_gui_endpoint(api_config), api_config.trigger_endpoint)
        api_config.web_gui_endpoint = "/"
        self.assertNotEqual(api._web_gui_endpoint(api_config), "/")

    def test_protected_config_fields_are_not_generic_web_save_fields(self):
        self.assertNotIn("web_gui_users", api._WEB_GUI_API_FIELDS)
        self.assertNotIn("web_gui_run_defaults_json", api._WEB_GUI_API_FIELDS)
        self.assertIn("web_gui_run_defaults_json", api._WEB_GUI_PERSIST_API_FIELDS)

    def test_clearing_saved_upload_token_falls_back_to_global_token(self):
        base = deepcopy(get_config())
        base.api.web_gui_run_defaults_json = json.dumps({
            "output": {"route_maps_upload_token": "saved-secret"}
        })
        fake_manager = types.SimpleNamespace(config=base)
        with (
            patch.object(api, "get_config", return_value=base),
            patch.object(api, "_persist_web_gui_config", return_value="backup.py"),
            patch.object(api.config_module, "config_manager", fake_manager),
        ):
            api._save_web_run_defaults({
                "run_settings": {"output": {"enable_excel_output": True}},
                "clear_upload_token": True,
            })

        stored = json.loads(fake_manager.config.api.web_gui_run_defaults_json)
        self.assertNotIn("route_maps_upload_token", stored["output"])

    def test_session_cookie_uses_wall_clock_expiry_and_worker_path_is_absolute(self):
        api_config = deepcopy(get_config().api)
        headers = Message()
        session = types.SimpleNamespace(
            token="session-token",
            csrf_token="csrf-token",
            expires_at=time.time() + 3600,
        )
        cookie_headers = api._web_session_cookie_headers(api_config, headers, session)
        max_ages = [
            int(value.split("Max-Age=", 1)[1].split(";", 1)[0])
            for name, value in cookie_headers
            if name == "Set-Cookie"
        ]
        self.assertTrue(all(3590 <= value <= 3600 for value in max_ages))

        command = api._web_run_worker_command("input.json", "output.json")
        if not getattr(api.sys, "frozen", False):
            self.assertTrue(os.path.isabs(command[1]))
            self.assertTrue(command[1].endswith("cvrp_run_worker.py"))

    def test_session_and_csrf_headers_are_redacted_from_logs(self):
        handler = object.__new__(api.CVRPApiHandler)
        handler.headers = Message()
        handler.headers["Cookie"] = "CVRP_WEB_SESSION=secret-session"
        handler.headers["X-CSRF-Token"] = "secret-csrf"
        handler.headers["User-Agent"] = "test-agent"

        safe = handler._safe_request_headers()
        self.assertEqual(safe["Cookie"], "***")
        self.assertEqual(safe["X-CSRF-Token"], "***")
        self.assertEqual(safe["User-Agent"], "test-agent")

    def test_forwarded_login_ip_is_used_only_for_trusted_proxy(self):
        api_config = deepcopy(get_config().api)
        api_config.web_gui_trusted_proxy_ips = "127.0.0.1,::1"
        headers = Message()
        headers["X-Forwarded-For"] = "203.0.113.99, 198.51.100.7"

        # Walk right-to-left: an attacker-supplied left entry cannot override
        # the first untrusted hop appended by the trusted local proxy.
        self.assertEqual(
            api._web_login_client_ip(api_config, headers, ("127.0.0.1", 1234)),
            "198.51.100.7",
        )
        self.assertEqual(
            api._web_login_client_ip(api_config, headers, ("192.0.2.50", 1234)),
            "192.0.2.50",
        )


if __name__ == "__main__":
    unittest.main()
