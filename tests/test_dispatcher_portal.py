"""No application startup or external requests: profiles and dispatcher UI."""

import json
import os
import re
import shutil
import subprocess
import types
import unittest
from copy import deepcopy
from datetime import date
from pathlib import Path
from unittest.mock import patch

import cvrp_api_server as api
from config import get_config


class DispatcherPortalTests(unittest.TestCase):
    def setUp(self):
        self.base = deepcopy(get_config())
        self.base.api.web_gui_run_defaults_json = ""
        self.base.input.json_override_date = ""
        self.base.input.json_url = "https://example.invalid/orders"
        self.base.set_data.set_data_url = "https://example.invalid/results"
        self.base.output.route_maps_upload_url = "https://example.invalid/upload.php"
        self.base.output.route_maps_upload_token = "global-secret"
        self.manager = types.SimpleNamespace(config=self.base)

    def save_profile(self, settings, **options):
        with (patch.object(api, "get_config", side_effect=lambda: self.manager.config),
              patch.object(api.config_module, "config_manager", self.manager),
              patch.object(api, "_persist_web_gui_config", return_value="backup.py") as persist):
            result = api._save_web_run_defaults({"run_settings": settings, **options})
        self.assertFalse(persist.call_args.kwargs["include_vehicles"])
        self.assertFalse(persist.call_args.kwargs["include_cvrp"])
        return result

    def build(self, payload):
        with patch.object(api, "get_config", return_value=self.manager.config):
            return api._build_web_run_config_override(payload)[0]

    def test_profile_is_independent_and_run_without_save_is_temporary(self):
        self.save_profile({"input": {"json_sklad": "999"}, "output": {"excel_output_dir": "D:/web"}})
        self.assertEqual("999", self.build({}).input.json_sklad)
        self.assertNotEqual("999", self.build({"profile": "global"}).input.json_sklad)
        current = self.build({"run_settings": {"input": {"json_sklad": "100"}}})
        self.assertEqual("100", current.input.json_sklad)
        self.assertEqual("999", self.build({}).input.json_sklad)
        with patch.object(api, "get_config", return_value=self.manager.config):
            self.assertIsNone(api._build_request_config_override(None, None)[0])

    def test_routing_quality_report_saves_separately_for_tests_and_main(self):
        self.base.output.enable_routing_quality_report = False
        self.save_profile({"output": {"enable_routing_quality_report": True}})
        self.assertTrue(self.build({}).output.enable_routing_quality_report)
        self.assertFalse(self.build({"profile": "global"}).output.enable_routing_quality_report)
        self.assertFalse(self.build({"run_settings": {"output": {"enable_routing_quality_report": False}}}).output.enable_routing_quality_report)
        self.assertTrue(self.build({}).output.enable_routing_quality_report)

    def test_global_save_does_not_clear_saved_web_profile(self):
        self.save_profile({"output": {"excel_output_dir": "D:/web"}})
        profile = self.manager.config.api.web_gui_run_defaults_json
        with (patch.object(api, "get_config", side_effect=lambda: self.manager.config),
              patch.object(api.config_module, "config_manager", self.manager),
              patch.object(api, "_persist_web_gui_config", return_value="backup.py")):
            api._apply_web_gui_config_save({"input": {"json_sklad": "201"}, "output": {"excel_output_dir": "D:/global"}})
        self.assertEqual(profile, self.manager.config.api.web_gui_run_defaults_json)
        self.assertEqual("D:/web", self.build({}).output.excel_output_dir)
        self.assertEqual("D:/global", self.build({"profile": "global"}).output.excel_output_dir)

    def test_fleet_is_always_shared_and_cannot_be_stored_in_profile(self):
        self.save_profile({"output": {"enable_excel_output": True}})
        self.manager.config.vehicles[0].count = 17
        self.assertEqual(17, self.build({}).vehicles[0].count)
        self.assertEqual(17, self.build({"profile": "global"}).vehicles[0].count)
        with self.assertRaises(ValueError):
            self.save_profile({"vehicles": []})
        with self.assertRaises(ValueError):
            self.build({"profile": "unknown"})

    def test_uploading_maps_is_independent_from_sending_results(self):
        self.save_profile({"output": {"enable_interactive_map": True, "route_maps_upload_mode": "effect_upload"},
                           "set_data": {"enable_set_data_upload": False}})
        result = self.build({})
        self.assertEqual("effect_upload", result.output.route_maps_upload_mode)
        self.assertFalse(result.set_data.enable_set_data_upload)
        disabled = self.build({"run_settings": {"output": {"route_maps_upload_mode": "disabled"}}})
        self.assertEqual("disabled", disabled.output.route_maps_upload_mode)
        self.assertEqual("effect_upload", self.build({}).output.route_maps_upload_mode)

    def test_saved_secret_is_independent_and_redacted(self):
        self.save_profile({"output": {"route_maps_upload_token": "web-secret"}})
        self.save_profile({"output": {"enable_excel_output": True}})
        self.assertEqual("web-secret", self.build({}).output.route_maps_upload_token)
        self.assertEqual("global-secret", self.build({"profile": "global"}).output.route_maps_upload_token)
        redacted = api._redact_web_run_settings(api._web_run_settings_from_config(self.build({})))
        self.assertNotIn("web-secret", json.dumps(redacted))
        self.save_profile({"output": {"enable_excel_output": True}}, clear_upload_token=True)
        self.assertEqual("", self.build({}).output.route_maps_upload_token)
        self.assertEqual("global-secret", self.base.output.route_maps_upload_token)

    def test_old_ignored_presets_are_not_resurrected(self):
        self.base.api.web_gui_run_defaults_json = json.dumps({"input": {"json_sklad": "old"}})
        self.assertNotEqual("old", self.build({}).input.json_sklad)

    def test_full_snapshot_retains_settings_omitted_by_the_form(self):
        self.base.output.excel_bus_number_prefix = "111"
        self.save_profile({"output": {"excel_output_dir": "D:/web"}})
        self.manager.config.output.excel_bus_number_prefix = "222"
        self.assertEqual("111", self.build({}).output.excel_bus_number_prefix)

    def test_sheet_name_accepts_text_or_none_and_invalid_types_are_rejected(self):
        for sheet in ("Заявки", None):
            self.assertEqual(sheet, self.build({"run_settings": {"input": {"sheet_name": sheet}}}).input.sheet_name)
        for settings in ({"sheet_name": []}, {"json_timeout_seconds": True}, {"json_override_date": "31/02/2026"}):
            with self.assertRaises(ValueError):
                self.build({"run_settings": {"input": settings}})

    def test_saturday_is_saved_dynamic_and_resolved_only_for_execution(self):
        self.save_profile({"input": {"json_override_date": "current_week_saturday"},
                           "output": {"saturday_excel_bus_number_prefix": "999", "saturday_excel_bus_number_digits": 2}})
        result = self.build({})
        resolver = api._resolve_dynamic_api_date
        with patch.object(api, "_resolve_dynamic_api_date", side_effect=lambda raw: resolver(raw, date(2026, 9, 9))):
            api._prepare_web_run_dates(result)
        self.assertEqual("12/09/2026", result.input.json_override_date)
        self.assertEqual("999", result.output.excel_bus_number_prefix)
        self.assertEqual("2026-09-12_събота", result.output._api_run_date_stamp)
        self.assertEqual("current_week_saturday", self.build({}).input.json_override_date)

    def test_input_persistence_is_an_explicit_separate_section(self):
        source = '@dataclass\nclass InputConfig:\n    json_sklad: str = "106"\n'
        updated = api._replace_class_field_literal(source, "InputConfig", "json_sklad", "128")
        self.assertIn('json_sklad: str = "128"', updated)

    def test_changed_settings_require_a_new_confirmation(self):
        revision = api._web_config_revision(self.base)
        self.base.vehicles[0].count += 1
        with patch.object(api, "get_config", return_value=self.base):
            with self.assertRaisesRegex(ValueError, "променени"):
                api._start_web_run({"expected_revision": revision})

    def test_progress_budget_is_snapshotted_without_starting_a_worker(self):
        with (patch.object(api, 'get_config', return_value=self.base),
              patch.object(api, '_RUN_STATUS', {}),
              patch.object(api, '_persist_run_status'),
              patch.object(api.threading, 'Thread') as thread):
            started, status, _, _ = api._start_web_run({'run_settings':{'cvrp':{'time_limit_seconds':120}}})
            self.assertTrue(started)
            self.assertEqual(120, status['search_time_limit_seconds'])
            self.base.cvrp.time_limit_seconds = 999
            self.assertEqual(120, api._run_status_snapshot()['search_time_limit_seconds'])
            thread.return_value.start.assert_called_once()

    def test_logs_include_configured_solver_and_recorded_process_log(self):
        status = {"run_id": "example", "process_log": "D:/runtime/captured.log"}
        self.base.logging.log_file = "D:/runtime/solver.log"
        with (patch.object(api, "get_config", return_value=self.base),
              patch.object(api, "_api_logs_dir", return_value="D:/runtime"),
              patch.object(api, "_run_status_snapshot", return_value=status),
              patch.object(api, "_tail_text_file", return_value="Последен ред") as tail):
            payload = api._web_gui_logs_payload()
        self.assertEqual("D:/runtime/solver.log", payload["logs"]["solver"]["path"])
        self.assertEqual(status["process_log"], payload["logs"]["run"]["path"])
        self.assertEqual(3, tail.call_count)

    def test_vehicle_zero_service_and_midnight_survive_render_and_save_literal(self):
        vehicle = deepcopy(self.base.vehicles[0])
        vehicle.service_time_minutes = 0
        vehicle.start_time_minutes = 0
        payload = api._vehicle_to_web_dict(vehicle, self.base)
        self.assertEqual(0, payload["service_time_minutes"])
        self.assertEqual(0, payload["start_time_minutes"])
        literal = api._vehicle_config_literal(vehicle)
        self.assertIn("service_time_minutes=0,", literal)
        self.assertIn("start_time_minutes=0,", literal)

    def test_logs_deduplicate_same_file(self):
        self.base.logging.log_file = "D:/runtime/solver.log"
        with (patch.object(api, "get_config", return_value=self.base),
              patch.object(api, "_api_logs_dir", return_value="D:/runtime"),
              patch.object(api, "_run_status_snapshot", return_value={"process_log": self.base.logging.log_file}),
              patch.object(api, "_tail_text_file", return_value="") as tail):
            payload = api._web_gui_logs_payload()
        self.assertEqual(2, len(payload["logs"]))
        self.assertEqual(2, tail.call_count)

    def test_login_brand_and_safety_are_independent_of_installation_title(self):
        self.base.api.web_gui_title = "Bizant тест"
        self.base.api.web_gui_public_url = "http://localhost/web"
        service = types.SimpleNamespace(credential_store=types.SimpleNamespace(list_usernames=lambda: ["user"]))
        with patch.object(api, "_get_web_auth_service", return_value=service):
            page = api._web_gui_login_html(self.base.api)
        self.assertIn("<title>Вход · Bizant</title>", page)
        self.assertNotIn("Bizant тест", page)
        self.assertNotIn("Връзката не е шифрована", page)
        self.assertNotIn("Работно място за супервайзор транспорт", page)
        self.assertNotIn('class="warning"', page)
        self.assertIn('autocomplete="current-password"', page)
        self.assertIn('id="passwordToggle" type="button"', page)
        self.assertIn('role="alert"', page)
        self.assertEqual("Bizant тест", self.base.api.web_gui_title)
        script = re.search(r"<script>(.*?)</script>", page, re.S)[1]
        if shutil.which("node"):
            result = subprocess.run([shutil.which("node"), "--check", "-"], input=script, text=True,
                                    encoding="utf-8", capture_output=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)

        if os.environ.get("BIZANT_TEST_NODE_MODULES"):
            result = subprocess.run([shutil.which("node"), str(Path(__file__).with_name("dispatcher_login_dom.cjs"))],
                                    input=json.dumps({"page": page}), text=True, encoding="utf-8",
                                    capture_output=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)

    def test_javascript_syntax_and_labels(self):
        page = api._web_gui_html(self.base.api, "127.0.0.1", 8084)
        self.assertNotIn("Работно място за супервайзор транспорт", page)
        self.assertIn('grid-template-columns:var(--fleet-columns)', page)
        self.assertIn('data-core-field=service_time_minutes', page)
        self.assertIn('id="fleetColumns" aria-hidden="true"', page)
        self.assertIn('#serverLine:empty { display:none; }', page)
        for removed in ('Автопаркът е зареден.', 'Заредени са записаните настройки.', 'На живо · обновяване', 'Само файлове · без изпращане',
                        'Само за тестовете. Стартирането не записва настройките.', 'Настройките са записани', 'profileScope',
                        'Общи за тестовете и основните настройки.', 'Бусовете са запазени за всички следващи стартирания.',
                        'Останалите ти промени във формата са запазени.'):
            self.assertNotIn(removed, page)
        self.assertIn('setVehicleSettingsMessage("Бусовете са запазени.", "ok")', page)
        script = re.search(r"<script>(.*?)</script>", page, re.S)[1]
        node = shutil.which("node")
        if node:
            result = subprocess.run([node, "--check", "-"], input=script, text=True, encoding="utf-8", capture_output=True, timeout=30)
            self.assertEqual(0, result.returncode, result.stderr)
        for text in ("Качвай картите за шофьорите", "Основни настройки", "Заявки", "Изпращане"):
            self.assertIn(text, page)
        labels = re.findall(r'<label[^>]*>(.*?)</label>', page.split('<script>', 1)[0], re.S)
        self.assertFalse(any(re.search(r'\b(setData|DoneFlag|worker|timeout|proximity|Solver)\b', label) for label in labels))

    def test_header_shows_configured_division_path_not_installation_title(self):
        for endpoint in ('/hell', '/roshen', '/transport/hell', "/hell&test"):
            with self.subTest(endpoint=endpoint):
                self.base.api.web_gui_endpoint = endpoint
                self.base.api.web_gui_title = 'Unrelated installation title'
                page = api._web_gui_html(self.base.api, '127.0.0.1', 8095)
                header = page.split('<body>', 1)[1].split('</header>', 1)[0]
                self.assertIn('id="divisionPath" aria-label="Дивизия">' + api.html.escape(endpoint.strip('/').replace('_',' ').title()) + '</span>', header)
                self.assertNotIn('Unrelated installation title', header)

    def test_start_has_no_confirmation_dialog_or_scroll_to_logs(self):
        page = api._web_gui_html(self.base.api, '127.0.0.1', 8095)
        for removed in ('planConfirmation', 'confirmPlan', 'pendingPlan', "$('progressSection').scrollIntoView"):
            self.assertNotIn(removed, page)
        self.assertIn('await submitPlan(useGlobal)', page)
        self.assertIn('expected_revision:state.revision', page)
        self.assertIn('Изчисляване на маршрути', page)
        self.assertNotIn('Търсене на решение', page)

    def test_work_surface_prioritises_active_progress_and_logs(self):
        page = api._web_gui_html(self.base.api, '127.0.0.1', 8095)
        body = page.split('<body>', 1)[1].split('<script>', 1)[0]
        header = body.split('</header>', 1)[0]
        self.assertIn('id="executionProgress" aria-live="polite" hidden', header)
        self.assertEqual(1, body.count('id="executionProgress"'))
        ordered = ['fleetSection', 'runActionsSection', 'settingsDrawer', 'runSettingsSection', 'progressSection']
        positions = [body.index('id="' + item + '"') for item in ordered]
        self.assertEqual(sorted(positions), positions)
        self.assertIn('<div class="execution-logs" id="executionLogs">', body)
        self.assertNotIn('Покажи логовете', body)
        self.assertNotIn('Скрий логовете', body)
        self.assertIn('<details id="settingsDrawer" class="settings-drawer">', body)
        self.assertIn('<summary><span>Настройки</span></summary>', body)
        self.assertNotIn('Заявки · Маршрути · Карти и отчети · Изпращане', body)
        self.assertNotIn('settings-drawer-sections', body)
        self.assertIn('id="planButton"', body[:body.index('id="settingsDrawer"')])

    def test_compact_header_and_settings_have_responsive_styles(self):
        from supervisor_ui import supervisor_css
        css = supervisor_css()
        self.assertIn('.run-progress[hidden] { display:none !important; }', css)
        self.assertIn('.workspace-header { position:sticky; top:0;', css)
        self.assertIn('flex-basis:100%; max-width:none;', css)
        self.assertNotIn('settings-drawer-sections', css)
        self.assertIn('scroll-margin-top:140px;', css)
        self.assertNotIn('.execution-logs:not([open])', css)

    def test_clean_workspace_has_one_start_action_and_no_fixed_footer(self):
        from supervisor_ui import supervisor_css
        page = api._web_gui_html(self.base.api, '127.0.0.1', 8095)
        body = page.split('<body>', 1)[1].split('<script>', 1)[0]
        self.assertEqual(1, body.count('onclick="startRun()"'))
        self.assertEqual(1, body.count('onclick="saveVehicles()"'))
        self.assertEqual(1, body.count('onclick="saveActiveProfile()"'))
        for removed in ('toolbarStart', 'toolbarSaveFleet', 'toolbarProfile', 'saveProfileButton'):
            self.assertNotIn(removed, page)
        self.assertIn('id="fleetSaveButton" onclick="saveVehicles()" hidden', body)
        css = supervisor_css()
        self.assertNotIn('position:fixed', css)
        self.assertIn('.fleet-columns { display:none; }', css)
        self.assertIn('clip-path:none; white-space:normal;', css)
        self.assertIn('font-variant-numeric:tabular-nums;', css)

    def test_settings_menu_styles_keep_rows_readable_and_badge_empty_state_hidden(self):
        from supervisor_ui import supervisor_css
        css = supervisor_css()
        self.assertIn('.change-badge:empty { display:none; }', css)
        self.assertIn('grid-template-columns:minmax(0,1fr); gap:8px;', css)
        self.assertIn('summary:focus-visible', css)
        self.assertIn('min-height:60px;', css)
        self.assertIn('font-size:1rem;', css)
        self.assertIn('font-size:.875rem;', css)
        self.assertIn("summary::after { content:'';", css)
        self.assertIn('summary > span { flex-direction:column; align-items:flex-start;', css)
        self.assertNotIn('min-height:96px;', css)
        self.assertNotIn('scope-note', css)

    @unittest.skipUnless(os.environ.get("BIZANT_TEST_NODE_MODULES"), "Optional DOM test dependency not configured")
    def test_dispatcher_interactions_without_a_browser_or_network(self):
        settings = api._redact_web_run_settings(api._web_run_settings_from_config(self.base))
        web = deepcopy(settings)
        web["input"]["json_sklad"] = "WEB"
        web["output"]["excel_output_dir"] = "D:/web"
        vehicle = api._vehicle_to_web_dict(self.base.vehicles[0], self.base)
        vehicle.update(name="Тестов бус", start_time_minutes=0, count=2)
        payload = {"page": api._web_gui_html(self.base.api, "127.0.0.1", 8084), "config": {
            "app_name": "Bizant Test", "revision": "synthetic-revision", "vehicles": [vehicle], "vehicle_types": [vehicle["vehicle_type"]],
            "depots": [{"name": vehicle["start_depot_name"], "coordinates": "42,23"}],
            "web_run_settings": web, "web_run_global_settings": settings, "run_status": {"running": False, "status": "idle"}}}
        result = subprocess.run([shutil.which("node"), str(Path(__file__).with_name("dispatcher_portal_dom.cjs"))],
                                input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True, timeout=30)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
