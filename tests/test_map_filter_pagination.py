"""One overview filter, with all routes available; no routing or browser calls."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from output_handler import _compact_map_controls_css
from tests.test_output_map_visibility import VehicleMapGroupingTests as Fixture


class OverviewMapFilterTests(unittest.TestCase):
    def create(self, provider, count=12):
        routes = [Fixture._route(i + 1, str(i), [Fixture._customer(str(i), "Test", 42.72, "")]) for i in range(count)]
        with patch("output_handler.get_config", return_value=Fixture._config_without_zones()), patch("requests.sessions.Session.request", side_effect=AssertionError("No HTTP")):
            document = Fixture._map_generator(provider).create_map(
                SimpleNamespace(routes=routes, dropped_customers=[]),
                SimpleNamespace(warehouse_customers=[]), (42.7, 23.3))
        page = document.html_content if provider == "google" else document.get_root().render()
        return document, page

    def run_js(self, code, check=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overview.cjs"
            path.write_text(code, encoding="utf-8")
            result = subprocess.run([shutil.which("node"), *(["--check"] if check else []), str(path)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_both_providers_keep_all_routes_without_paging_and_load_inline(self):
        for provider in ("osm", "google"):
            with self.subTest(provider=provider):
                document, page = self.create(provider)
                self.assertNotIn("paginateMapChoices", page)
                self.assertNotIn("map-filter-pages", page)
                self.assertNotIn("Предишна страница", page)
                if provider == "osm":
                    control = next(child for child in document._children.values() if child.__class__.__name__ == "LayerControl")
                    self.assertEqual(len(control.overlays), 12)
                    self.assertTrue(all('class="route-load"' in label for label in control.overlays))
                else:
                    data = json.loads(re.search(r"const MAP_DATA = (.*?);\s*let infoWindow;", page, re.S).group(1))
                    self.assertEqual(len(data['routes']), 12)
                if shutil.which("node"):
                    for script in re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", page, re.S):
                        if script.strip(): self.run_js(script, check=True)
        css = _compact_map_controls_css()
        self.assertIn('.route-load {display:inline;', css)
        self.assertNotIn('.route-load {display:block', css)
        self.assertIn("overflow:visible !important", css)
        self.assertNotIn("overflow-y:auto", css)

    @unittest.skipUnless(shutil.which("node") and os.environ.get("BIZANT_TEST_NODE_MODULES"), "Node/linkedom required")
    def test_all_google_checkboxes_filter_routes_independently(self):
        _, page = self.create("google", 25)
        render_filter = page.split('function renderFilter(', 1)[1].split('function renderCourseCustomerPanel(', 1)[0]
        data = json.loads(re.search(r"const MAP_DATA = (.*?);\s*let infoWindow;", page, re.S).group(1))
        linkedom = str(Path(os.environ['BIZANT_TEST_NODE_MODULES']) / 'linkedom')
        self.run_js("const assert=require('node:assert/strict');\n"
            + "const {parseHTML}=require(" + json.dumps(linkedom) + ");\n"
            + "const {document,window}=parseHTML('<div id=\"route-filter\"></div>');\n"
            + "const MAP_DATA=" + json.dumps(data) + ";\n"
            + "function escapeHtml(value){return String(value).replaceAll('&','&amp;').replaceAll('<','&lt;')}\n"
            + 'function renderFilter(' + render_filter + "\n"
            + """
const map={};
function layer(){return {map,values:{},getMap(){return this.map},set(k,v){this.values[k]=v},get(k){return this.values[k]},setMap(v){this.map=v}}}
const objects=Object.fromEntries(MAP_DATA.routes.map(route=>[route.id,{line:layer(),markers:[layer()],startMarker:layer()}]));
renderFilter(objects,map);
const root=document.getElementById('route-filter');
const rows=[...root.querySelectorAll('.route-row')];
assert.equal(rows.length,25);
assert.ok(rows.every(row=>!row.hidden));
assert.equal(root.querySelectorAll('.map-filter-pages').length,0);
root.querySelector('details').setAttribute('open','');
const boxes=[...root.querySelectorAll('input')];
assert.ok(boxes.every(box=>box.hasAttribute('checked')));
for(const box of [boxes[0],boxes[24]]){
  box.checked=false; box.dispatchEvent(new window.Event('change'));
  const route=objects[box.dataset.route];
  assert.equal(route.line.map,null);assert.equal(route.markers[0].map,null);assert.equal(route.startMarker.map,null);
}
assert.equal(objects[boxes[12].dataset.route].line.map,map);
boxes[24].checked=true;boxes[24].dispatchEvent(new window.Event('change'));
assert.equal(objects[boxes[24].dataset.route].line.map,map);
assert.equal(objects[boxes[0].dataset.route].line.map,null);
assert.ok(rows.every(row=>!row.hidden));
assert.ok(rows.every(row=>row.querySelector('.route-load').textContent.length>0));
""")

    def test_gps_customer_tool_remains_on_overview_even_with_one_route(self):
        for provider in ('osm', 'google'):
            for count in (1, 12):
                with self.subTest(provider=provider, count=count):
                    document, page = self.create(provider, count)
                    if provider == 'osm':
                        self.assertTrue(any(child.__class__.__name__ == 'CoordinatePinSearch' for child in document._children.values()))
                    else:
                        self.assertIn('<div id="coordinate-search"></div>', page)


if __name__ == "__main__": unittest.main()
