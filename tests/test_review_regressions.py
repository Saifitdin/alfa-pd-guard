"""Регрессии, найденные повторным аудитом.

Каждый тест здесь — воспроизведение конкретного дефекта, который до правки
проходил незамеченным: два из них были утечками ФИО.
"""

from __future__ import annotations

import base64
import os

import pytest

from pdguard.core.entities import PDType
from pdguard.core.pipeline import CHUNK_SIZE, _split_chunks
from pdguard.core.store import Cipher, InvalidStoreKey

from helpers import entities_of, types_in


# --- регистр: прямое требование ТЗ «идентификация не должна зависеть от регистра»

@pytest.mark.parametrize(
    "text",
    [
        "КЛИЕНТ ИВАНОВ ИВАН ИВАНОВИЧ, ПАСПОРТ 4509 123456",
        "клиент иванов иван иванович, паспорт 4509 123456",
        "Клиент ИВАНОВ Иван Иванович",
        "на имя петровой анны сергеевны",
    ],
)
def test_fio_is_case_insensitive(pipeline, policy, text: str) -> None:
    """До правки ФИО капсом и в нижнем регистре уходило в LLM открытым."""
    masked = pipeline.mask(text, policy).masked_text
    for secret in ("Иванов Иван Иванович", "ИВАНОВ ИВАН ИВАНОВИЧ", "иванов иван иванович",
                   "петровой анны сергеевны", "ИВАНОВ Иван Иванович"):
        assert secret not in masked, f"утечка ФИО: {masked}"
    assert PDType.FIO in types_in(pipeline, text)


def test_caps_initials(pipeline) -> None:
    found = entities_of(pipeline, "КЛИЕНТ ИВАНОВ И.И., ПАСПОРТ 4509 123456", PDType.FIO)
    assert found and "ИВАНОВ" in found[0].value


def test_lowercase_without_patronymic_stays_conservative(pipeline) -> None:
    """Без заглавных букв и без отчества сигнала нет — и ложных срабатываний тоже."""
    assert not entities_of(pipeline, "вчера утром шли дела на север", PDType.FIO)


def test_caps_abbreviations_are_not_names(pipeline) -> None:
    """Капс-поддержка не должна превращать «ГУ МВД РОССИИ» в ФИО."""
    text = "выдан ГУ МВД РОССИИ ПО Г. МОСКВЕ, ООО РОМАШКА"
    assert not entities_of(pipeline, text, PDType.FIO)


# --- известная фамилия у живого человека

def test_famous_surname_with_patronymic_is_fully_masked(pipeline, policy) -> None:
    """«Толстой Игорь Петрович» — клиент, а не классик: фамилия тоже маскируется."""
    text = "Позвонил Толстой Игорь Петрович по вопросу карты"
    masked = pipeline.mask(text, policy).masked_text
    assert "Толстой" not in masked
    found = entities_of(pipeline, text, PDType.FIO)
    assert found and found[0].value == "Толстой Игорь Петрович"


def test_famous_full_name_still_stopped(pipeline) -> None:
    for text in (
        "Стихи Николая Алексеевича Некрасова изучают в школе",
        "Лев Николаевич Толстой написал «Войну и мир»",
        "Поэт Александр Сергеевич Пушкин",
    ):
        assert not entities_of(pipeline, text, PDType.FIO), text


# --- держатель карты

def test_card_brand_is_not_a_holder(pipeline) -> None:
    text = "Карта VISA CLASSIC 4276 3800 1234 5679, VALID THRU 12/28"
    assert not entities_of(pipeline, text, PDType.CARD_HOLDER)


def test_title_case_holder_is_detected(pipeline) -> None:
    text = "Держатель карты Ivan Petrov, карта 4276 3800 1234 5679"
    found = entities_of(pipeline, text, PDType.CARD_HOLDER)
    assert found and found[0].value == "Ivan Petrov"


# --- даты прописью в именительном падеже

@pytest.mark.parametrize(
    "text",
    [
        "дата рождения пятнадцатое марта 1985 года",
        "дата рождения: третье мая 1990",
        "дата рождения двадцать первое июня 2001 г.",
    ],
)
def test_nominative_ordinal_dates(pipeline, text: str) -> None:
    assert PDType.BIRTH_DATE in types_in(pipeline, text), text


# --- резак крупных текстов

def test_chunk_splitter_always_makes_progress() -> None:
    """Единственный перенос в начале блока без пробелов не должен плодить
    вырожденные куски длиной в несколько символов."""
    blob = "а" * 5 + "\n" + "б" * 60_000
    chunks = _split_chunks(blob)
    sizes = [len(chunk) for _, chunk in chunks]
    assert all(size >= CHUNK_SIZE // 2 for size in sizes[:-1]), sizes
    assert len(chunks) <= 5


def test_chunk_splitter_covers_whole_text() -> None:
    text = ("Клиент Иванов Иван Иванович, паспорт 4509 123456. " * 2000)
    chunks = _split_chunks(text)
    assert chunks[0][0] == 0
    assert chunks[-1][0] + len(chunks[-1][1]) == len(text)
    for (offset, chunk) in chunks:
        assert text[offset : offset + len(chunk)] == chunk


# --- ключ шифрования

def test_malformed_store_key_gives_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDGUARD_STORE_KEY", "не-base64!!!")
    with pytest.raises(InvalidStoreKey):
        Cipher()


def test_wrong_length_store_key_gives_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PDGUARD_STORE_KEY", base64.b64encode(os.urandom(20)).decode())
    with pytest.raises(InvalidStoreKey, match="20 байт"):
        Cipher()
