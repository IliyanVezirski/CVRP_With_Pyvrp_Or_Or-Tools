"""HTTP integration against the synthetic demo/second profiles only."""
import json
import sys
import requests
import time

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:18095'
def login(slug,password='demo-pass'):
    session=requests.Session()
    response=session.post(base+'/'+slug+'/api/login',json={'username':'tester','password':password},headers={'Origin':base},timeout=20)
    assert response.status_code==200,(response.status_code,response.text)
    session.headers['X-CSRF-Token']=session.cookies.get('CVRP_WEB_CSRF')
    session.headers['Origin']=base
    return session
a=login('demo');old=login('demo');b=login('second')
assert a.get(base+'/demo/api/config').status_code==200
assert requests.get(base+'/second/api/config',headers={'Cookie':'CVRP_WEB_SESSION='+a.cookies.get('CVRP_WEB_SESSION')}).status_code==401
data=a.get(base+'/demo/api/config').json()
assert data['profile_identity']['trial_mode']
assert data['profile_identity']['name']=='Демонстрация'
seconds=int(time.time())%300+120
saved=a.post(base+'/demo/api/run-defaults',json={'expected_revision':data['revision'],'run_settings':{'cvrp':{'time_limit_seconds':seconds}}})
assert saved.status_code==200,saved.text
stale=a.post(base+'/demo/api/run-defaults',json={'expected_revision':data['revision'],'run_settings':{'cvrp':{'time_limit_seconds':124}}})
assert stale.status_code==400,stale.text
long='ю :; '*6000
assert a.post(base+'/demo/api/change-password',json={'current_password':'demo-pass','new_password':long,'confirmation':long}).status_code==200
assert old.get(base+'/demo/api/config').status_code==401
assert b.get(base+'/second/api/config').status_code==200
new=login('demo',long)
assert new.post(base+'/demo/api/change-password',json={'current_password':long,'new_password':'demo-pass','confirmation':'demo-pass'}).status_code==200
a=login('demo')
data=a.get(base+'/demo/api/config').json()
ops=data['operations_web'];key=ops['vehicles'][0]['key']
policies={key:{'enabled':True,'rules':[{'min':0,'max':None,'service':{'mode':'fixed','seconds':123+seconds}}]}}
fleet_body={'vehicles':data['vehicles'],'dynamic_policies':policies,
            'operations_revision':ops['revision'],'expected_revision':data['revision']}
saved=a.post(base+'/demo/api/vehicles',json=fleet_body)
assert saved.status_code==200,saved.text
data=a.get(base+'/demo/api/config').json()
assert data['operations_web']['settings']['flexible'][key]==policies[key]
assert a.post(base+'/demo/api/vehicles',json=fleet_body).status_code==400
ops=data['operations_web']
calendar={'enabled':True,'entries':[{'id':'date-test','name':'Test date','kind':'date','date':'2026-11-02','weekdays':[],
          'enabled':True,'vehicles':{key:{'enabled':True,'count':1,'max_time_hours':7.5,'capacity':None,'service':{'mode':'dynamic','rules':policies[key]['rules']}}}}]}
saved=a.post(base+'/demo/api/operations',json={'section':'calendar','value':calendar,'revision':ops['revision']})
assert saved.status_code==200,saved.text
data=a.get(base+'/demo/api/config').json()
assert data['operations_web']['settings']['calendar']['entries'][0]['vehicles'][key]['max_time_hours']==7.5
print(json.dumps({'ok':True,'profile_isolation':True,'password_change':True,'long_password_characters':len(long),'stale_save_rejected':True}))
