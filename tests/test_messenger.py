"""MessengerService: участники, порядок, курсор прочтения, MongoDB."""
import os
from uuid import uuid4
import pytest
from pymongo import MongoClient
from conftest import URLS,headers

def test_send_list_read_and_persistence(http,person,dialog):
    url=URLS['messenger']+f"/api/v1/dialogs/{dialog['id']}"
    one=http.post(url+'/messages',headers=headers(person),json={'text':'Первое'})
    two=http.post(url+'/messages',headers=headers(person),json={'text':'Второе'})
    assert one.status_code==two.status_code==201
    a,b=one.json(),two.json();assert b['sequence']>a['sequence']
    listing=http.get(url+'/messages',headers=headers(person),params={'limit':1,'offset':1})
    assert listing.json()['items'][0]['id']==b['id']
    assert http.patch(url+'/read',headers=headers(person),json={'last_read_message_id':b['id']}).json()['last_read_message_id']==b['id']
    assert http.patch(url+'/read',headers=headers(person),json={'last_read_message_id':a['id']}).json()['last_read_message_id']==b['id']
    with MongoClient(os.getenv('TEST_MESSENGER_DB_URL','mongodb://127.0.0.1:57018/')) as client:
        assert client.social_messenger.messages.find_one({'_id':b['id']})['text']=='Второе'
        assert client.social_messenger.dialogs.find_one({'_id':dialog['id']})['read'][person['id']]==b['sequence']

def test_nonparticipant_forbidden(http,dialog,make_user):
    outsider=make_user();url=URLS['messenger']+f"/api/v1/dialogs/{dialog['id']}"
    assert http.get(url+'/messages',headers=headers(outsider)).status_code==403
    assert http.post(url+'/messages',headers=headers(outsider),json={'text':'Secret'}).status_code==403
    assert http.patch(url+'/read',headers=headers(outsider),json={'last_read_message_id':str(uuid4())}).status_code==403

@pytest.mark.parametrize('value',['',' ','x'*10001])
def test_message_validation(http,person,dialog,value):
    assert http.post(URLS['messenger']+f"/api/v1/dialogs/{dialog['id']}/messages",
                     headers=headers(person),json={'text':value}).status_code==422

def test_duplicate_participants(http,person):
    assert http.post(URLS['messenger']+'/api/v1/dialogs',headers=headers(person),
                     json={'participant_ids':[person['id'],person['id']]}).status_code==422

def test_caller_must_participate(http,person,make_user):
    a,b=make_user(),make_user()
    assert http.post(URLS['messenger']+'/api/v1/dialogs',headers=headers(person),
                     json={'participant_ids':[a['id'],b['id']]}).status_code==422

def test_unknown_participant(http,person):
    assert http.post(URLS['messenger']+'/api/v1/dialogs',headers=headers(person),
                     json={'participant_ids':[person['id'],str(uuid4())]}).status_code==404

def test_missing_dialog_and_message(http,person,dialog):
    assert http.get(URLS['messenger']+f'/api/v1/dialogs/{uuid4()}/messages',headers=headers(person)).status_code==404
    assert http.patch(URLS['messenger']+f"/api/v1/dialogs/{dialog['id']}/read",headers=headers(person),
                      json={'last_read_message_id':str(uuid4())}).status_code==404

@pytest.mark.parametrize('params',[{'limit':0},{'offset':-1}])
def test_pagination_invalid(http,person,dialog,params):
    assert http.get(URLS['messenger']+f"/api/v1/dialogs/{dialog['id']}/messages",headers=headers(person),params=params).status_code==422
