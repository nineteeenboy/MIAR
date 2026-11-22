"""ModerationService: PostgreSQL, права модератора и transactional outbox."""
import hmac
import json
import os
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID, uuid4
import pika
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import create_engine, text
from common.api import actor, check_post, fetch, USERS_URL

engine = create_engine(os.getenv('MODERATION_DB_URL',
    'postgresql+psycopg://social:social@127.0.0.1:55435/moderation'), pool_pre_ping=True)
MODERATOR_KEY = os.getenv('MODERATOR_KEY', 'social-demo-moderator')
AMQP_URL = os.getenv('AMQP_URL', 'amqp://social:social@127.0.0.1:5672/%2F')
INTERACTION_URL = os.getenv('INTERACTION_URL', 'http://127.0.0.1:8003')
EVENT_QUEUE = 'social.moderation.decisions'

@asynccontextmanager
async def lifespan(app):
    with engine.begin() as db:
        db.execute(text('''CREATE TABLE IF NOT EXISTS reports (
            id UUID PRIMARY KEY, reporter_id UUID NOT NULL, target_type TEXT NOT NULL,
            target_id UUID NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
            decision TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())'''))
        db.execute(text('''CREATE TABLE IF NOT EXISTS outbox (
            id UUID PRIMARY KEY, report_id UUID UNIQUE REFERENCES reports(id) ON DELETE CASCADE,
            payload JSONB NOT NULL, sent BOOLEAN NOT NULL DEFAULT FALSE)'''))
    yield
    engine.dispose()

app = FastAPI(title='Social Network Moderation API', lifespan=lifespan, docs_url=None, redoc_url=None)

class ReportInput(BaseModel):
    target_type: Literal['user', 'post', 'comment']
    target_id: UUID
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator('reason')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Empty reason')
        return value.strip()

class DecisionInput(BaseModel):
    status: Literal['accepted', 'rejected']
    decision: str = Field(min_length=1, max_length=2000)

    @field_validator('decision')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Empty decision')
        return value.strip()

def is_moderator(key):
    return key is not None and hmac.compare_digest(key, MODERATOR_KEY)

def moderator(x_moderator_key: str | None = Header(default=None)):
    if not is_moderator(x_moderator_key):
        raise HTTPException(403, 'Moderator access required')

def dispatch_pending():
    """One outbox event may be redelivered; consumers deduplicate by event_id."""
    delivered = 0
    with engine.begin() as db:
        rows = db.execute(text('SELECT * FROM outbox WHERE NOT sent FOR UPDATE SKIP LOCKED')).mappings().all()
        if not rows:
            return 0
        with pika.BlockingConnection(pika.URLParameters(AMQP_URL)) as connection:
            channel = connection.channel()
            channel.queue_declare(EVENT_QUEUE, durable=True)
            channel.confirm_delivery()
            for row in rows:
                channel.basic_publish('', EVENT_QUEUE,
                    json.dumps(row['payload'], ensure_ascii=False).encode(),
                    pika.BasicProperties(delivery_mode=2, content_type='application/json',
                                         message_id=str(row['id'])), mandatory=True)
                db.execute(text('UPDATE outbox SET sent=TRUE WHERE id=:id'), {'id': row['id']})
                delivered += 1
    return delivered

@app.post('/api/v1/reports', status_code=201)
def create(data: ReportInput, user_id: UUID = Depends(actor)):
    if data.target_type == 'user':
        fetch(f'{USERS_URL}/api/v1/users/{data.target_id}')
    elif data.target_type == 'post':
        check_post(data.target_id)
    else:
        fetch(f'{INTERACTION_URL}/internal/comments/{data.target_id}')
    values = {'id': uuid4(), 'reporter': user_id, **data.model_dump()}
    with engine.begin() as db:
        row = db.execute(text('''INSERT INTO reports(id,reporter_id,target_type,target_id,reason)
            VALUES (:id,:reporter,:target_type,:target_id,:reason) RETURNING *'''), values).mappings().one()
    return dict(row)

@app.get('/api/v1/reports/{report_id}')
def read(report_id: UUID, x_user_id: UUID | None = Header(default=None),
         x_moderator_key: str | None = Header(default=None)):
    if not is_moderator(x_moderator_key):
        if x_user_id is None:
            raise HTTPException(403, 'Reporter or moderator access required')
        # actor also checks that the reporter exists and is active.
        actor(x_user_id)
    with engine.connect() as db:
        row = db.execute(text('SELECT * FROM reports WHERE id=:id'), {'id': report_id}).mappings().first()
    if row is None:
        raise HTTPException(404, 'Report not found')
    if not is_moderator(x_moderator_key) and row['reporter_id'] != x_user_id:
        raise HTTPException(403, 'Report belongs to another user')
    return dict(row)

@app.get('/api/v1/reports', dependencies=[Depends(moderator)])
def reports(status: Literal['open','accepted','rejected'] | None = None,
            limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    with engine.connect() as db:
        rows = db.execute(text('''SELECT * FROM reports WHERE (CAST(:s AS TEXT) IS NULL OR status=:s)
            ORDER BY created_at,id LIMIT :l OFFSET :o'''), {'s': status, 'l': limit, 'o': offset}).mappings()
        return {'items': [dict(row) for row in rows], 'limit': limit, 'offset': offset}

@app.patch('/api/v1/reports/{report_id}', dependencies=[Depends(moderator)])
def decide(report_id: UUID, data: DecisionInput):
    with engine.begin() as db:
        row = db.execute(text('SELECT * FROM reports WHERE id=:id FOR UPDATE'),
                         {'id': report_id}).mappings().first()
        if row is None:
            raise HTTPException(404, 'Report not found')
        if row['status'] != 'open':
            if row['status'] == data.status and row['decision'] == data.decision:
                return dict(row)
            raise HTTPException(409, 'Decision already recorded')
        result = db.execute(text('''UPDATE reports SET status=:status, decision=:decision
            WHERE id=:id RETURNING *'''), {'id': report_id, **data.model_dump()}).mappings().one()
        event_id = uuid4()
        payload = {'event_id': str(event_id), 'event': 'moderation.decision.created',
                   'report_id': str(report_id), 'target_type': row['target_type'],
                   'target_id': str(row['target_id']), **data.model_dump()}
        db.execute(text('INSERT INTO outbox(id,report_id,payload) VALUES (:id,:r,CAST(:p AS JSONB))'),
                   {'id': event_id, 'r': report_id, 'p': json.dumps(payload, ensure_ascii=False)})
    # A broker outage leaves a durable pending row; it never rolls back a recorded decision.
    try:
        dispatch_pending()
    except pika.exceptions.AMQPError:
        pass
    return dict(result)
