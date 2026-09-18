"""Presentation helpers for the transport supervisor's local Web workspace."""


def supervisor_css():
    return """
    :root { --bg:#f3f5f8; --text:#1c3046; --muted:#62748a; --accent:#2463d5; --line:#e4e9f0; --fleet-columns:28px minmax(130px,1.4fr) 76px minmax(112px,.85fr) 136px minmax(160px,1.2fr) 44px; }
    main { padding:24px 24px 36px; gap:20px; }
    .workspace-header { background:#142b45; padding:18px 24px; gap:16px 24px; }
    .workspace-header h1 { font-size:1.375rem; letter-spacing:-.6px; }
    .workspace-header .pill, .workspace-header .pill.ok { color:#d3e1ef; background:transparent; padding:0; }
    .workspace-header .pill::before { content:''; display:inline-block; width:6px; height:6px; border-radius:50%; margin-right:7px; background:#96adc6; vertical-align:middle; }
    .workspace-header .pill.ok::before { background:#6bd4ac; }
    .workspace-header .pill.bad { color:#ffcad1; background:transparent; }
    .workspace-header .pill.bad::before { background:#ff8899; }
    section { border-color:var(--line); border-radius:10px; box-shadow:none; }
    h2 { font-size:1.0625rem; font-weight:650; letter-spacing:-.25px; }
    button { min-height:38px; border-radius:7px; border-color:var(--line); }
    button.primary { background:var(--accent); border-color:var(--accent); }
    button.quiet { background:transparent; border-color:transparent; color:var(--muted); }
    button.quiet:hover { background:#edf3ff; color:var(--text); }
    #serverLine:empty { display:none; }
    .workspace-brand { display:flex; flex-wrap:wrap; align-items:center; gap:6px 12px; min-width:0; }
    .division-path { padding:3px 9px; border-left:1px solid #53708f; color:#c8d7e9; font-size:15px; font-weight:500; overflow-wrap:anywhere; min-width:0; }
    #vehicleSettingsStatus:empty, #runSettingsStatus:empty, #logsState:empty, #toolbarSend:empty { display:none; }
    #fleetSection { padding:0; background:#fff; border-color:var(--line); }
    #fleetSection > .section-head { padding:18px 20px; margin:0; }
    #fleetSection h2 { margin:0; }
    .vehicles-wrap { padding:0; border:0; border-radius:0; background:transparent; overflow:visible; }
    .vehicle-list { gap:0; }
    .fleet-columns, .vehicle-core { display:grid; grid-template-columns:var(--fleet-columns); gap:12px; align-items:center; width:100%; }
    .fleet-columns { padding:11px 20px; background:#f8fafc; border-block:1px solid var(--line); color:var(--muted); font-size:.875rem; }
    .fleet-columns > span { min-width:0; overflow-wrap:anywhere; }
    .vehicle-core .vehicle-field > label { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip-path:inset(50%); white-space:nowrap; }
    .vehicle-core .vehicle-field { width:auto !important; min-width:0; }
    .vehicle-core .vehicle-field.checkbox { align-self:center; min-height:38px; }
    .vehicle-core .vehicle-field.checkbox > label { position:static; width:100%; height:38px; margin:0; overflow:visible; clip-path:none; display:flex; align-items:center; justify-content:center; cursor:pointer; }
    .vehicle-core .vehicle-field.checkbox input { flex-shrink:0; }
    .vehicle-core :is(input:not([type=checkbox]),select) { height:38px; min-height:38px; min-width:0; border:1px solid var(--line); border-radius:6px; padding:6px 8px; font-size:.875rem; color:var(--text); background:#fff; }
    .vehicle-core input[type=number] { text-align:right; font-variant-numeric:tabular-nums; }
    .vehicle-row .vehicle-card-header { display:none; }
    .vehicle-row:nth-child(n) { gap:0; padding:11px 20px; border:0; border-radius:0; background:#fff; box-shadow:none; }
    .vehicle-row + .vehicle-row { border-top:1px solid var(--line); }
    .vehicle-row:last-child { border-radius:0 0 10px 10px; }
    .vehicle-row.inactive, .vehicle-row.unavailable { opacity:1; background:#f8fafc; }
    .vehicle-row.unavailable input:not([type=checkbox]) { color:var(--muted); }
    #vehicleSettingsStatus { margin:0; padding:12px 20px; border-top:1px solid var(--line); }
    .vehicle-extra { width:100%; border:0; padding:0; }
    .vehicle-extra:not([open]) { display:none; }
    .vehicle-extra > summary { display:none; }
    .vehicle-extra[open] { margin-top:14px; border-top:1px solid var(--line); padding:14px 0 4px; }
    .vehicle-extra-fields { padding-top:0; }
    .vehicle-field[hidden], .service-formula-preview[hidden] { display:none !important; }
    .service-policy-fields { grid-column:1 / -1; flex-basis:100%; display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin:0 0 12px; padding:16px; border:1px solid var(--line); border-radius:8px; background:#f7f9fc; min-width:0; }
    .service-policy-fields[hidden] { display:none !important; }
    .service-policy-fields legend { padding:0 6px; font-weight:600; }
    .service-policy-fields .vehicle-field { width:auto !important; min-width:0; }
    .service-formula-preview { margin:0; color:var(--muted); font-size:13px; }
    @media(max-width:760px) { .service-policy-fields { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media(max-width:420px) { .service-policy-fields { grid-template-columns:1fr; } }
    .vehicle-extra .remove-vehicle { margin-top:12px; }
    .vehicle-tools { display:flex; flex-direction:column; gap:4px; }
    .vehicle-change-count { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
    .vehicle-extra-toggle { min-height:38px; padding:0; width:44px; font-size:24px; line-height:1; background:transparent; border-color:transparent; color:var(--muted); }
    .vehicle-extra-toggle[aria-expanded=true] { background:#edf3ff; color:#2463d5; }
    .vehicle-row.is-modified { box-shadow:inset 3px 0 #d69319; }
    :is(input,select,textarea).is-modified { border-color:#cc8b18; box-shadow:inset 3px 0 #cc8b18; background:#fffaf0; }
    .vehicle-core :is(input,select).is-modified { border-color:#cc8b18; background:#fffaf0; }
    input[type=checkbox].is-modified { outline:2px solid #cc8b18; outline-offset:3px; }
    .field-error { color:#b42318; font-size:13px; line-height:1.4; margin:5px 0 0; overflow-wrap:anywhere; }
    [aria-invalid=true] { border-color:#c62828 !important; box-shadow:inset 3px 0 #c62828 !important; }
    .change-badge { display:inline-block; color:#895008; font-size:13px; font-weight:600; }
    .change-badge:empty { display:none; }
    .supervisor-actions { display:flex; flex-wrap:wrap; gap:10px 18px; align-items:center; margin-top:10px; }
    .action-context { display:flex; flex-wrap:wrap; gap:8px 16px; align-items:center; margin-right:auto; min-width:0; }
    .send-indicator { color:#416078; }
    .send-indicator.external { color:#9a3412; font-weight:650; }
    .toolbar-buttons { display:flex; flex-wrap:wrap; gap:8px; }
    .workspace-header { position:sticky; top:0; z-index:30; }
    .run-progress { display:grid; grid-template-columns:minmax(0,1fr) max-content; flex:1 1 380px; max-width:720px; align-items:center; gap:4px 16px; padding:0; margin:0; min-width:0; color:#fff; }
    .run-progress[hidden] { display:none !important; }
    .run-progress strong { font-size:14px; line-height:1.4; }
    #executionElapsed { font-size:13px; color:#d4e2f2; white-space:nowrap; }
    .execution-meter-row { grid-column:1/-1; display:flex; align-items:center; gap:12px; }
    .execution-meter-row[hidden] { display:none; }
    #executionMeter { flex:1; min-width:0; height:5px; border:0; border-radius:999px; overflow:hidden; background:#3a536e; accent-color:#8dbdff; }
    #executionMeter::-webkit-progress-bar { background:#3a536e; border-radius:999px; }
    #executionMeter::-webkit-progress-value { background:#78b5ff; border-radius:999px; transition:width .25s; }
    #executionMeter::-moz-progress-bar { background:#78b5ff; border-radius:999px; }
    #executionPercent { min-width:46px; text-align:right; font-size:14px; font-weight:700; color:#fff; }
    .execution-logs { border:0; padding:0; }
    #progressSection { max-width:none; padding:0; }
    #progressSection > .section-head { padding:13px 20px; margin:0; border-bottom:1px solid var(--line); }
    #progressSection h2 { margin:0; }
    #logsBox { height:clamp(240px,38vh,500px); max-height:none; margin:0; padding:16px 20px; background:#fff; color:#334b65; border:0; border-radius:0 0 10px 10px; font:13px/1.65 Consolas,monospace; }
    #runDetails:empty, #lastSaved:empty { display:none; }
    #runDetails:not(:empty), #lastSaved:not(:empty) { display:block; padding:0 20px; }
    #runActionsSection { padding:0; background:transparent; border:0; border-radius:0; box-shadow:none; }
    .settings-drawer { margin:0; padding:0; min-width:0; border:1px solid #cbd5e1; border-radius:10px; background:#fff; }
    .settings-drawer > summary { display:flex; align-items:center; gap:18px; min-height:52px; padding:12px 18px; list-style:none; font-size:14px; color:#334d67; }
    .settings-drawer > summary::-webkit-details-marker { display:none; }
    .settings-drawer > summary::after { content:''; flex:0 0 8px; width:8px; height:8px; margin-left:auto; border:solid #53657b; border-width:0 2px 2px 0; transform:rotate(45deg); }
    .settings-drawer[open] > summary { border-bottom:1px solid var(--line); }
    .settings-drawer[open] > summary::after { transform:rotate(225deg); }
    .settings-drawer > summary:hover { background:#f7f9fc; border-radius:10px; }
    .vehicle-empty { margin:0; padding:20px; color:var(--muted); }
    #runSettingsSection { background:#fff; padding:18px; border:0; }
    #runActionsSection .portal-nav [role=tablist] { gap:4px; border-bottom:1px solid var(--line); }
    #runActionsSection .portal-nav button { padding:10px 12px; border:0; border-bottom:2px solid transparent; border-radius:0; }
    #runActionsSection .portal-nav button[aria-selected=true] { background:transparent; color:var(--text); border-bottom-color:var(--accent); }
    #runSettingsSection > .settings-grid { grid-template-columns:minmax(0,1fr); gap:8px; align-items:start; margin-top:12px; }
    #runSettingsSection > .settings-grid > .settings-accordion { grid-column:1/-1; min-width:0; border:1px solid #cbd5e1; border-radius:8px; background:#fff; box-shadow:none; }
    #runSettingsSection > .settings-grid > .settings-accordion[open] { border-color:#5076a0; }
    #runSettingsSection .settings-accordion > summary { padding:16px 18px; min-height:60px; gap:20px; }
    #runSettingsSection .settings-accordion > summary > span { display:flex; align-items:center; justify-content:space-between; gap:16px; flex:1; min-width:0; }
    #runSettingsSection .settings-accordion > summary::after { content:''; width:8px; height:8px; padding:0; margin:0 3px 4px; border:solid #53657b; border-width:0 2px 2px 0; border-radius:0; background:none; transform:rotate(45deg); }
    #runSettingsSection .settings-accordion > summary:hover { background:#f7f9fc; }
    #runSettingsSection .settings-accordion > summary:focus-visible { outline:2px solid #1d4ed8; outline-offset:-3px; }
    #runSettingsSection .settings-accordion[open] > summary { background:#f4f7fb; border-bottom:1px solid #d7e1ed; box-shadow:inset 3px 0 #315f91; }
    #runSettingsSection .settings-accordion[open] > summary::after { content:''; transform:rotate(225deg); margin-top:4px; margin-bottom:0; }
    #runSettingsSection .settings-accordion-title { font-size:1rem; font-weight:650; line-height:1.5; letter-spacing:0; color:#172b4d; margin:0; overflow-wrap:anywhere; }
    #runSettingsSection .settings-accordion-description { margin:0 0 16px; color:#526174; font-size:.875rem; line-height:1.5; }
    .settings-preview { display:block; flex:0 1 55%; margin:0; font-size:.875rem; line-height:1.5; color:#526174; font-weight:400; text-align:right; overflow-wrap:anywhere; }
    .settings-preview:empty { display:none; }
    #runSettingsSection .settings-accordion-body { padding:20px; }
    #runSettingsSection .field-grid, #runSettingsSection .solver-settings-grid { column-gap:20px; row-gap:6px; }
    #runSettingsSection label { color:#334d67; font-weight:500; }
    #runSettingsSection :is(input,select,textarea) { min-width:0; }
    #runSettingsSection .check-line { min-height:48px; border-color:#dce5ef; padding:12px 14px; }
    #runSettingsSection .check-line:has(input:checked) { background:#edf4ff; border-color:#adc6ec; }
    #runSettingsSection .check-line:has(input:disabled) { opacity:.65; }
    #runSettingsSection .connection-settings { background:#f8fafc; margin-top:18px; padding:14px 16px; }
    #runSettingsSection .connection-settings > summary { font-size:14px; color:#426280; }
    #runSettingsSection .connection-settings[open] > summary { margin-bottom:12px; }
    #runSettingsSection .run-actions { border:0; padding-top:6px; }
    #runSettingsStatus { margin-top:12px; }
    :is(input,select,textarea,details,section) { scroll-margin-top:140px; scroll-margin-bottom:24px; }
    @media(max-width:1100px) {
      .workspace-header > .run-progress { order:3; flex-basis:100%; max-width:none; margin:0; }
      .workspace-header > .actions { margin-left:auto; }
    }
    @media(max-width:1000px) {
      .fleet-columns { display:none; }
      .vehicle-core { grid-template-columns:28px minmax(120px,1.4fr) 80px minmax(110px,1fr) 44px; gap:10px; align-items:end; }
      .vehicle-core .vehicle-field > label { position:static; width:auto; height:auto; margin:0 0 5px; overflow:visible; clip-path:none; white-space:normal; font-size:.875rem; }
      .vehicle-core [data-core-field=service_time_minutes] { grid-column:2/4; }
      .vehicle-core [data-core-field=start_depot_name] { grid-column:4/6; }
      .vehicle-core .vehicle-tools { grid-column:5; grid-row:1; }
      .vehicle-core .vehicle-field.active { align-self:end; }
    }
    @media(max-width:640px) {
      main { padding:12px 12px 24px; gap:16px; }
      .vehicle-row:nth-child(n) { padding:14px; }
      .vehicle-core { grid-template-columns:44px minmax(0,1fr) minmax(0,1fr) 44px; gap:10px; }
      .vehicle-core [data-core-field=name] { grid-column:2/4; }
      .vehicle-core [data-core-field=count], .vehicle-core [data-core-field=service_time_minutes] { grid-column:2; }
      .vehicle-core [data-core-field=capacity] { grid-column:3/5; }
      .vehicle-core [data-core-field=start_depot_name] { grid-column:3/5; }
      .vehicle-core .vehicle-tools { grid-column:4; }
      .vehicle-core :is(input:not([type=checkbox]),select) { height:44px; min-height:44px; font-size:1rem; }
      .vehicle-core .vehicle-field.checkbox, .vehicle-core .vehicle-field.checkbox > label, .vehicle-extra-toggle { min-height:44px; }
      #fleetSection > .section-head, #progressSection > .section-head { padding:14px; }
      #fleetSection > .section-head .actions { gap:6px; }
      .profile-heading .profile-actions { width:100%; }
      .profile-heading .profile-actions button { width:100%; }
      button { min-height:44px; }
      .supervisor-actions { gap:8px; }
      .workspace-header { flex-direction:row; align-items:center; }
      .workspace-header > .run-progress { width:100%; gap:4px 8px; }
      .run-progress strong { overflow-wrap:anywhere; }
      #executionElapsed { font-size:12px; white-space:normal; text-align:right; }
      #runSettingsSection { padding:14px; }
      #runSettingsSection > .settings-grid { grid-template-columns:1fr; }
      #runSettingsSection .settings-accordion > summary { padding:14px; }
      #runSettingsSection .settings-accordion > summary > span { flex-direction:column; align-items:flex-start; gap:3px; }
      #runSettingsSection .settings-preview { flex:auto; text-align:left; }
      #runSettingsSection .settings-accordion-body { padding:14px; }
    }
    """


