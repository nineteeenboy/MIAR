"""CommunityService: сообщество, членство и запрет выхода владельца."""
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.mark.parametrize("name", ["", " ", "x" * 101])
def test_invalid_name(modules, name):
    with pytest.raises(ValidationError):
        modules["community"].CommunityInput(name=name)


def test_default_description_and_trim(modules):
    data = modules["community"].CommunityInput(name=" Сообщество ")
    assert data.name == "Сообщество"
    assert data.description == ""


def test_description_limit(modules):
    with pytest.raises(ValidationError):
        modules["community"].CommunityInput(name="A", description="x" * 2001)


@pytest.mark.parametrize("found", [True, False])
def test_require_community(modules, sql_db, ids, found):
    service = modules["community"]
    db = sql_db(service)
    expected = {"id": ids[0], "owner_id": ids[1]} if found else None
    db.execute.return_value.mappings.return_value.first.return_value = expected
    if found:
        assert service.require_community(db, ids[0]) == expected
    else:
        with pytest.raises(HTTPException) as error:
            service.require_community(db, ids[0])
        assert error.value.status_code == 404


def test_create_assigns_owner(modules, sql_db, ids, monkeypatch):
    service = modules["community"]
    monkeypatch.setattr(service, "uuid4", lambda: ids[0])
    data = service.CommunityInput(name=" Клуб ")
    result = service.create(data, user_id=ids[1])
    assert result == {"id": ids[0], "owner_id": ids[1], "name": "Клуб", "description": ""}
    assert sql_db(service).execute.call_count == 2
    service.engine.begin.assert_called_once()
    assert sql_db(service).execute.call_args.args[1]["owner_id"] == ids[1]


def test_read_delegates_lookup(modules, sql_db, ids, monkeypatch):
    service = modules["community"]
    lookup = Mock(return_value={"id": ids[0]})
    monkeypatch.setattr(service, "require_community", lookup)
    assert service.read(ids[0]) == {"id": ids[0]}
    lookup.assert_called_once_with(sql_db(service), ids[0])


def test_join_checks_community_before_write(modules, sql_db, ids, monkeypatch):
    service = modules["community"]
    lookup = Mock(return_value={"owner_id": ids[2]})
    monkeypatch.setattr(service, "require_community", lookup)
    assert service.join(ids[0], user_id=ids[1]).status_code == 204
    lookup.assert_called_once_with(sql_db(service), ids[0], lock=True)
    assert sql_db(service).execute.call_args.args[1] == {"c": ids[0], "u": ids[1]}


def test_owner_cannot_leave(modules, sql_db, ids, monkeypatch):
    service = modules["community"]
    monkeypatch.setattr(service, "require_community", Mock(return_value={"owner_id": ids[1]}))
    with pytest.raises(HTTPException) as error:
        service.leave(ids[0], user_id=ids[1])
    assert error.value.status_code == 409
    sql_db(service).execute.assert_not_called()


def test_member_can_leave(modules, sql_db, ids, monkeypatch):
    service = modules["community"]
    monkeypatch.setattr(service, "require_community", Mock(return_value={"owner_id": ids[2]}))
    assert service.leave(ids[0], user_id=ids[1]).status_code == 204
    assert sql_db(service).execute.call_args.args[1] == {"c": ids[0], "u": ids[1]}


@pytest.mark.parametrize("operation", ["join", "leave"])
def test_missing_community_prevents_write(modules, sql_db, ids, monkeypatch, operation):
    service = modules["community"]
    monkeypatch.setattr(service, "require_community", Mock(side_effect=HTTPException(404)))
    with pytest.raises(HTTPException):
        getattr(service, operation)(ids[0], user_id=ids[1])
    sql_db(service).execute.assert_not_called()
