"""Проверки собственных хранилищ двух исходных сервисов через созданные API-данные."""
import os
from pymongo import MongoClient
from sqlalchemy import text

def test_users_business_record_in_postgres(person,sql):
    with sql['users'].connect() as db:
        row=db.execute(text('SELECT email,display_name FROM profiles WHERE id=:id'),{'id':person['id']}).mappings().one()
        assert row['email']==person['email']
        assert row['display_name']==person['display_name']

def test_content_business_record_in_mongo(post):
    with MongoClient(os.getenv('TEST_CONTENT_DB_URL','mongodb://127.0.0.1:57017/')) as client:
        row=client.social_content.posts.find_one({'_id':post['id']})
        assert row['text']==post['text']
        assert row['author_id']==post['author_id']
