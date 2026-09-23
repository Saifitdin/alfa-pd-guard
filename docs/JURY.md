# Инструкция по проверке

Всё проверяется через `curl` за несколько минут. Сервис должен быть запущен:

```bash
docker compose up --build          # либо
PYTHONPATH=src .venv/bin/python -m uvicorn pdguard.main:app --port 8000
```

Готовый скрипт со всеми сценариями сразу: `./demo/demo.sh`

---

## 1. Контракт: маскирование и демаскирование по `payload_id`

```bash
curl -s -X POST http://localhost:8000/process -H 'Content-Type: application/json' \
  -d '{"payload":"Клиент Иванов Иван Иванович, паспорт 4509 123456","payload_id":"j-1"}'
# {"result":"Клиент И. И. И., паспорт 45** ****56"}

curl -s -X POST http://localhost:8000/process -H 'Content-Type: application/json' \
  -d '{"payload":"Клиент И. И. И., паспорт 45** ****56","payload_id":"j-1"}'
# {"result":"Клиент Иванов Иван Иванович, паспорт 4509 123456"}
```

Проверяется: маска, обратное преобразование, совпадение с оригиналом посимвольно.

**Идемпотентность.** Повторите первый запрос ещё раз — вернётся та же маска,
а не результат демаскирования. Это важно для ретраев проверяющей системы.

---

## 2. Полнота: все 17 типов в одном тексте

```bash
curl -s -X POST http://localhost:8000/v1/mask -H 'Content-Type: application/json' -d '{
 "text":"Клиент Петрова Анна Сергеевна, дата рождения 12.07.1990, место рождения: г. Тверь, гражданство: Российская Федерация, паспорт серия 4517 номер 998877, выдан ГУ МВД России по г. Санкт-Петербургу, код подразделения 780-012, дата выдачи 20.01.2015, водительское удостоверение 7799 456123, адрес: 190000, г. Санкт-Петербург, ул. Малая Морская, д. 10, кв. 5, ИНН 500100732259, телефон +7 916 123-45-67, почта anna@example.com, карта 4276 3800 1234 5679, держатель ANNA PETROVA, CVV 123, пин-код 4321"
}'
```

В ответе:
* `masked_text` — обработанный текст;
* `detected` — список находок: тип, человекочитаемое название, позиции,
  уверенность и сработавший детектор;
* `masked_types` — перечень типов ПД, найденных в этом запросе.

---

## 3. Ловушки: чего маскировать не нужно

```bash
curl -s -X POST http://localhost:8000/v1/mask -H 'Content-Type: application/json' -d '{
 "text":"Поэт Александр Пушкин родился в Москве. Отделение банка: г. Москва, ул. Каланчевская, д. 27. Номер заказа 1234 5678 1234 5670."
}'
```

Ожидается пустой `detected`: классик, адрес офиса и номер заказа персональными
данными клиента не являются. Номер заказа, к слову, проходит алгоритм Луна —
отсекается по диапазону BIN.

А вот однофамилец в клиентском контексте распознаётся:

```bash
curl -s -X POST http://localhost:8000/v1/mask -H 'Content-Type: application/json' \
  -d '{"text":"Клиент Пушкин Андрей Викторович, паспорт 4509 123456"}'
```

---

## 4. Вариации написания

```bash
for t in "паспорт серия 4509 номер 123456" \
         "ПАСПОРТ 4509 123456" \
         "дата рождения 15 марта 1985 года" \
         "дата рождения: пятнадцатого марта 1985 года" \
         "дата рождения 03.15.1985" \
         "дата рождения 1985-03-15"; do
  echo -n "$t  ->  "
  curl -s -X POST http://localhost:8000/v1/mask -H 'Content-Type: application/json' \
    -d "{\"text\":\"$t\"}" | python3 -c "import json,sys;print(json.load(sys.stdin)['masked_text'])"
done
```

Регистр не влияет, разделяющие слова распознаются, поддержаны 11 форматов дат
включая запись прописью и американский порядок.

---

## 5. Настройка под систему-потребителя

Одна и та же строка, разные системы:

```bash
# token — для LLM-контура
curl -s -X POST http://localhost:8000/process -H 'Content-Type: application/json' \
  -H 'X-API-Key: demo-key-alfagen-chat' \
  -d '{"payload":"Клиент Иванов Иван Иванович","payload_id":"sys-a"}'
# {"result":"Клиент [FIO_1]"}

# synthetic — для аналитической песочницы
curl -s -X POST http://localhost:8000/process -H 'Content-Type: application/json' \
  -H 'X-API-Key: demo-key-analytics' \
  -d '{"payload":"Клиент Иванов Иван Иванович","payload_id":"sys-b"}'
# {"result":"Клиент Петров Пётр Петрович"}
```

