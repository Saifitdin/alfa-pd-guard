"""Модуль безопасности персональных данных.

Здесь же подхватывается локальный ``.env``: он должен быть прочитан раньше
любого другого модуля пакета, потому что настройки и адаптер LLM читают
окружение при первом обращении. Внешняя зависимость для этого не нужна.
Уже заданные переменные окружения имеют приоритет над файлом.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    candidates = (
        Path(os.getenv("PDGUARD_ENV_FILE", "")),
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    )
    for path in candidates:
        if not path or not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value
        break


_load_dotenv()
