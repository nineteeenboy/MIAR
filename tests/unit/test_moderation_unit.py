"""ModerationService: права, решения и outbox с mock PostgreSQL/RabbitMQ."""
import json
from unittest.mock import MagicMock, Mock

import pika
import pytest
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.mark.parametrize("key,allowed", [(None, False), ("wrong", False), ("test-key", True)])
def test_moderator_access(modules, monkeypatch, key, allowed):
    service = modules["moderation"]
    monkeypatch.setattr(service, "MODERATOR_KEY", "test-key")
    if allowed:
        assert service.moderator(key) is None
    else:
        with pytest.raises(HTTPException) as error:
            service.moderator(key)
        assert error.value.status_code == 403


@pytest.mark.parametrize("reason", ["", " ", "x" * 2001])
def test_invalid_report_reason(modules, ids, reason):
    with pytest.raises(ValidationError):
        modules["moderation"].ReportInput(target_type="post", target_id=ids[0], reason=reason)


def test_invalid_target_type(modules, ids):
    with pytest.raises(ValidationError):
        modules["moderation"].ReportInput(target_type="unknown", target_id=ids[0], reason="A")


@pytest.mark.parametrize("status,decision", [("open", "A"), ("accepted", " ")])
def test_invalid_decision(modules, status, decision):
    with pytest.raises(ValidationError):
        modules["moderation"].DecisionInput(status=status, decision=decision)


@pytest.mark.parametrize("target", ["user", "post", "comment"])
def test_report_routes_target_check(modules, sql_db, ids, monkeypatch, target):
    service = modules["moderation"]
    fetch, check = Mock(), Mock()
    monkeypatch.setattr(service, "fetch", fetch)
    monkeypatch.setattr(service, "check_post", check)
    sql_db(service).execute.return_value.mappings.return_value.one.return_value = {"id": ids[2]}
    data = service.ReportInput(target_type=target, target_id=ids[0], reason=" Жалоба ")
    assert service.create(data, user_id=ids[1]) == {"id": ids[2]}
    if target == "post":
        check.assert_called_once_with(ids[0])
        fetch.assert_not_called()
    else:
        assert str(ids[0]) in fetch.call_args.args[0]
        assert ("/users/" if target == "user" else "/internal/comments/") in fetch.call_args.args[0]
    assert sql_db(service).execute.call_args.args[1]["reason"] == "Жалоба"


def test_unknown_target_does_not_create_report(modules, sql_db, ids, monkeypatch):
    service = modules["moderation"]
    monkeypatch.setattr(service, "check_post", Mock(side_effect=HTTPException(404)))
    data = service.ReportInput(target_type="post", target_id=ids[0], reason="Жалоба")
    with pytest.raises(HTTPException):
        service.create(data, user_id=ids[1])
    sql_db(service).execute.assert_not_called()


def test_read_without_identity_is_forbidden(modules, sql_db, ids):
    service = modules["moderation"]
    with pytest.raises(HTTPException) as error:
        service.read(ids[0], x_user_id=None, x_moderator_key=None)
    assert error.value.status_code == 403
    sql_db(service).execute.assert_not_called()


@pytest.mark.parametrize("caller,expected", [(1, None), (2, 403)])
def test_report_owner_access(modules, sql_db, ids, monkeypatch, caller, expected):
    service = modules["moderation"]
    monkeypatch.setattr(service, "actor", Mock())
    row = {"id": ids[0], "reporter_id": ids[1], "status": "open"}
    sql_db(service).execute.return_value.mappings.return_value.first.return_value = row
    if expected is None:
        assert service.read(ids[0], ids[caller], None) == row
    else:
        with pytest.raises(HTTPException) as error:
            service.read(ids[0], ids[caller], None)
        assert error.value.status_code == expected


def test_moderator_reads_missing_report(modules, sql_db, ids, monkeypatch):
    service = modules["moderation"]
    monkeypatch.setattr(service, "MODERATOR_KEY", "test-key")
    sql_db(service).execute.return_value.mappings.return_value.first.return_value = None
    with pytest.raises(HTTPException) as error:
        service.read(ids[0], None, "test-key")
    assert error.value.status_code == 404


