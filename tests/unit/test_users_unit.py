"""UsersService: валидация и управление профилем без PostgreSQL."""
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError


def test_profile_normalization(modules):
    users = modules["users"]
    data = users.ProfileInput(email="  Name@Example.org ", display_name=" Андрей ")
    assert data.email == "name@example.org"
    assert data.display_name == "Андрей"


@pytest.mark.parametrize("email", ["invalid", "a b@example.org", "a@@example.org"])
def test_invalid_email(modules, email):
    with pytest.raises(ValidationError):
        modules["users"].ProfileInput(email=email, display_name="Андрей")


@pytest.mark.parametrize("name", ["", " ", "x" * 81], ids=["empty", "space", "long"])
def test_invalid_name(modules, name):
    with pytest.raises(ValidationError):
        modules["users"].ProfileInput(email="a@example.org", display_name=name)


def test_create_profile(modules, sql_db, ids, monkeypatch):
    users = modules["users"]
    monkeypatch.setattr(users, "uuid4", lambda: ids[0])
    data = users.ProfileInput(email="a@example.org", display_name="Анна")
    result = users.create_profile(data)
    assert result["id"] == ids[0]
    assert result["blocked"] is False
    assert result["created_at"].tzinfo is not None
    params = sql_db(users).execute.call_args.args[1]
    assert params["email"] == "a@example.org"


def test_duplicate_email_becomes_conflict(modules, sql_db):
    users = modules["users"]
    sql_db(users).execute.side_effect = IntegrityError("insert", {}, Exception())
    data = users.ProfileInput(email="a@example.org", display_name="Анна")
    with pytest.raises(HTTPException) as error:
        users.create_profile(data)
    assert error.value.status_code == 409


@pytest.mark.parametrize("found", [True, False])
def test_read_profile(modules, sql_db, ids, found):
    users = modules["users"]
    row = {"id": ids[0], "display_name": "Анна"} if found else None
    sql_db(users).execute.return_value.mappings.return_value.first.return_value = row
    if found:
        assert users.get_profile(ids[0])["display_name"] == "Анна"
    else:
        with pytest.raises(HTTPException) as error:
            users.get_profile(ids[0])
        assert error.value.status_code == 404


def test_update_trims_name(modules, sql_db, ids):
    users = modules["users"]
    db = sql_db(users)
    db.execute.return_value.mappings.return_value.first.return_value = {"id": ids[0]}
    users.update_profile(ids[0], users.ProfileUpdate(display_name=" Новое имя "))
    assert db.execute.call_args.args[1]["name"] == "Новое имя"


def test_update_missing_profile(modules, sql_db, ids):
    users = modules["users"]
    sql_db(users).execute.return_value.mappings.return_value.first.return_value = None
    with pytest.raises(HTTPException) as error:
        users.update_profile(ids[0], users.ProfileUpdate(display_name="Новое имя"))
    assert error.value.status_code == 404


def test_blank_update_does_not_write(modules, sql_db, ids):
    users = modules["users"]
    with pytest.raises(HTTPException) as error:
        users.update_profile(ids[0], users.ProfileUpdate(display_name=" "))
    assert error.value.status_code == 422
    sql_db(users).execute.assert_not_called()


@pytest.mark.parametrize("affected", [0, 1])
def test_delete_profile(modules, sql_db, ids, affected):
    users = modules["users"]
    sql_db(users).execute.return_value = Mock(rowcount=affected)
    if affected:
        assert users.delete_profile(ids[0]).status_code == 204
    else:
        with pytest.raises(HTTPException) as error:
            users.delete_profile(ids[0])
        assert error.value.status_code == 404
