"""InteractionService: отдельная PostgreSQL, четыре бизнес-операции."""
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, text
from common.api import actor, check_post

engine = create_engine(os.getenv('INTERACTION_DB_URL',
    'postgresql+psycopg://social:social@127.0.0.1:55433/interactions'), pool_pre_ping=True)

@asynccontextmanager
async def lifespan(app):
    with engine.begin() as db:
        db.execute(text('''CREATE TABLE IF NOT EXISTS likes (
            post_id UUID NOT NULL, user_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), PRIMARY KEY(post_id,user_id))'''))
        db.execute(text('''CREATE TABLE IF NOT EXISTS comments (
            id UUID PRIMARY KEY, post_id UUID NOT NULL, author_id UUID NOT NULL,
            text TEXT NOT NULL CHECK(length(btrim(text)) BETWEEN 1 AND 2000),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())'''))
        db.execute(text('CREATE INDEX IF NOT EXISTS comments_post ON comments(post_id,created_at,id)'))
    yield
    engine.dispose()

app = FastAPI(title='Social Network Interactions API', lifespan=lifespan, docs_url=None, redoc_url=None)

class CommentInput(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator('text')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Empty comment')
        return value.strip()

@app.put('/api/v1/posts/{post_id}/likes/me', status_code=204)
def like(post_id: UUID, user_id: UUID = Depends(actor)):
    check_post(post_id)
    with engine.begin() as db:
        db.execute(text('INSERT INTO likes(post_id,user_id) VALUES (:p,:u) ON CONFLICT DO NOTHING'),
                   {'p': post_id, 'u': user_id})
    return Response(status_code=204)

@app.delete('/api/v1/posts/{post_id}/likes/me', status_code=204)
def unlike(post_id: UUID, user_id: UUID = Depends(actor)):
    check_post(post_id)
    with engine.begin() as db:
        db.execute(text('DELETE FROM likes WHERE post_id=:p AND user_id=:u'), {'p': post_id, 'u': user_id})
    return Response(status_code=204)

@app.post('/api/v1/posts/{post_id}/comments', status_code=201)
def comment(post_id: UUID, data: CommentInput, user_id: UUID = Depends(actor)):
    check_post(post_id)
    with engine.begin() as db:
        row = db.execute(text('''INSERT INTO comments(id,post_id,author_id,text)
            VALUES (:id,:p,:u,:t) RETURNING *'''),
            {'id': uuid4(), 'p': post_id, 'u': user_id, 't': data.text}).mappings().one()
    return dict(row)

@app.get('/api/v1/posts/{post_id}/comments')
def comments(post_id: UUID, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    check_post(post_id)
    with engine.connect() as db:
        rows = db.execute(text('''SELECT * FROM comments WHERE post_id=:p
            ORDER BY created_at,id LIMIT :l OFFSET :o'''), {'p': post_id, 'l': limit, 'o': offset}).mappings()
        return {'items': [dict(row) for row in rows], 'limit': limit, 'offset': offset}

@app.get('/internal/comments/{comment_id}', include_in_schema=False)
def internal_comment(comment_id: UUID):
    with engine.connect() as db:
        row = db.execute(text('SELECT * FROM comments WHERE id=:id'), {'id': comment_id}).mappings().first()
    if row is None:
        raise HTTPException(404, 'Comment not found')
    return dict(row)


from common.observability import setup
setup(app, "interaction")