**Демаскирование по системе.** Песочнице оно запрещено политикой:

```bash
curl -s -X POST http://localhost:8000/process -H 'Content-Type: application/json' \
  -H 'X-API-Key: demo-key-analytics' \
  -d '{"payload":"Клиент Петров Пётр Петрович","payload_id":"sys-b"}'
# {"error":"demasking_disabled","system_id":"analytics-sandbox"}
```

**Отключённая система:**

```bash
curl -s -X POST http://localhost:8000/process -H 'Content-Type: application/json' \
  -H 'X-API-Key: demo-key-legacy' \
  -d '{"payload":"тест","payload_id":"sys-c"}'
# {"error":"system_disabled","system_id":"legacy-crm"}
```

Включить её на лету и проверить снова:

```bash
curl -s -X POST http://localhost:8000/admin/systems/legacy-crm/toggle \
  -H 'Content-Type: application/json' -d '{"enabled":true}'
```

Все политики целиком: `curl -s http://localhost:8000/admin/systems`

---

## 6. Контекстное маскирование

```bash
# ПИН сам по себе — не маскируем
curl -s -X POST http://localhost:8000/v1/mask -H 'Content-Type: application/json' \
  -d '{"text":"Забыл свой пин-код 4321, что делать?"}'

# ПИН вместе с картой — маскируем
curl -s -X POST http://localhost:8000/v1/mask -H 'Content-Type: application/json' \
  -d '{"text":"Карта 4276 3800 1234 5679, пин-код 4321"}'
```

Правило настраивается в `config/systems.yaml` → `combination_rules`.

---

## 7. Цепочка с LLM: проверка на утечку

```bash
curl -s -X POST http://localhost:8000/v1/llm/chat -H 'Content-Type: application/json' \
  -d '{"prompt":"Клиент Иванов Иван Иванович, карта 4276 3800 1234 5679, составь ответ"}'
```

Смотреть поле `prompt_sent_to_llm` — это дословно то, что ушло во внешнюю модель.
Поле `answer` — ответ после обратной подстановки.

---

## 8. Логи и метрики

**Логи** — в stdout сервиса, структурный JSON. По каждому запросу пишется список
выявленных типов ПД, их количество и латентность. Значений персональных данных
в логах нет по построению.

```bash
docker compose logs -f pdguard        # либо смотреть вывод uvicorn
```

**Сводка производительности:**

```bash
curl -s http://localhost:8000/stats
# {"rps":1523.1,"tps":38078.7,"latency_avg_ms":0.276,"latency_p95_ms":0.34,"latency_p99_ms":0.466,...}
```

**Prometheus:** `curl -s http://localhost:8000/metrics | grep pdguard_`

---

## 9. Самопроверка контракта

Симулятор проверяющей системы из Приложения B — пары по `payload_id`, таймаут
10 с, ретраи, `Retry-After`, остановка после пяти невалидных ответов подряд,
плюс проверка, что в маске не осталось исходных данных:

```bash
python bench/contract_check.py --url http://localhost:8000
# Демаскирование точное:   30/30  (100.0%)
# Утечек ПД в маске:       0
# РЕЗУЛЬТАТ: контракт пройден
```

Тот же скрипт можно натравить на эталонную реализацию из ТЗ:

```bash
python bench/contract_check.py --url https://process-test.holydev.space --rounds 1
```

Эталон пройдёт протокол (10/10), но покажет 9 утечек из 10 — он не маскирует,
а только демонстрирует формат обмена. Это ожидаемо и указано в самом ТЗ.

---

## 10. Нагрузка и крупный текст

```bash
python bench/loadtest.py --url http://localhost:8000 --duration 30 --concurrency 64
python bench/bench_pipeline.py --iterations 3000     # включая прогон на 100 000 токенов
```

---

## 11. Обработка ошибок

```bash
# невалидный запрос
curl -s -X POST http://localhost:8000/process -H 'Content-Type: application/json' -d '{"payload":"нет id"}'

# неизвестная система
curl -s -X POST http://localhost:8000/process -H 'Content-Type: application/json' \
  -H 'X-System-Id: no-such-system' -d '{"payload":"a","payload_id":"e-1"}'
```

Ответы машиночитаемые, без текста обрабатываемого запроса.

---

## Тесты

```bash
.venv/bin/python -m pytest -q
# 118 passed
```
