"""Формы ФИО для подстановки в ответ модели.

Полное «Петрова Анна Сергеевна» уместно в договоре, но в обращении по этикету
пишут «Уважаемая Анна Сергеевна». Модель имени не видит, поэтому форму выбираем
мы при демаскировании: либо по явной просьбе модели («[FIO_1:F:short]»), либо
по контексту — токен стоит сразу после слова-обращения.
"""

from __future__ import annotations

import re

_PATRONYMIC_SUFFIXES = (
    "ович", "евич", "ьич", "овна", "евна", "ична", "инична",
    "овича", "евича", "овны", "евны", "ичны", "овне", "евне", "овну", "евну",
    "овной", "евной", "оглы", "кызы", "угли",
)

#: Слова, после которых уместны только имя и отчество.
RE_SALUTATION_BEFORE = re.compile(
    r"(?:уважаем(?:ый|ая|ые)|дорог(?:ой|ая|ие)|здравствуйте|добр(?:ый|ое)\s+(?:день|вечер|утро)|"
    r"приветству(?:ю|ем)|привет)[,!]?\s*$",
    re.IGNORECASE,
)


def _is_patronymic(token: str) -> bool:
    return len(token) > 5 and token.endswith(_PATRONYMIC_SUFFIXES)


def _nice_case(token: str) -> str:
    """«ПЕТРОВА» и «петрова» → «Петрова»; уже нормальное написание не трогается."""
    if token.isupper() or token.islower():
        return token.capitalize()
    return token


def short_form(fio: str, given_names: frozenset[str] = frozenset()) -> str | None:
    """«Имя Отчество» из ФИО в любом порядке слов, либо None, если имя не найдено.

    Без отчества возвращается одно имя: «Уважаемая Анна» лучше, чем полное
    ФИО в обращении. Если ни имя, ни отчество не распознаны — None, и вызывающий
    код подставляет полную форму.
    """
    tokens = [t.strip(".,") for t in fio.split() if t.strip(".,")]
    lowered = [t.lower() for t in tokens]
    if not tokens:
        return None

    patronymic_index = next((i for i, t in enumerate(lowered) if _is_patronymic(t)), None)

    given_index: int | None = None
    if patronymic_index is not None and patronymic_index > 0 and lowered[patronymic_index - 1] in given_names:
        given_index = patronymic_index - 1
    else:
        given_index = next((i for i, t in enumerate(lowered) if t in given_names), None)
    if given_index is None and patronymic_index is not None and len(tokens) == 3:
        # Словарь не знает имени, но структура «Ф И О» / «И О Ф» однозначна.
        given_index = patronymic_index - 1 if patronymic_index >= 1 else None
    if given_index is None:
        return None

    parts = [_nice_case(tokens[given_index])]
    if patronymic_index is not None and patronymic_index != given_index:
        parts.append(_nice_case(tokens[patronymic_index]))
    return " ".join(parts)


def wants_short_form(text: str, token_start: int) -> bool:
    """Стоит ли токен в позиции обращения: «Уважаемая [FIO_1:F], …»."""
    return bool(RE_SALUTATION_BEFORE.search(text[max(0, token_start - 40) : token_start]))
