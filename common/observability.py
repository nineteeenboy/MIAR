"""Практика 10: Prometheus, JSON access logs и корреляция запросов."""
import json
import logging
import re
import time
from contextvars import ContextVar
from datetime import datetime,timezone
from uuid import uuid4

from prometheus_client import CollectorRegistry,Counter,Histogram,generate_latest,CONTENT_TYPE_LATEST
from starlette.responses import Response
from starlette.routing import Match
from common.security import SecurityMiddleware

request_id: ContextVar[str] = ContextVar('request_id',default='')
logger=logging.getLogger('social.access')
logger.setLevel(logging.INFO)
if not logger.handlers:
    logger.addHandler(logging.StreamHandler())
logger.propagate=False

class ObservabilityMiddleware:
    def __init__(self,app,service,router,counter,duration):
        self.app,self.service,self.router=app,service,router
        self.counter,self.duration=counter,duration

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['path'] in ('/metrics','/health'):
            return await self.app(scope,receive,send)
        incoming=dict(scope['headers']).get(b'x-request-id',b'').decode('latin1')
        rid=incoming if re.fullmatch(r'[A-Za-z0-9_-]{1,64}',incoming) else str(uuid4())
        marker=request_id.set(rid)
        route=next((r.path for r in self.router.routes if r.matches(scope)[0]==Match.FULL),'unmatched')
        status=500;started=time.perf_counter()
        async def wrapped_send(message):
            nonlocal status
            if message['type']=='http.response.start':
                status=message['status'];message.setdefault('headers',[]).append((b'x-request-id',rid.encode()))
            await send(message)
        try:await self.app(scope,receive,wrapped_send)
        finally:
            elapsed=time.perf_counter()-started
            self.counter.labels(self.service,scope['method'],route,str(status)).inc()
            self.duration.labels(self.service,scope['method'],route).observe(elapsed)
            logger.info(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),
                'level':'ERROR' if status>=500 else 'INFO','service':self.service,
                'method':scope['method'],'route':route,'status':status,
                'duration_ms':round(elapsed*1000,3),'request_id':rid},ensure_ascii=False))
            request_id.reset(marker)

def setup(app,service):
    registry=CollectorRegistry()
    counter=Counter('social_http_requests_total','Completed HTTP requests',
        ['service','method','route','status'],registry=registry)
    duration=Histogram('social_http_request_duration_seconds','HTTP request duration',
        ['service','method','route'],buckets=(.005,.01,.025,.05,.1,.25,.5,1,2,5),registry=registry)
    async def metrics(request):return Response(generate_latest(registry),media_type=CONTENT_TYPE_LATEST)
    async def health(request):return Response('ok')
    app.add_route('/metrics',metrics,include_in_schema=False)
    app.add_route('/health',health,include_in_schema=False)
    app.add_middleware(SecurityMiddleware,service=service)
    app.add_middleware(ObservabilityMiddleware,service=service,router=app.router,counter=counter,duration=duration)
