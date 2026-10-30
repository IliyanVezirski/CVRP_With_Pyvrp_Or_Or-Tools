"""Third inline service option; persisted with the fleet, never solver enum drift."""
def script():
    return r'''
    function enhanceDynamicService(row,vehicle,operational){
      row.opsKey=vehicle.key;row.opsRevision=operational.revision;
      row.opsDynamic=structuredClone(operational.settings.flexible[vehicle.key]||{enabled:false,rules:[]});
      const mode=row.querySelector('[data-field="service_time_mode"]');
      const option=opsEl('option','','Динамично');option.value='dynamic';mode.append(option);
      const details=opsEl('details','ops-advanced ops-dynamic');details.style.cssText='grid-column:1/-1;width:100%';
      details.append(opsEl('summary','','Настройки на динамичното обслужване'));
      const body=opsEl('div','ops-card');details.append(body);opsRules(body,row.opsDynamic.rules);
      body.append(opsEl('p','ops-note','Средни заявки на бус в депото. Индивидуалното обслужване на клиента е с предимство. Запази промените с „Запази бусовете“.'));
      const marker=opsEl('input');marker.type='hidden';marker.dataset.field='dynamic_policy';row.append(marker);
      row.querySelector('.vehicle-core').after(details);
      function mark(){marker.value=JSON.stringify(row.opsDynamic);refreshFleetChanges();}
      function visibility(){const dynamic=mode.value==='dynamic';details.hidden=!dynamic;
        row.querySelector('[data-field="service_time_minutes"]').closest('.vehicle-field').hidden=dynamic||mode.value==='formula';
        if(dynamic)row.querySelector('.service-formula-details').hidden=true;
      }
      row.opsRefreshDynamic=()=>{if(row.opsDynamic.enabled)mode.value='dynamic';visibility();};
      mode.addEventListener('change',event=>{if(event.bubbles){row.opsDynamic.enabled=mode.value==='dynamic';row.opsEditing=true;details.open=row.opsDynamic.enabled;mark();}visibility();});
      for(const event of ['input','change','click'])body.addEventListener(event,()=>queueMicrotask(mark));
      marker.value=JSON.stringify(row.opsDynamic);row.opsRefreshDynamic();
    }
    const dynamicOriginalCollectPayload=collectVehiclePayload;
    collectVehiclePayload=function(){const payload=dynamicOriginalCollectPayload(),rows=Array.from($('vehiclesBody').querySelectorAll('.vehicle-row')).filter(r=>r.opsDynamic);
      if(rows.length){payload.dynamic_policies=Object.fromEntries(rows.map(row=>[row.querySelector('[data-field="config_id"]').value.trim()||row.opsKey,structuredClone(row.opsDynamic)]));payload.operations_revision=rows[0].opsRevision;}
      return payload;
    };
    const dynamicOriginalAddVehicle=addVehicle;
    addVehicle=function(){dynamicOriginalAddVehicle();const row=$('vehiclesBody').lastElementChild;
      const operational=activeProfile==='web'?(state.operations_web||state.operations):state.operations;
      if(row&&operational)enhanceDynamicService(row,{key:row.querySelector('[data-field="config_id"]').value},operational);
    };
    '''
