// Run generated controls with synthetic map objects. No browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {parseHTML} = require(path.join(process.env.BIZANT_TEST_NODE_MODULES, 'linkedom'));
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const {document, window} = parseHTML('<html><head></head><body><div id="route-filter"></div></body></html>');
Object.defineProperty(window.HTMLInputElement.prototype, 'checked', {
  get(){return this.hasAttribute('checked')}, set(value){this.toggleAttribute('checked', !!value)}
});
const map = {layers:new Set(),hasLayer(layer){return this.layers.has(layer)},removeLayer(layer){this.layers.delete(layer)},fitBounds(){}};
const bounds = {extend(){},isEmpty(){return true},isValid(){return true},pad(){return this}};
const callbacks = [], frame = [];
class MutationObserver {constructor(callback){callbacks.push(callback)}observe(){}}
function flush(){callbacks.forEach(callback=>callback());while(frame.length)frame.shift()();}
let panel;
if(payload.provider === 'osm') {
  const L = {
    control(){return {addTo(map){document.body.append(this.onAdd(map));return this}}},
    DomUtil:{create(tag,className){const node=document.createElement(tag);node.className=className;return node}},
    DomEvent:{disableClickPropagation(){},disableScrollPropagation(){}},latLngBounds(){return bounds}
  };
  (payload.courses || []).forEach(course=>{
    window[course.layerName]={addTo(target){target.layers.add(this)}};
  });
  new Function('document','window','L','setTimeout',payload.mapName,payload.panelScript)(document,window,L,callback=>callback(),map);
  panel=document.querySelector(payload.multiple?'.multi-course-panel':'.route-client-panel');
} else {
  const objects=Object.fromEntries(payload.data.routes.map(route=>[route.id,{
    line:{setMap(){}},markers:[],markerObjects:[],startMarker:{setMap(){}}
  }]));
  const name=payload.multiple?'renderCourseCustomerPanel':'renderCustomerPanel';
  new Function('document','MAP_DATA','infoWindow','google','escapeHtml','formatRouteDuration','formatStackNumber','formatRouteNumber','objects','map',
    payload.visibilityScript+'\n'+payload.segmentsScript+'\n'+payload.panelScript+';'+name+'(document.getElementById("route-filter"),objects,map);')(
      document,payload.data,{close(){}},{maps:{LatLngBounds:function(){return bounds}}},String,String,String,String,objects,map);
  panel=document.querySelector('.client-panel');
}
const toggle=document.querySelector('.driver-panel-toggle'),close=document.querySelector('.driver-panel-close');
function assertPanelState(open) {
  assert.ok(panel, 'Customer panel must exist');
  for (const className of ['hidden','multi-course-hidden','route-client-panel-hidden','driver-panel-tall']) {
    assert.ok(!panel.classList.contains(className), 'Panel must not use a hidden or switchable-height state');
  }
  assert.equal(panel.hidden,!open);
  assert.equal(toggle.hidden,open);
  assert.equal(toggle.getAttribute('aria-expanded'),String(open));
  assert.equal(toggle.getAttribute('aria-controls'),panel.id);
  assert.notEqual(panel.style.display,'none');
  assert.ok(!document.getElementById('route-filter').classList.contains('collapsed'));
  assert.equal(document.querySelectorAll('.route-client-toggle,.route-client-close,.multi-course-toggle,.multi-course-close,.client-toggle,.client-close,.driver-panel-tools').length,0);
}
assertPanelState(false);
if(payload.mobile) {
  new Function('document','MutationObserver','requestAnimationFrame',payload.driverScript+';enableMobileDriverPanel();')(
    document,MutationObserver,callback=>frame.push(callback));
  flush();
  assertPanelState(false);
}
toggle.click();assertPanelState(true);
assert.ok(panel.querySelector('.route-client-stats,.client-route-stats').textContent.includes('Общо кашони'));
assert.ok(panel.querySelector('.route-client-card,.multi-course-client-card,.client-list .route-row,.course-client-row').textContent.includes('бр.'));
if(payload.mobile) {
  assert.ok(panel.querySelector('.driver-card'), 'Client cards must still be prepared');
  const stats=panel.querySelector('.driver-stats-toggle');
  assert.equal(stats.getAttribute('aria-expanded'),'false');
  stats.click();assert.equal(stats.getAttribute('aria-expanded'),'true');
  stats.click();assert.equal(stats.getAttribute('aria-expanded'),'false');
  assertPanelState(true);
}
const nav=panel.querySelector('details.driver-google-routes');
if(nav) assert.equal(nav.hasAttribute('open'),false);
if(payload.multiple) {
  const radios=[...panel.querySelectorAll('input[type=radio]')];
  radios[0].checked=false;radios[1].checked=true;
  radios[1].dispatchEvent(new window.Event('change'));flush();
  assert.ok(panel.textContent.includes('Final'),'Switching course must update customer data');
  assert.equal(panel.querySelector('details.driver-google-routes').hasAttribute('open'),false);
  assert.equal(radios[1].checked,true);
  assertPanelState(true);
  close.click();assertPanelState(false);
  toggle.click();assertPanelState(true);
  assert.equal(radios[1].checked,true,'Reopening must keep the selected course');
  assert.ok(panel.textContent.includes('Final'));
  if(payload.mobile)assert.ok(panel.querySelector('.driver-card'), 'New course cards must still be prepared');
}
close.click();assertPanelState(false);
toggle.click();assertPanelState(true);
assert.equal(document.querySelectorAll('.driver-panel-toggle').length,1);
assert.equal(document.querySelectorAll('.driver-panel-close').length,1);
console.log('PASS: initially hidden, open/close, route details, course change and collapsed Google parts');
