"""Замер латентности ядра без HTTP-слоя.

Показывает чистую стоимость идентификации и маскирования: именно она
определяет, какой RPS выдержит сервис на одном ядре.

Запуск:  python bench/bench_pipeline.py [--iterations 2000]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdguard.core.pipeline import MaskingPipeline  # noqa: E402

SAMPLES = [
    "Клиент Иванов Иван Иванович, паспорт 4509 123456",
    "Телефон +7 916 123-45-67, почта ivan.petrov@example.com",
    "Карта 4276 3800 1234 5679, CVV 123, держатель IVAN PETROV",
    "ИНН 500100732259, СНИЛС 112-233-445 95, дата рождения 15 марта 1985 года",
    "Адрес регистрации: 344002, г. Ростов-на-Дону, ул. Большая Садовая, д. 15, кв. 42",
    "Поэт Александр Пушкин родился в Москве — персональных данных здесь нет",
    (
        "Клиент Петрова Анна Сергеевна, дата рождения 12.07.1990, паспорт серия 4517 "
        "номер 998877, выдан ГУ МВД России по г. Санкт-Петербургу, код подразделения "
        "780-012, водительское удостоверение 7799 456123, адрес: 190000, г. Санкт-Петербург, "
        "ул. Малая Морская, д. 10, кв. 5, ИНН 500100732259, карта 4276 3800 1234 5679"
    ),
]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return ordered[index]


def run(iterations: int) -> None:
    pipeline = MaskingPipeline(ROOT / "config", ROOT / "data")
    policy = pipeline.policies.by_id("loadtest")

    # Прогрев: первый вызов оплачивает компиляцию и прогрев кэшей.
    for sample in SAMPLES:
        pipeline.mask(sample, policy)

    latencies: list[float] = []
    chars = 0
    started = time.perf_counter()
    for index in range(iterations):
        sample = SAMPLES[index % len(SAMPLES)]
        chars += len(sample)
        call_started = time.perf_counter()
        pipeline.mask(sample, policy)
        latencies.append(time.perf_counter() - call_started)
    wall = time.perf_counter() - started

    print(f"Итераций:            {iterations}")
    print(f"Средняя длина:       {chars // iterations} символов")
    print(f"Latency avg:         {statistics.mean(latencies) * 1000:.3f} мс")
    print(f"Latency p50:         {percentile(latencies, 0.50) * 1000:.3f} мс")
    print(f"Latency p95:         {percentile(latencies, 0.95) * 1000:.3f} мс")
    print(f"Latency p99:         {percentile(latencies, 0.99) * 1000:.3f} мс")
    print(f"Пропускная (1 ядро): {iterations / wall:.0f} запросов/с")
    print(f"Токенов в секунду:   {chars / 4 / wall:.0f} TPS")


def run_large(size_tokens: int) -> None:
    """Проверка требования «до 100 000 токенов»."""
    pipeline = MaskingPipeline(ROOT / "config", ROOT / "data")
    policy = pipeline.policies.by_id("loadtest")

    block = SAMPLES[-1] + " "
    text = (block * ((size_tokens * 4) // len(block) + 1))[: size_tokens * 4]

    started = time.perf_counter()
    result = pipeline.mask(text, policy)
    elapsed = time.perf_counter() - started
    print(f"\nКрупный текст:       ~{size_tokens} токенов ({len(text)} символов)")
    print(f"Время обработки:     {elapsed * 1000:.0f} мс")
    print(f"Найдено сущностей:   {len(result.entities)}")
    print(f"Типов ПД:            {len(result.masked_types)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--large-tokens", type=int, default=100_000)
    args = parser.parse_args()
    run(args.iterations)
    run_large(args.large_tokens)
