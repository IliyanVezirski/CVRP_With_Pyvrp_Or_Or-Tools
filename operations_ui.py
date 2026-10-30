"""Compact, accessible Web editors for schedules, service rules and locations."""


def markup():
    return '''<div class="ops-toolbar full" aria-label="Оперативни настройки">
      <button onclick="openOperations('calendar')">График <span id="opsCalendarBadge" class="ops-tag">изключен</span></button>
      <button onclick="openOperations('check')">Натоварване по депа</button>
      <button onclick="openOperations('locations')">Допълнителни настройки</button>
      <span class="ops-toolbar-note" id="opsAppliedSummary"></span>
    </div>
    <dialog id="operationsDialog" class="ops-dialog" aria-labelledby="opsTitle">
      <div class="ops-heading"><div><span class="eyebrow" id="opsEyebrow">БУСОВЕ И ПЛАНИРАНЕ</span><h2 id="opsTitle"></h2><p id="opsSubtitle" class="muted"></p></div>
      <button class="ops-close" onclick="closeOperations()" aria-label="Затвори">×</button></div>
      <div id="opsContent"></div><div id="opsError" role="alert" class="form-status bad"></div>
      <div class="ops-footer"><span id="opsSaveHint" class="muted">Основните настройки се запазват отделно.</span>
      <button onclick="closeOperations()">Отказ</button><button id="opsSave" class="primary" onclick="saveOperations()">Запази</button></div>
    </dialog>'''


def css():
    return '''
    .ops-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:12px 0}.ops-toolbar button{font-size:13px;background:white}.ops-toolbar-note{margin-left:auto;font-size:12px;color:var(--muted)}
    .ops-tag{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:#eef2f6;color:#596d84;margin-left:5px}.ops-tag.on{background:color-mix(in srgb,var(--accent) 12%,white);color:var(--accent-dark)}
    .ops-dialog{width:min(1040px,calc(100vw - 32px));max-height:90dvh;padding:0;border:1px solid var(--line);border-radius:16px;color:var(--text);box-shadow:0 24px 90px #10243c30;overflow:auto}.ops-dialog::backdrop{background:#12243d70;backdrop-filter:blur(3px)}
    .ops-heading{display:flex;gap:20px;justify-content:space-between;align-items:flex-start;padding:24px 28px;border-bottom:1px solid var(--line);background:#fff;position:sticky;top:0;z-index:3}.ops-heading h2{font-size:25px;margin:4px 0}.ops-heading p{margin:0}.ops-close{font-size:24px;padding:3px 12px;background:white;border:1px solid var(--line);flex-shrink:0}
    #opsContent{padding:24px 28px;background:#f8fafc;min-height:160px}.ops-footer{position:sticky;bottom:0;display:flex;gap:10px;align-items:center;justify-content:flex-end;background:white;padding:16px 28px;border-top:1px solid var(--line)}.ops-footer span{margin-right:auto}
    #opsError{padding:0 28px;background:white}#opsError:empty{display:none}.ops-card{padding:18px;background:white;border:1px solid var(--line);border-radius:12px;margin-bottom:12px}.ops-card h3{font-size:16px;margin:0 0 14px}.ops-row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}.ops-row>.ops-field{flex:1;min-width:110px}
    .ops-field{display:flex;flex-direction:column;gap:5px;font-size:12px;color:var(--muted)}.ops-field input,.ops-field select,.ops-field textarea{width:100%;box-sizing:border-box;font-size:14px;color:var(--text);padding:9px 11px;border:1px solid #cbd5e1;background:white}.ops-field:has(input[type=color]){flex:0 0 150px}.ops-field input[type=color]{width:100%;height:42px;padding:4px}.ops-field textarea{min-height:100px}.ops-toggle{display:flex;align-items:center;gap:9px;font-size:14px;cursor:pointer}.ops-section-title{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:18px 0 12px}.ops-section-title h3{margin:0;font-size:16px}.ops-empty{text-align:center;padding:28px;color:var(--muted);border:1px dashed #cbd5e1;border-radius:12px;background:white}
    .ops-rule{border-left:3px solid var(--accent)}.ops-rule .ops-row+.ops-row{margin-top:14px;padding-top:14px;border-top:1px solid #edf0f4}.ops-remove{background:white;border:1px solid var(--line);color:#a52626;font-size:12px}.ops-days{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}.ops-days label{cursor:pointer;padding:7px 10px;background:#f1f5f9;border-radius:7px;font-size:13px}.ops-days label:has(input:checked){color:var(--accent-dark);background:color-mix(in srgb,var(--accent) 13%,white)}.ops-days input{width:14px;height:14px;vertical-align:middle}
    .ops-note{font-size:12px;color:var(--muted);margin:10px 0}.ops-applied{width:100%;padding:8px 12px;border-radius:8px;background:color-mix(in srgb,var(--accent) 7%,white);font-size:12px;color:var(--accent-dark);display:flex;align-items:center;gap:12px}.ops-applied button{margin-left:auto;font-size:12px;min-height:30px;padding:4px 9px}.ops-flex-button{font-size:12px;min-height:34px;padding:6px 10px}.ops-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-bottom:16px}.ops-stat{background:white;border:1px solid var(--line);border-radius:12px;padding:18px}.ops-stat strong{font-size:30px;display:block;line-height:1.2;color:var(--accent-dark);margin:8px 0}.ops-stat small{color:var(--muted)}
    .ops-advanced>summary{font-size:15px;font-weight:600;padding:14px;cursor:pointer}.ops-advanced{background:white;border:1px solid var(--line);border-radius:10px;margin:10px 0}.ops-advanced>.ops-card{border:0;margin:0}.ops-service{margin-top:12px}.ops-bus{margin-top:12px}.ops-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.ops-date-entry{border-left:3px solid var(--accent)}
    @media(max-width:700px){.ops-heading,#opsContent{padding:18px}.ops-dialog{width:calc(100vw - 16px);max-height:95dvh}.ops-footer{padding:12px 18px;flex-wrap:wrap}.ops-footer span{width:100%}.ops-grid{grid-template-columns:1fr}.ops-row>.ops-field{min-width:130px}.ops-toolbar-note{width:100%;margin:0}.ops-heading h2{font-size:21px}}
    '''


