"""Самопроверка контракта: симулятор проверяющей системы из Приложения B ТЗ.

Повторяет правила проверки дословно:

* по каждому элементу датасета — пара запросов с одним ``payload_id``:
  прямой (маскирование) и обратный (демаскирование);
* таймаут одного запроса — 10 секунд, ответ дольше считается неответом;
* до 2 ретраев на запрос (итого 3 попытки);
* ``429`` невалидным не считается, счётчик подряд идущих невалидных не
  сбрасывает, учитывается ``Retry-After``;
* после 5 невалидных ответов подряд прогон останавливается.

Дополнительно проверяется то, ради чего модуль существует: что в маске не
осталось исходных персональных данных.

Запуск:
    python bench/contract_check.py --url http://localhost:8000
    python bench/contract_check.py --url https://process-test.holydev.space   # эталон
"""

from __future__ import annotations

import argparse
import json
import ssl
import statistics
import time
import urllib.error
import urllib.request
import uuid

TIMEOUT_SECONDS = 10.0
MAX_ATTEMPTS = 3
STOP_AFTER_INVALID = 5

#: (исходная строка, фрагменты, которых не должно остаться в маске)
DATASET: list[tuple[str, tuple[str, ...]]] = [
    ("Клиент Иванов Иван Иванович, паспорт 4509 123456",
     ("Иванов Иван Иванович", "4509 123456")),
    ("Телефон +7 916 123-45-67, почта ivan.petrov@example.com",
     ("+7 916 123-45-67", "ivan.petrov@example.com")),
    ("Карта 4276 3800 1234 5679, CVV 123, держатель IVAN PETROV",
     ("4276 3800 1234 5679", "IVAN PETROV")),
    ("ИНН 500100732259, дата рождения 15.03.1985",
     ("500100732259", "15.03.1985")),
    ("Адрес: 344002, г. Ростов-на-Дону, ул. Большая Садовая, д. 15, кв. 42",
     ("Большая Садовая", "344002")),
    ("Водительское удостоверение 7799 456123, гражданство: Российская Федерация",
     ("7799 456123",)),
    ("Паспорт серия 4517 номер 998877, выдан ГУ МВД России по г. Москве, код подразделения 770-053",
     ("4517", "998877", "770-053")),
    ("Место рождения: г. Волгоград, дата рождения пятнадцатого марта 1985 года",
     ("Волгоград",)),
    ("СНИЛС 112-233-445 95, заявка от клиента Петровой Анны Сергеевны",
     ("112-233-445 95", "Петровой Анны Сергеевны")),
    ("Поэт Александр Пушкин родился в Москве", ()),
]


class Stop(Exception):
    """Пять невалидных ответов подряд — прогон останавливается."""


class Checker:
    def __init__(self, url: str) -> None:
        self.url = url.rstrip("/") + "/process"
        self.context = ssl.create_default_context()
        self.context.check_hostname = False
        self.context.verify_mode = ssl.CERT_NONE  # проверка идёт с ssl_verify=false
        self.latencies: list[float] = []
        self.consecutive_invalid = 0
        self.throttled = 0
        self.retries = 0

    def _once(self, payload: str, payload_id: str) -> tuple[int, str | None, float | None]:
        body = json.dumps({"payload": payload, "payload_id": payload_id}, ensure_ascii=False)
        request = urllib.request.Request(
            self.url, data=body.encode(), headers={"Content-Type": "application/json"}
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=self.context) as response:
                elapsed = time.perf_counter() - started
                data = json.loads(response.read())
                return response.status, data.get("result"), elapsed
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After")
            if exc.code == 429:
                self.throttled += 1
                time.sleep(float(retry_after) if retry_after else 1.0)
            return exc.code, None, None
        except Exception:
            return 0, None, None

    def request(self, payload: str, payload_id: str) -> str | None:
        """Запрос с ретраями и учётом правила остановки."""
        for attempt in range(MAX_ATTEMPTS):
            status, result, elapsed = self._once(payload, payload_id)
            if status == 200 and isinstance(result, str):
                if elapsed is not None:
                    self.latencies.append(elapsed)
                self.consecutive_invalid = 0
                return result
            if status != 429:
                self.consecutive_invalid += 1
                if self.consecutive_invalid >= STOP_AFTER_INVALID:
                    raise Stop(f"{STOP_AFTER_INVALID} невалидных ответов подряд")
            if attempt < MAX_ATTEMPTS - 1:
                self.retries += 1
        return None


def run(url: str, rounds: int) -> int:
    checker = Checker(url)
    masked_ok = restored_ok = leaked = failed = 0
    total = 0

    print(f"Проверка контракта: {url}")
    print(f"Элементов: {len(DATASET)} × {rounds} кругов, таймаут {TIMEOUT_SECONDS:.0f} с, "
          f"до {MAX_ATTEMPTS} попыток\n")

    try:
        for _ in range(rounds):
            for original, secrets in DATASET:
                total += 1
                payload_id = uuid.uuid4().hex

                mask = checker.request(original, payload_id)
                if mask is None:
                    failed += 1
                    print(f"  НЕТ ОТВЕТА на маскировании: {original[:50]}")
                    continue
                masked_ok += 1

                still_visible = [s for s in secrets if s in mask]
                if still_visible:
                    leaked += 1
                    print(f"  УТЕЧКА В МАСКЕ: {still_visible} -> {mask[:70]}")

                restored = checker.request(mask, payload_id)
                if restored is None:
                    failed += 1
                    print(f"  НЕТ ОТВЕТА на демаскировании: {mask[:50]}")
                elif restored == original:
                    restored_ok += 1
                else:
                    print(f"  НЕ ВОССТАНОВЛЕНО:\n    ждали: {original}\n    пришло: {restored}")
    except Stop as stop:
        print(f"\nПРОГОН ОСТАНОВЛЕН: {stop}")

    print("\n" + "-" * 62)
    print(f"Пар обработано:          {total}")
    print(f"Маскирование ответило:   {masked_ok}/{total}")
    print(f"Демаскирование точное:   {restored_ok}/{total}"
          f"  ({restored_ok / total * 100:.1f}%)" if total else "")
    print(f"Утечек ПД в маске:       {leaked}")
    print(f"Без ответа:              {failed}")
    print(f"Ответов 429:             {checker.throttled}")
    print(f"Ретраев:                 {checker.retries}")
    if checker.latencies:
        ordered = sorted(checker.latencies)
        p95 = ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)]
        print(f"Latency avg / p95:       {statistics.mean(ordered) * 1000:.1f} / {p95 * 1000:.1f} мс")

    passed = failed == 0 and leaked == 0 and restored_ok == total and total > 0
    print("-" * 62)
    print("РЕЗУЛЬТАТ: контракт пройден" if passed else "РЕЗУЛЬТАТ: есть замечания (см. выше)")
    return 0 if passed else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--rounds", type=int, default=3)
    raise SystemExit(run(*vars(parser.parse_args()).values()))
