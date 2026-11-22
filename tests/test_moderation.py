"""ModerationService: права, решения, PostgreSQL/outbox и RabbitMQ."""
import json
import os
from uuid import uuid4
import pika
import pytest
from sqlalchemy import text
from conftest import URLS,headers,MODERATOR

def test_report_read_permissions(http,person,report,make_user):
    url=URLS['moderation']+f"/api/v1/reports/{report['id']}"
    assert http.get(url,headers=headers(person)).json()['status']=='open'
    assert http.get(url,headers=headers(make_user())).status_code==403
    assert http.get(url).status_code==403
    assert http.get(url,headers=MODERATOR).status_code==200

def test_decision_idempotent_outbox_rabbitmq(http,report,sql):
    url=URLS['moderation']+f"/api/v1/reports/{report['id']}"
    data={'status':'accepted','decision':'Нарушение подтверждено'}
    result=http.patch(url,headers=MODERATOR,json=data)
    assert result.status_code==200,result.text
    assert result.json()['status']=='accepted'
    assert http.patch(url,headers=MODERATOR,json=data).status_code==200
    assert http.patch(url,headers=MODERATOR,json={'status':'rejected','decision':'Другое'}).status_code==409
    with sql['moderation'].connect() as db:
        rows=db.execute(text('SELECT * FROM outbox WHERE report_id=:id'),{'id':report['id']}).mappings().all()
        assert len(rows)==1 and rows[0]['sent'] is True
        event=rows[0]['payload'];assert event['event']=='moderation.decision.created'
        assert event['target_id']==report['target_id']
    with pika.BlockingConnection(pika.URLParameters(os.getenv('AMQP_URL','amqp://social:social@127.0.0.1:5672/%2F'))) as conn:
        ch=conn.channel();count=ch.queue_declare('social.moderation.decisions',durable=True).method.message_count
        found=False
        # Unrelated messages are left unacked and requeued when this channel closes.
        for _ in range(count):
            method,properties,body=ch.basic_get('social.moderation.decisions',auto_ack=False)
            if method and json.loads(body)['event_id']==event['event_id']:
                assert properties.delivery_mode==2
                ch.basic_ack(method.delivery_tag);found=True;break
        assert found,'Confirmed decision event not present in RabbitMQ'

def test_list_filter_and_permission(http,report):
    url=URLS['moderation']+'/api/v1/reports'
    assert http.get(url).status_code==403
    result=http.get(url,headers=MODERATOR,params={'status':'open','limit':100})
    assert result.status_code==200,result.text
    assert report['id'] in [r['id'] for r in result.json()['items']]
    assert http.get(url,headers=MODERATOR,params={'status':'bad'}).status_code==422

def test_nonmoderator_cannot_decide(http,person,report):
    url=URLS['moderation']+f"/api/v1/reports/{report['id']}"
    data={'status':'accepted','decision':'Попытка'}
    assert http.patch(url,headers=headers(person),json=data).status_code==403
    assert http.patch(url,headers={'X-Moderator-Key':'wrong'},json=data).status_code==403

@pytest.mark.parametrize('reason',['',' ','x'*2001])
def test_reason_validation(http,person,post,reason):
    assert http.post(URLS['moderation']+'/api/v1/reports',headers=headers(person),
        json={'target_type':'post','target_id':post['id'],'reason':reason}).status_code==422

def test_unknown_target_and_report(http,person):
    url=URLS['moderation']+'/api/v1/reports'
    assert http.post(url,headers=headers(person),json={'target_type':'post','target_id':str(uuid4()),'reason':'Test'}).status_code==404
    assert http.get(url+f'/{uuid4()}',headers=MODERATOR).status_code==404

def test_user_and_comment_targets(http,person,post,make_report,sql):
    user_report=make_report('user',person['id'])
    comment=http.post(URLS['interaction']+f"/api/v1/posts/{post['id']}/comments",
                      headers=headers(person),json={'text':'Объект жалобы'}).json()
    comment_report=make_report('comment',comment['id'])
    with sql['moderation'].connect() as db:
        for report in [user_report,comment_report]:
            assert str(db.execute(text('SELECT target_id FROM reports WHERE id=:id'),
                                 {'id':report['id']}).scalar_one())==report['target_id']

@pytest.mark.parametrize('data',[{'status':'open','decision':'Test'},
                                {'status':'accepted','decision':' '},
                                {'status':'accepted','decision':'x'*2001}])
def test_decision_validation(http,report,data):
    assert http.patch(URLS['moderation']+f"/api/v1/reports/{report['id']}",headers=MODERATOR,json=data).status_code==422