def test_reports_page(modules, sql_db):
    service = modules["moderation"]
    sql_db(service).execute.return_value.mappings.return_value = [{"status": "open"}]
    result = service.reports(status="open", limit=2, offset=3)
    assert result == {"items": [{"status": "open"}], "limit": 2, "offset": 3}


@pytest.mark.parametrize("state,expected", [("missing", 404), ("conflict", 409), ("repeat", None)])
def test_decision_state_policy(modules, sql_db, ids, monkeypatch, state, expected):
    service = modules["moderation"]
    publish = Mock()
    monkeypatch.setattr(service, "dispatch_pending", publish)
    row = None if state == "missing" else {
        "id": ids[0], "status": "accepted", "decision": "Подтверждено",
    }
    sql_db(service).execute.return_value.mappings.return_value.first.return_value = row
    data = service.DecisionInput(
        status="rejected" if state == "conflict" else "accepted", decision="Подтверждено",
    )
    if expected is None:
        assert service.decide(ids[0], data) == row
    else:
        with pytest.raises(HTTPException) as error:
            service.decide(ids[0], data)
        assert error.value.status_code == expected
    assert sql_db(service).execute.call_count == 1  # Нет изменения и нового outbox.
    publish.assert_not_called()


@pytest.mark.parametrize("broker_down", [False, True])
def test_decision_creates_event_before_publish(modules, sql_db, ids, monkeypatch, broker_down):
    service = modules["moderation"]
    db = sql_db(service)
    db.execute.return_value.mappings.return_value.first.return_value = {
        "status": "open", "target_type": "post", "target_id": ids[1],
    }
    db.execute.return_value.mappings.return_value.one.return_value = {"status": "accepted"}
    monkeypatch.setattr(service, "uuid4", lambda: ids[2])

    def publish_after_transaction():
        assert service.engine.begin.return_value.__exit__.called
        if broker_down:
            raise pika.exceptions.AMQPConnectionError()

    monkeypatch.setattr(service, "dispatch_pending", publish_after_transaction)
    result = service.decide(
        ids[0], service.DecisionInput(status="accepted", decision="Подтверждено"),
    )
    assert result["status"] == "accepted"
    event = json.loads(db.execute.call_args_list[2].args[1]["p"])
    assert event["event_id"] == str(ids[2])
    assert event["target_id"] == str(ids[1])
    assert event["event"] == "moderation.decision.created"


def test_empty_outbox_does_not_connect(modules, sql_db, monkeypatch):
    service = modules["moderation"]
    sql_db(service).execute.return_value.mappings.return_value.all.return_value = []
    connection = Mock()
    monkeypatch.setattr(service.pika, "BlockingConnection", connection)
    assert service.dispatch_pending() == 0
    connection.assert_not_called()


def test_dispatch_persistent_event(modules, sql_db, ids, monkeypatch):
    service = modules["moderation"]
    payload = {"event_id": str(ids[0]), "event": "moderation.decision.created"}
    sql_db(service).execute.return_value.mappings.return_value.all.return_value = [
        {"id": ids[0], "payload": payload},
    ]
    connection = MagicMock()
    monkeypatch.setattr(service.pika, "BlockingConnection", connection)
    channel = connection.return_value.__enter__.return_value.channel.return_value
    assert service.dispatch_pending() == 1
    channel.confirm_delivery.assert_called_once()
    channel.queue_declare.assert_called_once_with(service.EVENT_QUEUE, durable=True)
    call = channel.basic_publish.call_args
    assert json.loads(call.args[2]) == payload
    assert call.args[3].delivery_mode == 2
    assert call.kwargs["mandatory"] is True
    assert sql_db(service).execute.call_args.args[1] == {"id": ids[0]}


def test_dispatch_failure_does_not_mark_sent(modules, sql_db, ids, monkeypatch):
    service = modules["moderation"]
    sql_db(service).execute.return_value.mappings.return_value.all.return_value = [
        {"id": ids[0], "payload": {"event_id": str(ids[0])}},
    ]
    monkeypatch.setattr(
        service.pika, "BlockingConnection", Mock(side_effect=pika.exceptions.AMQPConnectionError()),
    )
    with pytest.raises(pika.exceptions.AMQPConnectionError):
        service.dispatch_pending()
    assert sql_db(service).execute.call_count == 1  # Только SELECT, без UPDATE sent.
