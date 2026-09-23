"""Устойчивость к вариациям написания (п. 4.2 ТЗ, критерий 3.3)."""

from __future__ import annotations

import pytest

from pdguard.core.entities import PDType

from helpers import entities_of, types_in

CASE_VARIANTS = [
    "ПАСПОРТ 4509 123456",
    "паспорт 4509 123456",
    "ПаСпОрТ 4509 123456",
]


@pytest.mark.parametrize("text", CASE_VARIANTS)
def test_detection_is_case_insensitive(pipeline, text: str) -> None:
    assert entities_of(pipeline, text, PDType.PASSPORT_RF)


SEPARATOR_VARIANTS = [
    "паспорт серия 4509 номер 123456",
    "серия 4509 № 123456",
    "серия: 4509, номер: 123456",
    "паспорт сер. 45 09 ном. 123456",
    "Паспорт серии 4509 номер 123456",
]


@pytest.mark.parametrize("text", SEPARATOR_VARIANTS)
def test_passport_separator_words(pipeline, text: str) -> None:
    """«серия хххх номер хххххх» и его вариации — прямое требование ТЗ."""
    found = entities_of(pipeline, text, PDType.PASSPORT_RF)
    assert found, f"не распознан паспорт: {text}"
    assert "123456" in found[0].value


DATE_VARIANTS = [
    "дата рождения 15.03.1985",
    "дата рождения 15/03/1985",
    "дата рождения 15-03-1985",
    "дата рождения 1985.03.15",
    "дата рождения 1985-03-15",
    "дата рождения 03.15.1985",
    "дата рождения 15 марта 1985",
    "дата рождения 15 марта 1985 г.",
    "дата рождения 15 марта 1985 года",
    "дата рождения: пятнадцатого марта 1985 года",
    "date of birth March 15, 1985",
]


@pytest.mark.parametrize("text", DATE_VARIANTS)
def test_date_format_variations(pipeline, text: str) -> None:
    found = types_in(pipeline, text)
    assert PDType.BIRTH_DATE in found, f"не распознана дата рождения: {text}"


def test_american_and_russian_order_both_parse(pipeline) -> None:
    """мм.дд.гггг распознаётся, когда дд.мм.гггг календарно невозможен."""
    found = entities_of(pipeline, "дата рождения 03.25.1985", PDType.BIRTH_DATE)
    assert found
    assert found[0].meta.get("format") == "mm.dd.yyyy"


PHONE_VARIANTS = [
    "телефон +7 916 123-45-67",
    "телефон +7(916)123-45-67",
    "телефон 8 916 123 45 67",
    "телефон 89161234567",
    "телефон +79161234567",
]


@pytest.mark.parametrize("text", PHONE_VARIANTS)
def test_phone_format_variations(pipeline, text: str) -> None:
    assert entities_of(pipeline, text, PDType.PHONE), f"не распознан телефон: {text}"


def test_fio_word_order_variations(pipeline) -> None:
    for text in (
        "Клиент Иванов Иван Иванович",
        "Клиент Иван Иванович Иванов",
        "Клиент Иванов И.И.",
        "Клиент И.И. Иванов",
        "на имя Петровой Анны Сергеевны",
    ):
        assert PDType.FIO in types_in(pipeline, text), f"не распознано ФИО: {text}"


def test_card_with_different_separators(pipeline) -> None:
    for text in (
        "карта 4276380012345679",
        "карта 4276 3800 1234 5679",
        "карта 4276-3800-1234-5679",
    ):
        assert entities_of(pipeline, text, PDType.CARD_NUMBER), f"не распознана карта: {text}"
