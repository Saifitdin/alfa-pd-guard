"""Несколько воркеров с хранилищем в памяти — отказ на старте, а не тихая порча."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pdguard.main import create_app
from pdguard.settings import Settings

from conftest import ROOT


def _settings(backend: str) -> Settings:
    return Settings(config_dir=ROOT / "config", data_dir=ROOT / "data",
                    log_format="plain", store_backend=backend)


def test_multiple_workers_with_memory_store_refuse_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Так масштабируют на Render: WEB_CONCURRENCY=2 без Redis — демаскирование
    возвращало бы чужие результаты без единой ошибки в логах."""
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    with pytest.raises(RuntimeError, match="PDGUARD_STORE_BACKEND=memory"):
        with TestClient(create_app(_settings("memory"))):
            pass


def test_single_worker_with_memory_store_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    with TestClient(create_app(_settings("memory"))) as client:
        assert client.get("/health").json()["status"] == "ok"


def test_garbage_web_concurrency_is_treated_as_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEB_CONCURRENCY", "auto")
    with TestClient(create_app(_settings("memory"))) as client:
        assert client.get("/health").status_code == 200
