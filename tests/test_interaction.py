"""InteractionService: HTTP, идемпотентность и настоящая PostgreSQL."""
from uuid import uuid4
import pytest
from sqlalchemy import text
from conftest import URLS,headers

def test_like_idempotent_and_unlike(http,person,post,sql):
    url=URLS['interaction']+f"/api/v1/posts/{post['id']}/likes/me"
    assert http.put(url,headers=headers(person)).status_code==204
    assert http.put(url,headers=headers(person)).status_code==204
    with sql['interaction'].connect() as db:
        assert db.execute(text('SELECT count(*) FROM likes WHERE post_id=:p AND user_id=:u'),
                          {'p':post['id'],'u':person['id']}).scalar_one()==1
    assert http.delete(url,headers=headers(person)).status_code==204
    assert http.delete(url,headers=headers(person)).status_code==204
    with sql['interaction'].connect() as db:
        assert db.execute(text('SELECT count(*) FROM likes WHERE post_id=:p'),{'p':post['id']}).scalar_one()==0

def test_comment_create_list_persistence(http,person,post,sql):
    url=URLS['interaction']+f"/api/v1/posts/{post['id']}/comments"
    response=http.post(url,headers=headers(person),json={'text':'  Комментарий  '})
    assert response.status_code==201
    item=response.json();assert item['author_id']==person['id'];assert item['text']=='Комментарий'
    result=http.get(url,params={'limit':1,'offset':0}).json()
    assert result['items'][0]['id']==item['id']
    assert http.get(url,params={'offset':1}).json()['items']==[]
    with sql['interaction'].connect() as db:
        assert db.execute(text('SELECT text FROM comments WHERE id=:id'),{'id':item['id']}).scalar_one()=='Комментарий'

@pytest.mark.parametrize('value',['',' ','x'*2001])
def test_comment_validation(http,person,post,value):
    assert http.post(URLS['interaction']+f"/api/v1/posts/{post['id']}/comments",
                     headers=headers(person),json={'text':value}).status_code==422

def test_missing_post_and_actor(http,person):
    url=URLS['interaction']+f'/api/v1/posts/{uuid4()}/likes/me'
    assert http.put(url,headers=headers(person)).status_code==404
    assert http.put(url).status_code==422

@pytest.mark.parametrize('params',[{'limit':0},{'limit':101},{'offset':-1}])
def test_comments_pagination_invalid(http,post,params):
    assert http.get(URLS['interaction']+f"/api/v1/posts/{post['id']}/comments",params=params).status_code==422

def test_blocked_actor(http,person,post,sql):
    with sql['users'].begin() as db:
        db.execute(text('UPDATE profiles SET blocked=TRUE WHERE id=:id'),{'id':person['id']})
    assert http.put(URLS['interaction']+f"/api/v1/posts/{post['id']}/likes/me",headers=headers(person)).status_code==403
