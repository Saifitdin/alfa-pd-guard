"""Род в токене: «[FIO_1:F]» — чтобы модель писала «Уважаемая», а не «Уважаемый»."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pdguard.core.detect.gender import infer_gender
from pdguard.core.entities import PDType


@pytest.mark.parametrize(
    ("fio", "expected"),
    [
        ("Петрова Анна Сергеевна", "F"),
        ("Иванов Иван Иванович", "M"),
        ("Анна Сергеевна Петрова", "F"),          # другой порядок слов
        ("петрова анна сергеевна", "F"),          # нижний регистр
        ("ПЕТРОВА АННА СЕРГЕЕВНА", "F"),          # капс
        ("Сидорова Мария", "F"),                  # без отчества — по фамилии и имени
        ("Кузнецов Игорь", "M"),
        ("Никита Ткаченко", "M"),                 # имя на -а, но мужское; фамилия несклоняемая
        ("Илья Шмидт", "M"),
        ("Любовь Ким", "F"),                      # имя на согласный, но женское
        ("Ткаченко", None),                       # одной несклоняемой фамилии мало
        ("Шмидт", None),
    ],
)
def test_infer_gender(pipeline, fio: str, expected: str | None) -> None:
    assert pipeline.detectors.gender_of(fio) == expected


def test_infer_gender_without_dictionaries_still_uses_morphology() -> None:
    assert infer_gender("Иванова Ольга Петровна") == "F"
    assert infer_gender("Иванов Пётр Петрович") == "M"


def test_token_carries_gender_hint(pipeline) -> None:
    policy = pipeline.policies.by_id("alfagen-chat")
    result = pipeline.mask("Клиент Петрова Анна Сергеевна, карта 4276 3800 1234 5679", policy)
    assert "[FIO_1:F]" in result.masked_text
    assert "[CARD_NUMBER_1]" in result.masked_text          # у карты рода нет
    assert result.mapping["[FIO_1:F]"] == "Петрова Анна Сергеевна"


def test_token_without_gender_when_unknown(pipeline) -> None:
    policy = pipeline.policies.by_id("alfagen-chat")
    # «Ткаченко» распознаётся по суффиксу и клиентскому контексту, но рода не несёт.
    result = pipeline.mask("Клиент Ткаченко, паспорт 4509 123456", policy)
    assert "[FIO_1]" in result.masked_text
    assert "[FIO_1:" not in result.masked_text


def test_gender_hint_can_be_disabled(pipeline) -> None:
    policy = pipeline.policies.by_id("alfagen-chat")
    policy.token_gender_hint = False
    try:
        result = pipeline.mask("Клиент Петрова Анна Сергеевна", policy)
        assert "[FIO_1]" in result.masked_text and "[FIO_1:F]" not in result.masked_text
    finally:
        policy.token_gender_hint = True


@dataclass
class FakeRecord:
    original: str
    masked: str
    mapping: dict[str, str]


def test_demask_accepts_token_with_and_without_hint(pipeline) -> None:
    """Модель может вернуть «[FIO_1]» без «:F» — восстановление не должно сломаться."""
    policy = pipeline.policies.by_id("alfagen-chat")
    text = "Клиент Петрова Анна Сергеевна, телефон +7 916 123-45-67"
    result = pipeline.mask(text, policy)
    record = FakeRecord(text, result.masked_text, result.mapping)

    with_hint = pipeline.demask("Уважаемая [FIO_1:F], ваш номер [PHONE_1]", record)
    without_hint = pipeline.demask("Уважаемая [FIO_1], ваш номер [PHONE_1]", record)
    # В обращении подставляются имя и отчество — так принято по этикету.
    assert with_hint == without_hint == "Уважаемая Анна Сергеевна, ваш номер +7 916 123-45-67"


def test_synthetic_keeps_gender(pipeline) -> None:
    policy = pipeline.policies.by_id("analytics-sandbox")
    female = pipeline.mask("Клиент Петрова Анна Сергеевна", policy).masked_text
    male = pipeline.mask("Клиент Иванов Иван Иванович", policy).masked_text
    assert pipeline.detectors.gender_of(female.replace("Клиент ", "")) == "F"
    assert pipeline.detectors.gender_of(male.replace("Клиент ", "")) == "M"


def test_partial_mask_carries_no_gender(pipeline) -> None:
    policy = pipeline.policies.by_id("loadtest")
    assert pipeline.mask("Клиент Петрова Анна Сергеевна", policy).masked_text == "Клиент П. А. С."


def test_all_fio_types_masked_types_unchanged(pipeline) -> None:
    policy = pipeline.policies.by_id("alfagen-chat")
    result = pipeline.mask("Клиент Петрова Анна Сергеевна", policy)
    assert result.masked_types == [PDType.FIO]
