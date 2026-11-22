"""InteractionService: порядок проверок, лайки и комментарии без БД."""
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.mark.parametrize("value", ["", " ", "x" * 2001])
def test_invalid_comment(modules, value):
    with pytest.raises(ValidationError):
        modules["interaction"].CommentInput(text=value)


def test_comment_trims_text(modules):
    assert modules["interaction"].CommentInput(text=" Ответ ").text == "Ответ"


@pytest.mark.parametrize("operation", ["like", "unlike"])
def test_like_operations_check_post(modules, sql_db, ids, monkeypatch, operation):
    service = modules["interaction"]
    check = Mock()
    monkeypatch.setattr(service, "check_post", check)
    result = getattr(service, operation)(ids[0], user_id=ids[1])
    check.assert_called_once_with(ids[0])
    assert result.status_code == 204
    assert sql_db(service).execute.call_args.args[1] == {"p": ids[0], "u": ids[1]}


@pytest.mark.parametrize("operation", ["like", "unlike", "comment"])
def test_missing_post_prevents_write(modules, sql_db, ids, monkeypatch, operation):
    service = modules["interaction"]
    monkeypatch.setattr(service, "check_post", Mock(side_effect=HTTPException(404)))
    args = {"post_id": ids[0], "user_id": ids[1]}
    if operation == "comment":
        args["data"] = service.CommentInput(text="Ответ")
    with pytest.raises(HTTPException) as error:
        getattr(service, operation)(**args)
    assert error.value.status_code == 404
    sql_db(service).execute.assert_not_called()


def test_create_comment(modules, sql_db, ids, monkeypatch):
    service = modules["interaction"]
    monkeypatch.setattr(service, "check_post", Mock())
    monkeypatch.setattr(service, "uuid4", lambda: ids[2])
    expected = {"id": ids[2], "text": "Ответ", "author_id": ids[1]}
    sql_db(service).execute.return_value.mappings.return_value.one.return_value = expected
    assert service.comment(ids[0], service.CommentInput(text=" Ответ "), ids[1]) == expected
    assert sql_db(service).execute.call_args.args[1]["t"] == "Ответ"


def test_comments_list(modules, sql_db, ids, monkeypatch):
    service = modules["interaction"]
    monkeypatch.setattr(service, "check_post", Mock())
    sql_db(service).execute.return_value.mappings.return_value = [{"text": "Ответ"}]
    result = service.comments(ids[0], limit=2, offset=4)
    assert result == {"items": [{"text": "Ответ"}], "limit": 2, "offset": 4}
    assert sql_db(service).execute.call_args.args[1]["o"] == 4


@pytest.mark.parametrize("found", [True, False])
def test_internal_comment_lookup(modules, sql_db, ids, found):
    service = modules["interaction"]
    row = {"id": ids[0]} if found else None
    sql_db(service).execute.return_value.mappings.return_value.first.return_value = row
    if found:
        assert service.internal_comment(ids[0]) == row
    else:
        with pytest.raises(HTTPException) as error:
            service.internal_comment(ids[0])
        assert error.value.status_code == 404
