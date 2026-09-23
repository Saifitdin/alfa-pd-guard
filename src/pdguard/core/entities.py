"""Доменная модель: типы ПД, найденные сущности, результат обработки."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class PDType(StrEnum):
    """17 типов ПД из ТЗ + расширения.

    Значения совпадают с ключами в config/pd_types.yaml и с метками в логах.
    """

    FIO = "fio"
    BIRTH_DATE = "birth_date"
    BIRTH_PLACE = "birth_place"
    PASSPORT_RF = "passport_rf"
    CITIZENSHIP = "citizenship"
    PASSPORT_ISSUER = "passport_issuer"
    DEPARTMENT_CODE = "department_code"
    PASSPORT_ISSUE_DATE = "passport_issue_date"
    DRIVER_LICENSE = "driver_license"
    ADDRESS = "address"
    EMAIL = "email"
    PHONE = "phone"
    INN = "inn"
    CARD_NUMBER = "card_number"
    CVV = "cvv"
    PIN = "pin"
    CARD_HOLDER = "card_holder"

    # Расширения сверх обязательного перечня
    SNILS = "snils"
    FOREIGN_PASSPORT = "foreign_passport"
    BANK_ACCOUNT = "bank_account"
    DATE_GENERIC = "date_generic"


#: Типы, которые сами по себе ПД не образуют и маскируются только в связке.
#: Управляется в config/systems.yaml -> combination_rules.
CONTEXT_DEPENDENT: frozenset[PDType] = frozenset({PDType.CVV, PDType.PIN})


@dataclass(slots=True)
class Entity:
    """Найденный фрагмент ПД.

    start/end — позиции в исходной строке (Python-срез: text[start:end]).
    confidence — уверенность детектора [0..1]; используется при разрешении
    пересечений и может отсекаться порогом в политике системы.
    """

    #: Идентификатор типа. Значения PDType — это str, поэтому сюда же
    #: попадают пользовательские типы из config/pd_types.yaml.
    type: str
    start: int
    end: int
    value: str
    confidence: float = 1.0
    detector: str = ""
    #: Произвольные подсказки детектора (например, распознанный формат даты).
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: Entity) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(slots=True)
class MaskResult:
    """Результат прямого прохода (маскирование)."""

    masked_text: str
    entities: list[Entity]
    #: placeholder -> исходное значение. Основа для демаскирования ответа LLM.
    mapping: dict[str, str]
    #: Типы, которые были реально замаскированы (для логов и метрик).
    masked_types: list[str]
