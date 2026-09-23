#!/usr/bin/env bash
# Демонстрационный прогон всех ключевых сценариев.
#
#   ./demo/demo.sh [URL]      по умолчанию http://localhost:8000

set -uo pipefail
BASE="${1:-http://localhost:8000}"

bold() { printf "\n\033[1m%s\033[0m\n" "$1"; }
dim()  { printf "\033[2m%s\033[0m\n" "$1"; }

post() {
  curl -s -X POST "$BASE$1" -H 'Content-Type: application/json' "${@:3}" -d "$2"
}

pretty() { python3 -m json.tool --no-ensure-ascii 2>/dev/null || cat; }

field() { python3 -c "import json,sys; print(json.load(sys.stdin).get(sys.argv[1]))" "$1" 2>/dev/null; }

# Собирает JSON из пар ключ-значение, не полагаясь на кавычки shell.
json() { python3 -c "
import json, sys
print(json.dumps(dict(zip(sys.argv[1::2], sys.argv[2::2])), ensure_ascii=False))
" "$@"; }

if ! curl -sf -m 5 "$BASE/health" >/dev/null; then
  echo "Сервис недоступен по адресу $BASE"
  echo "Запустите: docker compose up --build   (или make run)"
  exit 1
fi

bold "0. Состояние сервиса"
curl -s "$BASE/health" | pretty

bold "1. Контракт: маскирование"
dim 'payload: Клиент Иванов Иван Иванович, паспорт 4509 123456'
MASK=$(post /process '{"payload":"Клиент Иванов Иван Иванович, паспорт 4509 123456","payload_id":"demo-1"}' | field result)
echo "result:  $MASK"

bold "2. Контракт: демаскирование тем же payload_id"
post /process "$(json payload "$MASK" payload_id demo-1)" | field result

bold "3. Идемпотентность: повтор прямого запроса возвращает ту же маску"
post /process '{"payload":"Клиент Иванов Иван Иванович, паспорт 4509 123456","payload_id":"demo-1"}' | field result

bold "4. Полнота: 17 типов ПД в одном тексте"
post /v1/mask '{"text":"Клиент Петрова Анна Сергеевна, дата рождения 12.07.1990, место рождения: г. Тверь, гражданство: Российская Федерация, паспорт серия 4517 номер 998877, выдан ГУ МВД России по г. Санкт-Петербургу, код подразделения 780-012, дата выдачи 20.01.2015, водительское удостоверение 7799 456123, адрес: 190000, г. Санкт-Петербург, ул. Малая Морская, д. 10, кв. 5, ИНН 500100732259, телефон +7 916 123-45-67, почта anna@example.com, карта 4276 3800 1234 5679, держатель ANNA PETROVA, CVV 123, пин-код 4321"}' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('маска:', d['masked_text'])
print('типов ПД найдено:', len(d['masked_types']))
print('типы:', ', '.join(d['masked_types']))
print('latency:', d['latency_ms'], 'мс')"

bold "5. Ловушки: классик, адрес отделения и номер заказа не маскируются"
post /v1/mask '{"text":"Поэт Александр Пушкин родился в Москве. Отделение банка: г. Москва, ул. Каланчевская, д. 27. Номер заказа 1234 5678 1234 5670."}' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('найдено сущностей:', len(d['detected']))
print('текст остался:', d['masked_text'])"

bold "6. А однофамилец классика в клиентском контексте — это ПД"
post /v1/mask '{"text":"Клиент Пушкин Андрей Викторович, паспорт 4509 123456"}' | field masked_text

bold "7. Вариации написания"
for t in "паспорт серия 4509 номер 123456" \
         "ПАСПОРТ 4509 123456" \
         "дата рождения 15 марта 1985 года" \
         "дата рождения: пятнадцатого марта 1985 года" \
         "дата рождения 03.15.1985" \
         "дата рождения 1985-03-15"; do
  printf "  %-45s -> " "$t"
  post /v1/mask "$(json text "$t")" | field masked_text
done

bold "8. Разные стратегии для разных систем-потребителей"
printf "  token (alfagen-chat):     "
post /process '{"payload":"Клиент Иванов Иван Иванович","payload_id":"demo-token"}' -H 'X-API-Key: demo-key-alfagen-chat' | field result
printf "  synthetic (analytics):    "
SYNTH=$(post /process '{"payload":"Клиент Иванов Иван Иванович","payload_id":"demo-synth"}' -H 'X-API-Key: demo-key-analytics' | field result)
echo "$SYNTH"
printf "  partial (loadtest):       "
post /process '{"payload":"Клиент Иванов Иван Иванович","payload_id":"demo-part"}' -H 'X-API-Key: demo-key-loadtest' | field result

bold "9. Демаскирование запрещено политикой песочницы"
post /process "$(json payload "$SYNTH" payload_id demo-synth)" -H 'X-API-Key: demo-key-analytics' | pretty

bold "10. Отключённая система не допускается к обработке"
post /process '{"payload":"тест","payload_id":"demo-legacy"}' -H 'X-API-Key: demo-key-legacy' | pretty

bold "11. Контекстное маскирование: ПИН без карты и с картой"
printf "  без карты: "
post /v1/mask '{"text":"Забыл свой пин-код 4321, что делать?"}' | field masked_text
printf "  с картой:  "
post /v1/mask '{"text":"Карта 4276 3800 1234 5679, пин-код 4321"}' | field masked_text

bold "12. Цепочка с LLM: что именно ушло в модель"
post /v1/llm/chat '{"prompt":"Клиент Иванов Иван Иванович, карта 4276 3800 1234 5679, составь ответ"}' \
  | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('ушло в LLM :', d['prompt_sent_to_llm'])
print('типы ПД    :', ', '.join(d['detected_pd_types']))
print('ответ после демаскирования:', d['answer'])"

bold "13. Метрики"
curl -s "$BASE/stats" | pretty

echo
dim "Полный список сценариев — docs/JURY.md"
