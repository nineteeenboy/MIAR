"""CommunityService: сообщества и роли в собственной PostgreSQL."""
import os
from contextlib import asynccontextmanager
from uuid import UUID, uuid4
from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, text
from common.api import actor

engine = create_engine(os.getenv('COMMUNITY_DB_URL',
    'postgresql+psycopg://social:social@127.0.0.1:55434/communities'), pool_pre_ping=True)

@asynccontextmanager
async def lifespan(app):
    with engine.begin() as db:
        db.execute(text('''CREATE TABLE IF NOT EXISTS communities (
            id UUID PRIMARY KEY, owner_id UUID NOT NULL, name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '')'''))
        db.execute(text('''CREATE TABLE IF NOT EXISTS memberships (
            community_id UUID REFERENCES communities(id) ON DELETE CASCADE,
            user_id UUID NOT NULL, role TEXT NOT NULL CHECK(role IN ('owner','member')),
            PRIMARY KEY(community_id,user_id))'''))
    yield
    engine.dispose()

app = FastAPI(title='Social Network Communities API', lifespan=lifespan, docs_url=None, redoc_url=None)

class CommunityInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)

    @field_validator('name')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Empty name')
        return value.strip()

def require_community(db, community_id, lock=False):
    sql = 'SELECT * FROM communities WHERE id=:id' + (' FOR UPDATE' if lock else '')
    row = db.execute(text(sql), {'id': community_id}).mappings().first()
    if row is None:
        raise HTTPException(404, 'Community not found')
    return dict(row)

@app.post('/api/v1/communities', status_code=201)
def create(data: CommunityInput, user_id: UUID = Depends(actor)):
    result = {'id': uuid4(), 'owner_id': user_id, **data.model_dump()}
    with engine.begin() as db:
        db.execute(text('INSERT INTO communities VALUES (:id,:owner_id,:name,:description)'), result)
        db.execute(text("INSERT INTO memberships VALUES (:id,:owner_id,'owner')"), result)
    return result

@app.get('/api/v1/communities/{community_id}')
def read(community_id: UUID):
    with engine.connect() as db:
        return require_community(db, community_id)

@app.put('/api/v1/communities/{community_id}/members/me', status_code=204)
def join(community_id: UUID, user_id: UUID = Depends(actor)):
    with engine.begin() as db:
        require_community(db, community_id, lock=True)
        db.execute(text("""INSERT INTO memberships VALUES (:c,:u,'member')
            ON CONFLICT DO NOTHING"""), {'c': community_id, 'u': user_id})
    return Response(status_code=204)

@app.delete('/api/v1/communities/{community_id}/members/me', status_code=204)
def leave(community_id: UUID, user_id: UUID = Depends(actor)):
    with engine.begin() as db:
        community = require_community(db, community_id, lock=True)
        if community['owner_id'] == user_id:
            raise HTTPException(409, 'Owner must transfer ownership before leaving')
        db.execute(text('DELETE FROM memberships WHERE community_id=:c AND user_id=:u'),
                   {'c': community_id, 'u': user_id})
    return Response(status_code=204)
