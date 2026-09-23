"""Несколько воркеров с хранилищем в памяти — отказ на старте, а не тихая порча."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pdguard.main import create_app
from pdguard.settings import Settings

from conftest import ROOT


UNREACHABLE_REDIS = "redis://127.0.0.1:1/0"  # порт 1 закрыт — отказ соединения мгновенный


def _settings(backend: str, redis_url: str | None = None) -> Settings:
    return Settings(config_dir=ROOT / "config", data_dir=ROOT / "data",
                    log_format="plain", store_backend=backend, redis_url=redis_url)


def test_multiple_workers_with_unreachable_redis_refuse_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настроен Redis, но он недоступен: откат на память в каждом воркере дал бы
    те же чужие результаты — на четырёх воркерах три четверти восстановлений
    возвращали маску. Отказ на старте вместо этого."""
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    monkeypatch.setenv("PDGUARD_STORE_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    with pytest.raises(RuntimeError, match="Redis по PDGUARD_REDIS_URL недоступен"):
        with TestClient(create_app(_settings("redis", UNREACHABLE_REDIS))):
            pass


def test_single_worker_with_unreachable_redis_degrades_and_health_tells_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Один процесс — откат допустим, но /health показывает фактическое хранилище."""
    monkeypatch.setenv("WEB_CONCURRENCY", "1")
    monkeypatch.setenv("PDGUARD_STORE_KEY", "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
    with TestClient(create_app(_settings("redis", UNREACHABLE_REDIS))) as client:
        body = client.get("/health").json()
        assert body["store_backend"] == "memory"
        assert body["store_configured"] == "redis"
        assert body["workers"] == 1


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
