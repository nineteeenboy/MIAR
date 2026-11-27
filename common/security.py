"""Практика 9: backend introspection в Keycloak и проверка ролей."""
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

import httpx
from fastapi import HTTPException
from starlette.responses import JSONResponse

@dataclass(frozen=True)
class Identity:
    subject: str
    roles: frozenset[str]

identity: ContextVar[Identity | None] = ContextVar('identity', default=None)

def introspect(token: str) -> Identity:
    issuer=os.getenv('OIDC_ISSUER','http://localhost:8080/realms/social-network')
    endpoint=os.getenv('OIDC_INTROSPECTION_URL',issuer+'/protocol/openid-connect/token/introspect')
    try:
        response=httpx.post(endpoint, data={'token':token,'token_type_hint':'access_token'},
            auth=(os.environ['OIDC_CLIENT_ID'],os.environ['OIDC_CLIENT_SECRET']),
            timeout=3.0,trust_env=False)
        if response.status_code != 200:
            raise HTTPException(503,'Identity provider unavailable')
        claims=response.json()
    except (httpx.RequestError,ValueError,KeyError):
        raise HTTPException(503,'Identity provider unavailable')
    audience=claims.get('aud',[])
    if isinstance(audience,str):audience=[audience]
    if (claims.get('active') is not True or claims.get('iss') != issuer
        or os.getenv('OIDC_AUDIENCE','social-api') not in audience
        or not isinstance(claims.get('exp'),(int,float)) or claims['exp']<=time.time()
        or claims.get('token_type','').lower() != 'bearer'):
        raise HTTPException(401,'Invalid or expired access token')
    try:subject=str(UUID(claims['sub']))
    except (KeyError,ValueError,TypeError):raise HTTPException(401,'Invalid subject')
    return Identity(subject,frozenset(claims.get('realm_access',{}).get('roles',[])))

def required_role(service,path,method):
    if not path.startswith('/api/v1/') and not path.startswith('/internal/'):
        return None
    if service=='moderation' and (path=='/api/v1/reports' and method=='GET' or method=='PATCH'):
        return 'moderator'
    if service=='messenger' or service=='moderation' or method in ('POST','PUT','PATCH','DELETE'):
        return 'user'
    return None

class SecurityMiddleware:
    def __init__(self,app,service):self.app,self.service=app,service

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        role=required_role(self.service,scope['path'],scope['method'])
        if role is None:return await self.app(scope,receive,send)
        headers=dict(scope['headers']);auth=headers.get(b'authorization',b'').decode('latin1')
        try:
            scheme,_,token=auth.partition(' ')
            if scheme.lower()!='bearer' or not token or len(token)>16384:
                raise HTTPException(401,'Bearer access token required')
            from starlette.concurrency import run_in_threadpool
            person=await run_in_threadpool(introspect,token)
            if role not in person.roles:raise HTTPException(403,f'Role {role} required')
            # Header identity is replaced, never trusted from the caller.
            scope['headers']=[(k,v) for k,v in scope['headers'] if k not in (b'x-user-id',b'x-moderator-key')]
            scope['headers'].append((b'x-user-id',person.subject.encode()))
            if self.service=='users' and scope['method'] in ('PATCH','DELETE'):
                if scope['path'].rsplit('/',1)[-1]!=person.subject and 'moderator' not in person.roles:
                    raise HTTPException(403,'Profile belongs to another user')
        except HTTPException as error:
            extra={'WWW-Authenticate':'Bearer'} if error.status_code==401 else {}
            return await JSONResponse({'detail':error.detail},error.status_code,headers=extra)(scope,receive,send)
        marker=identity.set(person)
        try:await self.app(scope,receive,send)
        finally:identity.reset(marker)
