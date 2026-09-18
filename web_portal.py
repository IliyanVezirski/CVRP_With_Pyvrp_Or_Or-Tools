"""Dispatcher-facing presentation. No optimisation, storage or network work."""

import html
import re


FIELD_LABELS = {
    "inSource": "Източник", "inDateMode": "Ден за доставка", "inDate": "Дата",
    "inWarehouses": "Складове (номера)", "inStatus": "Статус на заявките",
    "inSheet": "Лист в Excel", "inGroup": "Едно посещение за всички заявки на клиента",
    "inUrl": "Адрес за заявки", "inMethod": "Получаване", "inCommand": "Команда",
    "inExtra": "Допълнителни условия",
    "solverType": "Метод", "objectiveMetric": "Цел",
    "solverTimeLimit": "Време за търсене (сек.)",
    "pyvrpSeed": "Начален вариант (празно = автоматично)",
    "pyvrpSeedBase": "База за вариантите", "pyvrpNumNeighbours": "Близки клиенти за сравнение",
    "pyvrpIlsNoImprovement": "Опити без подобрение",
    "pyvrpIlsHistoryLength": "Запомнени оценки",
    "pyvrpMinPerturbations": "Минимални промени при ново търсене",
    "pyvrpMaxPerturbations": "Максимални промени при ново търсене",
    "pyvrpDisplayInterval": "Обновяване на напредъка (сек.)",
    "pyvrpSymmetricProximity": "Сравнявай близостта в двете посоки",
    "pyvrpExhaustiveOnBest": "Подробна проверка на най-добрия план",
    "pyvrpExtendedOperators": "Допълнителни подобрения",
    "pyvrpDisplayProgress": "Показвай напредъка",
    "pyvrpLibraryPenalties": "Стандартни наказания",
    "pyvrpPenaltyUpdateInterval": "Решения между корекциите",
    "pyvrpPenaltyIncrease": "Увеличаване на наказанието",
    "pyvrpPenaltyDecrease": "Намаляване на наказанието",
    "pyvrpPenaltyTarget": "Желан дял валидни решения (0–1)",
    "pyvrpPenaltyTolerance": "Допустимо отклонение от дела (0–1)",
    "pyvrpPenaltyMin": "Минимална тежест на ограниченията",
    "pyvrpPenaltyMax": "Максимална тежест на ограниченията",
    "pyvrpNextTimeout": "Максимално изчакване (сек.; 0 = автоматично)",
    "pyvrpNextFallback": "При проблем опитай с PyVRP 0.13",
    "orFirstSolution": "Начално разпределение", "orMetaheuristic": "Подобряване на плана",
    "orLnsTimeLimit": "Време за локално подобрение (сек.)", "orLnsNodes": "Близки спирки за сравнение",
    "orLnsArcs": "Скъпи отсечки за преразглеждане", "orLambda": "Сила на пренасочването на търсенето",
    "orParallelFirstStrategies": "Начални начини за разпределяне",
    "orParallelMetaheuristics": "Начини за подобряване", "orFullPropagation": "По-подробна проверка на ограниченията",
    "orLogSearch": "Показвай напредъка", "vroomWorkerTimeout": "Максимално изчакване (сек.)",
    "vroomThreads": "Едновременни изчисления (0 = автоматично)",
    "vroomExploration": "Задълбоченост на търсенето (0–5)",
    "vrpWorkerTimeout": "Максимално изчакване (сек.)", "vrpThreads": "Едновременни изчисления (0 = автоматично)",
    "vrpMaxGenerations": "Максимален брой стъпки", "vrpLogProgress": "Показвай напредъка",
    "solverNumWorkers": "Брой варианти (-1 = автоматично)",
    "solverParallelEnabled": "Търси варианти едновременно",
    "outVolumeUnit": "Общ надпис по подразбиране", "outMandatoryComment": "Бележка за задължителен клиент",
    "outMapEnabled": "Карти", "outCsvEnabled": "Таблица за обмен (CSV)",
    "outMapFile": "Файл за общата карта", "outRoutesDir": "Папка за карти",
    "outCsvFile": "Файл за обмен (CSV)", "outFoliumTiles": "Стил на картата",
    "outUploadMode": "Връзка за качване", "outUploadTimeout": "Изчакване (сек.)",
    "outUploadUrl": "Адрес за качване", "outUploadToken": "Нов код за достъп",
    "outClearUploadToken": "Изтрий запазения код за достъп",
    "outUploadTokenField": "Поле за кода за достъп", "outUploadFileField": "Поле за файла",
    "outUploadBusField": "Поле за номера на буса",
    "setDataUrl": "Адрес за изпращане", "setDataMethod": "Начин на изпращане",
    "setDataTimeout": "Изчакване (сек.)", "setDataCommand": "Команда за запис",
    "setDataDoneFlag": "Статус на обслужените", "setDataMainSkld": "Главен склад (номер)",
    "setDataVratzaSkld": "Склад Враца (номер)", "setDataDepotMap": "Депа и номера на складове",
    "setDataIdGrafik": "Номер на графика", "setDataIdGrafikTemplate": "Шаблон за графика",
    "setDataBukvaTemplate": "Обозначение на доставките", "setDataMakeGroup": "Групирай заявките",
    "setDataUnservedDoneFlag": "Начален статус на необслужените",
    "setDataUnservedFinalEnabled": "Промени статуса на необслужените след групиране",
    "setDataUnservedFinalDoneFlag": "Статус след групиране",
    "setDataUnservedGrafik": "График за необслужените",
    "setDataUnservedGrafikTemplate": "Шаблон за графика",
    "setDataUnservedBukvaTemplate": "Обозначение на необслужените",
    "setDataMakeGroupCommand": "Команда за групиране",
}