def supervisor_toolbar():
    return """
    <div class="supervisor-actions" id="profileStatusRow" aria-label="Действия за текущите настройки" hidden>
      <div class="action-context"><span id="toolbarSend" class="send-indicator"></span><span id="profileChangeCount" class="change-badge" aria-live="polite"></span></div>
      <button id="toolbarSaveProfile" onclick="saveActiveProfile()" hidden>Запази настройките</button>
    </div>
    """


def supervisor_progress():
    return """
    <div class="run-progress" id="executionProgress" aria-live="polite" hidden>
      <strong id="executionPhase"></strong><span id="executionElapsed"></span>
      <div class="execution-meter-row" id="executionMeterRow" hidden>
        <progress id="executionMeter" max="100" value="0" aria-label="Напредък на изпълнението"></progress>
        <span id="executionPercent"></span>
      </div>
    </div>
    """


def supervisor_script():
    return r"""
    let vehicleElementSerial=0;
    let fleetBaseline=new Map();
    let profileBaseline=new Map();
    let latestRunProgress=null;
    let settingsSaveBusy=false;
    let saveControlStates=[];
    const correctionFields=new Set();
    function beginSettingsSave(scope) {
      if(settingsSaveBusy)return false;
      settingsSaveBusy=true;
      const root=$(scope==='fleet'?'fleetSection':'runSettingsSection');
      saveControlStates=Array.from(root.querySelectorAll('input,select,textarea,button')).map(el=>[el,el.disabled]);
      saveControlStates.forEach(([el])=>{el.disabled=true;});
      syncSupervisorActions();
      return true;
    }
    function finishSettingsSave() {
      saveControlStates.forEach(([el,disabled])=>{el.disabled=disabled;});
      saveControlStates=[];settingsSaveBusy=false;syncSupervisorActions();
    }
    const controlValue=el=>el.type === 'checkbox' ? !!el.checked : String(el.value ?? '');
    const profileControls=()=>Array.from($('runSettingsSection').querySelectorAll('input[id],select[id],textarea[id]'));
    function captureProfileBaseline() {
      correctionFields.clear();
      profileBaseline=new Map(profileControls().map(el=>[el.id,controlValue(el)]));
      clearFieldErrors($('runSettingsSection'));
      refreshProfileChanges();
    }
    function refreshProfileChanges() {
      let count=0;
      profileControls().forEach(el=>{
        const changed=profileBaseline.has(el.id) && profileBaseline.get(el.id)!==controlValue(el);
        el.classList.toggle('is-modified',changed);
        if(changed)count++;
      });
      runSettingsDirty=count>0;profileDirty[activeProfile]=runSettingsDirty;
      $('profileChangeCount').textContent=count ? count+' незаписани '+(count===1?'промяна':'промени') : '';
      syncSupervisorActions();
      return count;
    }
    function rowFields(row) { return Array.from(row.querySelectorAll('[data-field]')); }
    function captureFleetBaseline() {
      fleetBaseline=new Map(Array.from($('vehiclesBody').querySelectorAll('.vehicle-row')).map(row=>
        [row.dataset.draftKey,new Map(rowFields(row).map(el=>[el.dataset.field,controlValue(el)]))]));
      refreshFleetChanges();
    }
    function refreshFleetChanges() {
      let count=0;const present=new Set();
      $('vehiclesBody').querySelectorAll('.vehicle-row').forEach(row=>{
        const key=row.dataset.draftKey;present.add(key);
        const baseline=fleetBaseline.get(key);let changedFields=0;
        rowFields(row).forEach(el=>{
          const changed=!!baseline && baseline.get(el.dataset.field)!==controlValue(el);
          el.classList.toggle('is-modified',changed);if(changed)changedFields++;
        });
        const rowCount=baseline ? changedFields : 1;count+=rowCount;
        row.classList.toggle('is-modified',rowCount>0);
        row.querySelector('.vehicle-change-count').textContent=!baseline ? 'Нов бус' : changedFields ? changedFields+' промени' : '';
      });
      for(const key of fleetBaseline.keys())if(!present.has(key))count++;
      vehicleSettingsDirty=count>0;
      $('fleetChangeCount').textContent=count ? count+' незаписани '+(count===1?'промяна':'промени') : '';
      $('fleetEmpty').hidden=present.size>0;
      $('fleetColumns').hidden=present.size===0;
      syncSupervisorActions();
      return count;
    }
    function enhanceVehicleRow(row) {
      const serial=++vehicleElementSerial;
      row.dataset.draftKey=row.dataset.originalIndex!=='' ? 'saved:'+row.dataset.originalIndex : 'new:'+serial;
      rowFields(row).forEach(el=>{
        el.id='vehicleField'+serial+'_'+el.dataset.field;
        const label=el.closest('.vehicle-field').querySelector('label');
        if(label)label.htmlFor=el.id;
        if(label && el.type==='checkbox') {
          const text=document.createElement('span');text.className='visually-hidden';text.textContent=label.textContent;
          label.replaceChildren(el,text);label.title=text.textContent;
        }
      });
      const core=document.createElement('div');core.className='vehicle-core';
      for(const key of ['enabled','name','count','capacity','service_time_mode','start_depot_name']) {
        const field=row.querySelector('[data-field="'+key+'"]').closest('.vehicle-field');
        field.dataset.coreField=key==='service_time_mode'?'service_time_minutes':key;core.append(field);
      }
      const extra=row.querySelector('.vehicle-extra'),extraFields=row.querySelector('.vehicle-extra-fields');
      for(const key of ['max_time_hours','start_time_minutes'])
        extraFields.append(row.querySelector('[data-field="'+key+'"]').closest('.vehicle-field'));
      const tools=document.createElement('div');tools.className='vehicle-tools';
      const toggle=document.createElement('button');toggle.type='button';toggle.className='vehicle-extra-toggle';
      toggle.textContent='⋯';toggle.setAttribute('aria-label','Още настройки');toggle.title='Още настройки';extra.id='vehicleExtra'+serial;
      toggle.setAttribute('aria-controls',extra.id);toggle.setAttribute('aria-expanded','false');
      toggle.addEventListener('click',()=>{extra.open=!extra.open;toggle.setAttribute('aria-expanded',String(extra.open));});
      extra.addEventListener('toggle',()=>toggle.setAttribute('aria-expanded',String(extra.open)));
      const changes=document.createElement('span');changes.className='vehicle-change-count';
      tools.append(toggle,changes);core.append(tools);extra.before(core);
      extra.append(row.querySelector('.remove-vehicle'));
      const serviceMode=row.querySelector('[data-field="service_time_mode"]');
      const formulaBox=document.createElement('fieldset');formulaBox.className='service-policy-fields';
      const legend=document.createElement('legend');legend.textContent='Формула';formulaBox.append(legend);
      extraFields.prepend(formulaBox);
      const preview=document.createElement('p');preview.className='service-formula-preview';
      preview.style.gridColumn='1 / -1';preview.setAttribute('aria-live','polite');
      const formulaKeys=['service_base_seconds','service_seconds_per_unit','service_quantity_units','service_max_seconds'];
      for(const key of formulaKeys)formulaBox.append(row.querySelector('[data-field="'+key+'"]').closest('.vehicle-field'));
      formulaBox.append(preview);
      function refreshService() {
        const formula=serviceMode.value==='formula';
        formulaBox.hidden=!formula;
        row.querySelector('[data-field="service_time_minutes"]').closest('.vehicle-field').hidden=formula;
        for(const key of formulaKeys)row.querySelector('[data-field="'+key+'"]').closest('.vehicle-field').hidden=!formula;
        preview.hidden=!formula;
        const [base,seconds,units,max]=formulaKeys.map(key=>Number(row.querySelector('[data-field="'+key+'"]').value));
        preview.textContent=units>0 && [base,seconds,max].every(v=>Number.isFinite(v)&&v>=0)
          ? 'Пример: '+[10,100,1000].map(q=>q+' единици → '+Math.floor(Math.min(base+q*seconds/units,max))+' сек').join(' · ')
          : 'Въведи валидни стойности. Броят единици трябва да е над 0.';
      }
      serviceMode.addEventListener('change',()=>{refreshService();extra.open=true;});
      for(const key of formulaKeys)row.querySelector('[data-field="'+key+'"]').addEventListener('input',refreshService);
      refreshService();
    }
    function syncSupervisorActions() {
      if(!$('planButton'))return;
      const global=activeProfile==='global',running=!!(state && state.run_status && state.run_status.running);
      $('toolbarSaveProfile').textContent=global?'Запази основните настройки':'Запази настройките';
      $('planButton').disabled=!state || running || planStarting || settingsSaveBusy;
      $('globalPlanButton').disabled=!state || running || planStarting || settingsSaveBusy;
      $('fleetSaveButton').disabled=settingsSaveBusy;
      $('toolbarSaveProfile').disabled=settingsSaveBusy;
      $('toolbarSaveProfile').hidden=!runSettingsDirty;
      $('fleetSaveButton').hidden=!vehicleSettingsDirty;
      const sends=bool('setDataEnabled');
      const maps=bool('outMapEnabled') && value('outUploadMode')!=='disabled';
      const description=sends ? 'Ще изпрати данни към системата'+(maps?' и ще качи карти':'') : maps?'Ще качи карти за шофьорите':'';
      $('toolbarSend').textContent=description;
      $('toolbarSend').classList.toggle('external',sends || maps);
      $('profileStatusRow').hidden=!runSettingsDirty && !description;
      updateSettingsPreviews();
    }
    function updateSettingsPreviews() {
      const previews={
        input:value('inSource')==='excel'?'Excel файл':value('inDateMode')==='saturday'?'Свързана система · събота':value('inDateMode')==='custom'?'Свързана система · '+(value('inDate') || 'избери дата'):'Свързана система · следващ работен ден',
        solver:(value('objectiveMetric')==='time'?'По-малко време':'По-малко километри')+' · '+(value('solverTimeLimit') || '—')+' сек. търсене',
        output:[bool('outMapEnabled')?'Карти':null,bool('outExcelEnabled')?'Excel':null,bool('outCsvEnabled')?'Табличен файл':null,bool('outChartsEnabled')?'Графики':null].filter(Boolean).join(' · ') || 'Без изходни файлове',
        'set-data':bool('setDataEnabled')?'Включено · променя данните в системата':'Изключено · без изпращане на резултати'
      };
      for(const [key,text] of Object.entries(previews))if($('settingsPreview_'+key))$('settingsPreview_'+key).textContent=text;
    }
    function clearFieldError(el) {
      if(!el)return;
      el.removeAttribute('aria-invalid');
      const errorId=el.dataset.errorId;
      if(errorId) {
        const error=$(errorId);if(error)error.remove();
        const descriptions=(el.getAttribute('aria-describedby') || '').split(/\s+/).filter(id=>id && id!==errorId);
        if(descriptions.length)el.setAttribute('aria-describedby',descriptions.join(' '));else el.removeAttribute('aria-describedby');
        delete el.dataset.errorId;
      }
    }
    function clearFieldErrors(root) {root.querySelectorAll('[aria-invalid=true]').forEach(clearFieldError);}
    function fieldError(element,message) {
      const el=typeof element==='string'?$(element):element;
      if(el) {
        clearFieldError(el);
        const text=document.createElement('div');text.className='field-error';
        text.id='error_'+el.id;text.textContent=message;el.dataset.errorId=text.id;
        el.setAttribute('aria-invalid','true');
        el.setAttribute('aria-describedby',((el.getAttribute('aria-describedby') || '')+' '+text.id).trim());
        el.after(text);
        correctionFields.clear();correctionFields.add(el.id);refreshCorrectionFields();
        // Reveal only the current error once. Typing must not keep reopening
        // a previously corrected section or close a section chosen by the user.
        const section=el.closest('details[data-settings-panel]');
        if(section)document.querySelectorAll('#runSettingsSection > .settings-grid > details').forEach(other=>{if(other!==section)other.open=false;});
        for(let parent=el.parentElement;parent;parent=parent.parentElement) {
          if(parent.tagName==='DETAILS')parent.open=true;
        }
        if(el.scrollIntoView)el.scrollIntoView({behavior:'smooth',block:'center'});
        if(el.focus)el.focus({preventScroll:true});
      }
      return new Error(message);
    }
    function refreshCorrectionFields() {
      for(const id of correctionFields) {
        const el=$(id);if(!el)continue;
        if(!settingsSaveBusy)el.disabled=false;
        el.hidden=false;
        for(let parent=el.parentElement;parent;parent=parent.parentElement) {
          if(parent.hasAttribute('hidden'))parent.hidden=false;
        }
      }
    }
    function validateVehicleRow(row) {
      const get=key=>row.querySelector('[data-field="'+key+'"]');
      const number=(key,min,optional=false)=>{
        const el=get(key),raw=el.value.trim(),n=Number(raw);
        if(optional && raw==='')return;
        if(raw==='' || !Number.isSafeInteger(n) || n<min)throw fieldError(el,'Въведи цяло число, не по-малко от '+min+'.');
      };
      number('count',0);number('capacity',1);number('fixed_cost',Number.MIN_SAFE_INTEGER);number('max_time_hours',1);
      number('service_time_minutes',0);number('reload_time_minutes',0);
      for(const key of ['service_base_seconds','service_seconds_per_unit','service_quantity_units','service_max_seconds']) {
        const el=get(key),n=Number(el.value);
        if(el.value.trim()==='' || !Number.isFinite(n) || n<0 || (key==='service_quantity_units'&&n===0))
          throw fieldError(el,key==='service_quantity_units'?'Въведи число, по-голямо от 0.':'Въведи неотрицателно число.');
      }
      number('max_distance_km',0,true);number('max_customers_per_day',1,true);
      if(!depotOptions.some(depot=>depot.name===get('start_depot_name').value))
        throw fieldError(get('start_depot_name'),'Избери съществуващо начално депо.');
      try{clockToMinutes(get('start_time_minutes').value);}catch(_){throw fieldError(get('start_time_minutes'),'Въведи час на тръгване между 00:00 и 23:59.');}
      for(const key of ['end_location','reload_location']) {
        const raw=get(key).value.trim(),normal=raw.toLocaleLowerCase('bg');
        const aliases=['main','depot','главно депо','основно депо','center','център','vratza','vratsa','враца'];
        if(!raw || aliases.includes(normal) || depotOptions.some(d=>d.name.trim().toLocaleLowerCase('bg')===normal))continue;
        const parts=raw.replaceAll(';',',').split(',').map(v=>v.trim()).filter(Boolean).map(Number);
        if(parts.length!==2 || !parts.every(Number.isFinite) || Math.abs(parts[0])>90 || Math.abs(parts[1])>180)
          throw fieldError(get(key),'Избери име на депо или въведи две координати: ширина, дължина.');
      }
    }
    function validateRunBasics() {
      const requireText=(id,message)=>{if(!value(id).trim())throw fieldError(id,message);};
      const requireUrl=(id,message)=>{try{const url=new URL(value(id));if(!['http:','https:'].includes(url.protocol))throw 0;}catch(_){throw fieldError(id,message);}};
      if(value('inSource')==='excel')requireText('inExcel','Посочи файла със заявки.');
      else requireUrl('inUrl','Посочи валиден адрес за получаване на заявки.');
      if(bool('outMapEnabled')) {
        requireText('outMapFile','Посочи файл за общата карта.');requireText('outRoutesDir','Посочи папка за картите на шофьорите.');
        if(value('outUploadMode')==='effect_upload')requireUrl('outUploadUrl','Посочи валиден адрес за качване на картите.');
      }
      if(bool('outExcelEnabled'))requireText('outExcelDir','Посочи папка за Excel отчетите.');
      if(bool('setDataEnabled'))requireUrl('setDataUrl','Посочи валиден адрес на приемащата система.');
    }
    function updateExecutionProgress(status,progress=null) {
      const key=String((status || {}).run_id || '');
      if(progress)latestRunProgress={key,...progress};
      else if(latestRunProgress && latestRunProgress.key!==key)latestRunProgress=null;
      const names={input:'Получаване на заявки',matrix:'Подготовка на пътищата',solve:'Изчисляване на маршрути',output:'Създаване на отчети',sending:'Изпращане на резултатите'};
      const running=!!(status && status.running),stateName=(status || {}).status;
      const completed=!running && ['completed','success'].includes(stateName);
      $('executionProgress').hidden=!running;
      $('executionProgress').dataset.running=String(running);
      $('executionPhase').textContent=running ? (names[latestRunProgress && latestRunProgress.phase] || 'Изпълнение · изчакване на информация')
        : ['completed','success'].includes(stateName)?'Изпълнението завърши':stateName==='stopped'?'Изпълнението е спряно':['failed','error'].includes(stateName)?'Изпълнението завърши с грешка':'Готовност';
      let elapsed=Number(status && status.elapsed_seconds);
      if(status && status.elapsed_seconds!=null && Number.isFinite(elapsed)) {
        elapsed=Math.max(0,Math.floor(elapsed));
        $('executionElapsed').textContent=(running?'Изминало време: ':'Продължителност: ')+Math.floor(elapsed/60)+' мин '+String(elapsed%60).padStart(2,'0')+' сек';
      } else $('executionElapsed').textContent=running?'Измерване на времето…':'Няма активно изчисление';
      const observed=latestRunProgress && latestRunProgress.percent;
      const percent=completed ? 100 : observed!=null && Number.isFinite(Number(observed)) ? Math.max(0,Math.min(99,Math.floor(Number(observed)))) : null;
      $('executionMeterRow').hidden=!running;
      const meter=$('executionMeter'),label=$('executionPercent');
      if(percent==null) {
        if(running)meter.removeAttribute('value');else meter.setAttribute('value','0');
        label.textContent='—';meter.removeAttribute('aria-valuetext');label.title='';
      } else {
        meter.setAttribute('value',String(percent));
        const estimated=!completed && latestRunProgress && latestRunProgress.estimated;
        label.textContent=(estimated?'≈':'')+percent+'%';
        label.title=estimated?'Ориентировъчен напредък по етапите и времето за търсене.':'';
        meter.setAttribute('aria-valuetext',(estimated?'Ориентировъчно ':'')+percent+'%');
      }
    }
    function initialiseSupervisorUI() {
      document.querySelectorAll('#runSettingsSection > .settings-grid > details').forEach(panel=>{
        const summary=panel.querySelector('summary'),copy=summary.querySelector('span');
        const preview=document.createElement('span');preview.className='settings-preview';preview.id='settingsPreview_'+panel.dataset.settingsPanel;copy.append(preview);
        panel.addEventListener('toggle',()=>{
          if(panel.open)document.querySelectorAll('#runSettingsSection > .settings-grid > details').forEach(other=>{if(other!==panel)other.open=false;});
        });
      });
      document.addEventListener('input',event=>{if(event.target.matches('input,select,textarea'))clearFieldError(event.target);});
      document.addEventListener('change',event=>{if(event.target.matches('input,select,textarea'))clearFieldError(event.target);});
      window.addEventListener('beforeunload',event=>{
        if(vehicleSettingsDirty || profileDirty.web || profileDirty.global) {event.preventDefault();event.returnValue='';}
      });
    }
    """
