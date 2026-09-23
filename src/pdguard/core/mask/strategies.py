"""Стратегии маскирования.

Четыре режима, выбираются в конфигурации системы-потребителя:

* ``partial``   — частичная маска с сохранением формы: «45** ****56».
                  Формат совпадает с примером из контракта ТЗ.
* ``full``      — все значащие символы заменяются на «*».
* ``token``     — подстановка плейсхолдера «[CARD_NUMBER_1]». Рекомендуется
                  для реального LLM-контура: не даёт коллизий при обратной
                  замене и не путает модель обрывками цифр.
* ``synthetic`` — замена правдоподобными синтетическими данными: смысл
                  запроса для модели сохраняется полностью.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from pdguard.core.entities import PDType

MASK_CHAR = "*"


@dataclass(frozen=True, slots=True)
class PartialRule:
    """Сколько значащих символов оставить в начале и в конце."""

    head: int = 0
    tail: int = 0


#: Значения по умолчанию. Переопределяются в config/pd_types.yaml.
DEFAULT_PARTIAL_RULES: dict[PDType, PartialRule] = {
    PDType.PASSPORT_RF: PartialRule(2, 2),
    PDType.CARD_NUMBER: PartialRule(4, 4),
    PDType.PHONE: PartialRule(2, 2),
    PDType.INN: PartialRule(2, 2),
    PDType.SNILS: PartialRule(0, 2),
    PDType.DRIVER_LICENSE: PartialRule(2, 2),
    PDType.DEPARTMENT_CODE: PartialRule(0, 0),
    PDType.BANK_ACCOUNT: PartialRule(4, 4),
    PDType.CVV: PartialRule(0, 0),
    PDType.PIN: PartialRule(0, 0),
    PDType.BIRTH_DATE: PartialRule(0, 0),
    PDType.PASSPORT_ISSUE_DATE: PartialRule(0, 0),
    PDType.DATE_GENERIC: PartialRule(0, 0),
    PDType.ADDRESS: PartialRule(0, 0),
    PDType.BIRTH_PLACE: PartialRule(0, 0),
    PDType.CITIZENSHIP: PartialRule(0, 0),
    PDType.PASSPORT_ISSUER: PartialRule(0, 0),
}


#: Типы, где значение — это число, а слова вокруг него служебные.
#: У них маскируются только цифры: «серия 4509 номер 123456» превращается
#: в «серия 45** номер ****56», а не в нечитаемое «се*** **** ***** ****56».
NUMERIC_TYPES: frozenset[str] = frozenset(
    {
        PDType.PASSPORT_RF, PDType.DRIVER_LICENSE, PDType.DEPARTMENT_CODE,
        PDType.CARD_NUMBER, PDType.CVV, PDType.PIN, PDType.INN, PDType.SNILS,
        PDType.PHONE, PDType.BANK_ACCOUNT, PDType.BIRTH_DATE,
        PDType.PASSPORT_ISSUE_DATE, PDType.DATE_GENERIC, PDType.FOREIGN_PASSPORT,
    }
)


def _significant_positions(value: str, digits_only: bool) -> list[int]:
    """Индексы символов, которые подлежат замене."""
    if digits_only:
        return [i for i, ch in enumerate(value) if ch.isdigit()]
    return [i for i, ch in enumerate(value) if ch.isalnum()]


def mask_keep_edges(value: str, rule: PartialRule, digits_only: bool = False) -> str:
    """Маскирует значащие символы, сохраняя разделители и длину строки."""
    positions = _significant_positions(value, digits_only)
    if not positions:
        return value
    keep_head = set(positions[: rule.head])
    keep_tail = set(positions[len(positions) - rule.tail :]) if rule.tail else set()
    chars = list(value)
    for index in positions:
        if index not in keep_head and index not in keep_tail:
            chars[index] = MASK_CHAR
    return "".join(chars)


def mask_initials(value: str) -> str:
    """«Иванов Иван Иванович» → «И. И. И.»"""
    parts = [part for part in value.replace(".", " ").split() if part]
    if not parts:
        return value
    return " ".join(f"{part[0].upper()}." for part in parts)


def mask_email(value: str) -> str:
    """«ivan.petrov@mail.ru» → «i****@****.ru» — домен верхнего уровня остаётся."""
    local, _, domain = value.partition("@")
    if not domain:
        return mask_keep_edges(value, PartialRule(1, 0))
    head = local[0] if local else ""
    domain_name, _, tld = domain.rpartition(".")
    masked_domain = MASK_CHAR * max(len(domain_name), 1)
    return f"{head}{MASK_CHAR * max(len(local) - 1, 1)}@{masked_domain}.{tld}"


_MONTH_STEMS = (
    "янв", "фев", "мар", "апр", "ма", "июн", "июл", "авг", "сен", "окт", "ноя", "дек",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
)
_DATE_KEEP_WORDS = frozenset({"г", "г.", "года", "год", "of", ","})


def mask_date(value: str) -> str:
    """Дата: скрывается всё, кроме названия месяца и слова «года».

    Иначе день прописью оставался бы на виду — «пятнадцатое марта ****» —
    хотя в числовой записи тот же день закрыт: «** марта ****».
    """
    parts = []
    for token in value.split(" "):
        lowered = token.lower().strip(".,")
        if lowered in _DATE_KEEP_WORDS or any(lowered.startswith(stem) for stem in _MONTH_STEMS):
            parts.append(token)
        else:
            parts.append(mask_keep_edges(token, PartialRule(0, 0), digits_only=False))
    return " ".join(parts)


#: Специальные обработчики, которые нельзя выразить правилом head/tail.
SPECIAL_PARTIAL: dict[PDType, Callable[[str], str]] = {
    PDType.FIO: mask_initials,
    PDType.CARD_HOLDER: mask_initials,
    PDType.EMAIL: mask_email,
    PDType.BIRTH_DATE: mask_date,
    PDType.PASSPORT_ISSUE_DATE: mask_date,
    PDType.DATE_GENERIC: mask_date,
}


def apply_partial(pd_type: str, value: str, rules: dict[str, PartialRule]) -> str:
    handler = SPECIAL_PARTIAL.get(pd_type)
    if handler is not None:
        return handler(value)
    return mask_keep_edges(
        value, rules.get(pd_type, PartialRule(0, 0)), digits_only=pd_type in NUMERIC_TYPES
    )


def apply_full(value: str) -> str:
    """Полная маска: скрываются и цифры, и буквы."""
    return mask_keep_edges(value, PartialRule(0, 0), digits_only=False)


def apply_token(pd_type: str, index: int, hint: str | None = None) -> str:
    """«[FIO_1]» или, с грамматической подсказкой рода, «[FIO_1:F]».

    Подсказка ничего не раскрывает, но без неё модель не может выбрать
    «Уважаемый» или «Уважаемая» — а ошибка в обращении заметна клиенту.
    """
    name = pd_type.name if isinstance(pd_type, PDType) else str(pd_type).upper()
    return f"[{name}_{index}:{hint}]" if hint else f"[{name}_{index}]"


# --------------------------------------------------------------------------
# Синтетическая замена
# --------------------------------------------------------------------------

#: Пулы по роду: подмена «Иванов Иван» на «Сидорова Анна» ломала бы обращение.
_SYNTH_NAMES_MALE = ("Петров Пётр Петрович", "Кузнецов Игорь Олегович", "Смирнов Андрей Викторович")
_SYNTH_NAMES_FEMALE = ("Сидорова Анна Ивановна", "Морозова Елена Сергеевна", "Волкова Мария Дмитриевна")
_SYNTH_NAMES = _SYNTH_NAMES_MALE + _SYNTH_NAMES_FEMALE
_SYNTH_CITIES = ("г. Тверь", "г. Псков", "г. Рязань")
_SYNTH_COUNTRIES = ("Российская Федерация",)


def _stable_choice(value: str, options: tuple[str, ...]) -> str:
    """Одно и то же значение всегда даёт одну и ту же подстановку."""
    digest = hashlib.blake2s(value.encode("utf-8"), digest_size=4).digest()
    return options[int.from_bytes(digest, "big") % len(options)]


def _digits_like(value: str, seed: str) -> str:
    """Генерирует цифры той же формы: «4509 123456» → «7712 884503»."""
    digest = hashlib.blake2s(seed.encode("utf-8"), digest_size=16).digest()
    stream = iter(digest * 4)
    chars = []
    for ch in value:
        chars.append(str(next(stream) % 10) if ch.isdigit() else ch)
    return "".join(chars)


def apply_synthetic(pd_type: str, value: str, gender: str | None = None) -> str:
    if pd_type in (PDType.FIO, PDType.CARD_HOLDER):
        pool = {"F": _SYNTH_NAMES_FEMALE, "M": _SYNTH_NAMES_MALE}.get(gender or "", _SYNTH_NAMES)
        return _stable_choice(value, pool)
    if pd_type in (PDType.ADDRESS, PDType.BIRTH_PLACE):
        return _stable_choice(value, _SYNTH_CITIES)
    if pd_type is PDType.CITIZENSHIP:
        return _SYNTH_COUNTRIES[0]
    if pd_type is PDType.EMAIL:
        return f"user{_digits_like('0000', value)}@example.org"
    return _digits_like(value, value)
