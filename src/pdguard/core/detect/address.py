"""Детектор адресов.

Адрес собирается из компонентов (индекс, регион, город, улица, дом, квартира).
Порог `min_components` не даёт принять одиночное «г. Москва» за адрес клиента.

Адреса отделений Банка лежат в стоп-листе: это вторая «ловушка» из критериев —
адрес офиса не является персональными данными клиента.
"""

from __future__ import annotations

import re

from pdguard.core.entities import Entity, PDType

_FLAGS = re.IGNORECASE | re.UNICODE

_INDEX = r"(?<!\d)\d{6}(?!\d)"
_REGION = r"[А-ЯЁ][а-яё\-]+\s+(?:обл\.|область|края?|респ\.|республик[аи]|округ|АО)"
_CITY = r"(?:г\.|гор\.|город|пос\.|посёлок|поселок|с\.|село|дер\.|деревня|ст\.|станица)\s*[А-ЯЁ][а-яё\-]+"
_STREET = (
    r"(?:ул\.|улица|пр-?кт\.?|пр-?т\.?|проспект|пер\.|переулок|ш\.|шоссе|наб\.|набережная|"
    r"б-?р\.?|бульвар|пл\.|площадь|туп\.|тупик|мкр\.|микрорайон|проезд|линия|аллея)"
    r"\s*[А-ЯЁ0-9][\w\-\s]{0,40}?(?=[,;]|\s+(?:д\.|дом|стр\.|корп\.)|$)"
)
_HOUSE = (
    r"(?:д\.|дом|влд\.|владение)\s*\d+[а-яё]?"
    r"(?:\s*(?:к\.|корп\.|корпус|стр\.|строение)\s*\d+[а-яё]?)?"
)
_APARTMENT = r"(?:кв\.|квартира|офис|оф\.|пом\.|помещение|комн\.)\s*\d+[а-яё]?"

_COMPONENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("index", _INDEX),
    ("region", _REGION),
    ("city", _CITY),
    ("street", _STREET),
    ("house", _HOUSE),
    ("apartment", _APARTMENT),
)

RE_COMPONENTS = re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in _COMPONENT_PATTERNS),
    _FLAGS,
)

#: Максимальный разрыв между компонентами, при котором они считаются одним адресом.
MAX_GAP = 4


def _normalize_address(value: str) -> str:
    return re.sub(r"[\s,.]+", " ", value).strip().lower()


class AddressDetector:
    def __init__(self, stop_addresses: frozenset[str], min_components: int = 2) -> None:
        self._stop = stop_addresses
        self._min_components = min_components

    def detect(self, text: str) -> list[Entity]:
        matches = [
            (m.start(), m.end(), m.lastgroup or "component") for m in RE_COMPONENTS.finditer(text)
        ]
        if not matches:
            return []

        found: list[Entity] = []
        chain_start, chain_end = matches[0][0], matches[0][1]
        kinds = {matches[0][2]}

        for start, end, kind in matches[1:]:
            gap = text[chain_end:start]
            if start - chain_end <= MAX_GAP and not gap.strip(" ,.;"):
                chain_end = end
                kinds.add(kind)
                continue
            self._flush(text, chain_start, chain_end, kinds, found)
            chain_start, chain_end, kinds = start, end, {kind}

        self._flush(text, chain_start, chain_end, kinds, found)
        return found

    def _flush(
        self,
        text: str,
        start: int,
        end: int,
        kinds: set[str],
        sink: list[Entity],
    ) -> None:
        if len(kinds) < self._min_components:
            return
        value = text[start:end].rstrip(" ,.;")
        end = start + len(value)
        if _normalize_address(value) in self._stop:
            return
        confidence = 0.95 if len(kinds) >= 3 else 0.85
        sink.append(
            Entity(PDType.ADDRESS, start, end, value, confidence, "address", {"parts": ",".join(sorted(kinds))})
        )
