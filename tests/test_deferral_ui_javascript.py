"""Execute the actual generated toggle JS without starting an app or solver."""
import json
from dataclasses import asdict
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from config import MainConfig
from cvrp_api_server import _web_gui_html
import test_output_map_visibility as map_tests


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for UI script checks")
class DeferralJavascriptTests(unittest.TestCase):
    def run_js(self, script, check=False):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "check.cjs"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run([shutil.which("node"), *(["--check"] if check else []), str(path)],
                                    capture_output=True, text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def maps():
        fixture = map_tests.VehicleMapGroupingTests
        c = fixture._customer("D", "Test client", 42.72, "Test note")
        solution = SimpleNamespace(routes=[], dropped_customers=[c])
        allocation = SimpleNamespace(warehouse_customers=[])
        with patch("output_handler.get_config", return_value=fixture._config_without_zones()):
            leaflet = fixture._map_generator("osm").create_map(solution, allocation, (42.7, 23.3)).get_root().render()
            google = fixture._map_generator("google").create_map(solution, allocation, (42.7, 23.3)).html_content
        return leaflet, google

    def test_generated_javascript_syntax(self):
        pages = [_web_gui_html(MainConfig().api, "127.0.0.1", 8095), *self.maps()]
        for page in pages:
            scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", page, re.S)
            for script in scripts:
                if script.strip():
                    self.run_js(script, check=True)

    def test_settings_switch_keeps_values_and_shows_correct_fields(self):
        page = _web_gui_html(MainConfig().api, "127.0.0.1", 8095)
        function = page[page.index("function updateDeferralFields()"):page.index("function collectOutputSettings()")]
        self.run_js("""
const assert = require('node:assert/strict');
const fields = {};
const $ = id => fields[id] ||= {value:'', checked:false, hidden:false, disabled:false, querySelector:() => $('geographicOption')};
const value = id => $(id).value;
const bool = id => $(id).checked;
const put = (id,v) => typeof v === 'boolean' ? $(id).checked=v : $(id).value=v;
const setDisabled = (ids,v) => ids.forEach(id => $(id).disabled=v);
""" + function + """
put('solverType','pyvrp_experimental'); put('allowCustomerSkipping',true);
put('priorityDroppingEnabled',true); put('dropVolumeWeight',0.2);
put('customerDroppingStrategy','legacy'); updateDeferralFields();
assert.equal($('legacyDeferralFields').hidden,false);
assert.equal($('geographicDeferralFields').hidden,true);
assert.equal($('dropVolumeWeight').disabled,false);
assert.equal($('dropBasePenalty').disabled,true);
put('customerDroppingStrategy','geographic'); updateDeferralFields();
assert.equal($('legacyDeferralFields').hidden,true);
assert.equal($('geographicDeferralFields').hidden,false);
assert.equal($('dropVolumeWeight').disabled,true);
assert.equal($('dropGeoReference').disabled,false);
assert.equal($('dropBasePenalty').disabled,false);
put('solverType','pyvrp'); updateDeferralFields();
assert.equal(value('customerDroppingStrategy'),'legacy');
assert.equal($('geographicOption').disabled,true);
assert.equal(value('dropVolumeWeight'),0.2);
put('priorityDroppingEnabled',false); updateDeferralFields();
assert.equal($('dropVolumeWeight').disabled,true);
assert.equal($('dropBasePenalty').disabled,false);
put('allowCustomerSkipping',false); updateDeferralFields();
assert.equal($('customerDroppingStrategy').disabled,true);
assert.equal($('dropBasePenalty').disabled,true);
""")

    def test_collect_actual_default_settings_and_custom_weights(self):
        page = _web_gui_html(MainConfig().api, "127.0.0.1", 8095)
        collect = page[page.index("function collectCvrpSettings()"):page.index("function collectRunSettings()")]
        helpers = page[page.index("function requiredInt("):page.index("function cookieValue(")]
        string_list = page[page.index("function stringList("):page.index("function intOrNull(")]
        load = "\n".join(line for line in page.splitlines() if re.match(r'\s*put\(".*cvrp\.', line))
        self.run_js("""
const assert = require('node:assert/strict'); const fields={};
const $=id=>fields[id] ||= {value:'',checked:false};
const value=id=>$(id).value;
const fieldLabel=(id,fallback)=>fallback;
const put=(id,v)=>typeof v==='boolean' ? $(id).checked=v : $(id).value=String(v ?? '');
""" + "const cvrp=" + json.dumps(asdict(MainConfig().cvrp)) + ";\n" + helpers + string_list + load + collect + """
let collected=collectCvrpSettings();
assert.equal(collected.min_customer_drop_penalty,45000);
assert.equal(collected.max_customer_drop_penalty,500000);
put('dropVolumeWeight',0.2); put('dropClosenessWeight',0.8);
put('dropMinPenalty',75000); put('dropMaxPenalty',800000);
collected=collectCvrpSettings();
assert.equal(collected.drop_volume_weight,0.2);
assert.equal(collected.drop_closeness_weight,0.8);
assert.equal(collected.min_customer_drop_penalty,75000);
assert.equal(collected.max_customer_drop_penalty,800000);
put('solverType','pyvrp_experimental'); put('customerDroppingStrategy','geographic');
collected=collectCvrpSettings();
assert.equal(collected.customer_dropping_strategy,'geographic');
assert.equal(collected.drop_volume_weight,0.2);
""")

    def test_leaflet_toggle_is_hidden_then_shows_and_hides(self):
        page, _ = self.maps()
        match = re.search(r"\(function\(\) \{\s*const map = (\w+);\s*const layer = (\w+);.*?\}\)\(\);", page, re.S)
        self.assertIsNotNone(match)
        self.run_js("""
const assert = require('node:assert/strict');
const element = () => ({style:{}, children:[], attributes:{}, appendChild(c){this.children.push(c)},
 setAttribute(k,v){this.attributes[k]=v}, addEventListener(k,v){this[k]=v}});
const document = {createElement:element};
let box; let visible=false; let fits=0;
const mapMock = {hasLayer:()=>visible, removeLayer:()=>{visible=false}, fitBounds:()=>{fits++}};
const layerMock = {addTo:()=>{visible=true}, getBounds:()=>({isValid:()=>true})};
const L = {control:()=>({addTo(){box=this.onAdd()}}),
 DomUtil:{create:element}, DomEvent:{disableClickPropagation(){}, disableScrollPropagation(){}}};
""" + f"const {match.group(1)}=mapMock; const {match.group(2)}=layerMock;\n" + match.group(0) + """
const button=box.children[0];
assert.equal(visible,false); assert.equal(button.attributes['aria-pressed'],'false');
button.click(); assert.equal(visible,true); assert.equal(fits,1);
assert.equal(button.attributes['aria-pressed'],'true');
button.click(); assert.equal(visible,false); assert.equal(fits,1);
""")

    def test_google_toggle_is_hidden_then_shows_and_hides(self):
        _, page = self.maps()
        function = page[page.index("function renderUnservedControl(map)"):page.index("function renderLegend()")]
        data = json.loads(re.search(r"const MAP_DATA = (.*);", page).group(1))
        self.run_js("""
const assert = require('node:assert/strict');
const element=()=>({style:{},children:[],attributes:{},appendChild(c){this.children.push(c)},
 setAttribute(k,v){this.attributes[k]=v},addEventListener(k,v){this[k]=v}});
const document={createElement:element};
const markers=[];
const google={maps:{Marker:class {constructor(o){this.map=o.map;markers.push(this)}
 setMap(m){this.map=m} addListener(){}}, SymbolPath:{CIRCLE:0}, ControlPosition:{RIGHT_BOTTOM:0},
 LatLngBounds:class {extend(){}}}};
const map={controls:[[]],fitBounds(){}};
const infoWindow={close(){}};
""" + "const MAP_DATA=" + json.dumps(data) + ";\n" + function + """
renderUnservedControl(map);
assert.equal(markers.length,1); assert.equal(markers[0].map,null);
const button=map.controls[0][0].children[0];
assert.equal(button.attributes['aria-pressed'],'false');
button.click(); assert.equal(markers[0].map,map);
button.click(); assert.equal(markers[0].map,null);
""")
