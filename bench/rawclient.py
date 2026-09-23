"""Минимальный генератор нагрузки на голых asyncio-сокетах.

httpx и ApacheBench на macOS упираются в собственные накладные расходы и
исчерпание эфемерных портов раньше, чем сервис — в процессор. Этот клиент
держит постоянные keep-alive соединения и шлёт запросы подряд, поэтому
измеряет именно потолок сервиса.

Запуск:  python bench/rawclient.py --connections 64 --duration 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

PAYLOAD = (
    "Клиент Иванов Иван Иванович, паспорт 4509 123456, "
    "телефон +7 916 123-45-67, карта 4276 3800 1234 5679"
)


async def connection_worker(
    host: str, port: int, path: str, deadline: float, latencies: list[float], counters: dict[str, int]
) -> None:
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError:
        counters["connect_errors"] += 1
        return

    index = 0
    try:
        while time.monotonic() < deadline:
            body = json.dumps({"text": PAYLOAD, "payload_id": f"raw-{id(writer)}-{index}"})
            index += 1
            raw = body.encode()
            request = (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(raw)}\r\n"
                f"Connection: keep-alive\r\n\r\n"
            ).encode() + raw

            started = time.perf_counter()
            writer.write(request)
            await writer.drain()

            headers = await reader.readuntil(b"\r\n\r\n")
            length = 0
            chunked = False
            for line in headers.split(b"\r\n"):
                lowered = line.lower()
                if lowered.startswith(b"content-length:"):
                    length = int(line.split(b":")[1])
                elif lowered.startswith(b"transfer-encoding:") and b"chunked" in lowered:
                    chunked = True
            if chunked:
                while True:
                    size_line = await reader.readuntil(b"\r\n")
                    size = int(size_line.strip() or b"0", 16)
                    await reader.readexactly(size + 2)
                    if size == 0:
                        break
            elif length:
                await reader.readexactly(length)

            latencies.append(time.perf_counter() - started)
            counters["ok"] += 1
    except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
        counters["io_errors"] += 1
    finally:
        writer.close()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(int(len(ordered) * fraction), len(ordered) - 1)]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8077)
    parser.add_argument("--path", default="/v1/mask")
    parser.add_argument("--connections", type=int, default=64)
    parser.add_argument("--duration", type=float, default=20.0)
    args = parser.parse_args()

    latencies: list[float] = []
    counters = {"ok": 0, "io_errors": 0, "connect_errors": 0}

    started = time.monotonic()
    deadline = started + args.duration
    await asyncio.gather(
        *(
            connection_worker(args.host, args.port, args.path, deadline, latencies, counters)
            for _ in range(args.connections)
        )
    )
    wall = time.monotonic() - started

    print(f"Соединений:      {args.connections}, длительность {wall:.1f} с")
    print(f"Запросов:        {counters['ok']}")
    print(f"RPS:             {counters['ok'] / wall:.0f}")
    print(f"Ошибок ввода:    {counters['io_errors']}, соединений не открылось: {counters['connect_errors']}")
    if latencies:
        print(f"Latency avg:     {statistics.mean(latencies) * 1000:.2f} мс")
        print(f"Latency p95:     {percentile(latencies, 0.95) * 1000:.2f} мс")
        print(f"Latency p99:     {percentile(latencies, 0.99) * 1000:.2f} мс")


if __name__ == "__main__":
    asyncio.run(main())
