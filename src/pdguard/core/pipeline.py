"""Конвейер обработки: идентификация → маскирование → демаскирование.

Здесь нет обращений к LLM и вообще никаких сетевых вызовов: распознавание
детерминированное, поэтому время ответа зависит только от длины текста и
предсказуемо под нагрузкой.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

from pdguard.core.detect.name_forms import wants_short_form
from pdguard.core.detect.registry import DetectorRegistry
from pdguard.core.entities import Entity, MaskResult, PDType
from pdguard.core.mask import strategies
from pdguard.core.policy import PolicyRegistry, SystemPolicy
from pdguard.core.store import MappingRecord

log = logging.getLogger(__name__)

#: Тексты длиннее порога режутся на части: так пиковая latency на 100k токенов
#: остаётся линейной, а не зависит от поведения регулярок на гигантской строке.
CHUNK_THRESHOLD = 40_000
CHUNK_SIZE = 20_000
#: Нахлёст между частями, чтобы сущность на стыке не потерялась.
CHUNK_OVERLAP = 200

#: Любой наш токен: «[FIO_1]», «[FIO_1:F]» → группа 1 = «FIO_1».
RE_TOKEN = re.compile(r"\[([A-Z_]+_\d+)(?::[A-Z])?\]")


def estimate_tokens(text: str) -> int:
    """Грубая оценка числа токенов для метрики TPS (≈4 символа на токен)."""
    return max(1, len(text) // 4)


def _split_chunks(text: str) -> list[tuple[int, str]]:
    """Режет текст по границам строк/пробелов, возвращая (offset, кусок)."""
    if len(text) <= CHUNK_THRESHOLD:
        return [(0, text)]

    chunks: list[tuple[int, str]] = []
    position = 0
    length = len(text)
    # Граница по переносу или пробелу берётся только из второй половины окна:
    # иначе единственный перенос в начале блока без пробелов давал бы
    # вырожденные куски длиной 5, 4, 3, 2, 1 символ и топтание на месте.
    min_cut = CHUNK_SIZE // 2
    while position < length:
        end = min(position + CHUNK_SIZE, length)
        if end < length:
            boundary = text.rfind("\n", position + min_cut, end)
            if boundary == -1:
                boundary = text.rfind(" ", position + min_cut, end)
            if boundary != -1:
                end = boundary
        chunks.append((position, text[position:end]))
        if end >= length:
            break
        position = end - CHUNK_OVERLAP
    return chunks


def resolve_overlaps(entities: list[Entity]) -> list[Entity]:
    """Оставляет непересекающиеся сущности: сначала уверенные, потом длинные.

    Принятые интервалы хранятся отсортированными по началу, поэтому проверка
    пересечения — это два соседа через бинарный поиск, а не перебор всего
    накопленного списка. На тексте в 100 000 токенов разница принципиальная:
    наивный перебор давал около пяти секунд только на этом шаге.
    """
    ordered = sorted(entities, key=lambda e: (-e.confidence, -e.length, e.start))
    starts: list[int] = []
    accepted: list[Entity] = []

    for entity in ordered:
        index = bisect_left(starts, entity.start)
        if index > 0 and accepted[index - 1].end > entity.start:
            continue
        if index < len(accepted) and entity.end > accepted[index].start:
            continue
        starts.insert(index, entity.start)
        accepted.insert(index, entity)

    return accepted


def apply_combination_rules(
    entities: list[Entity], rules: dict[str, tuple[str, ...]]
) -> tuple[list[Entity], list[str]]:
    """Отбрасывает типы, которые маскируются только в связке с другими.

    Пример из ТЗ: «пин-код карты» сам по себе не маскируем, «пин-код + номер
    карты» — маскируем.
    """
    if not rules:
        return entities, []

    present = {entity.type for entity in entities}
    kept: list[Entity] = []
    skipped: list[str] = []
    for entity in entities:
        required = rules.get(entity.type)
        if required and not present.intersection(required):
            skipped.append(entity.type)
            continue
        kept.append(entity)
    return kept, skipped


class MaskingPipeline:
    def __init__(self, config_dir: Path, data_dir: Path) -> None:
        self.policies = PolicyRegistry(config_dir)
        self.detectors = DetectorRegistry(data_dir)

    def reload(self) -> None:
        self.policies.reload()
        self.detectors.reload()

    # -- идентификация ----------------------------------------------------

    def detect(self, text: str) -> list[Entity]:
        entities: list[Entity] = []
        for offset, chunk in _split_chunks(text):
            chunk_entities = self.detectors.detect(chunk)
            for pattern in self.policies.custom_patterns:
                chunk_entities.extend(pattern.detect(chunk))
            if offset:
                for entity in chunk_entities:
                    entity.start += offset
                    entity.end += offset
            entities.extend(chunk_entities)
        return entities

    # -- прямой проход ----------------------------------------------------

    def mask(self, text: str, policy: SystemPolicy) -> MaskResult:
        entities = [
            entity
            for entity in self.detect(text)
            if entity.confidence >= policy.min_confidence and policy.wants(entity.type)
        ]
        entities = resolve_overlaps(entities)
        entities, _skipped = apply_combination_rules(entities, policy.combination_rules)

        if not entities:
            return MaskResult(text, [], {}, [])

        counters: dict[str, int] = defaultdict(int)
        mapping: dict[str, str] = {}
        pieces: list[str] = []
        cursor = 0

        for entity in entities:
            counters[entity.type] += 1
            replacement = self._render(entity, policy, counters[entity.type])
            pieces.append(text[cursor : entity.start])
            pieces.append(replacement)
            mapping.setdefault(replacement, entity.value)
            cursor = entity.end

        pieces.append(text[cursor:])
        masked_text = "".join(pieces)
        masked_types = sorted({entity.type for entity in entities})
        return MaskResult(masked_text, entities, mapping, masked_types)

    def _render(self, entity: Entity, policy: SystemPolicy, index: int) -> str:
        strategy = policy.masking_strategy
        # Род нужен только имени и только там, где замена должна остаться
        # грамматически живой: в токене — как подсказка, в синтетике — как
        # выбор пула. Частичная маска «И. И. И.» рода не несёт и не должна.
        gender = None
        if entity.type == PDType.FIO and strategy in ("token", "synthetic") and policy.token_gender_hint:
            gender = self.detectors.gender_of(entity.value)
        if strategy == "token":
            return strategies.apply_token(entity.type, index, hint=gender)
        if strategy == "synthetic":
            return strategies.apply_synthetic(entity.type, entity.value, gender=gender)
        if strategy == "full":
            return strategies.apply_full(entity.value)
        return strategies.apply_partial(entity.type, entity.value, self.policies.partial_rules)

    # -- обратный проход --------------------------------------------------

    def demask(self, text: str, record: MappingRecord) -> str:
        """Возвращает исходный текст.

        Быстрый путь — когда пришла ровно та маска, которую мы отдали: тогда
        отдаём сохранённый оригинал, и позиции знаков совпадают побайтово.
        Общий путь — обратная подстановка по карте плейсхолдеров: он нужен для
        ответа LLM, где маска встречается внутри сгенерированного текста.
        """
        if text == record.masked:
            return record.original

        restored = text
        # Длинные плейсхолдеры заменяем первыми: «[FIO_1]» не должен
        # пострадать от замены «[FIO_11]».
        for placeholder in sorted(record.mapping, key=len, reverse=True):
            if not placeholder:
                continue
            original = record.mapping[placeholder]
            token = RE_TOKEN.fullmatch(placeholder)
            if token:
                base = token.group(1)
                # Модель может вернуть «[FIO_1]» без подсказки «:F» или с
                # просьбой о короткой форме «[FIO_1:F:short]» — принимаем всё.
                pattern = re.compile(rf"\[{re.escape(base)}(?::[A-Z])?(?::(short))?\]")
                is_fio = base.startswith("FIO_")

                def _substitute(match: re.Match[str], original: str = original, is_fio: bool = is_fio) -> str:
                    if is_fio and (match.group(1) or wants_short_form(match.string, match.start())):
                        # В обращении — «Уважаемая Анна Сергеевна», а не полное ФИО.
                        return self.detectors.short_form_of(original) or original
                    return original

                restored = pattern.sub(_substitute, restored)
            elif placeholder in restored:
                restored = restored.replace(placeholder, original)
        return restored