def dispatcher_labels(page: str) -> str:
    """Replace presentation labels, never field names or integration values."""
    def label(match):
        name = FIELD_LABELS.get(match[2])
        return match[1] + html.escape(name) + "</label>" if name else match[0]
    page = re.sub(r'(<label\b[^>]*\bfor="([^"]+)"[^>]*>)(.*?)</label>', label, page, flags=re.S)
    # Older technical tooltips must not contradict the dispatcher-facing copy.
    def tooltip(match):
        tag = match[0]
        identity = re.search(r'\b(?:id|for)="([^"]+)"', tag)
        if identity and identity[1] in FIELD_LABELS:
            text = FIELD_LABELS[identity[1]]
            return re.sub(r'\btitle="[^"]*"', 'title="' + html.escape(text, quote=True) + '"', tag)
        return tag
    page = re.sub(r'<(?:input|select|label)\b[^>]*\btitle="[^"]*"[^>]*>', tooltip, page)
    return page


def portal_css() -> str:
    return """
    :root { --bg:#f3f6fa; --text:#172c45; --muted:#596d84; --accent:#185de0; --line:#dce4ee; }
    body { font-size:16px; line-height:1.45; }
    [hidden] { display:none !important; }
    header { padding:14px 28px; position:static; background:#122b47; color:white; border:0; }
    header h1 { font-size:20px; letter-spacing:-.4px; }
    header .muted { color:#bdd0e5; }
    header button { color:#e6effa; background:transparent; border-color:#45607d; }
    header .pill { background:#28435f; color:#dbeafe; }
    header .pill.ok { background:#dcfce7; color:#14532d; }
    main { padding:18px 24px 32px; gap:16px; }
    h2 { font-size:20px; letter-spacing:-.35px; }
    label, .vehicle-field label, summary, .check-line, .notice, .form-status { font-size:14px; }
    .muted, .settings-accordion-description, .secret-meta { font-size:13px; line-height:1.5; }
    input, select, textarea { min-height:42px; border-radius:8px; }
    input[type=checkbox] { min-height:0; accent-color:var(--accent); width:18px; height:18px; flex-shrink:0; }
    button { font:600 14px/1.4 'Segoe UI',sans-serif; padding:10px 15px; min-height:42px; border-radius:8px; transition:background .15s,box-shadow .15s; }
    button:hover { background:#eff5fd; }
    button.primary:hover { background:#124ebf; }
    button:disabled { opacity:.55; cursor:not-allowed; }
    :is(button,input,select,textarea,summary):focus-visible { outline:3px solid #79abff; outline-offset:3px; }
    section { border-radius:12px; padding:18px; box-shadow:none; min-width:0; }
    .portal-nav { display:flex; gap:6px; align-items:center; border-bottom:1px solid var(--line); padding-bottom:12px; }
    .portal-nav button { border:0; background:transparent; color:var(--muted); }
    .portal-nav button[aria-selected=true] { background:#dfeafd; color:#164ba4; }
    .portal-nav [role=tablist] { display:flex; gap:6px; flex-wrap:wrap; }
    .portal-nav { border:0; padding:0; }
    .profile-heading { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:14px; }
    .profile-heading h2 { margin:0; font-size:22px; }
    .visually-hidden { position:absolute; width:1px; height:1px; padding:0; overflow:hidden; clip-path:inset(50%); white-space:nowrap; }
    .eyebrow { font-size:12px; font-weight:750; letter-spacing:1.2px; text-transform:uppercase; color:var(--accent); }
    .profile-actions { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
    .profile-actions .primary { padding:12px 22px; }
    .settings-grid { gap:10px; }
    .settings-card, .solver-settings-card { background:white; border-color:var(--line); }
    .settings-accordion > summary { padding:17px 18px; }
    .settings-accordion-title { font-size:16px; margin:0 0 3px; }
    .settings-accordion > summary::after { content:'＋'; border:0; background:#f0f5fb; font-size:20px; width:32px; height:32px; padding:0; text-align:center; }
    .settings-accordion[open] > summary::after { content:'−'; }
    .settings-accordion-body { padding:18px; }
    .solver-fine-panel { border-color:var(--line); padding:16px; }
    .solver-settings-grid { grid-template-columns:repeat(3,minmax(0,1fr)); }
    .solver-fine-panel h4 { font-size:15px; }
    .check-line { padding:12px; border-radius:9px; }
    .connection-settings { background:#f7f9fc; padding:12px 16px; border:1px solid var(--line); border-radius:9px; }
    .vehicles-section { background:white; border-color:var(--line); }
    .vehicles-wrap { padding:0; border:0; background:transparent; }
    .vehicle-list { gap:12px; }
    .vehicle-row { border-color:var(--line); border-left:3px solid #4d7baf; padding:14px 16px; box-shadow:none; }
    .vehicle-row:nth-child(n) { border-left-color:#4d7baf; }
    .vehicle-field { flex:1 1 108px !important; width:auto !important; }
    .vehicle-field.gps { flex-basis:190px !important; }
    .vehicle-field.name { flex-basis:150px !important; }
    .vehicle-row .vehicle-card-header { padding-bottom:8px; gap:8px; }
    .vehicle-row .vehicle-field.checkbox { min-height:42px; flex:0 0 auto !important; }
    .vehicle-extra { flex:1 0 100%; margin:0; padding-top:8px; }
    .vehicle-extra > summary { font-weight:500; color:var(--muted); }
    .vehicle-extra-fields { display:flex; flex-wrap:wrap; gap:12px; padding-top:12px; }
    .vehicle-row .remove-vehicle { min-height:36px; padding:6px 10px; border-color:transparent; font-size:13px; }
    .vehicle-row .remove-vehicle:hover { background:#fef2f2; border-color:#fecaca; }
    .vehicle-field:has([data-field=config_id]) { display:none; }
    .vehicle-card-title { font-size:17px; }
    .vehicle-type-badge { font:600 12px/1.4 'Segoe UI',sans-serif; }
    .vehicle-number { font-size:12px; }
    .scope-badge { font-size:12px; background:#e8f1f7; color:#315572; }
    .logs-panel { max-width:none; }
    .logs-panel .section-head { align-items:center; }
    .logs-panel pre { margin:12px 0 0; min-height:220px; height:360px; max-height:65vh; white-space:pre-wrap; overflow-wrap:anywhere; font:13px/1.55 Consolas,monospace; border-radius:8px; padding:16px; }
    .log-state { font-size:13px; color:var(--muted); }
    .log-state.live { color:#167342; }
    .run-actions { justify-content:space-between; }
    .advanced-actions { border:0; padding:0; margin:0; }
    .advanced-actions > summary { font-weight:500; }
    .advanced-actions .actions { margin-top:10px; }
    @media(max-width:950px) { .solver-settings-grid { grid-template-columns:repeat(2,minmax(0,1fr)); } }
    @media(max-width:640px) { main { padding:12px; } header { padding:16px; } section { padding:16px; } .profile-heading h2 { font-size:24px; } .profile-actions { width:100%; } .profile-actions .primary { flex:1; } .portal-nav { flex-wrap:wrap; } .portal-nav .fleet-link { margin-left:0; } .solver-settings-grid,.field-grid { grid-template-columns:1fr; } }
    @media(prefers-reduced-motion:reduce) { * { transition:none !important; } }
    """


