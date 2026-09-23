"""Политики систем-потребителей и справочник типов ПД.

Весь конфигурируемый контур модуля собран здесь: какие системы допущены,
что именно им маскировать, каким способом и разрешено ли демаскирование.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pdguard.core.entities import Entity
from pdguard.core.mask.strategies import DEFAULT_PARTIAL_RULES, PartialRule

log = logging.getLogger(__name__)

MASKING_STRATEGIES = frozenset({"partial", "full", "token", "synthetic"})
ALL_TYPES = "all"


class ConfigError(RuntimeError):
    """Конфигурация не прошла валидацию на старте."""


@dataclass(slots=True)
class SystemPolicy:
    id: str
    name: str
    api_key: str
    enabled: bool = True
    masking_strategy: str = "partial"
    demasking_enabled: bool = True
    min_confidence: float = 0.7
    pd_types: frozenset[str] = field(default_factory=frozenset)
    all_types: bool = True
    combination_rules: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Добавлять ли к токену ФИО подсказку рода: «[FIO_1:F]».
    token_gender_hint: bool = True

    def wants(self, pd_type: str) -> bool:
        return self.all_types or pd_type in self.pd_types


@dataclass(slots=True)
class CustomPattern:
    pd_type: str
    regex: re.Pattern[str]
    markers: tuple[str, ...]
    confidence: float

    def detect(self, text: str) -> list[Entity]:
        found: list[Entity] = []
        for match in self.regex.finditer(text):
            if self.markers:
                window = text[max(0, match.start() - 60) : match.end() + 60].lower()
                if not any(marker in window for marker in self.markers):
                    continue
            found.append(
                Entity(
                    self.pd_type,
                    match.start(),
                    match.end(),
                    match.group(),
                    self.confidence,
                    f"custom:{self.pd_type}",
                )
            )
        return found


class PolicyRegistry:
    """Загружает systems.yaml и pd_types.yaml, отдаёт политику по API-ключу."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir
        self._by_key: dict[str, SystemPolicy] = {}
        self._by_id: dict[str, SystemPolicy] = {}
        self.titles: dict[str, str] = {}
        self.partial_rules: dict[str, PartialRule] = {}
        self.custom_patterns: list[CustomPattern] = []
        self.reload()

    # -- загрузка ---------------------------------------------------------

    def reload(self) -> None:
        self._load_types(self._config_dir / "pd_types.yaml")
        self._load_systems(self._config_dir / "systems.yaml")
        log.info(
            "Конфигурация загружена: систем=%d, типов ПД=%d, пользовательских шаблонов=%d",
            len(self._by_id),
            len(self.titles),
            len(self.custom_patterns),
        )

    def _load_types(self, path: Path) -> None:
        raw: dict[str, Any] = {}
        if path.exists():
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        titles: dict[str, str] = {}
        rules: dict[str, PartialRule] = dict(DEFAULT_PARTIAL_RULES)
        for pd_type, spec in (raw.get("types") or {}).items():
            spec = spec or {}
            titles[pd_type] = spec.get("title", pd_type)
            partial = spec.get("partial") or {}
            rules[pd_type] = PartialRule(int(partial.get("head", 0)), int(partial.get("tail", 0)))

        patterns: list[CustomPattern] = []
        for pd_type, spec in (raw.get("custom_patterns") or {}).items():
            spec = spec or {}
            pattern = spec.get("pattern")
            if not pattern:
                continue
            try:
                compiled = re.compile(pattern, re.IGNORECASE | re.UNICODE)
            except re.error as exc:
                raise ConfigError(f"Некорректный шаблон для типа {pd_type}: {exc}") from exc
            patterns.append(
                CustomPattern(
                    pd_type=pd_type,
                    regex=compiled,
                    markers=tuple(m.lower() for m in spec.get("markers", [])),
                    confidence=float(spec.get("confidence", 0.85)),
                )
            )
            titles.setdefault(pd_type, pd_type)

        self.titles = titles
        self.partial_rules = rules
        self.custom_patterns = patterns

    def _load_systems(self, path: Path) -> None:
        if not path.exists():
            raise ConfigError(f"Не найден файл конфигурации систем: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        defaults = raw.get("defaults") or {}

        by_key: dict[str, SystemPolicy] = {}
        by_id: dict[str, SystemPolicy] = {}
        for entry in raw.get("systems") or []:
            policy = self._build_policy(entry, defaults)
            if policy.api_key in by_key:
                raise ConfigError(f"Дублирующийся api_key у системы {policy.id}")
            by_key[policy.api_key] = policy
            by_id[policy.id] = policy

        if not by_id:
            raise ConfigError("В systems.yaml не описано ни одной системы")
        self._by_key, self._by_id = by_key, by_id

    def _build_policy(self, entry: dict[str, Any], defaults: dict[str, Any]) -> SystemPolicy:
        merged = {**defaults, **entry}
        system_id = merged.get("id")
        if not system_id:
            raise ConfigError("У системы отсутствует обязательное поле id")

        strategy = merged.get("masking_strategy", "partial")
        if strategy not in MASKING_STRATEGIES:
            raise ConfigError(
                f"Система {system_id}: неизвестная стратегия {strategy!r}, "
                f"допустимы {sorted(MASKING_STRATEGIES)}"
            )

        raw_types = merged.get("pd_types") or [ALL_TYPES]
        all_types = ALL_TYPES in raw_types
        unknown = {t for t in raw_types if t != ALL_TYPES} - set(self.titles)
        if unknown:
            raise ConfigError(f"Система {system_id}: неизвестные типы ПД {sorted(unknown)}")

        combination = {
            key: tuple(values or ())
            for key, values in (merged.get("combination_rules") or {}).items()
        }

        return SystemPolicy(
            id=system_id,
            name=merged.get("name", system_id),
            api_key=str(merged.get("api_key", "")),
            enabled=bool(merged.get("enabled", True)),
            masking_strategy=strategy,
            demasking_enabled=bool(merged.get("demasking_enabled", True)),
            min_confidence=float(merged.get("min_confidence", 0.7)),
            pd_types=frozenset(t for t in raw_types if t != ALL_TYPES),
            all_types=all_types,
            combination_rules=combination,
            token_gender_hint=bool(merged.get("token_gender_hint", True)),
        )

    # -- доступ -----------------------------------------------------------

    def by_api_key(self, api_key: str | None) -> SystemPolicy | None:
        if not api_key:
            return None
        return self._by_key.get(api_key)

    def by_id(self, system_id: str) -> SystemPolicy | None:
        return self._by_id.get(system_id)

    def all_systems(self) -> list[SystemPolicy]:
        return list(self._by_id.values())
