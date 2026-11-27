"""Фактические проверки практик 9–10. Секреты и JWT в протокол не выводятся."""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import httpx

ROOT=Path(__file__).resolve().parent
RESULTS=ROOT/'test-results'
RESULTS.mkdir(exist_ok=True)
NAMES=['users','content','interaction','community','messenger','moderation']
ISSUER='http://localhost:8080/realms/social-network'
CLIENT='social-api'
SECRET='social-api-local-demo-secret'
API=httpx.Client(timeout=10,trust_env=False)
evidence=[]

def record(name,condition,details):
    entry={'check':name,'passed':bool(condition),'details':details}
    evidence.append(entry)
    print(('PASS' if condition else 'FAIL')+' | '+name+' | '+str(details),flush=True)
    (RESULTS/'practices9-10.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    if not condition:raise AssertionError(name)

def token(user,short=False):
    response=API.post(ISSUER+'/protocol/openid-connect/token',data={
        'grant_type':'password','scope':'openid','client_id':'social-short-lived' if short else CLIENT,
        'client_secret':'short-lived-local-demo-secret' if short else SECRET,
        'username':user,'password':'Demo-pass-2026!'})
    response.raise_for_status()
    return response.json()

def claims(jwt):
    body=jwt.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(body+'='*((-len(body))%4)))

def call(service,method,path,jwt=None,**kwargs):
    headers=kwargs.pop('headers',{})
    if jwt:headers['Authorization']='Bearer '+jwt
    return API.request(method,f'http://localhost:{8001+NAMES.index(service)}'+path,headers=headers,**kwargs)

def query(expression):
    response=API.get('http://localhost:9090/api/v1/query',params={'query':expression})
    response.raise_for_status();return response.json()['data']['result']

def compose(*args):
    subprocess.run(['docker','compose','-f',str(ROOT/'docker-compose.yml'),*args],check=True,capture_output=True)

def wait_ready(url,seconds=90):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        try:
            if API.get(url).status_code==200:return
        except httpx.RequestError:pass
        time.sleep(2)
    raise RuntimeError('Not ready: '+url)

