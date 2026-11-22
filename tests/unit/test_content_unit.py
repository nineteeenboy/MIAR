"""ContentService: публикации и проверка автора с mock-зависимостями."""
from unittest.mock import Mock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.mark.parametrize("status,blocked,expected", [
    (200, False, None), (200, True, 403), (404, False, 404), (500, False, 503),
])
def test_author_policy(modules, ids, monkeypatch, status, blocked, expected):
    content = modules["content"]
    response = Mock(status_code=status)
    response.json.return_value = {"blocked": blocked}
    monkeypatch.setattr(content.httpx, "get", Mock(return_value=response))
    if expected is None:
        assert content.check_author(ids[0]) is None
    else:
        with pytest.raises(HTTPException) as error:
            content.check_author(ids[0])
        assert error.value.status_code == expected


def test_author_timeout(modules, ids, monkeypatch):
    content = modules["content"]
    monkeypatch.setattr(content.httpx, "get", Mock(side_effect=httpx.ConnectError("offline")))
    with pytest.raises(HTTPException) as error:
        content.check_author(ids[0])
    assert error.value.status_code == 503


@pytest.mark.parametrize("value", ["", " ", "x" * 5001])
def test_invalid_post_text(modules, ids, value):
    with pytest.raises(ValidationError):
        modules["content"].PostInput(author_id=ids[0], text=value)


def test_create_post_maps_document(modules, ids, monkeypatch):
    content = modules["content"]
    check = Mock()
    monkeypatch.setattr(content, "check_author", check)
    monkeypatch.setattr(content, "uuid4", lambda: ids[1])
    data = content.PostInput(author_id=ids[0], text=" Публикация ")
    result = content.create_post(data)
    check.assert_called_once_with(ids[0])
    assert result["id"] == str(ids[1])
    assert result["text"] == "Публикация"
    assert "_id" not in result
    assert content.posts.insert_one.call_args.args[0]["author_id"] == str(ids[0])


def test_failed_author_check_prevents_insert(modules, ids, monkeypatch):
    content = modules["content"]
    monkeypatch.setattr(content, "check_author", Mock(side_effect=HTTPException(403)))
    with pytest.raises(HTTPException):
        content.create_post(content.PostInput(author_id=ids[0], text="Публикация"))
    content.posts.insert_one.assert_not_called()


@pytest.mark.parametrize("found", [True, False])
def test_read_post(modules, ids, found):
    content = modules["content"]
    document = {"_id": str(ids[0]), "text": "Запись"} if found else None
    content.posts.find_one.return_value = document
    if found:
        assert content.get_post(ids[0]) == {"id": str(ids[0]), "text": "Запись"}
        assert document["_id"] == str(ids[0])  # Исходный документ не изменяется.
    else:
        with pytest.raises(HTTPException) as error:
            content.get_post(ids[0])
        assert error.value.status_code == 404


def test_list_uses_author_filter_and_page(modules, ids):
    content = modules["content"]
    cursor = content.posts.find.return_value.sort.return_value
    cursor.skip.return_value.limit.return_value = [{"_id": "post-1", "text": "A"}]
    result = content.list_posts(author_id=ids[0], limit=2, offset=3)
    content.posts.find.assert_called_once_with({"author_id": str(ids[0])})
    cursor.skip.assert_called_once_with(3)
    cursor.skip.return_value.limit.assert_called_once_with(2)
    assert result == {"items": [{"id": "post-1", "text": "A"}], "limit": 2, "offset": 3}


@pytest.mark.parametrize("affected", [0, 1])
def test_delete_post(modules, ids, affected):
    content = modules["content"]
    content.posts.delete_one.return_value = Mock(deleted_count=affected)
    if affected:
        assert content.delete_post(ids[0]).status_code == 204
    else:
        with pytest.raises(HTTPException) as error:
            content.delete_post(ids[0])
        assert error.value.status_code == 404
