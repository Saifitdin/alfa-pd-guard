"""Реестр детекторов: загрузка словарей и единая точка входа в распознавание."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from pdguard.core.entities import Entity

from .address import AddressDetector, _normalize_address
from .gender import infer_gender
from .name_forms import short_form
from .names import NameDetector, NameDictionaries
from .structured import STRUCTURED_DETECTORS

log = logging.getLogger(__name__)


def _load_names(path: Path) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """(все имена, мужские, женские). Секции «## male» / «## female» в файле
    делят словарь по роду; строки вне секций попадают только в общий набор."""
    if not path.exists():
        log.warning("Словарь имён не найден: %s", path)
        return frozenset(), frozenset(), frozenset()
    all_names: set[str] = set()
    by_section: dict[str, set[str]] = {"male": set(), "female": set()}
    section: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().lower()
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if not line or line.startswith("#"):
            continue
        all_names.add(line)
        if section in by_section:
            by_section[section].add(line)
    return frozenset(all_names), frozenset(by_section["male"]), frozenset(by_section["female"])


def _load_stoplist(path: Path) -> dict[str, frozenset[str]]:
    if not path.exists():
        log.warning("Стоп-лист не найден: %s", path)
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {
        "public_full_names": frozenset(x.lower() for x in raw.get("public_full_names", [])),
        "public_surnames": frozenset(x.lower() for x in raw.get("public_surnames", [])),
        "bank_addresses": frozenset(
            _normalize_address(x) for x in raw.get("bank_addresses", [])
        ),
        "organizations": frozenset(x.lower() for x in raw.get("organizations", [])),
    }


class DetectorRegistry:
    """Держит загруженные словари и прогоняет текст через все детекторы."""

    def __init__(self, data_dir: Path, address_min_components: int = 2) -> None:
        self._data_dir = data_dir
        self._address_min_components = address_min_components
        self.reload()

    def reload(self) -> None:
        """Перечитывает словари с диска — вызывается из POST /admin/reload."""
        names, self._male_names, self._female_names = _load_names(self._data_dir / "names_ru.txt")
        self._given_names = names
        stop = _load_stoplist(self._data_dir / "stoplist.yaml")

        self._names = NameDetector(
            NameDictionaries(
                given_names=names,
                stop_full_names=stop.get("public_full_names", frozenset()),
                stop_surnames=stop.get("public_surnames", frozenset()),
            )
        )
        self._addresses = AddressDetector(
            stop_addresses=stop.get("bank_addresses", frozenset()),
            min_components=self._address_min_components,
        )
        log.info(
            "Словари загружены: имён=%d, стоп-ФИО=%d, стоп-адресов=%d",
            len(names),
            len(stop.get("public_surnames", frozenset())),
            len(stop.get("bank_addresses", frozenset())),
        )

    def gender_of(self, fio: str) -> str | None:
        """«F» / «M» по ФИО или None — для грамматической подсказки в токене."""
        return infer_gender(fio, self._female_names, self._male_names)

    def short_form_of(self, fio: str) -> str | None:
        """«Имя Отчество» для обращения, либо None — тогда подставляется полное ФИО."""
        return short_form(fio, self._given_names)

    def detect(self, text: str) -> list[Entity]:
        entities: list[Entity] = []
        for detector in STRUCTURED_DETECTORS:
            entities.extend(detector(text))
        entities.extend(self._names.detect(text))
        entities.extend(self._addresses.detect(text))
        return entities
