// In-memory login UI regression: no real browser, user, or HTTP request.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {parseHTML} = require(path.join(process.env.BIZANT_TEST_NODE_MODULES,'linkedom'));
const {document,window} = parseHTML(JSON.parse(fs.readFileSync(0,'utf8')).page);
const $=id=>document.getElementById(id);
const requests=[],redirects=[];
let succeeds=false,release=null;
const fakeFetch=async(url,options)=>{
  requests.push({url,options});if(release)await release;
  return {ok:succeeds,json:async()=>succeeds?{}:{error:'Невалидни данни за вход.'}};
};
new Function('document','window','fetch',document.querySelector('script').textContent)(
  document,{location:{replace:url=>redirects.push(url)}},fakeFetch);
const submit=()=>$('loginForm').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
(async()=>{
  $('passwordToggle').dispatchEvent(new window.Event('click'));
  assert.equal($('password').type,'text');
  assert.equal($('passwordToggle').getAttribute('aria-pressed'),'true');
  $('passwordToggle').dispatchEvent(new window.Event('click'));
  assert.equal($('password').type,'password');
  $('username').value='dispatcher';$('password').value='  примерна; парола  ';
  submit();await new Promise(setImmediate);
  assert.equal($('loginError').textContent,'Невалидни данни за вход.');
  assert.equal(redirects.length,0);
  assert.equal(JSON.parse(requests[0].options.body).password,'  примерна; парола  ');
  assert.equal(requests[0].options.credentials,'same-origin');
  succeeds=true;let resume;release=new Promise(resolve=>{resume=resolve;});
  submit();submit();assert.equal(requests.length,2,'Double submit must not duplicate login');
  assert.equal($('loginButton').disabled,true);
  resume();await new Promise(setImmediate);
  assert.equal(redirects.length,1);
  assert.equal($('loginError').textContent,'');
  console.log('PASS: password visibility, exact password, errors, submit guard and login redirect');
})().catch(err=>{console.error(err);process.exitCode=1;});
