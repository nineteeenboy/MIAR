"""Реальные API/БД; очистка только объектов, созданных текущим тестом."""
import os
from uuid import uuid4
import httpx
import pytest
from pymongo import MongoClient
from sqlalchemy import create_engine, text

URLS = {name: os.getenv(f'TEST_{name.upper()}_URL', f'http://127.0.0.1:{port}')
        for name,port in [('users',8001),('content',8002),('interaction',8003),
                          ('community',8004),('messenger',8005),('moderation',8006)]}
DB_URLS = {name: os.getenv(f'TEST_{name.upper()}_DB_URL',
    f'postgresql+psycopg://social:social@127.0.0.1:{port}/{database}')
    for name,port,database in [('users',55432,'users'),('interaction',55433,'interactions'),
                               ('community',55434,'communities'),('moderation',55435,'moderation')]}
MODERATOR = {'X-Moderator-Key': os.getenv('TEST_MODERATOR_KEY','social-demo-moderator')}

def headers(user):
    return {'X-User-ID': user['id']}

@pytest.fixture(scope='session')
def http():
    with httpx.Client(trust_env=False, timeout=10.0) as client:
        yield client

@pytest.fixture
def sql():
    engines = {name:create_engine(url) for name,url in DB_URLS.items()}
    yield engines
    for engine in engines.values():
        engine.dispose()

@pytest.fixture
def make_user(http):
    ids=[]
    def make():
        result=http.post(URLS['users']+'/api/v1/users',json={
            'email':f'{uuid4()}@example.org','display_name':'Тестовый пользователь'})
        assert result.status_code==201,result.text
        user=result.json();ids.append(user['id']);return user
    yield make
    for user_id in ids:
        http.delete(URLS['users']+f'/api/v1/users/{user_id}')

@pytest.fixture
def person(make_user):
    return make_user()

@pytest.fixture
def post(http,person,sql):
    result=http.post(URLS['content']+'/api/v1/posts',json={'author_id':person['id'],'text':'Публикация для теста'})
    assert result.status_code==201,result.text
    data=result.json();yield data
    with sql['interaction'].begin() as db:
        for table in ('likes','comments'):
            db.execute(text(f'DELETE FROM {table} WHERE post_id=:id'),{'id':data['id']})
    http.delete(URLS['content']+f"/api/v1/posts/{data['id']}")

@pytest.fixture
def community(http,person,sql):
    response=http.post(URLS['community']+'/api/v1/communities',headers=headers(person),
                       json={'name':'Сообщество для теста','description':'Обсуждение публикаций'})
    assert response.status_code==201,response.text
    data=response.json();yield data
    with sql['community'].begin() as db:
        db.execute(text('DELETE FROM communities WHERE id=:id'),{'id':data['id']})

@pytest.fixture
def dialog(http,person,make_user):
    other=make_user()
    response=http.post(URLS['messenger']+'/api/v1/dialogs',headers=headers(person),
                       json={'participant_ids':[person['id'],other['id']]})
    assert response.status_code==201,response.text
    data=response.json();yield data
    with MongoClient(os.getenv('TEST_MESSENGER_DB_URL','mongodb://127.0.0.1:57018/')) as client:
        client.social_messenger.messages.delete_many({'dialog_id':data['id']})
        client.social_messenger.dialogs.delete_one({'_id':data['id']})

@pytest.fixture
def make_report(http,person,post,sql):
    ids=[]
    def make(target_type='post',target_id=None):
        response=http.post(URLS['moderation']+'/api/v1/reports',headers=headers(person),
            json={'target_type':target_type,'target_id':target_id or post['id'],'reason':'Проверка жалобы'})
        assert response.status_code==201,response.text
        data=response.json();ids.append(data['id']);return data
    yield make
    with sql['moderation'].begin() as db:
        for report_id in ids:
            db.execute(text('DELETE FROM reports WHERE id=:id'),{'id':report_id})

@pytest.fixture
def report(make_report):
    return make_report()
