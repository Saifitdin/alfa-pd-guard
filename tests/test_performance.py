"""Сторожевые тесты производительности.

Пороги заданы с большим запасом относительно измеренных значений: задача —
поймать алгоритмическую регрессию (например, возврат к квадратичному
разрешению пересечений), а не померить машину.
"""

from __future__ import annotations

import time

import pytest

SHORT = "Клиент Иванов Иван Иванович, паспорт 4509 123456, карта 4276 3800 1234 5679"

BLOCK = (
    "Клиент Петрова Анна Сергеевна, паспорт серия 4517 номер 998877, "
    "адрес: 190000, г. Санкт-Петербург, ул. Малая Морская, д. 10, кв. 5, "
    "ИНН 500100732259, карта 4276 3800 1234 5679. "
)


def test_short_payload_latency(pipeline, policy) -> None:
    """Типовая строка обрабатывается заметно быстрее целевой 1 секунды."""
    for _ in range(50):
        pipeline.mask(SHORT, policy)

    started = time.perf_counter()
    for _ in range(500):
        pipeline.mask(SHORT, policy)
    average = (time.perf_counter() - started) / 500

    assert average < 0.02, f"средняя латентность выросла до {average * 1000:.2f} мс"


@pytest.mark.parametrize("tokens", [100_000])
def test_large_payload_is_processed(pipeline, policy, tokens: int) -> None:
    """Требование ТЗ: до 100 000 токенов без падения."""
    text = (BLOCK * ((tokens * 4) // len(BLOCK) + 1))[: tokens * 4]

    started = time.perf_counter()
    result = pipeline.mask(text, policy)
    elapsed = time.perf_counter() - started

    assert result.entities, "на большом тексте не найдено ни одной сущности"
    assert "4276 3800 1234 5679" not in result.masked_text
    assert elapsed < 5.0, f"обработка 100k токенов заняла {elapsed:.1f} с"


def test_overlap_resolution_scales(pipeline) -> None:
    """Прямая проверка, что разрешение пересечений не квадратично."""
    from pdguard.core.pipeline import resolve_overlaps
    from pdguard.core.entities import Entity, PDType

    def measure(count: int) -> float:
        entities = [
            Entity(PDType.PHONE, i * 10, i * 10 + 8, "x", 0.9, "synthetic")
            for i in range(count)
        ]
        started = time.perf_counter()
        resolve_overlaps(entities)
        return time.perf_counter() - started

    small = measure(2_000)
    large = measure(20_000)

    # При квадратичном поведении рост был бы стократным.
    assert large < max(small * 30, 0.5), f"рост {large / max(small, 1e-9):.0f}x при 10x данных"
