"""Проверки introspection, ролей, отказа IdP и защиты заголовков."""
import time
import asyncio
import atexit
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI,Request,HTTPException
from common import security
from common.observability import setup

SUB='00000000-0000-4000-8000-000000000001'
# Windows creates a local event-loop wakeup socket. Construct the loop before
# network guards; HTTP itself uses ASGITransport and never opens a connection.
LOOP=asyncio.new_event_loop()
atexit.register(LOOP.close)

class InMemoryClient:
    def __init__(self,app):self.app=app
    def request(self,method,path,**kwargs):
        async def call():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://unit.local') as api:
                return await api.request(method,path,**kwargs)
        return LOOP.run_until_complete(call())
    def post(self,path,**kwargs):return self.request('POST',path,**kwargs)
    def get(self,path,**kwargs):return self.request('GET',path,**kwargs)

@pytest.fixture
def claims(monkeypatch):
    monkeypatch.setenv('OIDC_CLIENT_ID','social-api')
    monkeypatch.setenv('OIDC_CLIENT_SECRET','test-secret')
    return {'active':True,'iss':'http://localhost:8080/realms/social-network',
            'aud':['social-api'],'exp':int(time.time())+120,'sub':SUB,
            'token_type':'Bearer','realm_access':{'roles':['user']}}

@pytest.mark.parametrize('change,status',[
    ({'active':False},401),({'iss':'http://wrong'},401),({'aud':['other']},401),
    ({'exp':0},401),({'sub':'broken'},401),({'token_type':'Refresh'},401),
])
def test_invalid_introspection_claims(claims,monkeypatch,change,status):
    claims.update(change)
    monkeypatch.setattr(security.httpx,'post',Mock(return_value=Mock(status_code=200,json=lambda:claims)))
    with pytest.raises(HTTPException) as error:security.introspect('signed.token.value')
    assert error.value.status_code==status

def test_valid_identity(claims,monkeypatch):
    post=Mock(return_value=Mock(status_code=200,json=lambda:claims))
    monkeypatch.setattr(security.httpx,'post',post)
    assert security.introspect('signed.token.value')==security.Identity(SUB,frozenset({'user'}))
    assert post.call_args.kwargs['auth']==('social-api','test-secret')
    assert post.call_args.kwargs['data']['token']=='signed.token.value'

@pytest.mark.parametrize('failure',['offline','server','json'])
def test_identity_provider_fails_closed(claims,monkeypatch,failure):
    response=Mock(status_code=500 if failure=='server' else 200)
    response.json.side_effect=ValueError('bad')
    post=Mock(side_effect=httpx.ConnectError('offline')) if failure=='offline' else Mock(return_value=response)
    monkeypatch.setattr(security.httpx,'post',post)
    with pytest.raises(HTTPException) as error:security.introspect('token')
    assert error.value.status_code==503

def client(service='community'):
    app=FastAPI()
    @app.post('/api/v1/communities')
    def create(request:Request):return {'caller':request.headers.get('X-User-ID')}
    @app.get('/api/v1/reports')
    def reports():return {'items':[]}
    setup(app,service)
    return InMemoryClient(app)

@pytest.mark.parametrize('header',[{}, {'Authorization':'Basic abc'}, {'Authorization':'Bearer '}])
def test_missing_bearer_is_401(header):
    response=client().post('/api/v1/communities',headers=header)
    assert response.status_code==401
    assert response.headers['www-authenticate']=='Bearer'

def test_wrong_role_is_403(monkeypatch):
    monkeypatch.setattr(security,'introspect',lambda t:security.Identity(SUB,frozenset({'viewer'})))
    assert client().post('/api/v1/communities',headers={'Authorization':'Bearer token'}).status_code==403

def test_header_spoof_is_replaced(monkeypatch):
    monkeypatch.setattr(security,'introspect',lambda t:security.Identity(SUB,frozenset({'user'})))
    response=client().post('/api/v1/communities',headers={'Authorization':'Bearer token','X-User-ID':'spoof'})
    assert response.status_code==200 and response.json()['caller']==SUB
    assert security.identity.get() is None

def test_moderator_role_is_required(monkeypatch):
    monkeypatch.setattr(security,'introspect',lambda t:security.Identity(SUB,frozenset({'user'})))
    assert client('moderation').get('/api/v1/reports',headers={'Authorization':'Bearer token','X-Moderator-Key':'social-demo-moderator'}).status_code==403

def test_metrics_count_denied_requests():
    api=client();response=api.post('/api/v1/communities',headers={'X-Request-ID':'test-correlation'})
    assert response.status_code==401 and response.headers['x-request-id']=='test-correlation'
    metrics=api.get('/metrics').text
    assert 'status="401"' in metrics and 'social_http_request_duration_seconds_bucket' in metrics
