"""Управление профилями социальной сети. Собственная PostgreSQL."""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

engine = create_engine(os.getenv('USERS_DB_URL', 'postgresql+psycopg://social:social@localhost:55432/users'), pool_pre_ping=True)

@asynccontextmanager
async def lifespan(app):
    with engine.begin() as db:
        db.execute(text('CREATE TABLE IF NOT EXISTS profiles (id UUID PRIMARY KEY, email TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL, blocked BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMPTZ NOT NULL)'))
    yield
    engine.dispose()

app = FastAPI(title='Social Network Profiles API', version='2.0.0', lifespan=lifespan, docs_url=None, redoc_url=None)

class ProfileInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator('email')
    @classmethod
    def email_valid(cls, value):
        value = value.strip().lower()
        if value.count('@') != 1 or '.' not in value.split('@')[1] or any(x.isspace() for x in value):
            raise ValueError('Invalid email')
        return value

    @field_validator('display_name')
    @classmethod
    def name_valid(cls, value):
        if not value.strip():
            raise ValueError('Empty name')
        return value.strip()

class ProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)

@app.post('/api/v1/users', status_code=201)
def create_profile(data: ProfileInput):
    from common.security import identity
    person=identity.get()
    profile = dict(id=UUID(person.subject) if person else uuid4(), email=data.email, display_name=data.display_name,
                   blocked=False, created_at=datetime.now(timezone.utc))
    try:
        with engine.begin() as db:
            db.execute(text('INSERT INTO profiles VALUES (:id,:email,:display_name,:blocked,:created_at)'), profile)
    except IntegrityError:
        raise HTTPException(409, 'Email already exists')
    return profile

@app.get('/api/v1/users/{user_id}')
def get_profile(user_id: UUID):
    with engine.connect() as db:
        row = db.execute(text('SELECT * FROM profiles WHERE id=:id'), {'id': user_id}).mappings().first()
    if not row:
        raise HTTPException(404, 'User not found')
    return dict(row)

@app.patch('/api/v1/users/{user_id}')
def update_profile(user_id: UUID, data: ProfileUpdate):
    if not data.display_name.strip():
        raise HTTPException(422, 'Empty name')
    with engine.begin() as db:
        row = db.execute(text('UPDATE profiles SET display_name=:name WHERE id=:id RETURNING *'),
                         {'id': user_id, 'name': data.display_name.strip()}).mappings().first()
    if not row:
        raise HTTPException(404, 'User not found')
    return dict(row)

@app.delete('/api/v1/users/{user_id}', status_code=204)
def delete_profile(user_id: UUID):
    with engine.begin() as db:
        result = db.execute(text('DELETE FROM profiles WHERE id=:id'), {'id': user_id})
    if not result.rowcount:
        raise HTTPException(404, 'User not found')
    return Response(status_code=204)


from common.observability import setup
setup(app, "users")
