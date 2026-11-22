"""Публикации социальной сети. Собственная MongoDB и HTTP-проверка автора."""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4
import httpx
from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient

mongo = MongoClient(os.getenv('CONTENT_DB_URL', 'mongodb://localhost:57017/'), serverSelectionTimeoutMS=3000)
posts = mongo.social_content.posts
USERS_URL = os.getenv('USERS_URL', 'http://localhost:8001')

@asynccontextmanager
async def lifespan(app):
    posts.create_index([('author_id', 1), ('created_at', -1)])
    yield
    mongo.close()

app = FastAPI(title='Social Network Content API', version='1.0.0', lifespan=lifespan)

class PostInput(BaseModel):
    author_id: UUID
    text: str = Field(min_length=1, max_length=5000)

    @field_validator('text')
    @classmethod
    def text_valid(cls, value):
        if not value.strip():
            raise ValueError('Empty publication')
        return value.strip()

def check_author(author_id: UUID):
    """Вложенный HTTP-вызов. Сбой зависимости не разрешает публикацию."""
    try:
        response = httpx.get(f'{USERS_URL}/api/v1/users/{author_id}', timeout=3.0)
    except httpx.RequestError:
        raise HTTPException(503, 'Users service unavailable')
    if response.status_code == 404:
        raise HTTPException(404, 'Author not found')
    if response.status_code != 200:
        raise HTTPException(503, 'Users service error')
    if response.json()['blocked']:
        raise HTTPException(403, 'Author blocked')

def public(document):
    return {'id': document['_id'], **{k: v for k, v in document.items() if k != '_id'}}

@app.post('/api/v1/posts', status_code=201)
def create_post(data: PostInput):
    check_author(data.author_id)
    document = {'_id': str(uuid4()), 'author_id': str(data.author_id),
                'text': data.text, 'created_at': datetime.now(timezone.utc).isoformat()}
    posts.insert_one(document)
    return public(document)

@app.get('/api/v1/posts/{post_id}')
def get_post(post_id: UUID):
    document = posts.find_one({'_id': str(post_id)})
    if not document:
        raise HTTPException(404, 'Post not found')
    return public(document)

@app.get('/api/v1/posts')
def list_posts(author_id: UUID | None = None, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    query = {'author_id': str(author_id)} if author_id else {}
    cursor = posts.find(query).sort([('created_at', -1), ('_id', -1)]).skip(offset).limit(limit)
    return {'items': [public(doc) for doc in cursor], 'limit': limit, 'offset': offset}

@app.delete('/api/v1/posts/{post_id}', status_code=204)
def delete_post(post_id: UUID):
    if posts.delete_one({'_id': str(post_id)}).deleted_count == 0:
        raise HTTPException(404, 'Post not found')
    return Response(status_code=204)
