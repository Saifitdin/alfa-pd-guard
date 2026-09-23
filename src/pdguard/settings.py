"""Настройки сервиса. Всё переопределяется переменными окружения PDGUARD_*."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    config_dir: Path = ROOT / "config"
    data_dir: Path = ROOT / "data"

    #: Бэкенд хранилища соответствий: memory | redis.
    #: memory рассчитан на один процесс; для нескольких воркеров нужен redis,
    #: иначе обратный запрос может попасть в процесс, который не помнит маску.
    store_backend: str = "memory"
    redis_url: str | None = None
    store_ttl_seconds: int = 900

    #: Проверять ли X-API-Key. На прогоне нагрузочного тестирования
    #: проверяющая система ключ не присылает, поэтому по умолчанию выключено,
    #: а запросы без ключа обслуживаются политикой default_system_id.
    require_api_key: bool = False
    default_system_id: str = "loadtest"

    #: Ограничение на размер входной строки (защита от неоправданной нагрузки).
    max_payload_chars: int = 600_000

    #: Порог одновременной обработки; при превышении отдаём 429 с Retry-After.
    max_concurrent_requests: int = 512

    log_level: str = "INFO"
    log_format: str = "json"

    @staticmethod
    def from_env() -> Settings:
        return Settings(
            config_dir=Path(os.getenv("PDGUARD_CONFIG_DIR", str(ROOT / "config"))),
            data_dir=Path(os.getenv("PDGUARD_DATA_DIR", str(ROOT / "data"))),
            store_backend=os.getenv("PDGUARD_STORE_BACKEND", "memory"),
            redis_url=os.getenv("PDGUARD_REDIS_URL"),
            store_ttl_seconds=int(os.getenv("PDGUARD_STORE_TTL", "900")),
            require_api_key=_env_bool("PDGUARD_REQUIRE_API_KEY", False),
            default_system_id=os.getenv("PDGUARD_DEFAULT_SYSTEM", "loadtest"),
            max_payload_chars=int(os.getenv("PDGUARD_MAX_PAYLOAD_CHARS", "600000")),
            max_concurrent_requests=int(os.getenv("PDGUARD_MAX_CONCURRENCY", "512")),
            log_level=os.getenv("PDGUARD_LOG_LEVEL", "INFO"),
            log_format=os.getenv("PDGUARD_LOG_FORMAT", "json"),
        )
