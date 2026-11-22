"""Компонентные отказы UsersService во всех четырех новых сервисах."""
from importlib import import_module
from unittest.mock import patch
from uuid import uuid4
import httpx
import pytest
from fastapi.testclient import TestClient

CASES=[('interaction','/api/v1/posts/{id}/comments',{'text':'Test'}),
       ('community','/api/v1/communities',{'name':'Test'}),
       ('messenger','/api/v1/dialogs',{'participant_ids':[str(uuid4()),str(uuid4())]}),
       ('moderation','/api/v1/reports',{'target_type':'post','target_id':str(uuid4()),'reason':'Test'})]

@pytest.mark.parametrize('service,path,payload',CASES)
@pytest.mark.parametrize('failure',['timeout','server_error'])
def test_users_dependency_failure(service,path,payload,failure):
    app=import_module(service+'_service.app').app
    kwargs=({'side_effect':httpx.ConnectError('offline')} if failure=='timeout'
            else {'return_value':httpx.Response(500)})
    with patch('common.api.httpx.get',**kwargs):
        result=TestClient(app).post(path.format(id=uuid4()),headers={'X-User-ID':str(uuid4())},json=payload)
    assert result.status_code==503

def test_outbox_retains_event_when_broker_is_offline(http,report,sql):
    # Exercise the same business function against the real moderation database;
    # only the AMQP connection is replaced with an outage.
    import pika
    from moderation_service.app import decide,DecisionInput,dispatch_pending
    from sqlalchemy import text
    with patch('moderation_service.app.pika.BlockingConnection',side_effect=pika.exceptions.AMQPConnectionError()):
        result=decide(__import__('uuid').UUID(report['id']),DecisionInput(status='rejected',decision='Проверено'))
    assert result['status']=='rejected'
    with sql['moderation'].connect() as db:
        assert db.execute(text('SELECT sent FROM outbox WHERE report_id=:id'),{'id':report['id']}).scalar_one() is False
    assert dispatch_pending()>=1
    with sql['moderation'].connect() as db:
        assert db.execute(text('SELECT sent FROM outbox WHERE report_id=:id'),{'id':report['id']}).scalar_one() is True
