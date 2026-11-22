"""CommunityService: роли, повторное вступление, изоляция PostgreSQL."""
from uuid import uuid4
import pytest
from sqlalchemy import text
from conftest import URLS,headers

def test_owner_created_atomically(http,person,community,sql):
    result=http.get(URLS['community']+f"/api/v1/communities/{community['id']}")
    assert result.status_code==200;assert result.json()['owner_id']==person['id']
    with sql['community'].connect() as db:
        assert db.execute(text('SELECT role FROM memberships WHERE community_id=:c AND user_id=:u'),
                          {'c':community['id'],'u':person['id']}).scalar_one()=='owner'

def test_join_repeat_leave(http,community,make_user,sql):
    member=make_user();url=URLS['community']+f"/api/v1/communities/{community['id']}/members/me"
    assert http.put(url,headers=headers(member)).status_code==204
    assert http.put(url,headers=headers(member)).status_code==204
    with sql['community'].connect() as db:
        assert db.execute(text('SELECT count(*) FROM memberships WHERE community_id=:c AND user_id=:u'),
                          {'c':community['id'],'u':member['id']}).scalar_one()==1
    assert http.delete(url,headers=headers(member)).status_code==204
    assert http.delete(url,headers=headers(member)).status_code==204
    with sql['community'].connect() as db:
        assert db.execute(text('SELECT count(*) FROM memberships WHERE community_id=:c AND user_id=:u'),
                          {'c':community['id'],'u':member['id']}).scalar_one()==0

def test_owner_cannot_leave(http,person,community):
    assert http.delete(URLS['community']+f"/api/v1/communities/{community['id']}/members/me",
                       headers=headers(person)).status_code==409

@pytest.mark.parametrize('value',['',' ','x'*101])
def test_name_validation(http,person,value):
    assert http.post(URLS['community']+'/api/v1/communities',headers=headers(person),json={'name':value}).status_code==422

def test_missing_community_and_user(http,person):
    url=URLS['community']+f'/api/v1/communities/{uuid4()}'
    assert http.get(url).status_code==404
    assert http.put(url+'/members/me',headers=headers(person)).status_code==404
    assert http.post(URLS['community']+'/api/v1/communities',headers={'X-User-ID':str(uuid4())},
                     json={'name':'Test'}).status_code==404

def test_missing_actor(http):
    assert http.post(URLS['community']+'/api/v1/communities',json={'name':'Test'}).status_code==422
