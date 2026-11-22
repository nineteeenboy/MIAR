"""Predeployment: реальные HTTP-запросы, PostgreSQL и MongoDB."""
import os
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

USERS = os.getenv('TEST_USERS_URL', 'http://127.0.0.1:8001')
CONTENT = os.getenv('TEST_CONTENT_URL', 'http://127.0.0.1:8002')
HTTP = httpx.Client(trust_env=False, timeout=5.0)

@pytest.fixture
def profile():
    result = HTTP.post(f'{USERS}/api/v1/users', json={'email': f'{uuid4()}@example.org', 'display_name': 'Андрей'})
    assert result.status_code == 201
    data = result.json()
    yield data
    HTTP.delete(f"{USERS}/api/v1/users/{data['id']}")

def test_profile_read_update(profile):
    url = f"{USERS}/api/v1/users/{profile['id']}"
    assert HTTP.get(url).json()['display_name'] == 'Андрей'
    assert HTTP.patch(url, json={'display_name': 'Новое имя'}).json()['display_name'] == 'Новое имя'

def test_duplicate_email(profile):
    result = HTTP.post(f'{USERS}/api/v1/users', json={'email': profile['email'].upper(), 'display_name': 'Другой'})
    assert result.status_code == 409

@pytest.mark.parametrize('payload', [
    {'email': 'bad', 'display_name': 'Name'},
    {'email': 'ok@example.org', 'display_name': '  '},
    {'email': 'a b@example.org', 'display_name': 'Name'},
])
def test_profile_validation(payload):
    assert HTTP.post(f'{USERS}/api/v1/users', json=payload).status_code == 422

@pytest.mark.parametrize('path,expected', [('bad-uuid', 422), (str(uuid4()), 404)])
def test_user_missing(path, expected):
    assert HTTP.get(f'{USERS}/api/v1/users/{path}').status_code == expected

def test_publication_full_flow(profile):
    created = HTTP.post(f'{CONTENT}/api/v1/posts', json={'author_id': profile['id'], 'text': 'Первая публикация'})
    assert created.status_code == 201
    post = created.json()
    url = f"{CONTENT}/api/v1/posts/{post['id']}"
    try:
        assert HTTP.get(url).json()['text'] == 'Первая публикация'
        feed = HTTP.get(f'{CONTENT}/api/v1/posts', params={'author_id': profile['id']}).json()
        assert post['id'] in [item['id'] for item in feed['items']]
        assert HTTP.delete(url).status_code == 204
        assert HTTP.get(url).status_code == 404
    finally:
        HTTP.delete(url)

def test_unknown_author():
    response = HTTP.post(f'{CONTENT}/api/v1/posts', json={'author_id': str(uuid4()), 'text': 'Post'})
    assert response.status_code == 404

@pytest.mark.parametrize('body', ['', ' ', 'x' * 5001])
def test_post_validation(profile, body):
    assert HTTP.post(f'{CONTENT}/api/v1/posts', json={'author_id': profile['id'], 'text': body}).status_code == 422

@pytest.mark.parametrize('params', [{'limit': 0}, {'limit': 101}, {'offset': -1}])
def test_pagination_validation(params):
    assert HTTP.get(f'{CONTENT}/api/v1/posts', params=params).status_code == 422

def test_blocked_author(profile):
    engine = create_engine('postgresql+psycopg://social:social@localhost:55432/users')
    with engine.begin() as conn:
        conn.execute(text('UPDATE profiles SET blocked=TRUE WHERE id=:id'), {'id': profile['id']})
    try:
        assert HTTP.post(f'{CONTENT}/api/v1/posts', json={'author_id': profile['id'], 'text': 'Post'}).status_code == 403
    finally:
        engine.dispose()

def test_postgres_unique_and_rollback():
    engine = create_engine('postgresql+psycopg://social:social@localhost:55432/users')
    with engine.connect() as conn:
        tx = conn.begin()
        try:
            conn.execute(text('CREATE TEMP TABLE test_profiles (email TEXT UNIQUE NOT NULL) ON COMMIT DROP'))
            conn.execute(text("INSERT INTO test_profiles VALUES ('test@example.org')"))
            with pytest.raises(IntegrityError):
                with conn.begin_nested():
                    conn.execute(text("INSERT INTO test_profiles VALUES ('test@example.org')"))
            assert conn.execute(text('SELECT count(*) FROM test_profiles')).scalar_one() == 1
        finally:
            tx.rollback()
    engine.dispose()

def test_mongo_insert_read_unique():
    with MongoClient('mongodb://localhost:57017/') as client:
        collection = client.social_test[f'posts_{uuid4().hex}']
        try:
            collection.insert_one({'_id': 'post-1', 'text': 'Hello'})
            assert collection.find_one({'_id': 'post-1'})['text'] == 'Hello'
            with pytest.raises(DuplicateKeyError):
                collection.insert_one({'_id': 'post-1', 'text': 'Duplicate'})
        finally:
            collection.drop()
