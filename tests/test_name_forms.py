"""Обращение по имени и отчеству: «Уважаемая Анна Сергеевна», а не полное ФИО."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pdguard.core.detect.name_forms import short_form, wants_short_form


@pytest.mark.parametrize(
    ("fio", "expected"),
    [
        ("Петрова Анна Сергеевна", "Анна Сергеевна"),
        ("Анна Сергеевна Петрова", "Анна Сергеевна"),      # порядок И О Ф
        ("Иванов Иван Иванович", "Иван Иванович"),
        ("ПЕТРОВА АННА СЕРГЕЕВНА", "Анна Сергеевна"),      # капс → нормальный регистр
        ("петрова анна сергеевна", "Анна Сергеевна"),
        ("Сидорова Мария", "Мария"),                        # без отчества — только имя
        ("Никита Ткаченко", "Никита"),
        ("Ткаченко", None),                                 # имени нет — полная форма
        ("Шмидт", None),
    ],
)
def test_short_form(pipeline, fio: str, expected: str | None) -> None:
    assert pipeline.detectors.short_form_of(fio) == expected


def test_short_form_uses_structure_when_name_unknown() -> None:
    """Имени нет в словаре, но «Ф И О» с отчеством однозначно."""
    assert short_form("Ткаченко Зульфикар Рустамович") == "Зульфикар Рустамович"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Уважаемая [FIO_1:F]", True),
        ("Уважаемый [FIO_1:M],", True),
        ("Здравствуйте, [FIO_1]", True),
        ("Добрый день, [FIO_1:F]!", True),
        ("Дорогая [FIO_1:F]", True),
        ("Клиент [FIO_1:F] обратился", False),
        ("Договор с [FIO_1:F] подписан", False),
    ],
)
def test_salutation_context(text: str, expected: bool) -> None:
    assert wants_short_form(text, text.index("[")) is expected


@dataclass
class FakeRecord:
    original: str
    masked: str
    mapping: dict[str, str]


def _record(pipeline, text: str):
    policy = pipeline.policies.by_id("alfagen-chat")
    result = pipeline.mask(text, policy)
    return FakeRecord(text, result.masked_text, result.mapping)


def test_salutation_gets_name_and_patronymic(pipeline) -> None:
    record = _record(pipeline, "Клиент Петрова Анна Сергеевна, карта 4276 3800 1234 5679")
    answer = "Уважаемая [FIO_1:F], ваша карта [CARD_NUMBER_1] перевыпущена. Договор на имя [FIO_1:F] обновлён."
    restored = pipeline.demask(answer, record)
    assert restored.startswith("Уважаемая Анна Сергеевна, ")
    # Вне обращения — полное ФИО, как в исходном тексте.
    assert "Договор на имя Петрова Анна Сергеевна обновлён." in restored
    assert "4276 3800 1234 5679" in restored


def test_explicit_short_suffix(pipeline) -> None:
    record = _record(pipeline, "Клиент Иванов Иван Иванович")
    assert pipeline.demask("Спасибо, [FIO_1:M:short]!", record) == "Спасибо, Иван Иванович!"
    assert pipeline.demask("Спасибо, [FIO_1:short]!", record) == "Спасибо, Иван Иванович!"


def test_salutation_without_patronymic_uses_given_name(pipeline) -> None:
    record = _record(pipeline, "Клиент Сидорова Мария, паспорт 4509 123456")
    assert pipeline.demask("Уважаемая [FIO_1:F]!", record) == "Уважаемая Мария!"


def test_salutation_falls_back_to_full_name(pipeline) -> None:
    record = _record(pipeline, "Клиент Ткаченко, паспорт 4509 123456")
    assert pipeline.demask("Уважаемый [FIO_1]!", record) == "Уважаемый Ткаченко!"


def test_exact_mask_roundtrip_unaffected(pipeline) -> None:
    text = "Уважаемая Петрова Анна Сергеевна, ваш паспорт 4509 123456"
    record = _record(pipeline, text)
    assert pipeline.demask(record.masked, record) == text
