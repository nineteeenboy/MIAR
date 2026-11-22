"""MessengerService: участники, сообщения и курсор без MongoDB."""
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.mark.parametrize("value", ["", " ", "x" * 10001])
def test_invalid_message(modules, value):
    with pytest.raises(ValidationError):
        modules["messenger"].MessageInput(text=value)


@pytest.mark.parametrize("size", [1, 10000])
def test_message_length_boundaries(modules, size):
    assert len(modules["messenger"].MessageInput(text="x" * size).text) == size


@pytest.mark.parametrize("state,expected", [("missing", 404), ("outsider", 403)])
def test_membership_errors(modules, ids, state, expected):
    service = modules["messenger"]
    service.db.dialogs.find_one.return_value = (
        None if state == "missing" else {"participant_ids": [str(ids[2])]}
    )
    with pytest.raises(HTTPException) as error:
        service.require_member(ids[0], ids[1])
    assert error.value.status_code == expected


def test_member_is_allowed(modules, ids):
    service = modules["messenger"]
    dialog = {"participant_ids": [str(ids[1])]}
    service.db.dialogs.find_one.return_value = dialog
    assert service.require_member(ids[0], ids[1]) == dialog


@pytest.mark.parametrize("kind", ["duplicate", "caller_absent"])
def test_invalid_participants_do_not_create_dialog(modules, ids, monkeypatch, kind):
    service = modules["messenger"]
    members = [ids[1], ids[1]] if kind == "duplicate" else [ids[2], ids[3]]
    check = Mock()
    monkeypatch.setattr(service, "check_user", check)
    with pytest.raises(HTTPException) as error:
        service.create(service.DialogInput(participant_ids=members), user_id=ids[1])
    assert error.value.status_code == 422
    check.assert_not_called()
    service.db.dialogs.insert_one.assert_not_called()


def test_unknown_participant_prevents_creation(modules, ids, monkeypatch):
    service = modules["messenger"]
    monkeypatch.setattr(service, "check_user", Mock(side_effect=HTTPException(404)))
    with pytest.raises(HTTPException):
        service.create(service.DialogInput(participant_ids=ids[:2]), user_id=ids[0])
    service.db.dialogs.insert_one.assert_not_called()


def test_create_checks_each_participant(modules, ids, monkeypatch):
    service = modules["messenger"]
    check = Mock()
    monkeypatch.setattr(service, "check_user", check)
    monkeypatch.setattr(service, "uuid4", lambda: ids[2])
    result = service.create(service.DialogInput(participant_ids=ids[:2]), user_id=ids[0])
    assert result == {"id": str(ids[2]), "participant_ids": list(map(str, ids[:2]))}
    assert check.call_count == 2
    assert service.db.dialogs.insert_one.call_args.args[0]["sequence"] == 0


def test_send_maps_storage_result(modules, ids, monkeypatch):
    service = modules["messenger"]
    monkeypatch.setattr(service, "require_member", Mock())
    monkeypatch.setattr(service, "uuid4", lambda: ids[2])
    service.db.dialogs.find_one_and_update.return_value = {"sequence": 7}
    result = service.send(ids[0], service.MessageInput(text=" Привет "), ids[1])
    assert result["sequence"] == 7
    assert result["sender_id"] == str(ids[1])
    assert result["text"] == "Привет"
    assert result["id"] == str(ids[2])


def test_outsider_cannot_send(modules, ids, monkeypatch):
    service = modules["messenger"]
    monkeypatch.setattr(service, "require_member", Mock(side_effect=HTTPException(403)))
    with pytest.raises(HTTPException):
        service.send(ids[0], service.MessageInput(text="Привет"), ids[1])
    service.db.messages.insert_one.assert_not_called()
    service.db.dialogs.find_one_and_update.assert_not_called()


def test_messages_page_mapping(modules, ids, monkeypatch):
    service = modules["messenger"]
    monkeypatch.setattr(service, "require_member", Mock())
    cursor = service.db.messages.find.return_value.sort.return_value
    cursor.skip.return_value.limit.return_value = [{"_id": "m-1", "text": "A"}]
    result = service.messages(ids[0], limit=2, offset=3, user_id=ids[1])
    assert result == {"items": [{"id": "m-1", "text": "A"}], "limit": 2, "offset": 3}


def test_read_missing_message_does_not_update(modules, ids, monkeypatch):
    service = modules["messenger"]
    monkeypatch.setattr(service, "require_member", Mock())
    service.db.messages.find_one.return_value = None
    with pytest.raises(HTTPException) as error:
        service.mark_read(ids[0], service.ReadInput(last_read_message_id=ids[2]), ids[1])
    assert error.value.status_code == 404
    service.db.dialogs.find_one_and_update.assert_not_called()


def test_read_returns_current_cursor_not_old_ack(modules, ids, monkeypatch):
    service = modules["messenger"]
    monkeypatch.setattr(service, "require_member", Mock())
    service.db.messages.find_one.side_effect = [{"sequence": 3}, {"_id": str(ids[3])}]
    service.db.dialogs.find_one_and_update.return_value = {"read": {str(ids[1]): 9}}
    result = service.mark_read(ids[0], service.ReadInput(last_read_message_id=ids[2]), ids[1])
    assert result == {"last_read_message_id": str(ids[3])}
    update = service.db.dialogs.find_one_and_update.call_args.args[1]
    assert update == {"$max": {f"read.{ids[1]}": 3}}