def main(outages=False):
    discovery=API.get(ISSUER+'/.well-known/openid-configuration').json()
    record('OIDC discovery',discovery.get('issuer')==ISSUER,{'issuer':discovery.get('issuer')})
    tokens={name:token(name)['access_token'] for name in ['student','moderator','viewer','other']}
    subjects={name:claims(value)['sub'] for name,value in tokens.items()}
    header=json.loads(base64.urlsafe_b64decode(tokens['student'].split('.')[0]+'=='))
    record('Keycloak JWT',header['alg']=='RS256',{'alg':header['alg'],'subject':subjects['student'],'roles':claims(tokens['student'])['realm_access']['roles']})
    for name in ['student','moderator','other']:
        response=call('users','POST','/api/v1/users',tokens[name],json={'email':name+'@miar.local','display_name':name})
        record('Profile '+name,response.status_code in (201,409),{'status':response.status_code})
    requests=[('users','POST','/api/v1/users',{}),('content','POST','/api/v1/posts',{}),
        ('interaction','PUT','/api/v1/posts/'+str(uuid4())+'/likes/me',None),
        ('community','POST','/api/v1/communities',{}),('messenger','POST','/api/v1/dialogs',{}),
        ('moderation','GET','/api/v1/reports',None)]
    for name,method,path,body in requests:
        response=call(name,method,path,json=body)
        record('No JWT '+name,response.status_code==401,{'status':response.status_code})
    for label,value in [('invalid','not.a.jwt'),('tampered',tokens['student'][:-12]+'aBcD0123wXyZ')]:
        response=call('content','POST','/api/v1/posts',value,json={'author_id':subjects['student'],'text':'A'})
        record(label+' JWT',response.status_code==401,{'status':response.status_code})
    response=call('content','POST','/api/v1/posts',tokens['viewer'],json={'author_id':subjects['viewer'],'text':'A'})
    record('Missing user role',response.status_code==403,{'status':response.status_code})
    student=token('student')['access_token']
    rid='miar-demo-'+uuid4().hex[:12]
    response=call('content','POST','/api/v1/posts',student,headers={'X-Request-ID':rid},json={'author_id':subjects['student'],'text':'Практики 9–10: защищенная публикация'})
    record('Protected post creation',response.status_code==201,{'status':response.status_code,'request_id':response.headers.get('x-request-id')})
    post=response.json()['id']
    response=call('content','POST','/api/v1/posts',student,json={'author_id':subjects['other'],'text':'Spoof'})
    record('Author spoof rejected',response.status_code==403,{'status':response.status_code})
    response=call('interaction','POST',f'/api/v1/posts/{post}/comments',student,headers={'X-User-ID':subjects['other']},json={'text':'Проверка JWT subject'})
    record('Header identity ignored',response.status_code==201 and response.json().get('author_id')==subjects['student'],{'status':response.status_code,'author_id':response.json().get('author_id')})
    response=call('content','DELETE',f'/api/v1/posts/{post}',tokens['other'])
    record('Other author delete denied',response.status_code==403,{'status':response.status_code})
    response=call('community','POST','/api/v1/communities',student,json={'name':'MIAR security community'})
    record('JWT community owner',response.status_code==201 and response.json().get('owner_id')==subjects['student'],{'status':response.status_code})
    response=call('messenger','POST','/api/v1/dialogs',student,json={'participant_ids':[subjects['student'],subjects['other']]})
    record('Protected dialog creation',response.status_code==201,{'status':response.status_code})
    dialog=response.json()['id']
    response=call('messenger','POST',f'/api/v1/dialogs/{dialog}/messages',student,json={'text':'Защищенное сообщение'})
    record('Protected message',response.status_code==201,{'status':response.status_code})
    response=call('messenger','GET',f'/api/v1/dialogs/{dialog}/messages',tokens['moderator'])
    record('Dialog outsider denied',response.status_code==403,{'status':response.status_code})
    response=call('moderation','POST','/api/v1/reports',student,json={'target_type':'post','target_id':post,'reason':'Тестовая жалоба'})
    record('Protected report creation',response.status_code==201,{'status':response.status_code})
    report=response.json()['id']
    response=call('moderation','GET','/api/v1/reports',student,headers={'X-Moderator-Key':'social-demo-moderator'})
    record('Legacy moderator header rejected',response.status_code==403,{'status':response.status_code})
    response=call('moderation','GET','/api/v1/reports',tokens['moderator'])
    record('Moderator list allowed',response.status_code==200,{'status':response.status_code})
    response=call('moderation','PATCH',f'/api/v1/reports/{report}',tokens['moderator'],json={'status':'accepted','decision':'Проверено модератором'})
    record('Moderator decision allowed',response.status_code==200,{'status':response.status_code})
    short=token('student',short=True)
    record('Short-lived token issued',short['expires_in']<=10,{'expires_in':short['expires_in']})
    time.sleep(short['expires_in']+1)
    response=call('community','POST','/api/v1/communities',short['access_token'],json={'name':'Expired'})
    record('Expired signed JWT',response.status_code==401,{'status':response.status_code})
    if outages:
        compose('stop','keycloak')
        try:
            response=call('community','POST','/api/v1/communities',student,json={'name':'Fail closed'})
            record('Keycloak outage fails closed',response.status_code==503,{'status':response.status_code})
        finally:
            compose('start','keycloak');wait_ready(ISSUER+'/.well-known/openid-configuration')
        compose('stop','community-service')
        try:
            time.sleep(23)
            values=query('up{job="social-api",service="community"}')
            record('Prometheus detects stopped API',bool(values) and values[0]['value'][1]=='0',values)
            alerts=query('ALERTS{alertname="SocialTargetDown",alertstate="firing"}')
            record('Target down alert fires',any('community-service' in v['metric'].get('instance','') for v in alerts),alerts)
        finally:
            compose('start','community-service');wait_ready('http://localhost:8004/health')
    # Wait for scrapes and Docker log discovery without fabricating results.
    time.sleep(12)
    apis=query('up{job="social-api"}')
    record('Six API targets up',len(apis)==6 and all(v['value'][1]=='1' for v in apis),apis)
    pg=query('pg_up');mongo=query('mongodb_up')
    record('Four PostgreSQL databases up',len(pg)==4 and all(v['value'][1]=='1' for v in pg),pg)
    record('Two MongoDB databases up',len(mongo)==2 and all(v['value'][1]=='1' for v in mongo),mongo)
    metrics=query('sum by(service,status) (social_http_requests_total)')
    record('HTTP counters include denials',any(v['metric'].get('status')=='401' for v in metrics),{'series':len(metrics)})
    logs=API.get('http://localhost:3100/loki/api/v1/query_range',params={'query':'{job="miar-docker"}','limit':5000,'since':'30m'}).json()
    streams=logs.get('data',{}).get('result',[])
    api_names={s['stream'].get('service') for s in streams if s['stream'].get('kind')=='api'}
    db_names={s['stream'].get('service') for s in streams if s['stream'].get('kind')=='database'}
    record('Logs from six APIs',all(n+'-service' in api_names for n in NAMES),sorted(api_names))
    record('Logs from six databases',all(n+'-db' in db_names for n in NAMES),sorted(db_names))
    correlation=API.get('http://localhost:3100/loki/api/v1/query_range',params={'query':f'{{kind="api"}} |= "{rid}"','limit':50,'since':'30m'}).json()['data']['result']
    correlated={s['stream']['service'] for s in correlation}
    record('Request ID links Content and Users',{'content-service','users-service'}<=correlated,{'request_id':rid,'services':sorted(correlated)})
    (RESULTS/'correlation.json').write_text(json.dumps(correlation,ensure_ascii=False,indent=2),encoding='utf-8')
    wait_ready('http://localhost:3000/api/health',seconds=180)
    record('Grafana dashboard available',API.get('http://localhost:3000/api/dashboards/uid/miar-social').status_code==200,{'dashboard':'MIAR Social Network'})
    print(f'RESULT: {len(evidence)} passed, 0 failed',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--outages',action='store_true')
    main(parser.parse_args().outages)
