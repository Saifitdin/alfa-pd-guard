"""Стратегии маскирования и корректность обратного преобразования."""

from __future__ import annotations

from dataclasses import dataclass

from pdguard.core.entities import PDType
from pdguard.core.mask import strategies
from pdguard.core.mask.strategies import PartialRule

SAMPLE = "Клиент Иванов Иван Иванович, паспорт 4509 123456, карта 4276 3800 1234 5679"


@dataclass
class FakeRecord:
    original: str
    masked: str
    mapping: dict[str, str]


def test_partial_matches_contract_example(pipeline) -> None:
    """Пример из Приложения A ТЗ: «Клиент И. И. И., паспорт 45** ****56»."""
    policy = pipeline.policies.by_id("loadtest")
    result = pipeline.mask("Клиент Иванов Иван Иванович, паспорт 4509 123456", policy)
    assert result.masked_text == "Клиент И. И. И., паспорт 45** ****56"


def test_mask_preserves_length_and_positions(pipeline) -> None:
    """Частичная маска сохраняет длину строки — позиции знаков не едут."""
    policy = pipeline.policies.by_id("loadtest")
    text = "паспорт 4509 123456"
    assert len(pipeline.mask(text, policy).masked_text) == len(text)


def test_full_strategy_hides_everything(pipeline) -> None:
    policy = pipeline.policies.by_id("legacy-crm")
    policy.enabled = True
    result = pipeline.mask("паспорт 4509 123456", policy)
    assert not any(ch.isdigit() for ch in result.masked_text)


def test_token_strategy_is_reversible(pipeline) -> None:
    policy = pipeline.policies.by_id("alfagen-chat")
    result = pipeline.mask(SAMPLE, policy)
    assert "[FIO_1:M]" in result.masked_text
    record = FakeRecord(SAMPLE, result.masked_text, result.mapping)
    assert pipeline.demask(result.masked_text, record) == SAMPLE


def test_token_demask_works_inside_llm_answer(pipeline) -> None:
    """Ответ модели содержит плейсхолдеры внутри нового текста."""
    policy = pipeline.policies.by_id("alfagen-chat")
    result = pipeline.mask(SAMPLE, policy)
    record = FakeRecord(SAMPLE, result.masked_text, result.mapping)

    llm_answer = "Уважаемый [FIO_1], ваша карта [CARD_NUMBER_1] перевыпущена. Владелец: [FIO_1]."
    restored = pipeline.demask(llm_answer, record)
    assert restored.startswith("Уважаемый Иван Иванович, ")      # в обращении — имя и отчество
    assert "Владелец: Иванов Иван Иванович." in restored            # вне обращения — полное ФИО
    assert "4276 3800 1234 5679" in restored


def test_synthetic_keeps_text_readable(pipeline) -> None:
    policy = pipeline.policies.by_id("analytics-sandbox")
    result = pipeline.mask(SAMPLE, policy)
    assert "*" not in result.masked_text
    assert "Иванов Иван Иванович" not in result.masked_text


def test_synthetic_is_stable_for_same_value() -> None:
    first = strategies.apply_synthetic(PDType.FIO, "Иванов Иван Иванович")
    second = strategies.apply_synthetic(PDType.FIO, "Иванов Иван Иванович")
    assert first == second


def test_mask_keep_edges_respects_separators() -> None:
    assert strategies.mask_keep_edges("4509 123456", PartialRule(2, 2), digits_only=True) == "45** ****56"


def test_email_mask_keeps_tld() -> None:
    masked = strategies.mask_email("ivan.petrov@example.com")
    assert masked.endswith(".com")
    assert "ivan.petrov" not in masked


def test_initials() -> None:
    assert strategies.mask_initials("Иванов Иван Иванович") == "И. И. И."


def test_exact_roundtrip_for_partial_strategy(pipeline) -> None:
    """Частичная маска необратима сама по себе — восстановление идёт по записи."""
    policy = pipeline.policies.by_id("loadtest")
    result = pipeline.mask(SAMPLE, policy)
    record = FakeRecord(SAMPLE, result.masked_text, result.mapping)
    assert pipeline.demask(result.masked_text, record) == SAMPLE


def test_no_pd_left_in_masked_text(pipeline) -> None:
    policy = pipeline.policies.by_id("loadtest")
    text = (
        "Иванов Иван Иванович, дата рождения 15.03.1985, паспорт 4509 123456, "
        "ИНН 500100732259, карта 4276 3800 1234 5679, CVV 123, "
        "телефон +7 916 123-45-67, почта ivan@example.com"
    )
    masked = pipeline.mask(text, policy).masked_text
    for secret in (
        "Иванов Иван Иванович", "15.03.1985", "4509 123456", "500100732259",
        "4276 3800 1234 5679", "+7 916 123-45-67", "ivan@example.com",
    ):
        assert secret not in masked, f"утечка в маске: {secret}"
