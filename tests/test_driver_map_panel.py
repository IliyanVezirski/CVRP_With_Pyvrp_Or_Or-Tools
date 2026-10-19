"""Synthetic map/GUI regressions. No routing, uploads, browser or app server."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import cvrp_api_server as api
from config import get_config
from driver_map_panel import driver_panel_css, driver_panel_js, customer_panel_visibility_js
from map_display_settings import (mandatory_customer_comment, validate_mandatory_customer_comment,
                                  driver_total_volume_label, driver_customer_volume_label, validate_driver_volume_label)
from output_handler import _compact_map_controls_css
from tests.test_output_map_visibility import VehicleMapGroupingTests as Fixture


class DriverMapPanelTests(unittest.TestCase):
    def create(self, provider, mobile, multiple=False, total_label="", customer_label=""):
        clients = [Fixture._customer(str(i), f"Client {i}, Ltd", 42.71 + i / 1000, "Visible delivery note") for i in range(11)]
        clients[0].mandatory = True
        # Mandatory is independent of the time-window feature.
        clients[0].time_windows = []
        clients[0].time_window_start_minutes = clients[0].time_window_end_minutes = None
        routes = [Fixture._route(1, "a", clients)]
        if multiple:
            routes.append(Fixture._route(2, "b", [Fixture._customer("last", "Final", 42.72, "Second comment")]))
        generator = Fixture._map_generator(provider)
        generator.config.volume_unit_label = "кашони"
        generator.config.mandatory_customer_comment = 'Важно <доставка> & "днес"'
        generator.config.mobile_driver_panel = mobile
        generator.config.driver_total_volume_label = total_label
        generator.config.driver_customer_volume_label = customer_label
        with patch("output_handler.get_config", return_value=Fixture._config_without_zones()), patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")):
            doc = generator.create_vehicle_route_map(routes, list(range(1, len(routes) + 1)), (42.7, 23.3), routes)
        page = doc.html_content if provider == "google" else doc.get_root().render()
        return generator, doc, page, routes

    def test_single_and_multicourse_leaflet_modes_keep_annotations_and_navigation(self):
        for mobile in (False, True):
            for multiple in (False, True):
                with self.subTest(mobile=mobile, multiple=multiple):
                    generator, doc, page, routes = self.create("osm", mobile, multiple)
                    panels = list(doc._children.values())
                    if multiple:
                        panel = next(child for child in panels if child.__class__.__name__ == "MultiCourseCustomerPanel")
                        content = json.loads(panel.courses)[0]["panelHtml"]
                        self.assertIn('multi-course-client-card-mandatory', content)
                    else:
                        panel = next(child for child in panels if getattr(child, "_name", "") == "SingleRouteCustomerPanel")
                        content = json.loads(panel.panel_html)
                        self.assertIn('route-client-card-mandatory', content)
                        self.assertEqual('SingleRouteGoogleSegmentsPanel' in [getattr(child, '_name', '') for child in panels], not mobile)
                    self.assertIn('Важно &lt;доставка&gt; &amp; &quot;днес&quot;', content)
                    self.assertIn('Visible delivery note', content)
                    self.assertIn('Работно време:', content)
                    self.assertIn('кашони', content.lower())
                    self.assertNotIn('Стекове:', content)
                    self.assertEqual('MobileDriverPanel' in [getattr(child, '_name', '') for child in panels], mobile)
                    self.assertEqual('driver-google-routes' in content, mobile or multiple)
                    for attrs in re.findall(r'<details\b([^>]*)>', content):
                        self.assertNotRegex(attrs, r'\bopen(?:\s|=|$)')
                    self.assertNotIn('Маршрутът е разделен на части, за да се спази', page)
                    self.assertIn('text-decoration:none !important', page)
                    self.assertIn('focus-visible', page)
                    segments = generator._build_google_route_segments(routes[0])
                    labels = [label for part in segments if not part['isReturn'] for label in part['stopLabels']]
                    self.assertEqual(labels, [f'{i+1}. Client {i}, Ltd' for i in range(11)])
                    self.assertEqual(len(segments), 3)  # Same split, including return to depot.
                    self.assertIn('<li>1. Client 0, Ltd</li>', generator._google_segment_stops_html(segments[0]))

    def test_mobile_customer_toggle_is_top_right_only_on_small_screens(self):
        css = driver_panel_css()
        mobile_css = css.split('@media(max-width:700px)', 1)[1]
        rule = re.search(r'\.bizant-mobile-driver \.driver-panel-toggle\s*\{([^}]+)\}', mobile_css).group(1)
        self.assertIn('top:max(12px,env(safe-area-inset-top))', rule)
        self.assertIn('right:max(12px,env(safe-area-inset-right))', rule)
        self.assertIn('bottom:auto', rule)
        self.assertIn('left:auto', rule)
        self.assertNotIn('.driver-panel-toggle', css.split('@media(max-width:700px)', 1)[0])
        for provider in ('osm', 'google'):
            for multiple in (False, True):
                with self.subTest(provider=provider, multiple=multiple):
                    _, _, page, _ = self.create(provider, True, multiple)
                    self.assertIn('top:max(12px,env(safe-area-inset-top))', page)
                    self.assertNotIn('position:fixed; bottom:12px; left:12px', page)

    def test_google_modes_share_settings_and_valid_javascript(self):
        for multiple in (False, True):
            _, _, page, _ = self.create("google", True, multiple)
            data = json.loads(re.search(r"const MAP_DATA = (.*?);\s*let infoWindow;", page, re.S).group(1))
            self.assertTrue(data['mobileDriverPanel'])
            self.assertTrue(data['routes'][0]['markers'][0]['mandatory'])
            self.assertFalse(data['routes'][0]['markers'][0]['hasTimeWindow'])
            self.assertEqual(data['volumeUnit'], 'кашони')
            self.assertEqual(data['mandatoryCustomerComment'], 'Важно <доставка> & "днес"')
            self.assertIn('driver-google-routes', page)
            if shutil.which('node'):
                for script in re.findall(r'<script(?:\s[^>]*)?>(.*?)</script>', page, re.S):
                    with tempfile.TemporaryDirectory() as directory:
                        path = Path(directory) / 'check.js'
                        path.write_text(script, encoding='utf-8')
                        result = subprocess.run([shutil.which('node'), '--check', str(path)], capture_output=True, text=True, timeout=20)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gps_customer_tool_is_absent_from_all_individual_maps(self):
        for provider in ('osm', 'google'):
            for mobile in (False, True):
                for multiple in (False, True):
                    with self.subTest(provider=provider, mobile=mobile, multiple=multiple):
                        _, document, page, _ = self.create(provider, mobile, multiple)
                        if provider == 'osm':
                            self.assertFalse(any(child.__class__.__name__ == 'CoordinatePinSearch' for child in document._children.values()))
                            self.assertNotIn('＋ Клиенти по GPS', page)
                        else:
                            self.assertNotIn('<div id="coordinate-search">', page)
                            self.assertIn('if (!MAP_DATA.singleRouteNumber && !MAP_DATA.courseFilter)', page)

    @unittest.skipUnless(shutil.which('node'), 'Node required for generated navigation markup')
    def test_google_parts_start_collapsed_in_all_individual_modes(self):
        for mobile in (False, True):
            for multiple in (False, True):
                with self.subTest(mobile=mobile, multiple=multiple):
                    _, _, page, _ = self.create('google', mobile, multiple)
                    data = json.loads(re.search(r"const MAP_DATA = (.*?);\s*let infoWindow;", page, re.S).group(1))
                    function = 'function renderGoogleSegments(' + page.split('function renderGoogleSegments(', 1)[1].split('function renderCustomerPanel(', 1)[0]
                    code = "const MAP_DATA=" + json.dumps(data) + ";\nfunction escapeHtml(value){return String(value)}\n" + function + "\nprocess.stdout.write(renderGoogleSegments(MAP_DATA.routes[0]));"
                    result = subprocess.run([shutil.which('node')], input=code, capture_output=True, text=True, encoding='utf-8', timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(result.stdout.startswith('<details class="driver-google-routes"><summary>'))
                    self.assertNotRegex(result.stdout.split('>', 1)[0], r'\bopen(?:\s|=|$)')
                    self.assertIn('<li>1. Client 0, Ltd</li>', result.stdout)
                    self.assertIn('target="_blank" rel="noopener"', result.stdout)

    def test_legacy_web_defaults_and_independent_override(self):
        base = deepcopy(get_config())
        base.output = SimpleNamespace(**{key: value for key, value in vars(base.output).items() if key not in {'mobile_driver_panel', 'mandatory_customer_comment'}})
        defaults = api._web_run_settings_from_config(base)['output']
        self.assertFalse(defaults['mobile_driver_panel'])
        self.assertIn('Задължителен', defaults['mandatory_customer_comment'])
        with patch.object(api, 'get_config', return_value=base):
            changed, applied, ignored = api._build_web_run_config_override({'run_settings': {'output': {
                'mobile_driver_panel': True, 'mandatory_customer_comment': 'Не пропускай'}}})
        self.assertTrue(changed.output.mobile_driver_panel)
        self.assertEqual(changed.output.mandatory_customer_comment, 'Не пропускай')
        self.assertFalse(hasattr(base.output, 'mobile_driver_panel'))
        self.assertEqual(ignored, [])

    def test_independent_volume_labels_have_safe_legacy_defaults(self):
        old = SimpleNamespace(volume_unit_label='стекове')
        self.assertEqual(driver_total_volume_label(old), 'Стекове')
        self.assertEqual(driver_customer_volume_label(old), 'стекове')
        self.assertEqual(driver_customer_volume_label(old, short=True), 'ст.')
        old.driver_total_volume_label = 'Общо BGN'
        old.driver_customer_volume_label = 'лв.'
        self.assertEqual(driver_total_volume_label(old), 'Общо BGN')
        self.assertEqual(driver_customer_volume_label(old), 'лв.')
        self.assertEqual(driver_customer_volume_label(old, short=True), 'лв.')
        self.assertEqual(validate_driver_volume_label('  '), '')
        self.assertEqual(validate_driver_volume_label('x'*40), 'x'*40)
        with self.assertRaises(ValueError): validate_driver_volume_label('x'*41)

    def test_volume_label_text_is_escaped_in_all_individual_modes(self):
        total, customer = 'Общо <кашони>', 'бр. & "пакети"'
        for provider in ('osm', 'google'):
            for mobile in (False, True):
                for multiple in (False, True):
                    with self.subTest(provider=provider,mobile=mobile,multiple=multiple):
                        generator, doc, page, routes = self.create(provider,mobile,multiple,total,customer)
                        self.assertEqual(generator.config.volume_unit_label,'кашони')
                        if provider == 'google':
                            data = json.loads(re.search(r"const MAP_DATA = (.*?);\s*let infoWindow;",page,re.S).group(1))
                            self.assertEqual(data['driverTotalUnit'],total)
                            self.assertEqual(data['driverCustomerUnit'],customer)
                            self.assertEqual(data['driverCustomerUnitShort'],customer)
                            self.assertEqual(data['volumeUnit'],customer)
                            self.assertEqual(data['volumeUnitShort'],total)
                            self.assertIn('escapeHtml(MAP_DATA.driverTotalUnit)',page)
                            self.assertIn('escapeHtml(MAP_DATA.driverCustomerUnitShort)',page)
                        else:
                            name = 'MultiCourseCustomerPanel' if multiple else 'SingleRouteCustomerPanel'
                            panel = next(child for child in doc._children.values() if child.__class__.__name__==name)
                            content = json.loads(panel.courses)[0]['panelHtml'] if multiple else json.loads(panel.panel_html)
                            self.assertIn('Общо &lt;кашони&gt;:',content)
                            self.assertIn('бр. &amp; &quot;пакети&quot;',content)
                            self.assertNotIn('Общо <кашони>',content)

    def test_web_volume_labels_override_old_config_without_changing_global_unit(self):
        base=deepcopy(get_config())
        fields={'driver_total_volume_label','driver_customer_volume_label'}
        base.output=SimpleNamespace(**{key:value for key,value in vars(base.output).items() if key not in fields})
        defaults=api._web_run_settings_from_config(base)['output']
        self.assertEqual(defaults['driver_total_volume_label'],'')
        self.assertEqual(defaults['driver_customer_volume_label'],'')
        values={'driver_total_volume_label':'Общо BGN','driver_customer_volume_label':'лв.'}
        with patch.object(api,'get_config',return_value=base):
            changed, applied, ignored=api._build_web_run_config_override({'run_settings':{'output':values}})
            self.assertEqual(ignored,[])
            self.assertEqual(changed.output.driver_total_volume_label,'Общо BGN')
            self.assertEqual(changed.output.driver_customer_volume_label,'лв.')
            self.assertEqual(changed.output.volume_unit_label,base.output.volume_unit_label)
            self.assertFalse(hasattr(base.output,'driver_total_volume_label'))
            for field in fields:
                with self.assertRaises(ValueError):
                    api._build_web_run_config_override({'run_settings':{'output':{field:'x'*41}}})

    def test_desktop_can_save_only_new_volume_labels_into_an_old_config(self):
        import config_gui
        gui=config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
        gui.cfg=deepcopy(get_config());gui.cfg.vehicles=[]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.py'
            path.write_text('from dataclasses import dataclass\n@dataclass\nclass OutputConfig:\n    enable_interactive_map: bool = True\n',encoding='utf-8')
            with patch.object(config_gui,'_base_dir',directory):
                gui._apply_to_config_file({'output.driver_total_volume_label':'Общо BGN','output.driver_customer_volume_label':'лв.'})
            scope={};exec(compile(path.read_text(encoding='utf-8'),str(path),'exec'),scope)
            saved=scope['OutputConfig']()
            self.assertEqual(saved.driver_total_volume_label,'Общо BGN')
            self.assertEqual(saved.driver_customer_volume_label,'лв.')

    @unittest.skipUnless(shutil.which('node') and os.environ.get('BIZANT_TEST_NODE_MODULES'), 'Node/linkedom required')
    def test_generated_panel_starts_hidden_and_keeps_course_selection(self):
        for provider in ('osm', 'google'):
            for mobile in (False, True):
                for multiple in (False, True):
                    with self.subTest(provider=provider, mobile=mobile, multiple=multiple):
                        _, doc, page, _ = self.create(provider, mobile, multiple, 'Общо кашони', 'бр.')
                        payload = dict(provider=provider, mobile=mobile, multiple=multiple, driverScript=driver_panel_js(), visibilityScript=customer_panel_visibility_js())
                        if provider == 'osm':
                            name = 'MultiCourseCustomerPanel' if multiple else 'SingleRouteCustomerPanel'
                            panel = next(child for child in doc._children.values() if child.__class__.__name__ == name)
                            payload.update(panelScript=str(panel._template.module.script(panel, {})), mapName=doc.get_name())
                            if multiple: payload['courses'] = json.loads(panel.courses)
                        else:
                            payload['data'] = json.loads(re.search(r"const MAP_DATA = (.*?);\s*let infoWindow;", page, re.S).group(1))
                            name = 'renderCourseCustomerPanel' if multiple else 'renderCustomerPanel'
                            end = 'function renderCoordinateSearch(' if multiple else '</script>'
                            payload['panelScript'] = 'function ' + name + '(' + page.split('function ' + name + '(', 1)[1].split(end, 1)[0]
                            payload['segmentsScript'] = 'function renderGoogleSegments(' + page.split('function renderGoogleSegments(', 1)[1].split('function renderCustomerPanel(', 1)[0]
                        result = subprocess.run([shutil.which('node'), str(Path(__file__).with_name('driver_panel_controls_dom.cjs'))],
                                                input=json.dumps(payload), capture_output=True, text=True, encoding='utf-8', timeout=20)
                        self.assertEqual(result.returncode, 0, result.stderr)

    def test_panel_large_height_when_open_and_hidden_css_on_desktop_and_mobile(self):
        css = driver_panel_css()
        base = css.split('@media(max-width:700px)', 1)[0]
        for styles in (base, _compact_map_controls_css()):
            for selector in ('.route-client-panel', '.multi-course-panel', '#route-filter .client-panel'):
                self.assertIn(selector, styles)
            self.assertIn('height:85dvh !important', styles)
            self.assertIn('max-height:85dvh !important', styles)
            self.assertNotIn('driver-panel-tall', styles)
        self.assertNotIn('52dvh', css)
        self.assertNotIn('driver-panel-tools', driver_panel_js())
        self.assertNotIn('Свий панела', driver_panel_js())
        self.assertNotIn('#route-filter.course-filter-control', css)
        self.assertIn('.client-panel[hidden]', _compact_map_controls_css())
        self.assertIn('.driver-panel-toggle[hidden] {display:none !important;}', _compact_map_controls_css())

    def test_desktop_save_adds_fields_to_preserved_config(self):
        import config_gui
        gui = config_gui.ConfigGUI.__new__(config_gui.ConfigGUI)
        gui.cfg = deepcopy(get_config()); gui.cfg.vehicles = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.py'
            path.write_text('from dataclasses import dataclass\n@dataclass\nclass OutputConfig:\n    enable_interactive_map: bool = True\n', encoding='utf-8')
            with patch.object(config_gui, '_base_dir', directory):
                gui._apply_to_config_file({'output.mobile_driver_panel': True, 'output.mandatory_customer_comment': 'Достави "днес"'})
            scope = {}; exec(compile(path.read_text(encoding='utf-8'), str(path), 'exec'), scope)
            self.assertTrue(scope['OutputConfig']().mobile_driver_panel)
            self.assertEqual(scope['OutputConfig']().mandatory_customer_comment, 'Достави "днес"')

    def test_comment_validation_and_alerts_stay_visible_when_collapsed(self):
        self.assertIn('Задължителен', mandatory_customer_comment(SimpleNamespace()))
        for invalid in ('', ' ' * 3, 'x' * 201):
            with self.assertRaises(ValueError): validate_mandatory_customer_comment(invalid)
        css = driver_panel_css()
        hidden_rules = [rule for rule in css.split('}') if 'display:none' in rule]
        for rule in hidden_rules:
            self.assertNotIn('mandatory-customer-note', rule)
            self.assertNotIn('route-client-comment', rule)
            self.assertNotIn('route-client-window', rule)
            self.assertNotIn('client-attention', rule)
        self.assertIn("event.target.closest('a,button')", driver_panel_js())
        self.assertIn("item === card && open", driver_panel_js())


if __name__ == '__main__': unittest.main()
