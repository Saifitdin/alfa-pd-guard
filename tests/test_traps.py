"""Ловушки из критериев оценивания: что маскировать НЕ нужно.

Проверяем, что модуль не ломает смысл запроса к модели, вычищая всё подряд.
"""

from __future__ import annotations

import pytest

from pdguard.core.entities import PDType

from helpers import entities_of, types_in

PUBLIC_FIGURE_TEXTS = [
    "Поэт Александр Пушкин родился в Москве",
    "Александр Сергеевич Пушкин написал «Евгения Онегина»",
    "Памятник установлен на площади имени Пушкина",
    "Роман Льва Толстого «Война и мир» есть в библиотеке",
    "Космонавт Юрий Гагарин совершил первый полёт",
    "Периодическая таблица названа в честь Дмитрия Менделеева",
]


@pytest.mark.parametrize("text", PUBLIC_FIGURE_TEXTS)
def test_public_figures_are_not_personal_data(pipeline, text: str) -> None:
    assert PDType.FIO not in types_in(pipeline, text), f"ложное срабатывание ФИО: {text}"


def test_birthplace_of_public_figure_is_not_personal_data(pipeline) -> None:
    """«Поэт ... родился в Москве» — место рождения классика, а не клиента."""
    text = "Поэт Александр Пушкин родился в Москве"
    assert not entities_of(pipeline, text, PDType.BIRTH_PLACE)


def test_birthplace_of_client_is_personal_data(pipeline) -> None:
    text = "Клиент Сидоров Пётр Ильич, паспорт 4509 123456, родился в Волгограде"
    assert entities_of(pipeline, text, PDType.BIRTH_PLACE)


def test_bank_branch_address_is_not_personal_data(pipeline) -> None:
    text = "Отделение банка находится по адресу: г. Москва, ул. Каланчевская, д. 27"
    assert not entities_of(pipeline, text, PDType.ADDRESS)


def test_client_context_beats_public_surname(pipeline) -> None:
    """Однофамилец классика — всё-таки клиент, если контекст клиентский."""
    text = "Клиент Пушкин Андрей Викторович, паспорт 4509 123456"
    assert PDType.FIO in types_in(pipeline, text)


def test_organization_is_not_person(pipeline) -> None:
    text = "Заявка передана в Альфа-Банк для рассмотрения"
    assert PDType.FIO not in types_in(pipeline, text)


def test_order_number_is_not_card(pipeline) -> None:
    text = "Номер заказа 1234 5678 1234 5670 в системе доставки"
    assert not entities_of(pipeline, text, PDType.CARD_NUMBER)


def test_standalone_city_is_not_address(pipeline) -> None:
    """Одиночный компонент не образует адрес при min_components=2."""
    assert not entities_of(pipeline, "Встреча пройдёт в г. Казань", PDType.ADDRESS)


def test_bare_date_without_context_is_generic(pipeline) -> None:
    """Дата совещания — не дата рождения."""
    found = types_in(pipeline, "Совещание перенесено на 12.11.2026")
    assert PDType.BIRTH_DATE not in found
    assert PDType.PASSPORT_ISSUE_DATE not in found


def test_pin_alone_is_not_masked(pipeline, policy) -> None:
    """Правило комбинаций: ПИН без номера карты не маскируется."""
    result = pipeline.mask("Мой пин-код 4321, помогите вспомнить", policy)
    assert result.masked_text == "Мой пин-код 4321, помогите вспомнить"
    assert PDType.PIN not in result.masked_types


def test_pin_with_card_is_masked(pipeline, policy) -> None:
    result = pipeline.mask("Карта 4276 3800 1234 5679, пин-код 4321", policy)
    assert PDType.PIN in result.masked_types
    assert "4321" not in result.masked_text
