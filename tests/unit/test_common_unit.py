"""Общие HTTP-помощники: только mock-ответы, ни одного настоящего запроса."""
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException


def test_fetch_success(modules, monkeypatch):
    api = modules["common"]
    response = Mock(status_code=200)
    response.json.return_value = {"id": "user-1"}
    request = Mock(return_value=response)
    monkeypatch.setattr(api.httpx, "get", request)
    assert api.fetch("http://test.local/user") == {"id": "user-1"}
    request.assert_called_once_with("http://test.local/user", timeout=3.0, trust_env=False)


@pytest.mark.parametrize("status,expected", [(404, 404), (500, 503), (302, 503)])
def test_fetch_error_status(modules, monkeypatch, status, expected):
    api = modules["common"]
    monkeypatch.setattr(api.httpx, "get", Mock(return_value=Mock(status_code=status)))
    with pytest.raises(HTTPException) as error:
        api.fetch("http://test.local/user")
    assert error.value.status_code == expected


def test_fetch_timeout(modules, monkeypatch):
    api = modules["common"]
    monkeypatch.setattr(api.httpx, "get", Mock(side_effect=httpx.ConnectError("offline")))
    with pytest.raises(HTTPException) as error:
        api.fetch("http://test.local/user")
    assert error.value.status_code == 503


def test_fetch_invalid_json(modules, monkeypatch):
    api = modules["common"]
    response = Mock(status_code=200)
    response.json.side_effect = ValueError("Invalid JSON")
    monkeypatch.setattr(api.httpx, "get", Mock(return_value=response))
    with pytest.raises(HTTPException) as error:
        api.fetch("http://test.local/user")
    assert error.value.status_code == 503


@pytest.mark.parametrize("blocked", [False, True])
def test_check_user_policy(modules, ids, monkeypatch, blocked):
    api = modules["common"]
    user = {"id": str(ids[0]), "blocked": blocked}
    monkeypatch.setattr(api, "fetch", Mock(return_value=user))
    if blocked:
        with pytest.raises(HTTPException) as error:
            api.check_user(ids[0])
        assert error.value.status_code == 403
    else:
        assert api.check_user(ids[0]) == user


def test_actor_preserves_identity(modules, ids, monkeypatch):
    api = modules["common"]
    check = Mock()
    monkeypatch.setattr(api, "check_user", check)
    assert api.actor(ids[0]) == ids[0]
    check.assert_called_once_with(ids[0])


def test_check_post_uses_content_api(modules, ids, monkeypatch):
    api = modules["common"]
    fetch = Mock(return_value={"id": str(ids[0])})
    monkeypatch.setattr(api, "fetch", fetch)
    assert api.check_post(ids[0])["id"] == str(ids[0])
    assert fetch.call_args.args[0].endswith(f"/api/v1/posts/{ids[0]}")


def test_real_network_is_disabled():
    import socket
    with pytest.raises(AssertionError, match="Network access is forbidden"):
        socket.create_connection(("127.0.0.1", 8001))
