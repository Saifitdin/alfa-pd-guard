"""Адаптер LLM для демонстрации полной цепочки.

Модуль намеренно не зависит от конкретного провайдера: любой
OpenAI-совместимый эндпоинт задаётся переменными окружения. Если провайдер не
настроен или недоступен, включается локальная заглушка — цепочка остаётся
рабочей, и демо не падает из-за внешнего сервиса. Это и есть требуемая
«деградация функционала при недоступности сервисов».

Корпоративный AlfaGen (https://alfagen.alfabank.ru/continue-dev/) — тот же
OpenAI-совместимый контракт с одной особенностью: сертификат выпущен
Russian Trusted CA, которого нет в пакете certifi. Поэтому клиент доверяет
объединённому бандлу «certifi + certs/russian_trusted_ca.pem» — и только для
этого соединения, а не для системы целиком.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("pdguard.llm")

_ROOT = Path(__file__).resolve().parents[2]
_BUNDLED_CA = _ROOT / "certs" / "russian_trusted_ca.pem"
_ENDPOINT_SUFFIXES = ("chat/completions", "v1/chat/completions")

#: Плейсхолдеры вроде [FIO_1] модель должна вернуть нетронутыми — иначе
#: обратной подстановке не за что зацепиться. Язык задаём явно: на промпт с
#: латинскими плейсхолдерами DeepSeek без подсказки уходит в английский.
SYSTEM_PROMPT = (
    "Отвечай на русском языке. В тексте могут встречаться плейсхолдеры в квадратных "
    "скобках вида [FIO_1], [CARD_NUMBER_1], [PHONE_1] — это защищённые данные клиента. "
    "Используй их в ответе как есть, не изменяй, не расшифровывай и не придумывай "
    "им значения. Суффикс после двоеточия — это род человека: [FIO_1:F] — женщина, "
    "[FIO_1:M] — мужчина; согласуй с ним обращение и глаголы («Уважаемая», «Уважаемый»), "
    "а сам плейсхолдер повторяй целиком, вместе с суффиксом. В обращении к клиенту "
    "добавляй к плейсхолдеру имени суффикс :short — «Уважаемая [FIO_1:F:short],» — "
    "так подставятся только имя и отчество, как принято по этикету."
)


def _settings() -> tuple[str | None, str | None, str, float]:
    """Читается при каждом вызове: .env подхватывается без перезапуска модуля."""
    return (
        os.getenv("PDGUARD_LLM_URL"),
        os.getenv("PDGUARD_LLM_KEY"),
        os.getenv("PDGUARD_LLM_MODEL", "gpt-4o-mini"),
        float(os.getenv("PDGUARD_LLM_TIMEOUT", "60")),
    )


def _mock_answer(masked_prompt: str) -> str:
    """Заглушка возвращает промпт обратно, сохраняя плейсхолдеры.

    Так на демо видно главное: модель получила только маску, а обратная
    подстановка восстановила исходные значения уже на нашей стороне.
    """
    return f"[LLM-заглушка] Получен запрос: «{masked_prompt}». Данные клиента в запросе отсутствуют."


@lru_cache(maxsize=1)
def _ca_bundle() -> str | bool:
    """Путь к бандлу доверенных CA для LLM-клиента.

    PDGUARD_LLM_CA_BUNDLE задаёт готовый файл. Иначе, если в проекте лежит
    сертификат Russian Trusted CA, он склеивается с certifi во временный
    бандл. Если ни того ни другого нет — стандартная проверка httpx.
    """
    explicit = os.getenv("PDGUARD_LLM_CA_BUNDLE")
    if explicit:
        return explicit
    if not _BUNDLED_CA.is_file():
        return True
    try:
        import certifi
    except ImportError:
        return str(_BUNDLED_CA)
    import tempfile

    combined = Path(tempfile.gettempdir()) / "pdguard-ca-bundle.pem"
    combined.write_bytes(Path(certifi.where()).read_bytes() + b"\n" + _BUNDLED_CA.read_bytes())
    return str(combined)


def _candidate_endpoints(url: str) -> list[str]:
    """Полный адрес используется как есть; базовый URL дополняется путём.

    В инструкции AlfaGen указаны оба варианта базы — «/continue-dev/» и
    «/continue-dev/v1», — поэтому при 404 пробуется второй суффикс.
    """
    if url.rstrip("/").endswith("/chat/completions"):
        return [url]
    base = url.rstrip("/") + "/"
    return [base + suffix for suffix in _ENDPOINT_SUFFIXES]


async def call_llm(masked_prompt: str) -> tuple[str, dict[str, object]]:
    url, key, model, timeout = _settings()
    if not url or not key:
        return _mock_answer(masked_prompt), {"provider": "mock", "reason": "not_configured"}

    try:
        import httpx

        last_status: int | None = None
        async with httpx.AsyncClient(timeout=timeout, verify=_ca_bundle()) as client:
            for endpoint in _candidate_endpoints(url):
                response = await client.post(
                    endpoint,
                    headers={"Authorization": f"Bearer {key}"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": SYSTEM_PROMPT},
                            {"role": "user", "content": masked_prompt},
                        ],
                        "temperature": 0.2,
                        # Гейтвей AlfaGen без явного stream=false отвечает
                        # 400 с пустым телом; OpenAI-совместимым провайдерам
                        # поле не мешает.
                        "stream": False,
                    },
                )
                last_status = response.status_code
                if response.status_code == 404:
                    continue
                response.raise_for_status()
                data = response.json()
                answer = data["choices"][0]["message"]["content"]
                return answer, {
                    "provider": "openai_compatible",
                    "model": data.get("model", model),
                    "endpoint": endpoint,
                    "usage": data.get("usage"),
                }
        raise RuntimeError(f"все адреса вернули 404 (последний статус {last_status})")
    except Exception as exc:  # noqa: BLE001 - любая ошибка провайдера не должна ронять цепочку
        # В лог не попадают ни ключ, ни текст запроса — только класс ошибки и статус.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        log.warning(
            "LLM недоступна, отдаём заглушку",
            extra={"error_type": type(exc).__name__, "status": status},
        )
        return _mock_answer(masked_prompt), {
            "provider": "mock",
            "reason": "llm_unavailable",
            "error_type": type(exc).__name__,
            "status": status,
        }
