"""HTTP-нагрузка по логике проверяющей системы.

Повторяет сценарий из Приложения B: на каждый элемент датасета — пара запросов
с одним payload_id (маскирование, затем демаскирование), замер латентности и
проверка того, что обратный проход вернул исходную строку.

Запуск:
    python bench/loadtest.py --url http://127.0.0.1:8000 --duration 30 --concurrency 64
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
import uuid

import httpx

DATASET = [
    "Клиент Иванов Иван Иванович, паспорт 4509 123456",
    "Телефон +7 916 123-45-67, почта ivan.petrov@example.com",
    "Карта 4276 3800 1234 5679, CVV 123, держатель IVAN PETROV",
    "ИНН 500100732259, дата рождения 15 марта 1985 года",
    "Адрес: 344002, г. Ростов-на-Дону, ул. Большая Садовая, д. 15, кв. 42",
    "Водительское удостоверение 7799 456123, гражданство: Российская Федерация",
    "Поэт Александр Пушкин родился в Москве",
    (
        "Клиент Петрова Анна Сергеевна, дата рождения 12.07.1990, паспорт серия 4517 "
        "номер 998877, выдан ГУ МВД России по г. Санкт-Петербургу, код подразделения 780-012"
    ),
]


class Results:
    def __init__(self) -> None:
        self.latencies: list[float] = []
        self.ok = 0
        self.mismatch = 0
        self.throttled = 0
        self.errors = 0


async def worker(
    client: httpx.AsyncClient, url: str, deadline: float, results: Results
) -> None:
    index = 0
    while time.monotonic() < deadline:
        original = DATASET[index % len(DATASET)]
        index += 1
        payload_id = uuid.uuid4().hex

        try:
            started = time.perf_counter()
            masked = await client.post(
                url, json={"payload": original, "payload_id": payload_id}
            )
            results.latencies.append(time.perf_counter() - started)
            if masked.status_code == 429:
                results.throttled += 1
                continue
            masked.raise_for_status()
            mask_text = masked.json()["result"]

            started = time.perf_counter()
            restored = await client.post(
                url, json={"payload": mask_text, "payload_id": payload_id}
            )
            results.latencies.append(time.perf_counter() - started)
            if restored.status_code == 429:
                results.throttled += 1
                continue
            restored.raise_for_status()

            if restored.json()["result"] == original:
                results.ok += 1
            else:
                results.mismatch += 1
        except Exception:  # noqa: BLE001 - в нагрузочном скрипте считаем, а не падаем
            results.errors += 1


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--concurrency", type=int, default=64)
    args = parser.parse_args()

    endpoint = args.url.rstrip("/") + "/process"
    results = Results()
    limits = httpx.Limits(max_connections=args.concurrency * 2, max_keepalive_connections=args.concurrency * 2)

    started = time.monotonic()
    deadline = started + args.duration
    async with httpx.AsyncClient(timeout=10.0, limits=limits, verify=False) as client:
        await asyncio.gather(
            *(worker(client, endpoint, deadline, results) for _ in range(args.concurrency))
        )
    wall = time.monotonic() - started

    total_requests = len(results.latencies)
    print(f"Длительность:        {wall:.1f} с, конкурентность {args.concurrency}")
    print(f"Запросов:            {total_requests}  ({total_requests / wall:.0f} RPS)")
    print(f"Успешных пар:        {results.ok}")
    print(f"Несовпадений:        {results.mismatch}")
    print(f"429 (throttle):      {results.throttled}")
    print(f"Ошибок:              {results.errors}")
    if results.latencies:
        print(f"Latency avg:         {statistics.mean(results.latencies) * 1000:.2f} мс")
        print(f"Latency p95:         {percentile(results.latencies, 0.95) * 1000:.2f} мс")
        print(f"Latency p99:         {percentile(results.latencies, 0.99) * 1000:.2f} мс")
    pairs = results.ok + results.mismatch
    if pairs:
        print(f"Точность roundtrip:  {results.ok / pairs * 100:.2f}%")


if __name__ == "__main__":
    asyncio.run(main())