def script():
    return r'''
    let opsData=null, opsSection='', opsKey='', opsDraft=null, opsBusy=false, opsPoll=null;
    const opsDays=['Пон','Вт','Ср','Чет','Пет','Съб','Нед'];
    function opsEl(tag,cls,text){const el=document.createElement(tag);if(cls)el.className=cls;if(text!==undefined)el.textContent=text;return el;}
    function opsButton(text,action,cls=''){const b=opsEl('button',cls,text);b.type='button';b.onclick=action;return b;}
    function opsField(parent,label,object,key,type='text',choices=null){
      const wrap=opsEl('label','ops-field'),caption=opsEl('span','',label);let input=opsEl(choices?'select':type==='textarea'?'textarea':'input');
      if(choices)choices.forEach(([v,t])=>{const o=opsEl('option','',t);o.value=v;input.append(o)});else if(type!=='textarea'){input.type=type;if(type==='number'){input.min='0';input.step='any'}}
      input.value=object[key]??'';input.oninput=()=>{object[key]=type==='number'?(input.value===''?null:Number(input.value)):input.value;};
      wrap.append(caption,input);parent.append(wrap);return input;
    }
    function opsToggle(parent,label,object,key,change){const l=opsEl('label','ops-toggle'),c=opsEl('input');c.type='checkbox';c.checked=!!object[key];c.onchange=()=>{object[key]=c.checked;if(change)change()};l.append(c,document.createTextNode(label));parent.append(l);return c;}
    function opsDayFields(parent,obj){const box=opsEl('div','ops-days');opsDays.forEach((label,i)=>{const l=opsEl('label'),c=opsEl('input');c.type='checkbox';c.checked=(obj.weekdays||[]).includes(i);c.onchange=()=>{obj.weekdays=opsDays.map((_,n)=>n).filter(n=>n===i?c.checked:obj.weekdays.includes(n))};l.append(c,document.createTextNode(' '+label));box.append(l)});parent.append(box)}
    function opsService(parent,policy,inherit=true,dynamic=true){
      const box=opsEl('div','ops-service'),row=opsEl('div','ops-row');box.append(row);parent.append(box);
      const options=[...(inherit?[['inherit','От настройките на буса']]:[]),['fixed','Фиксирано'],['formula','Формула'],...(dynamic?[['dynamic','Според натоварването']]:[])];
      const mode=opsField(row,'Обслужване',policy,'mode','text',options),fields=opsEl('div','ops-row');box.append(fields);
      function draw(){fields.replaceChildren();if(policy.mode==='fixed'){policy.seconds??=480;opsField(fields,'Секунди на клиент',policy,'seconds','number')}
        if(policy.mode==='formula'){policy.base??=0;policy.seconds??=12;policy.units??=1;policy.maximum??=900;
          [['Начални секунди','base'],['Добави секунди','seconds'],['За количество','units'],['Максимум секунди','maximum']].forEach(([l,k])=>opsField(fields,l,policy,k,'number'))}
        if(policy.mode==='dynamic'){policy.rules??=[];opsRules(fields,policy.rules)} }
      let previousMode=policy.mode;mode.onchange=()=>{if(previousMode==='fixed')policy._fixed=policy.seconds;if(previousMode==='formula')policy._rate=policy.seconds;policy.mode=mode.value;if(policy.mode==='fixed')policy.seconds=policy._fixed??480;if(policy.mode==='formula')policy.seconds=policy._rate??12;previousMode=policy.mode;draw()};draw();
    }
    function opsRules(parent,rules){const wrap=opsEl('div');wrap.style.width='100%';parent.append(wrap);
      function draw(){wrap.replaceChildren();if(!rules.length)wrap.append(opsEl('div','ops-empty','Няма зависимости. Добави диапазон на натоварване.'));
        rules.forEach((rule,i)=>{const card=opsEl('div','ops-card ops-rule'),row=opsEl('div','ops-row');
          opsField(row,'От заявки/бус (включително)',rule,'min','number');opsField(row,'До заявки/бус (без включване)',rule,'max','number');
          row.append(opsButton('Премахни',()=>{rules.splice(i,1);draw()},'ops-remove'));card.append(row);opsService(card,rule.service,false,false);wrap.append(card)});
        wrap.append(opsButton('+ Добави зависимост',()=>{rules.push({min:rules.at(-1)?.max??0,max:null,service:{mode:'fixed',seconds:480}});draw()}));
        wrap.append(opsEl('p','ops-note','Пример: 0–20 означава под 20; следващото правило може да започва от 20. Празно „До“ = без горна граница.'));
      }draw();
    }
    async function openOperations(section,key=''){
      if(opsBusy)return;if(vehicleSettingsDirty){setVehicleSettingsMessage('Първо запази промените по бусовете.','bad');return;}
      try{opsData=await apiFetch('/operations?profile='+activeProfile);opsSection=section;opsKey=key;opsDraft=structuredClone(opsData.settings[section]);
        $('opsError').textContent='';$('opsSave').hidden=false;$('opsSaveHint').textContent='Основните настройки се запазват отделно.';drawOperations();$('operationsDialog').showModal();
      }catch(e){setVehicleSettingsMessage(e.message,'bad')}
    }
    function closeOperations(){if(opsBusy)return;clearTimeout(opsPoll);$('operationsDialog').close();}
    function drawOperations(){const content=$('opsContent');content.replaceChildren();
      const titles={flexible:'Гъвкаво обслужване',calendar:'График на бусовете',check:'Натоварване по депа',locations:'Допълнителни настройки'};
      $('opsTitle').textContent=titles[opsSection];$('opsSubtitle').textContent={flexible:'Обслужване според средните заявки на бус в депото.',calendar:'Седмични настройки и изключения за конкретни дати.',check:'Само преброяване. Без оптимизация, SetData или качване на карти.',locations:'Локации, трафик, зони и цвят на интерфейса.'}[opsSection];
      if(opsSection==='flexible'){
        const v=opsData.vehicles.find(v=>v.key===opsKey);$('opsTitle').textContent='Гъвкаво обслужване · '+(v?.name||'Бус');
        const flex=opsDraft[opsKey]??={enabled:false,rules:[]};const card=opsEl('div','ops-card');opsToggle(card,'Включено за този бус',flex,'enabled');content.append(card);opsRules(content,flex.rules);
        content.append(opsEl('p','ops-note','Индивидуалното обслужване от входните данни е с предимство. Без подходящо правило се запазва основното обслужване.'));
      }
      if(opsSection==='calendar')drawCalendar(content);
      if(opsSection==='check')drawLoadCheck(content);
      if(opsSection==='locations')drawLocations(content);
    }
    function drawCalendar(content){const head=opsEl('div','ops-card');opsToggle(head,'Графикът е активен',opsDraft,'enabled');content.append(head);
      const list=opsEl('div');content.append(list);
      function draw(){list.replaceChildren();if(!opsDraft.entries.length)list.append(opsEl('div','ops-empty','Добави седмичен график или дата от месечния календар.'));
        opsDraft.entries.forEach((entry,i)=>{const card=opsEl('details','ops-card ops-date-entry');const title=opsEl('summary','',entry.name+' · '+(entry.kind==='date'?entry.date:entry.weekdays.map(d=>opsDays[d]).join(', ')));card.append(title);
          const row=opsEl('div','ops-row');opsField(row,'Име',entry,'name');opsToggle(row,'Активен',entry,'enabled');row.append(opsButton('Изтрий',()=>{opsDraft.entries.splice(i,1);draw()},'ops-remove'));card.append(row);
          if(entry.kind==='date')opsField(row,'Дата',entry,'date','date');else opsDayFields(card,entry);
          const rows=opsEl('div');card.append(rows);
          opsData.vehicles.forEach(v=>{const group=opsEl('div','ops-card ops-bus'),selected={on:!!entry.vehicles[v.key]};
            const toggle=opsToggle(group,v.name+' · '+v.count+' бр.',selected,'on',()=>{if(selected.on)entry.vehicles[v.key]={enabled:v.base.enabled,count:v.base.count,service:{mode:'inherit'}};else delete entry.vehicles[v.key];render()});
            const fields=opsEl('div');group.append(fields);function render(){fields.replaceChildren();if(!entry.vehicles[v.key])return;const p=entry.vehicles[v.key],r=opsEl('div','ops-row');
              opsToggle(r,'Работи',p,'enabled');opsField(r,'Налични бусове',p,'count','number');
              const advanced=opsEl('details','ops-advanced');advanced.append(opsEl('summary','','Капацитет и работно време'));const extra=opsEl('div','ops-row ops-card');
              ['capacity','max_time_hours'].forEach(k=>{p[k]??=v.base[k];opsField(extra,k==='capacity'?'Капацитет':'Работни часове',p,k,'number')});advanced.append(extra);fields.append(r,advanced);opsService(fields,p.service)}render();rows.append(group)});
          list.append(card)});
      }draw();const actions=opsEl('div','ops-row');
      function add(kind){const today=new Date(),d=new Date(today.getTime()-today.getTimezoneOffset()*60000).toISOString().slice(0,10);opsDraft.entries.push({id:crypto.randomUUID?crypto.randomUUID():String(Date.now()),name:kind==='date'?'Изключение за дата':'Седмичен график',enabled:true,kind,date:d,weekdays:[0,1,2,3,4],vehicles:{}});draw();list.lastElementChild.open=true;}
      actions.append(opsButton('+ Седмичен график',()=>add('weekly')),opsButton('+ Дата от календара',()=>add('date')));content.append(actions,opsEl('p','ops-note','Конкретната дата е с предимство пред седмичния график. Непосочените бусове използват основните си настройки.'));
    }
    function drawLoadCheck(content){const last=opsData.effective.snapshot||opsData.settings.last_check;
      if(last){const stats=opsEl('div','ops-stats');Object.values(last.depots||{}).filter(v=>v.buses||v.orders).forEach(v=>{const s=opsEl('div','ops-stat');s.append(opsEl('span','',v.name),opsEl('strong','',v.average===null?'Няма бус':v.average.toFixed(2)),opsEl('small','',v.orders+' заявки / '+v.buses+' буса'));stats.append(s)});content.append(stats,opsEl('p','ops-note','За дата '+last.date+' · проверено '+new Date(last.checked_at).toLocaleString('bg-BG')));(last.warnings||[]).forEach(w=>content.append(opsEl('p','form-status bad',w)))}
      const card=opsEl('div','ops-card'),row=opsEl('div','ops-row');opsToggle(card,'Автоматична проверка',opsDraft,'enabled');opsField(row,'Час (София)',opsDraft,'time','time');opsField(row,'Източник на заявките',opsDraft,'profile','text',[['global','Основни настройки'],['web','Тестов профил']]);opsField(row,'Броене',opsDraft,'count_mode','text',[['documents','Отделни документи'],['visits','Клиентски посещения']]);card.append(row);opsDayFields(card,opsDraft);content.append(card);
      const map=opsEl('div','ops-card');map.append(opsEl('h3','','Складове от заявките → депа'));const rows=opsEl('div');map.append(rows);
      const mapping=Object.entries(opsDraft.depot_map).map(([warehouse,depot])=>({warehouse,depot}));function draw(){rows.replaceChildren();mapping.forEach((m,i)=>{const r=opsEl('div','ops-row');opsField(r,'Номер на склад',m,'warehouse');opsField(r,'Депо',m,'depot','text',opsData.depots.map(d=>[d.key,d.name]));r.append(opsButton('×',()=>{mapping.splice(i,1);draw()},'ops-remove'));rows.append(r)});opsDraft._mapping=mapping;}draw();map.append(opsButton('+ Свържи склад',()=>{mapping.push({warehouse:'',depot:opsData.depots[0]?.key||''});draw()}));content.append(map);
      const action=opsButton(opsData.check_state.running?'Проверява се…':'Провери сега и приложи',runLoadCheck,'primary');action.disabled=opsData.check_state.running;content.append(action);
      if(opsData.check_state.error)content.append(opsEl('p','form-status bad',opsData.check_state.error));
      content.append(opsEl('p','ops-note','Проверката използва запазените настройки и датата за доставка от избрания профил. Запази промените преди проверка. Непосочените складове следват съществуващата връзка от SetData.'));
    }
    async function runLoadCheck(){try{await saveOperations(false);if($('opsError').textContent)return;await apiFetch('/operations/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile:opsDraft.profile})});await pollLoadCheck()}catch(e){$('opsError').textContent=e.message}}
    async function pollLoadCheck(){opsData=await apiFetch('/operations?profile='+activeProfile);drawOperations();if(opsData.check_state.running){opsPoll=setTimeout(()=>pollLoadCheck().catch(e=>{$('opsError').textContent=e.message}),1500)}else{await refreshOperationsFleet();}}
    const opsLabels={depot_location:'Главно депо',center_location:'Център',vratza_depot_location:'Депо Враца',depot_locations:'Допълнителни депа',center_zone_mode:'Форма на основната зона',center_zone_polygon:'Точки на основния полигон',center_zone_radius_km:'Радиус на центъра (км)',show_center_zone_on_map:'Показвай зоната на картата',center_zones:'Допълнителни център зони',enable_center_zone_priority:'Приоритет за център',enable_center_zone_restrictions:'Ограничения за център',discount_center_bus:'Множител за център бус',center_bus_outside_center_penalty:'Глоба за център бус извън зоната',internal_bus_center_penalty:'Глоба за основен бус',external_bus_center_penalty:'Глоба за външен бус',special_bus_center_penalty:'Глоба за специализиран бус',vratza_bus_center_penalty:'Глоба за бус Враца',city_center_coords:'Център на трафик зоната',city_traffic_radius_km:'Радиус на трафика (км)',city_traffic_duration_multiplier:'Множител на времето',enable_city_traffic_adjustment:'Корекция за трафик',show_city_traffic_zone_on_map:'Показвай трафик зоната',traffic_zones:'Допълнителни трафик зони',name:'Име',mode:'Форма',center_coords:'Център (ширина, дължина)',radius_km:'Радиус (км)',polygon:'Точки на полигона',enabled:'Активна',show_on_map:'Показвай на картата',enable_priority:'Приоритет',enable_restrictions:'Ограничения',priority_vehicle_types:'Типове с приоритет',restricted_vehicle_types:'Типове с ограничения',discount_priority_vehicle:'Множител за приоритетните',priority_vehicle_outside_penalty:'Глоба извън зоната',vehicle_penalties:'Глоби по тип бус',duration_multiplier:'Множител на времето'};
    function drawLocations(content){opsDraft={...structuredClone(opsData.locations),...(opsDraft||{})};
      const color=opsEl('div','ops-card'),r=opsEl('div','ops-row'),v={accent:opsData.settings.accent};color.append(opsEl('h3','','Цвят на интерфейса'));opsField(r,'Акцентиращ цвят',v,'accent','color');r.append(opsButton('Приложи цвят',async()=>{try{const result=await apiFetch('/operations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({section:'accent',value:v.accent,revision:opsData.revision})});opsData.revision=result.operations.revision;opsData.settings.accent=v.accent;await refreshOperationsFleet();applyOpsTheme(v.accent)}catch(e){$('opsError').textContent=e.message}}));color.append(r);content.append(color);
      const groups=[['Депа и локации',['depot_location','center_location','vratza_depot_location','depot_locations']],['Трафик',['enable_city_traffic_adjustment','city_center_coords','city_traffic_radius_km','city_traffic_duration_multiplier','show_city_traffic_zone_on_map','traffic_zones']],['Център и ограничения',Object.keys(opsDraft).filter(k=>!['depot_location','center_location','vratza_depot_location','depot_locations','enable_city_traffic_adjustment','city_center_coords','city_traffic_radius_km','city_traffic_duration_multiplier','show_city_traffic_zone_on_map','traffic_zones'].includes(k))]];
      groups.forEach(([title,keys])=>{const section=opsEl('details','ops-advanced'),body=opsEl('div','ops-card ops-grid');section.append(opsEl('summary','',title),body);keys.forEach(k=>opsLocationField(body,opsDraft,k));content.append(section)});
    }
    function opsLocationField(parent,obj,key){const val=obj[key],label=opsLabels[key]||vehicleNames[key]||key;
      if(typeof val==='boolean'){opsToggle(parent,label,obj,key);return}
      if(typeof val==='number'){opsField(parent,label,obj,key,'number');return}
      if(['mode','center_zone_mode'].includes(key)){opsField(parent,label,obj,key,'text',[['circle','Кръг'],['polygon','Полигон']]);return}
      if((Array.isArray(val)&&val.length===2&&val.every(n=>typeof n==='number'))||['depot_location','center_location','vratza_depot_location','city_center_coords','center_coords'].includes(key)){
        const v={text:(val||[]).join(', ')};const field=opsField(parent,label,v,'text');field.onchange=()=>{obj[key]=field.value.split(/[,;\s]+/).filter(Boolean).map(Number)};return}
      if(key.includes('polygon')){const v={text:(val||[]).map(p=>p.join(', ')).join('\n')};const input=opsField(parent,label+' — една точка на ред',v,'text','textarea');input.onchange=()=>{obj[key]=input.value.split('\n').filter(x=>x.trim()).map(x=>x.trim().split(/[,;\s]+/).map(Number))};return}
      if(key.endsWith('vehicle_types')){const box=opsEl('div');box.append(opsEl('span','ops-note',label));Object.entries(vehicleNames).forEach(([v,t])=>{const item={on:val.includes(v)};opsToggle(box,t,item,'on',()=>{obj[key]=item.on?[...obj[key],v]:obj[key].filter(x=>x!==v)})});parent.append(box);return}
      if(Array.isArray(val)){const box=opsEl('div');box.style.gridColumn='1 / -1';box.append(opsEl('h3','',label));function draw(){box.querySelectorAll('.ops-zone').forEach(e=>e.remove());val.forEach((zone,i)=>{const d=opsEl('details','ops-advanced ops-zone'),grid=opsEl('div','ops-card ops-grid');d.append(opsEl('summary','',zone.name||'Зона'));Object.keys(zone).forEach(k=>opsLocationField(grid,zone,k));grid.append(opsButton('Изтрий зона',()=>{val.splice(i,1);draw()},'ops-remove'));d.append(grid);box.append(d)})}draw();box.append(opsButton('+ Добави зона',()=>{val.push(key==='traffic_zones'?{name:'Нова трафик зона',center_coords:[42.7,23.3],radius_km:5,duration_multiplier:1.2,enabled:true,show_on_map:false}:{name:'Нова център зона',mode:'circle',center_coords:[42.7,23.3],radius_km:2,polygon:[],enabled:true,show_on_map:true,enable_priority:true,enable_restrictions:true,priority_vehicle_types:['center_bus'],restricted_vehicle_types:['internal_bus'],discount_priority_vehicle:0.9,priority_vehicle_outside_penalty:0,vehicle_penalties:{internal_bus:40000}});draw()}));parent.append(box);return}
      if(val&&typeof val==='object'){const box=opsEl('div');box.style.gridColumn='1 / -1';box.append(opsEl('h3','',label));Object.keys(val).forEach(k=>{if(key==='depot_locations'){const row=opsEl('div','ops-row');const v={text:val[k].join(', ')};const inp=opsField(row,k,v,'text');inp.onchange=()=>{val[k]=inp.value.split(/[,;\s]+/).filter(Boolean).map(Number)};row.append(opsButton('×',()=>{delete val[k];drawOperations()},'ops-remove'));box.append(row)}else opsLocationField(box,val,k)});if(key==='depot_locations')box.append(opsButton('+ Добави депо',()=>{const name=prompt('Име на новото депо:');if(name?.trim()){if(Object.hasOwn(val,name.trim()))return;Object.defineProperty(val,name.trim(),{value:[42.7,23.3],writable:true,enumerable:true,configurable:true});drawOperations()}}));parent.append(box);return}
      opsField(parent,label,obj,key);
    }
    async function saveOperations(close=true){if(opsBusy)return;opsBusy=true;$('opsSave').disabled=true;$('opsError').textContent='';
      try{const value=structuredClone(opsDraft);if(opsSection==='check'){value.depot_map={};for(const m of value._mapping||[]){if(!m.warehouse.trim())throw Error('Посочи номер на всеки склад.');if(Object.hasOwn(value.depot_map,m.warehouse.trim()))throw Error('Складът е посочен два пъти.');value.depot_map[m.warehouse.trim()]=m.depot;}delete value._mapping;}
        const result=await apiFetch('/operations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({section:opsSection,value,revision:opsData.revision})});opsData.revision=result.operations.revision;opsData.settings=result.operations.settings;
        if(opsSection==='locations')opsDraft=structuredClone(result.operations.locations);await refreshOperationsFleet();if(close)$('operationsDialog').close();
      }catch(e){$('opsError').textContent=e.message||String(e)}finally{opsBusy=false;$('opsSave').disabled=false}}
    async function refreshOperationsFleet(){if(vehicleSettingsDirty)return;const data=await apiFetch('/config');renderVehicleState(data);}
    function applyOpsTheme(accent){if(!/^#[0-9a-f]{6}$/i.test(accent))return;let style=$('opsTheme');if(!style){style=opsEl('style');style.id='opsTheme';document.head.append(style)}const rgb=[1,3,5].map(i=>parseInt(accent.slice(i,i+2),16)),mix=n=>'#'+rgb.map(x=>Math.round(x*n).toString(16).padStart(2,'0')).join('');const lum=rgb.reduce((s,v,i)=>s+[.2126,.7152,.0722][i]*(v/255<=.04045?v/255/12.92:((v/255+.055)/1.055)**2.4),0);style.textContent=`:root{--accent:${accent};--accent-dark:${mix(.42)};--accent-hover:${mix(.82)};--accent-ink:${lum>.179?'#101827':'white'}}`;}
    const opsOriginalVehicleState=renderVehicleState;
    renderVehicleState=function(data){opsOriginalVehicleState(data);const operational=activeProfile==='web'?(data.operations_web||data.operations):data.operations;if(!operational)return;opsData=opsData||operational;applyOpsTheme(operational.settings.accent);$('opsCalendarBadge').textContent=operational.settings.calendar.enabled?'активен':'изключен';$('opsCalendarBadge').classList.toggle('on',operational.settings.calendar.enabled);
      $('opsAppliedSummary').textContent='За '+operational.effective.date;
      Array.from($('vehiclesBody').querySelectorAll('.vehicle-row')).forEach((row,i)=>{const v=operational.vehicles[i];if(!v)return;const button=opsButton('Гъвкаво обслужване',()=>openOperations('flexible',v.key),'ops-flex-button');row.querySelector('.vehicle-extra-fields').append(button);
        const note=operational.effective.notes[v.key];if(note){const box=opsEl('div','ops-applied');box.append(opsEl('span','',note),opsButton('Основни стойности',()=>{row.opsEditing=true;applyEffectiveRow(row,false);const baseline=fleetBaseline.get(row.dataset.draftKey);if(baseline)for(const key of Object.keys(row.opsBase)){if(JSON.stringify(row.opsBase[key])===JSON.stringify(row.opsValues[key]))continue;const input=row.querySelector(`[data-field="${key}"]`);if(input)baseline.set(key,controlValue(input));}refreshFleetChanges();box.querySelector('button').disabled=true}));row.append(box)}
        row.opsBase=v.base;row.opsValues=v.values;applyEffectiveRow(row,true);});captureFleetBaseline();};
    function applyEffectiveRow(row,effective){if(!row.opsBase)return;const fields=['enabled','count','capacity','max_time_hours','start_time_minutes','service_time_mode','service_time_minutes','service_base_seconds','service_seconds_per_unit','service_quantity_units','service_max_seconds'];for(const key of fields){const input=row.querySelector(`[data-field="${key}"]`);if(!input||JSON.stringify(row.opsBase[key])===JSON.stringify(row.opsValues[key]))continue;const val=(effective?row.opsValues:row.opsBase)[key];if(input.type==='checkbox')input.checked=val;else input.value=key==='start_time_minutes'?minutesToClock(val):val;input.disabled=effective;}const mode=row.querySelector('[data-field="service_time_mode"]');if(mode)mode.dispatchEvent(new Event('change'));updateVehicleCard(row);}
    const opsOriginalCollectVehicles=collectVehicles;
    collectVehicles=function(){const rows=Array.from($('vehiclesBody').querySelectorAll('.vehicle-row')).filter(r=>r.opsBase&&!r.opsEditing);rows.forEach(r=>applyEffectiveRow(r,false));try{return opsOriginalCollectVehicles()}finally{rows.forEach(r=>applyEffectiveRow(r,true))}};
    const opsOriginalSwitchProfile=switchProfile;switchProfile=function(profile){opsOriginalSwitchProfile(profile);if(state&&!vehicleSettingsDirty)renderVehicleState(state)};
    let opsViewStamp='';setInterval(async()=>{if(!state||vehicleSettingsDirty||settingsSaveBusy||$('operationsDialog').open)return;try{const latest=await apiFetch('/operations?profile='+activeProfile);const stamp=JSON.stringify([latest.revision,latest.effective.signature,latest.effective.snapshot?.checked_at]);if(opsViewStamp&&stamp!==opsViewStamp)await refreshOperationsFleet();opsViewStamp=stamp;}catch(e){}},15000);
    '''
