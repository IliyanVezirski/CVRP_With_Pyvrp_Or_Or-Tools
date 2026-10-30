// Headless integration test of generated HTML with fully mocked HTTP.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict'),cp=require('node:child_process');
const {chromium}=require(process.env.BIZANT_PLAYWRIGHT);
const root=path.resolve(__dirname,'..');
const fixture=JSON.parse(cp.execFileSync(path.join(root,'.venv/Scripts/python.exe'),[path.join(__dirname,'operations_fixture.py')],{encoding:'utf8',env:{...process.env,PYTHONIOENCODING:'utf-8'},maxBuffer:5e6}));
const output=process.env.BIZANT_UI_OUTPUT||path.resolve(root,'../build_candidates/fleet_operations_20261030/ui');fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'msedge'});
 const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'bg-BG',timezoneId:'Europe/Sofia'});
 const page=await context.newPage(),errors=[],requests=[];
 page.on('pageerror',e=>errors.push(String(e)));
 let ops=structuredClone(fixture.config.operations_web);
 await context.route('**/*',async route=>{
  const url=new URL(route.request().url());let body;
  if(url.pathname==='/hell')return route.fulfill({contentType:'text/html',body:fixture.page});
  if(url.pathname==='/hell/login')return route.fulfill({contentType:'text/html',body:fixture.login});
  if(url.pathname.endsWith('/operations')){
   if(route.request().method()==='POST'){const req=route.request().postDataJSON();ops.settings[req.section]=req.value;body={status:'saved',operations:ops};requests.push(req)}else body=ops;
  }else if(url.pathname.endsWith('/config'))body=fixture.config;
  else if(url.pathname.endsWith('/run')){requests.push({run:route.request().postDataJSON()});body={run:{status:'running',running:true}}}
  else if(url.pathname.endsWith('/logs'))body={logs:{},run_status:{running:false,status:'idle'}};
  else if(url.pathname.endsWith('/status'))body={run_status:{running:false,status:'idle'}};
  else body={};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.goto('http://bizant.test/hell');await page.locator('.vehicle-row').first().waitFor();
 await page.screenshot({path:path.join(output,'workspace.png'),fullPage:true});
 assert.equal(await page.locator('#planButton').isVisible(),true);assert.equal(await page.locator('#globalPlanButton').isVisible(),true);
 page.once('dialog',d=>d.dismiss());await page.locator('#globalPlanButton').click();assert.equal(requests.filter(x=>x.run).length,0);
 const overlay=await page.evaluate(()=>{
   const data=structuredClone(state),op=data.operations_web;
   op.vehicles[0].values.count=3;op.vehicles[0].values.service_time_minutes=1;
   op.effective.notes[op.vehicles[0].key]='Тестов график';renderVehicleState(data);
   const row=document.querySelector('.vehicle-row');
   const shown=Number(row.querySelector('[data-field="count"]').value);
   const saved=collectVehicles()[0].count;
   Array.from(row.querySelectorAll('button')).find(b=>b.textContent==='Основни стойности').click();
   return {shown,saved,dirty:vehicleSettingsDirty};
 });
 assert.deepEqual(overlay,{shown:3,saved:5,dirty:false});
 await page.evaluate(()=>refreshOperationsFleet());
 await page.getByRole('button',{name:'График',exact:false}).first().click();await page.locator('#operationsDialog').waitFor();
 await page.getByRole('button',{name:'+ Седмичен график',exact:true}).click();
 await page.getByLabel('Маршрут · 5 бр.',{exact:true}).check();
 const service=page.locator('.ops-service select').first();await service.selectOption('dynamic');
 await page.getByRole('button',{name:'+ Добави зависимост',exact:true}).click();
 await page.locator('.ops-rule .ops-service select').selectOption('formula');
 await page.screenshot({path:path.join(output,'calendar.png'),fullPage:true});
 await page.locator('#opsSave').click();await page.waitForFunction(()=>!document.getElementById('operationsDialog').open);
 const saved=requests.find(r=>r.section==='calendar');assert.equal(saved.value.entries[0].vehicles.route.service.mode,'dynamic');
 await page.locator('.vehicle-extra-toggle').first().click();await page.getByRole('button',{name:'Гъвкаво обслужване',exact:true}).first().click();
 await page.getByLabel('Включено за този бус').check();await page.getByRole('button',{name:'+ Добави зависимост',exact:true}).click();
 await page.locator('.ops-rule .ops-service select').selectOption('formula');
 await page.screenshot({path:path.join(output,'flexible.png'),fullPage:true});
 await page.locator('#opsSave').click();await page.waitForFunction(()=>!document.getElementById('operationsDialog').open);
 assert(requests.some(r=>r.section==='flexible'&&r.value.route.enabled));
 await page.getByRole('button',{name:'Натоварване по депа',exact:true}).click();
 await page.screenshot({path:path.join(output,'load.png'),fullPage:true});await page.getByRole('button',{name:'Затвори',exact:true}).click();
 await page.getByRole('button',{name:'Допълнителни настройки',exact:true}).first().click();await page.getByText('Депа и локации',{exact:true}).click();await page.getByText('Трафик',{exact:true}).click();
 await page.screenshot({path:path.join(output,'advanced.png'),fullPage:true});await page.getByRole('button',{name:'Затвори',exact:true}).click();
 await page.setViewportSize({width:390,height:844});await page.getByRole('button',{name:'График',exact:false}).first().click();
 await page.screenshot({path:path.join(output,'mobile.png'),fullPage:true});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'No mobile horizontal overflow');
 await page.getByRole('button',{name:'Затвори',exact:true}).click();page.once('dialog',d=>d.accept());await page.locator('#globalPlanButton').click();
 await page.waitForTimeout(100);const official=requests.find(x=>x.run);assert.equal(official.run.profile,'global');assert.deepEqual(official.run.run_settings,{});
 assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,screenshots:output,posts:requests.length}));await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