def portal_navigation() -> str:
    return """
    <nav class="portal-nav" aria-label="Работни настройки">
      <div role="tablist" aria-label="Настройки за стартиране">
      <button id="webProfileTab" role="tab" aria-selected="true" aria-controls="runSettingsSection" onclick="switchProfile('web')">Тестове</button>
      <button id="globalProfileTab" role="tab" aria-selected="false" aria-controls="runSettingsSection" onclick="switchProfile('global')">Основни настройки</button>
      </div>
    </nav>
    """


def portal_heading() -> str:
    return """
    <div class="profile-heading">
      <div><h2 class="visually-hidden" id="profileTitle">Тестове</h2>""" + portal_navigation() + """</div>
      <div class="profile-actions">
        <button class="primary" id="planButton" onclick="startRun()" disabled>Стартирай тест</button>
      </div>
    </div>
    """


def portal_input_settings() -> str:
    return """
    <details class="settings-card settings-accordion" data-settings-panel="input">
      <summary><span><span class="settings-accordion-title">Заявки</span></span></summary>
      <div class="settings-accordion-body">
        <p class="settings-accordion-description">Източник, дата и складове.</p>
        <div class="field-grid">
          <div><label for="inSource">Откъде да вземем заявките?</label><select id="inSource"><option value="http_json">От свързаната система</option><option value="excel">От Excel файл</option></select></div>
          <div data-input-http><label for="inDateMode">За кой ден?</label><select id="inDateMode"><option value="auto">Следващ работен ден</option><option value="saturday">Събота от текущата седмица</option><option value="custom">Избери дата</option></select></div>
          <div id="inDateBox" hidden><label for="inDate">Дата за доставка</label><input id="inDate" type="date"></div>
          <div data-input-http><label for="inWarehouses">Номера на складовете</label><input id="inWarehouses" placeholder="Например: 106,128"></div>
          <div data-input-http><label for="inStatus">Статус на заявките за получаване</label><input id="inStatus"></div>
          <div class="wide" data-input-excel hidden><label for="inExcel">Файл със заявки</label><input id="inExcel"><p class="muted">Път до файла на компютъра, на който работи Bizant.</p></div>
          <div data-input-excel hidden><label for="inSheet">Лист във файла</label><input id="inSheet" placeholder="Първият лист"></div>
          <div class="wide check-line"><input id="inGroup" type="checkbox"><label for="inGroup">Обединявай заявките за един клиент в едно посещение</label></div>
        </div>
        <details class="connection-settings" data-input-http><summary>Връзка със системата</summary><div class="field-grid">
          <div class="wide"><label for="inUrl">Адрес за получаване на заявки</label><input id="inUrl" type="url"></div>
          <div><label for="inMethod">Начин на получаване</label><select id="inMethod"><option value="GET">Чрез адрес</option><option value="POST">Чрез изпращане на данни</option></select></div>
          <div><label for="inCommand">Команда за получаване</label><input id="inCommand"></div>
          <div><label for="inTimeout">Изчакване (сек.)</label><input id="inTimeout" type="number" min="1" max="3600"></div>
          <div><label for="inExtra">Допълнителни условия на връзката</label><input id="inExtra"></div>
        </div></details>
      </div>
    </details>
    """


