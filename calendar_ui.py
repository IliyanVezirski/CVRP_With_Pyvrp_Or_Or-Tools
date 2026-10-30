"""Day-oriented weekly and full-month fleet calendar."""
def css():
    return '''.calendar-tabs{display:flex;gap:8px;flex-wrap:wrap;margin:16px 0}.calendar-tabs button.active{background:var(--accent);color:var(--accent-ink)}.calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:7px;margin:14px 0}.calendar-day{display:flex;flex-direction:column;align-items:flex-start;gap:5px;min-height:78px;padding:10px;background:white;border:1px solid #dbe3ec;border-radius:10px;cursor:pointer}.calendar-day strong{font-size:17px}.calendar-day small{font-size:11px;color:#6b7e94}.calendar-day.configured{border-bottom:3px solid var(--accent)}.calendar-day.selected{background:color-mix(in srgb,var(--accent) 9%,white);outline:2px solid var(--accent)}.calendar-weekday{font-size:12px;text-align:center;color:#718096}.calendar-bus{display:grid;grid-template-columns:minmax(150px,1fr) 100px 100px;gap:14px;align-items:center}.calendar-bus>details{grid-column:1/-1}.calendar-footer{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}.division-path{display:inline-flex!important;align-items:center;padding:5px 12px!important;border:1px solid #ffffff30;border-radius:24px!important;background:#ffffff12!important;color:#e5efff!important;font-weight:600;letter-spacing:.2px;font-size:14px!important}@media(max-width:600px){.calendar-grid{gap:4px}.calendar-day{min-height:66px;padding:7px 4px}.calendar-day small{font-size:10px}.calendar-bus{grid-template-columns:1fr 80px}.calendar-bus>.ops-note{grid-column:1/-1}}'''


