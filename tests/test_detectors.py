"""Покрытие всех 17 обязательных типов ПД из ТЗ."""

from __future__ import annotations

import pytest

from pdguard.core.entities import PDType

from helpers import entities_of, types_in

CASES: list[tuple[str, str, str]] = [
    (PDType.FIO, "Клиент Иванов Иван Иванович обратился в отделение", "Иванов Иван Иванович"),
    (PDType.BIRTH_DATE, "Дата рождения: 15.03.1985", "15.03.1985"),
    (PDType.BIRTH_PLACE, "Место рождения: г. Волгоград", "г. Волгоград"),
    (PDType.PASSPORT_RF, "Паспорт 4509 123456 выдан отделением", "4509 123456"),
    (PDType.CITIZENSHIP, "Гражданство: Российская Федерация", "Российская Федерация"),
    (PDType.DEPARTMENT_CODE, "Паспорт, код подразделения 770-053", "770-053"),
    (PDType.DRIVER_LICENSE, "Водительское удостоверение 7799 123456", "7799 123456"),
    (PDType.EMAIL, "Почта клиента ivan.petrov@example.com для связи", "ivan.petrov@example.com"),
    (PDType.PHONE, "Телефон +7 916 123-45-67 для связи", "+7 916 123-45-67"),
    (PDType.INN, "ИНН 500100732259 подтверждён", "500100732259"),
    (PDType.CARD_NUMBER, "Карта 4276 3800 1234 5679 заблокирована", "4276 3800 1234 5679"),
    (PDType.SNILS, "СНИЛС 112-233-445 95 в анкете", "112-233-445 95"),
]


@pytest.mark.parametrize(("pd_type", "text", "expected"), CASES, ids=[c[0] for c in CASES])
def test_detects_type(pipeline, pd_type: str, text: str, expected: str) -> None:
    found = entities_of(pipeline, text, pd_type)
    assert found, f"тип {pd_type} не найден в тексте: {text}"
    assert any(expected in entity.value or entity.value in expected for entity in found), (
        f"ожидали значение {expected!r}, получили {[e.value for e in found]}"
    )


def test_passport_issuer(pipeline) -> None:
    text = "Паспорт 4509 123456, выдан ГУ МВД России по г. Москве, дата выдачи 20.01.2015"
    found = entities_of(pipeline, text, PDType.PASSPORT_ISSUER)
    assert found
    assert "МВД" in found[0].value


def test_issue_date_separated_from_birth_date(pipeline) -> None:
    text = "Дата рождения 15.03.1985, паспорт выдан 20.01.2015"
    found = types_in(pipeline, text)
    assert PDType.BIRTH_DATE in found
    assert PDType.PASSPORT_ISSUE_DATE in found


def test_cvv_and_pin_with_card(pipeline) -> None:
    text = "Карта 4276 3800 1234 5679, CVV 123, пин-код 4321"
    found = types_in(pipeline, text)
    assert {PDType.CARD_NUMBER, PDType.CVV, PDType.PIN} <= found


def test_card_holder(pipeline) -> None:
    text = "Держатель карты IVAN PETROV, карта 4276 3800 1234 5679"
    found = entities_of(pipeline, text, PDType.CARD_HOLDER)
    assert found
    assert "IVAN PETROV" in found[0].value


def test_address_full(pipeline) -> None:
    text = "Адрес регистрации: 344002, г. Ростов-на-Дону, ул. Большая Садовая, д. 15, кв. 42"
    found = entities_of(pipeline, text, PDType.ADDRESS)
    assert found
    assert "Большая Садовая" in found[0].value


def test_luhn_filters_random_digits(pipeline) -> None:
    """16 цифр без валидной контрольной суммы — не номер карты."""
    assert not entities_of(pipeline, "Заказ 1234 5678 1234 5679 оформлен", PDType.CARD_NUMBER)


def test_inn_checksum_filters_random_digits(pipeline) -> None:
    assert not entities_of(pipeline, "Идентификатор записи 1234567890 в реестре", PDType.INN)


def test_all_required_types_are_configured(pipeline) -> None:
    """Все 17 обязательных типов описаны в справочнике."""
    required = {
        PDType.FIO, PDType.BIRTH_DATE, PDType.BIRTH_PLACE, PDType.PASSPORT_RF,
        PDType.CITIZENSHIP, PDType.PASSPORT_ISSUER, PDType.DEPARTMENT_CODE,
        PDType.PASSPORT_ISSUE_DATE, PDType.DRIVER_LICENSE, PDType.ADDRESS,
        PDType.EMAIL, PDType.PHONE, PDType.INN, PDType.CARD_NUMBER,
        PDType.CVV, PDType.PIN, PDType.CARD_HOLDER,
    }
    assert len(required) == 17
    assert required <= set(pipeline.policies.titles)
