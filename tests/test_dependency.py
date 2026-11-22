"""Компонентные проверки отказа HTTP-зависимости без подмены базы."""
from unittest.mock import patch
from uuid import uuid4
import httpx
from fastapi.testclient import TestClient
from content_service.app import app

def test_dependency_timeout():
    with patch('content_service.app.httpx.get', side_effect=httpx.ConnectError('offline')):
        response = TestClient(app).post('/api/v1/posts', json={'author_id': str(uuid4()), 'text': 'Hello'})
    assert response.status_code == 503

def test_dependency_server_error():
    with patch('content_service.app.httpx.get', return_value=httpx.Response(500)):
        response = TestClient(app).post('/api/v1/posts', json={'author_id': str(uuid4()), 'text': 'Hello'})
    assert response.status_code == 503
