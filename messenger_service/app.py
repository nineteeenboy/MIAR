"""MessengerService: отдельная MongoDB; доступ только участникам."""
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import UUID, uuid4
from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from pymongo import MongoClient, ReturnDocument
from common.api import actor, check_user

mongo = MongoClient(os.getenv('MESSENGER_DB_URL', 'mongodb://127.0.0.1:57018/'), serverSelectionTimeoutMS=3000)
db = mongo.social_messenger

@asynccontextmanager
async def lifespan(app):
    db.messages.create_index([('dialog_id', 1), ('sequence', 1)], unique=True)
    yield
    mongo.close()

app = FastAPI(title='Social Network Messenger API', lifespan=lifespan, docs_url=None, redoc_url=None)

class DialogInput(BaseModel):
    participant_ids: list[UUID] = Field(min_length=2, max_length=100)

class MessageInput(BaseModel):
    text: str = Field(min_length=1, max_length=10000)

    @field_validator('text')
    @classmethod
    def clean(cls, value):
        if not value.strip():
            raise ValueError('Empty message')
        return value.strip()

class ReadInput(BaseModel):
    last_read_message_id: UUID

def public(doc):
    return {'id': doc['_id'], **{k: v for k,v in doc.items() if k != '_id'}}

def require_member(dialog_id, user_id):
    dialog = db.dialogs.find_one({'_id': str(dialog_id)})
    if dialog is None:
        raise HTTPException(404, 'Dialog not found')
    if str(user_id) not in dialog['participant_ids']:
        raise HTTPException(403, 'Not a dialog participant')
    return dialog

@app.post('/api/v1/dialogs', status_code=201)
def create(data: DialogInput, user_id: UUID = Depends(actor)):
    participants = [str(value) for value in data.participant_ids]
    if len(set(participants)) != len(participants) or str(user_id) not in participants:
        raise HTTPException(422, 'Participants must be unique and include the caller')
    for participant in participants:
        check_user(participant)
    doc = {'_id': str(uuid4()), 'participant_ids': participants, 'sequence': 0, 'read': {}}
    db.dialogs.insert_one(doc)
    return {'id': doc['_id'], 'participant_ids': participants}

@app.post('/api/v1/dialogs/{dialog_id}/messages', status_code=201)
def send(dialog_id: UUID, data: MessageInput, user_id: UUID = Depends(actor)):
    require_member(dialog_id, user_id)
    # Atomic increment assigns a stable order even for simultaneous senders.
    dialog = db.dialogs.find_one_and_update({'_id': str(dialog_id)}, {'$inc': {'sequence': 1}},
                                            return_document=ReturnDocument.AFTER)
    doc = {'_id': str(uuid4()), 'dialog_id': str(dialog_id), 'sender_id': str(user_id),
           'text': data.text, 'sequence': dialog['sequence'],
           'created_at': datetime.now(timezone.utc).isoformat()}
    db.messages.insert_one(doc)
    return public(doc)

@app.get('/api/v1/dialogs/{dialog_id}/messages')
def messages(dialog_id: UUID, limit: int = Query(20, ge=1, le=100),
             offset: int = Query(0, ge=0), user_id: UUID = Depends(actor)):
    require_member(dialog_id, user_id)
    cursor = db.messages.find({'dialog_id': str(dialog_id)}).sort('sequence', 1).skip(offset).limit(limit)
    return {'items': [public(doc) for doc in cursor], 'limit': limit, 'offset': offset}

@app.patch('/api/v1/dialogs/{dialog_id}/read')
def mark_read(dialog_id: UUID, data: ReadInput, user_id: UUID = Depends(actor)):
    require_member(dialog_id, user_id)
    message = db.messages.find_one({'_id': str(data.last_read_message_id), 'dialog_id': str(dialog_id)})
    if message is None:
        raise HTTPException(404, 'Message not found in dialog')
    key = f'read.{user_id}'
    # $max is monotonic; an older acknowledgement cannot move the cursor back.
    result = db.dialogs.find_one_and_update({'_id': str(dialog_id)},
        {'$max': {key: message['sequence']}}, return_document=ReturnDocument.AFTER)
    current = db.messages.find_one({'dialog_id': str(dialog_id), 'sequence': result['read'][str(user_id)]})
    return {'last_read_message_id': current['_id']}


from common.observability import setup
setup(app, "messenger")
