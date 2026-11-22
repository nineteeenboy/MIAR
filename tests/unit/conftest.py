"""Изоляция unit-тестов: никаких серверов, контейнеров и настоящих БД."""
import importlib
import importlib.util
import socket
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
NAMES = ("users", "content", "interaction", "community", "messenger", "moderation")


@pytest.fixture(autouse=True)
def forbid_network():
    """Ошибка теста при любой попытке открыть сетевое соединение."""
    error = AssertionError("Network access is forbidden in unit tests")
    with (
        patch.object(socket.socket, "connect", side_effect=error),
        patch.object(socket.socket, "connect_ex", side_effect=error),
        patch("socket.create_connection", side_effect=error),
    ):
        yield


@pytest.fixture(scope="session")
def isolated_modules():
    """Загружаем исходные app.py под отдельными именами для этого набора.

    Конструкторы хранилищ подменены ДО импорта: даже MongoDB не запускает
    фоновые подключения. Обычные модули интеграционных тестов не меняются.
    """
    loaded = {}
    with patch("sqlalchemy.create_engine"), patch("pymongo.MongoClient"):
        for name in NAMES:
            alias = f"_unit_{name}_app"
            path = ROOT / f"{name}_service" / "app.py"
            spec = importlib.util.spec_from_file_location(alias, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[alias] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    loaded["common"] = importlib.import_module("common.api")
    yield loaded
    for name in NAMES:
        sys.modules.pop(f"_unit_{name}_app", None)


@pytest.fixture
def modules(isolated_modules, monkeypatch):
    """Каждый тест получает новые mock-хранилища без общей истории вызовов."""
    for name in NAMES:
        module = isolated_modules[name]
        if hasattr(module, "engine"):
            engine = MagicMock(name=f"{name}_engine")
            connection = MagicMock(name=f"{name}_connection")
            engine.begin.return_value.__enter__.return_value = connection
            engine.connect.return_value.__enter__.return_value = connection
            monkeypatch.setattr(module, "engine", engine)
        if name == "content":
            monkeypatch.setattr(module, "posts", MagicMock(name="posts"))
        if name == "messenger":
            monkeypatch.setattr(module, "db", MagicMock(name="messenger_db"))
    return isolated_modules


@pytest.fixture
def sql_db():
    return lambda module: module.engine.connect.return_value.__enter__.return_value


@pytest.fixture
def ids():
    return [UUID(int=value) for value in range(1, 6)]
