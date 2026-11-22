"""HTTP-зависимости внутренних API социальной сети."""
import os
from uuid import UUID
import httpx
from fastapi import Header, HTTPException

USERS_URL = os.getenv('USERS_URL', 'http://127.0.0.1:8001')
CONTENT_URL = os.getenv('CONTENT_URL', 'http://127.0.0.1:8002')

def fetch(url):
    try:
        response = httpx.get(url, timeout=3.0, trust_env=False)
    except httpx.RequestError:
        raise HTTPException(503, 'Dependency unavailable')
    if response.status_code == 404:
        raise HTTPException(404, 'Referenced resource not found')
    if response.status_code != 200:
        raise HTTPException(503, 'Dependency error')
    try:
        return response.json()
    except ValueError:
        raise HTTPException(503, 'Invalid dependency response')

def check_user(user_id):
    user = fetch(f'{USERS_URL}/api/v1/users/{user_id}')
    if user.get('blocked'):
        raise HTTPException(403, 'User blocked')
    return user

def check_post(post_id):
    return fetch(f'{CONTENT_URL}/api/v1/posts/{post_id}')

def actor(x_user_id: UUID = Header()):
    # Заголовок внутреннего API передается доверенным шлюзом после проверки токена.
    check_user(x_user_id)
    return x_user_id
