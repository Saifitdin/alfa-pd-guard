"""Структурное логирование.

Главное правило модуля: в логи не попадают ни исходный текст, ни значения
персональных данных. Логируются только типы ПД, их количество, длины и тайминги —
этого достаточно для разбора инцидентов и ровно столько, чтобы лог сам не стал
источником утечки.

На случай ошибки разработчика стоит предохранитель: PDRedactionFilter вырезает
из любых сообщений то, что похоже на карту, телефон, email, паспорт или ИНН.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

#: Предохранитель: шаблоны, которые вырезаются из любого лог-сообщения.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"), "<email>"),
    (re.compile(r"(?<!\d)(?:\+7|8)[\s\-(]?\d{3}[\s\-)]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"), "<phone>"),
    (re.compile(r"(?<!\d)\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)"), "<card>"),
    (re.compile(r"(?<!\d)\d{4}[\s\-]?\d{6}(?!\d)"), "<docnum>"),
    (re.compile(r"(?<!\d)\d{10,12}(?!\d)"), "<idnum>"),
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class PDRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(
                redact(arg) if isinstance(arg, str) else arg for arg in record.args
            )
        return True


_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime", "message", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if fmt == "json"
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    handler.addFilter(PDRedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn пишет в свои логгеры — заворачиваем их в тот же обработчик.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