def portal_script() -> str:
    return r"""
    let activeProfile = 'web';
    const profileDrafts = {web:null, global:null};
    const profileDirty = {web:false, global:false};
    let planStarting = false;
    const inputFields = {inSource:'input_source', inExcel:'excel_file_path', inSheet:'sheet_name', inUrl:'json_url',
      inMethod:'json_http_method', inCommand:'json_command', inWarehouses:'json_sklad', inStatus:'json_done_flag',
      inExtra:'json_extra_query', inTimeout:'json_timeout_seconds', inGroup:'enable_customer_document_grouping'};
    const vehicleNames = {internal_bus:'Основен',center_bus:'Център',external_bus:'Външен',special_bus:'Специализиран',vratza_bus:'Враца'};
    const strategyNames = {AUTOMATIC:'Автоматичен избор',PATH_CHEAPEST_ARC:'Най-изгодна следваща отсечка',SAVINGS:'Обединяване на курсове',SWEEP:'По посока около депото',CHRISTOFIDES:'Свързване на близки точки',PARALLEL_CHEAPEST_INSERTION:'Най-изгодно добавяне на клиент',GUIDED_LOCAL_SEARCH:'Насочено подобряване',SIMULATED_ANNEALING:'По-широко изследване на варианти',TABU_SEARCH:'Търсене без повторение'};
    function fieldLabel(id, fallback) {
      const label = document.querySelector('label[for="' + id + '"]');
      return label ? label.textContent.trim() : fallback;
    }
    function minutesToClock(raw) {
      const minutes=Number(raw);
      return String(Math.floor(minutes / 60)).padStart(2,'0') + ':' + String(minutes % 60).padStart(2,'0');
    }
    function clockToMinutes(text) {
      const parts=/^(\d{2}):(\d{2})$/.exec(text);
      if(!parts || Number(parts[1]) > 23 || Number(parts[2]) > 59) throw new Error('Посочи валиден час на тръгване за всеки бус.');
      return Number(parts[1])*60+Number(parts[2]);
    }
    function captureProfileDraft() {
      profileDrafts[activeProfile] = Array.from($('runSettingsSection').querySelectorAll('input[id], select[id], textarea[id]'))
        .map(el => ({id:el.id,value:el.value,checked:el.checked}));
      profileDirty[activeProfile] = runSettingsDirty;
    }
    function savedProfileSettings(profile) {
      return profile === 'global' ? (state.web_run_global_settings || {}) : (state.web_run_settings || {});
    }
    function switchProfile(profile) {
      if (!state || profile === activeProfile || settingsSaveBusy) return;
      captureProfileDraft();
      activeProfile = profile;
      renderRunSettings(savedProfileSettings(profile));
      const draft = profileDrafts[profile];
      if (draft) draft.forEach(item => { const el=$(item.id); if(el) {el.value=item.value;el.checked=item.checked;} });
      runSettingsDirty = profileDirty[profile];
      updateRunFieldStates(); updateInputFields(); updateTimeObjectiveFieldState(); syncStrategyChoices();
      updateVolumeUnitLabels();
      refreshProfileChanges();
      updateProfileSurface();
      setRunSettingsMessage(runSettingsDirty ? 'Има незаписани промени в този раздел.' : '', runSettingsDirty ? '' : 'ok');
    }
    function updateProfileSurface() {
      const global = activeProfile === 'global';
      document.body.dataset.profile = activeProfile;
      $('webProfileTab').setAttribute('aria-selected', String(!global));
      $('globalProfileTab').setAttribute('aria-selected', String(global));
      $('webProfileTab').tabIndex=global ? -1:0; $('globalProfileTab').tabIndex=global ? 0:-1;
      $('runSettingsSection').setAttribute('role','tabpanel');
      $('runSettingsSection').setAttribute('aria-labelledby',global ? 'globalProfileTab':'webProfileTab');
      $('profileTitle').textContent = global ? 'Основни настройки' : 'Тестове';
      $('planButton').hidden = global;
      $('globalStartActions').hidden = !global;
      updatePlanSummary();
      syncSupervisorActions();
    }
    function updateInputFields() {
      const remote = value('inSource') === 'http_json';
      document.querySelectorAll('[data-input-http]').forEach(el => {el.hidden=!remote;});
      document.querySelectorAll('[data-input-excel]').forEach(el => {el.hidden=remote;});
      $('inDateBox').hidden = !remote || value('inDateMode') !== 'custom';
    }
    function renderInputSettings(input) {
      for (const [id,key] of Object.entries(inputFields)) put(id,input[key]);
      const raw=String(input.json_override_date || '');
      put('inDateMode',raw === 'current_week_saturday' ? 'saturday' : raw ? 'custom' : 'auto');
      const parts=raw.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
      put('inDate',parts ? `${parts[3]}-${parts[2]}-${parts[1]}` : raw);
      updateInputFields();
    }
    function collectInputSettings() {
      const input={};
      for(const [id,key] of Object.entries(inputFields)) input[key] = id === 'inGroup' ? bool(id) : value(id);
      input.sheet_name = value('inSheet').trim() || null;
      input.json_timeout_seconds=requiredInt('inTimeout','Изчакване при получаване',1,3600);
      const mode=value('inDateMode');
      if(input.input_source === 'http_json' && mode === 'custom' && !value('inDate')) throw fieldError('inDate','Избери дата за доставка.');
      input.json_override_date=mode === 'saturday' ? 'current_week_saturday' : mode === 'custom' ? value('inDate').split('-').reverse().join('/') : '';
      return input;
    }
    function updatePlanSummary() {
      syncSupervisorActions();
    }
    function resetActiveProfile() {
      if(settingsSaveBusy)return;
      if(runSettingsDirty && !confirm('Да отхвърля ли промените само в този раздел?')) return;
      profileDrafts[activeProfile]=null; profileDirty[activeProfile]=false;
      renderRunSettings(savedProfileSettings(activeProfile)); updateProfileSurface();
    }
    async function saveActiveProfile() {
      if(settingsSaveBusy)return;
      if(activeProfile === 'global') return saveGlobalSettings();
      try {
        const payload={run_settings:collectRunSettings(),clear_upload_token:bool('outClearUploadToken')};
        if(!beginSettingsSave('profile'))return;
        const result=await apiFetch('/run-defaults',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify(payload)});
        if(result.config) {applyCommonState(result.config);renderRunSettings(result.config.web_run_settings);}
        profileDrafts.web=null; profileDirty.web=false; updateProfileSurface();
        setRunSettingsMessage('Настройките за тестовете са запазени. Основните настройки и бусовете не са променени.','ok');
      } catch(err) {setRunSettingsMessage(err.message || String(err),'bad');}
      finally {finishSettingsSave();}
    }
    async function submitPlan(useGlobal=false) {
      if(!state || settingsSaveBusy || planStarting || (state.run_status && state.run_status.running)) return;
      if(vehicleSettingsDirty) {
        const text='Първо запази промените по бусовете или ги отхвърли чрез „Обнови“.';
        setRunSettingsMessage(text,'bad');setVehicleSettingsMessage(text,'bad');
        if($('fleetSection').scrollIntoView)$('fleetSection').scrollIntoView({behavior:'smooth',block:'start'});
        return;
      }
      const settings=useGlobal ? state.web_run_global_settings : collectRunSettings();
      if(!useGlobal && bool('outClearUploadToken')) settings.output.route_maps_upload_token='';
      const payload={profile:useGlobal ? 'global':'web',run_settings:useGlobal ? {} : settings,expected_revision:state.revision};
      planStarting=true;syncSupervisorActions();
      try {
        const result=await apiFetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
        setStatus(result.run || {}); setLogsLive(true);
        setRunSettingsMessage('','ok');
        await loadLogs(false);
      } catch(err) {setRunSettingsMessage(err.message || String(err),'bad');}
      finally {planStarting=false;syncSupervisorActions();}
    }
    function initialiseDispatcherControls() {
      ['webProfileTab','globalProfileTab'].forEach((id,index)=>$(id).addEventListener('keydown',event=>{
        if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
        event.preventDefault();
        const next=event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1-index;
        switchProfile(next ? 'global':'web'); $(next ? 'globalProfileTab':'webProfileTab').focus();
      }));
      // Advanced controls remain available, but never occupy the daily screen.
      const solverBody=document.querySelector('[data-settings-panel=solver] .settings-accordion-body');
      const advanced=document.createElement('details');advanced.className='connection-settings';
      const title=document.createElement('summary');title.textContent='Допълнителни настройки';advanced.append(title);
      solverBody.querySelectorAll('.solver-fine-panel').forEach(panel=>advanced.append(panel));solverBody.append(advanced);
      for(const id of ['orFirstSolution','orMetaheuristic']) {
        Array.from($(id).options).forEach(option=>{const key=option.value;option.value=key;option.textContent=strategyNames[key] || key;});
      }
      for(const id of ['orParallelFirstStrategies','orParallelMetaheuristics']) {
        const original=$(id),box=document.createElement('div');box.className='check-grid';original.hidden=true;
        const keys=id === 'orParallelFirstStrategies' ? ['AUTOMATIC','PATH_CHEAPEST_ARC','SAVINGS','SWEEP','CHRISTOFIDES','PARALLEL_CHEAPEST_INSERTION'] : ['AUTOMATIC','GUIDED_LOCAL_SEARCH','SIMULATED_ANNEALING','TABU_SEARCH'];
        keys.forEach(key=>{const label=document.createElement('label'),check=document.createElement('input');label.className='check-line';check.type='checkbox';check.dataset.strategyTarget=id;check.value=key;label.append(check,document.createTextNode(strategyNames[key]));box.append(label);
          check.addEventListener('change',()=>{original.value=Array.from(box.querySelectorAll('input:checked')).map(el=>el.value).join('\n');});});
        original.after(box);
      }
      ['inSource','inDateMode'].forEach(id=>$(id).addEventListener('change',updateInputFields));
      updateProfileSurface();
    }
    function syncStrategyChoices() {
      document.querySelectorAll('[data-strategy-target]').forEach(el=>{el.checked=stringList(value(el.dataset.strategyTarget)).includes(el.value);});
    }
    """