def script():
    return r'''
    let calendarView='weekly',calendarMonth=new Date().toLocaleDateString('sv-SE').slice(0,7),calendarSelected=0;
    drawCalendar=function(content){
      const toggle=opsEl('div','ops-card');opsToggle(toggle,'Графикът е активен',opsDraft,'enabled');content.append(toggle);
      const tabs=opsEl('div','calendar-tabs');for(const [value,label] of [['weekly','Седмичен'],['month','Месечен']])tabs.append(opsButton(label,()=>{calendarView=value;calendarSelected=value==='weekly'?0:calendarMonth+'-01';drawOperations()},calendarView===value?'active':''));
      if(calendarView==='month'){const obj={month:calendarMonth},input=opsField(tabs,'Месец',obj,'month','month');input.onchange=()=>{calendarMonth=input.value;calendarSelected=calendarMonth+'-01';drawOperations()};}
      content.append(tabs);
      const kind=calendarView==='weekly'?'weekly':'date';
      function matches(e,key){return e.kind===kind&&(kind==='weekly'?e.weekdays.includes(Number(key)):e.date===key)}
      function policies(key){const result=Object.fromEntries(opsData.vehicles.map(v=>[v.key,{enabled:v.base.enabled,count:v.base.count,service:{mode:'inherit'}}]));
        if(kind==='date'){const d=new Date(key+'T12:00:00'),weekday=(d.getDay()+6)%7;for(const e of opsDraft.entries)if(e.enabled&&e.kind==='weekly'&&e.weekdays.includes(weekday))Object.assign(result,structuredClone(e.vehicles));}
        for(const e of opsDraft.entries)if(e.enabled&&matches(e,key))Object.assign(result,structuredClone(e.vehicles));return result;
      }
      function remove(key){opsDraft.entries=opsDraft.entries.flatMap(e=>{if(!matches(e,key))return[e];if(kind==='date')return[];const rest=e.weekdays.filter(d=>d!==Number(key));return rest.length?[{...e,weekdays:rest}]:[]});}
      function put(key,values){remove(key);const entry={id:kind+'-'+key,name:kind==='weekly'?opsDays[Number(key)]:key,kind,enabled:true,weekdays:kind==='weekly'?[Number(key)]:[],date:kind==='date'?key:'',vehicles:structuredClone(values)};opsDraft.entries.push(entry);return entry;}
      const grid=opsEl('div','calendar-grid'),buttons=new Map();content.append(grid);
      let keys=[];
      if(kind==='weekly')keys=[0,1,2,3,4,5,6];
      else{const [year,month]=calendarMonth.split('-').map(Number),days=new Date(year,month,0).getDate(),start=(new Date(year,month-1,1).getDay()+6)%7;
        for(const day of opsDays)grid.append(opsEl('div','calendar-weekday',day));for(let i=0;i<start;i++)grid.append(opsEl('div'));
        keys=Array.from({length:days},(_,i)=>calendarMonth+'-'+String(i+1).padStart(2,'0'));
      }
      if(!keys.includes(calendarSelected))calendarSelected=keys[0];
      function refreshTiles(){for(const [key,button] of buttons){const values=policies(key),total=Object.values(values).reduce((s,v)=>s+(v.enabled?Number(v.count)||0:0),0);button.querySelector('small').textContent=total+' буса';button.classList.toggle('configured',opsDraft.entries.some(e=>e.enabled&&matches(e,key)));button.classList.toggle('selected',key===calendarSelected);}}
      for(const key of keys){const button=opsButton('',()=>{calendarSelected=key;drawOperations()},'calendar-day');button.setAttribute('aria-label',kind==='weekly'?opsDays[key]:key);button.append(opsEl('strong','',kind==='weekly'?opsDays[key]:String(Number(key.slice(-2)))),opsEl('small','',''));grid.append(button);buttons.set(key,button);}refreshTiles();
      const editor=opsEl('div','ops-card');editor.append(opsEl('h3','',kind==='weekly'?'Бусове за '+opsDays[calendarSelected]:'Бусове за '+new Date(calendarSelected+'T12:00:00').toLocaleDateString('bg-BG')));
      const values=policies(calendarSelected);
      const chosen={enabled:opsDraft.entries.some(e=>e.enabled&&matches(e,calendarSelected))};
      opsToggle(editor,'Задай отделни настройки за този ден',chosen,'enabled',()=>{if(chosen.enabled)put(calendarSelected,values);else remove(calendarSelected);drawOperations()});
      editor.append(opsEl('p','ops-note','0 буса означава, че не работи. Наличните бройки са от текущия автопарк. Без отделна настройка се наследява '+(kind==='weekly'?'основният автопарк.':'седмичният график.')));
      for(const vehicle of opsData.vehicles){const policy=values[vehicle.key],row=opsEl('div','ops-card calendar-bus');
        opsToggle(row,vehicle.name||'Бус',policy,'enabled',()=>{if(policy.enabled&&!policy.count)policy.count=vehicle.base.count;put(calendarSelected,values);drawOperations()});
        const input=opsField(row,'Брой',policy,'count','number');input.step='1';input.max=String(vehicle.base.count);
        input.onchange=()=>{const n=Number(input.value);if(!Number.isInteger(n)||n<0||n>vehicle.base.count){input.setCustomValidity('От 0 до '+vehicle.base.count+' буса.');input.reportValidity();return;}input.setCustomValidity('');policy.count=n;policy.enabled=n>0;put(calendarSelected,values);drawOperations()};
        row.append(opsEl('span','ops-note','Налични: '+vehicle.base.count));
        const details=opsEl('details','ops-advanced');details.append(opsEl('summary','','Обслужване и ограничения'));const body=opsEl('div','ops-card');
        policy.service??={mode:'inherit'};opsService(body,policy.service);
        const limits=opsEl('div','ops-grid');opsField(limits,'Капацитет (празно = от буса)',policy,'capacity','number');opsField(limits,'Работно време в часове (празно = от буса)',policy,'max_time_hours','number');body.append(limits);details.append(body);row.append(details);
        for(const event of ['input','change','click'])body.addEventListener(event,()=>queueMicrotask(()=>{put(calendarSelected,values);chosen.enabled=true;editor.querySelector('input[type=checkbox]').checked=true;refreshTiles()}));
        editor.append(row);
      }
      const actions=opsEl('div','calendar-footer');actions.append(opsButton('Върни наследените настройки',()=>{remove(calendarSelected);drawOperations()}));
      actions.append(opsButton(kind==='weekly'?'Копирай към делничните дни':'Приложи този ден за целия месец',()=>{if(!confirm('Да копирам ли тези бусове и настройки за '+(kind==='weekly'?'понеделник–петък?':'всички дати от избрания месец?')))return;for(const key of(kind==='weekly'?[0,1,2,3,4]:keys))put(key,values);drawOperations()}));
      editor.append(actions);content.append(editor);
      content.append(opsEl('p','ops-note','Конкретната дата е с предимство пред седмичния график. Всички промени в календара се записват с „Запази“.'));
    };
    '''
