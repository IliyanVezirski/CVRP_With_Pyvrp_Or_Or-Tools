"""Leaflet drawing editor, using the same libraries as Desktop Settings."""
def markup():
    return '''<dialog id="zoneMapDialog" class="ops-dialog" style="width:min(1100px,calc(100vw - 24px))"><div class="ops-heading"><div><span class="eyebrow">ЗОНИ</span><h2 id="zoneMapTitle">Начертай зона</h2><p class="muted">Начертай или редактирай границата върху картата.</p></div><button class="ops-close" aria-label="Затвори картата" onclick="closeZoneMap()">×</button></div><div id="zoneMapCanvas" style="height:min(60vh,550px);min-height:300px"></div><p id="zoneMapError" class="form-status bad" style="padding:0 24px" role="alert"></p><div class="ops-footer"><span class="muted">След прилагане запази допълнителните настройки.</span><button onclick="closeZoneMap()">Отказ</button><button class="primary" onclick="applyZoneMap()">Приложи зоната</button></div></dialog>'''


def script():
    return r'''
    let zoneMapAssets=null,zoneMap=null,zoneLayers=null,zoneTarget=null;
    async function loadZoneMapAssets(){if(window.L?.Control?.Draw)return;if(zoneMapAssets)return zoneMapAssets;
      zoneMapAssets=(async()=>{for(const url of ['https://unpkg.com/leaflet@1.9.4/dist/leaflet.css','https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.css']){const link=document.createElement('link');link.rel='stylesheet';link.href=url;document.head.append(link);}
        for(const url of ['https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','https://unpkg.com/leaflet-draw@1.0.4/dist/leaflet.draw.js'])await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src=url;script.onload=resolve;script.onerror=()=>reject(Error('Картата не се зареди. Провери интернет връзката.'));document.head.append(script)});
      })();try{await zoneMapAssets}catch(e){zoneMapAssets=null;throw e;}
    }
    async function openZoneMap(object,type='center',main=false){zoneTarget={object,type,main};$('zoneMapError').textContent='';$('zoneMapTitle').textContent=object.name|| (type==='traffic'?'Трафик зона':'Център зона');$('zoneMapDialog').showModal();
      try{await loadZoneMapAssets();if(!$('zoneMapDialog').open)return;if(zoneMap)zoneMap.remove();
        const center=main?(type==='traffic'?object.city_center_coords:object.center_location):object.center_coords;
        const radius=main?(type==='traffic'?object.city_traffic_radius_km:object.center_zone_radius_km):object.radius_km;
        const mode=main?object.center_zone_mode:object.mode,polygon=main?object.center_zone_polygon:object.polygon;
        zoneMap=L.map('zoneMapCanvas').setView(center||[42.6977,23.3219],12);window.bizantZoneMap=zoneMap;
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{attribution:'&copy; OpenStreetMap',maxZoom:19}).addTo(zoneMap);
        zoneLayers=new L.FeatureGroup().addTo(zoneMap);
        let layer;if(type!=='traffic'&&mode==='polygon'&&polygon?.length>=3)layer=L.polygon(polygon);else if(center&&radius>0)layer=L.circle(center,{radius:radius*1000});
        if(layer){zoneLayers.addLayer(layer);zoneMap.fitBounds(layer.getBounds(),{padding:[30,30]});}
        L.drawLocal.draw.toolbar.buttons.polygon='Начертай полигон';L.drawLocal.draw.toolbar.buttons.circle='Начертай кръг';L.drawLocal.edit.toolbar.buttons.edit='Редактирай зоната';L.drawLocal.edit.toolbar.buttons.remove='Изтрий зоната';
        zoneMap.addControl(new L.Control.Draw({draw:{polygon:type==='traffic'?false:{allowIntersection:false,showArea:true},circle:{},polyline:false,rectangle:false,marker:false,circlemarker:false},edit:{featureGroup:zoneLayers,remove:true}}));
        zoneMap.on(L.Draw.Event.CREATED,event=>{zoneLayers.clearLayers();zoneLayers.addLayer(event.layer)});setTimeout(()=>zoneMap.invalidateSize(),100);
      }catch(error){$('zoneMapError').textContent=error.message;}
    }
    function closeZoneMap(){if(zoneMap){zoneMap.remove();zoneMap=null;window.bizantZoneMap=null;}zoneLayers=null;$('zoneMapDialog').close();}
    function applyZoneMap(){const layers=zoneLayers?.getLayers()||[];if(layers.length!==1){$('zoneMapError').textContent='Начертай една зона.';return;}
      const {object,type,main}=zoneTarget,layer=layers[0],circle=layer instanceof L.Circle;
      const center=circle?layer.getLatLng():layer.getBounds().getCenter(),coords=[Number(center.lat.toFixed(8)),Number(center.lng.toFixed(8))];
      if(type==='traffic'&&!circle){$('zoneMapError').textContent='Трафик зоната трябва да е кръг.';return;}
      if(main){if(type==='traffic'){object.city_center_coords=coords;object.city_traffic_radius_km=layer.getRadius()/1000;}else{object.center_location=coords;object.center_zone_mode=circle?'circle':'polygon';if(circle)object.center_zone_radius_km=layer.getRadius()/1000;else object.center_zone_polygon=layer.getLatLngs()[0].map(p=>[Number(p.lat.toFixed(8)),Number(p.lng.toFixed(8))]);}}
      else{object.center_coords=coords;if(circle)object.radius_km=layer.getRadius()/1000;if(type!=='traffic'){object.mode=circle?'circle':'polygon';if(!circle)object.polygon=layer.getLatLngs()[0].map(p=>[Number(p.lat.toFixed(8)),Number(p.lng.toFixed(8))]);}}
      closeZoneMap();drawOperations();
    }
    '''
