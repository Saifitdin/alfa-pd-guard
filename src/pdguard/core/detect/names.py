"""Детектор ФИО.

Это самый опасный тип с точки зрения ложных срабатываний: любая пара слов с
заглавной буквы формально похожа на имя. Поэтому решение принимается не по
форме, а по трём независимым сигналам:

1. морфология — отчество (-ович/-евна) и фамильные суффиксы (-ов/-ский/-дзе);
2. словарь личных имён;
3. контекст — слова «клиент», «на имя», «плательщик» рядом с находкой.

Поверх этого работает стоп-лист публичных персон: «поэт Александр Пушкин» не
является персональными данными клиента. Стоп-лист лежит в data/stoplist.yaml
и пополняется без изменения кода.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from pdguard.core.entities import Entity, PDType

_FLAGS = re.IGNORECASE | re.UNICODE

#: Включая косвенные падежи: «Иванович», «Ивановича», «Сергеевны».
PATRONYMIC_SUFFIXES = (
    "ович", "овича", "овичу", "овичем", "евич", "евича", "евичу", "евичем",
    "ьевич", "иевич", "ыч", "ича", "ич",
    "овна", "овны", "овне", "овну", "овной",
    "евна", "евны", "евне", "евну", "евной",
    "ьевна", "иевна", "ична", "ичны", "инична", "иничны",
    "оглы", "кызы", "угли",
)

#: Фамильные окончания, тоже с косвенными падежами: «Петров», «Петрова»,
#: «Петровой», «Достоевского».
SURNAME_SUFFIXES = (
    "ов", "ова", "ову", "овым", "ове", "овой", "овы",
    "ев", "ева", "еву", "евым", "еве", "евой",
    "ёв", "ёва", "ёву", "ин", "ина", "ину", "иным", "ине", "иной",
    "ын", "ына", "ыну", "ыной",
    "ский", "ского", "скому", "ским", "ская", "ской", "скую",
    "цкий", "цкого", "цкая", "цкой",
    "енко", "ко", "ук", "юк", "чук", "ян", "яна", "янц", "швили", "дзе",
    "иа", "ия", "их", "ых", "ец", "ца", "ак", "як", "ник", "арь", "ба", "ва",
)

#: Окончания, которые отбрасываются при поиске имени в словаре:
#: «Анны» → «Анна», «Ивана» → «Иван», «Сергею» → «Сергей».
_CASE_ENDINGS = ("ы", "и", "у", "е", "ю", "ой", "ей", "ем", "ом", "а", "я")

#: Слова-маркеры, повышающие доверие к находке и разрешающие одиночное имя.
CLIENT_MARKERS = (
    "клиент", "на имя", "фио", "ф.и.о", "получател", "плательщик", "заемщик",
    "заёмщик", "держател", "владелец", "абонент", "пациент", "сотрудник",
    "гражданин", "гражданка", "представител", "доверенн", "поручител",
    "застрахованн", "вкладчик", "покупател", "продавец", "паспорт", "инн",
)

#: Маркеры публичного упоминания: рядом с ними заглавные слова почти всегда
#: относятся к исторической фигуре или произведению, а не к клиенту.
#: Сюда намеренно не включены адресные слова («улица», «проспект»): они часто
#: соседствуют с настоящим ФИО клиента и давали бы пропуски.
PUBLIC_MARKERS = (
    "поэт", "писател", "композитор", "художник", "актёр", "актер",
    "режиссёр", "режиссер", "учёный", "ученый", "академик", "космонавт",
    "полководец", "император", "царь", "философ", "музыкант", "певец", "певица",
    "персонаж", "роман ", "повест", "стихотворен", "стихи ", "стихов", "поэм",
    "произведен", "написал", "сочинил", "биограф", "цитат", "именем", "имени",
    "музей", "театр", "премия", "памятник", "в честь",
)

#: Слова, которые пишутся с заглавной буквы, но именем не являются.
#: Отрезаются с краёв найденной последовательности: в «Клиент Иванов Иван
#: Иванович» именем являются только три последних слова.
NON_NAME_WORDS = frozenset(
    """
    клиент клиента клиенту клиентом гражданин гражданка гражданину держатель
    держателя получатель получателя плательщик плательщика заемщик заёмщик
    владелец владельца абонент абонента пациент пациента сотрудник сотрудника
    менеджер специалист представитель поручитель вкладчик покупатель продавец
    паспорт паспорта адрес адреса телефон телефона почта почты карта карты
    дата даты место места серия серии номер номера код кода инн снилс фио
    отделение отделении банк банка договор договора счет счёт сумма рублей
    уважаемый уважаемая здравствуйте добрый прошу настоящим согласно
    заявление заявлении справка справке выдан выдана выдано российская
    федерация россия москва санкт-петербург общество компания организация
    да нет также однако если при после для это все всё
    """.split()
)

#: Слово с заглавной буквы либо целиком капсом: «Иванов» и «ИВАНОВ» равноправны —
#: ТЗ требует независимости идентификации от регистра.
RE_CAP_WORD = r"(?:[А-ЯЁ][а-яё]+|[А-ЯЁ]{2,})(?:-(?:[А-ЯЁ][а-яё]+|[А-ЯЁ]{2,}))?"
#: До пяти слов подряд: служебные слова по краям отрезаются уже после захвата,
#: иначе «Клиент Иванов Иван Иванович» обрезался бы до «Иванов Иван», и
#: отчество осталось бы в тексте незамаскированным.
RE_NAME_SEQ = re.compile(rf"(?<![\w.]){RE_CAP_WORD}(?:\s+{RE_CAP_WORD}){{0,4}}(?![\w])")

_PATRONYMIC_ALT = "|".join(sorted(PATRONYMIC_SUFFIXES, key=len, reverse=True))
#: Текст целиком в нижнем регистре лишает нас главного сигнала — заглавной
#: буквы. Поэтому строчные ФИО ищутся только вокруг отчества: оно почти не
#: встречается вне имени человека, и такой якорь не даёт перебирать каждое
#: слово текста.
#: Сначала находится само отчество, и только вокруг него подбираются соседние
#: слова. Вариант с общей регуляркой «до двух слов + отчество + до двух слов»
#: пробовал расшириться от каждого слова текста и стоил +17% на 100k токенов.
RE_LOWER_PATRONYMIC = re.compile(rf"(?<![\w.])[а-яё]{{3,}}(?:{_PATRONYMIC_ALT})(?![\w])")
RE_LOWER_WORDS_BEFORE = re.compile(r"((?:[а-яё]+\s+){1,2})$")
RE_LOWER_WORDS_AFTER = re.compile(r"^((?:\s+[а-яё]+){1,2})")
#: Сколько символов вокруг отчества просматривается в поисках соседних слов.
LOWER_NEIGHBOUR_SPAN = 80
#: Без заглавных букв порог выше: нужен и словарный сигнал, и отчество.
LOWERCASE_THRESHOLD = 0.9

#: Максимальная длина ФИО в токенах: фамилия, имя, отчество.
MAX_NAME_TOKENS = 3


def _candidate_windows(
    spans: list[tuple[str, int, int]]
) -> list[list[tuple[str, int, int]]]:
    """Возможные группы токенов, от самых длинных к коротким.

    Длинный ряд заглавных слов («Вчера Иванов Иван Иванович позвонил») может
    содержать ФИО не с начала, поэтому проверяются все окна подходящей длины.
    """
    windows: list[list[tuple[str, int, int]]] = []
    for size in range(min(MAX_NAME_TOKENS, len(spans)), 0, -1):
        for start in range(len(spans) - size + 1):
            windows.append(spans[start : start + size])
    return windows

#: «Иванов И. И.» и «И. И. Иванов»
RE_INITIALS_AFTER = re.compile(rf"(?<![\w.])({RE_CAP_WORD})\s*,?\s+([А-ЯЁ]\.\s*[А-ЯЁ]?\.?)(?![\w])")
RE_INITIALS_BEFORE = re.compile(rf"(?<![\w.])([А-ЯЁ]\.\s*[А-ЯЁ]?\.?)\s+({RE_CAP_WORD})(?![\w])")


@dataclass(slots=True)
class NameDictionaries:
    given_names: frozenset[str]
    stop_full_names: frozenset[str]
    stop_surnames: frozenset[str]

    @staticmethod
    def empty() -> NameDictionaries:
        return NameDictionaries(frozenset(), frozenset(), frozenset())


@lru_cache(maxsize=65536)
def _is_patronymic(token: str) -> bool:
    lowered = token.lower()
    return any(lowered.endswith(suffix) for suffix in PATRONYMIC_SUFFIXES) and len(lowered) > 5


@lru_cache(maxsize=65536)
def _is_surname_shaped(token: str) -> bool:
    lowered = token.lower()
    return any(lowered.endswith(suffix) for suffix in SURNAME_SUFFIXES) and len(lowered) > 3


@lru_cache(maxsize=65536)
def _given_lookup(lowered: str, given_names: frozenset[str]) -> bool:
    """Поиск в словаре имён с учётом косвенных падежей.

    Кэш здесь важнее, чем кажется: одно и то же слово проверяется из
    нескольких окон, а на крупном тексте имена повторяются тысячи раз.
    """
    if lowered in given_names:
        return True
    for ending in _CASE_ENDINGS:
        if not lowered.endswith(ending):
            continue
        stem = lowered[: -len(ending)]
        if len(stem) < 2:
            continue
        if stem in given_names:
            return True
        # «Анны» → «Анн» → «Анна»; «Сергею» → «Серге» → «Сергей»
        if any(stem + tail in given_names for tail in ("а", "я", "й", "ь", "")):
            return True
    return False


def _normalize(tokens: list[str]) -> str:
    return " ".join(t.lower() for t in tokens)


RE_QUOTED = re.compile(r"«[^»]{0,120}»|\"[^\"]{0,120}\"|“[^”]{0,120}”")


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Диапазоны текста в кавычках: там обычно названия, а не имена клиентов."""
    return [(m.start(), m.end()) for m in RE_QUOTED.finditer(text)]


def _trim_non_name_words(matched: str, offset: int) -> list[tuple[str, int, int]]:
    """Отрезает служебные слова с краёв последовательности заглавных слов.

    «Клиент Иванов Иван Иванович» → «Иванов Иван Иванович» с абсолютными
    позициями токенов. Пустой список означает, что имени здесь нет.
    """
    spans: list[tuple[str, int, int]] = []
    position = 0
    for token in matched.split():
        start = matched.index(token, position)
        spans.append((token, offset + start, offset + start + len(token)))
        position = start + len(token)

    while spans and spans[0][0].lower() in NON_NAME_WORDS:
        spans.pop(0)
    while spans and spans[-1][0].lower() in NON_NAME_WORDS:
        spans.pop()
    return spans


class NameDetector:
    """Ищет ФИО и отсеивает публичные персоны по стоп-листу."""

    def __init__(self, dictionaries: NameDictionaries) -> None:
        self._dicts = dictionaries

    def _is_given(self, token: str) -> bool:
        return _given_lookup(token.lower(), self._dicts.given_names)

    def _is_stopped(self, tokens: list[str], text: str, start: int, end: int) -> bool:
        """Публичная персона или «Пушкин» без клиентского контекста."""
        normalized = _normalize(tokens)
        if normalized in self._dicts.stop_full_names:
            return True

        lowered = {t.lower() for t in tokens}
        if lowered & self._dicts.stop_surnames:
            window = text[max(0, start - 80) : min(len(text), end + 80)].lower()
            has_client = any(marker in window for marker in CLIENT_MARKERS)
            has_public = any(marker in window for marker in PUBLIC_MARKERS)
            has_patronymic = any(_is_patronymic(token) for token in tokens)
            if has_public:
                return True
            # Отчество — почти безошибочный признак живого человека, а не
            # ссылки на классика: «Толстой Игорь Петрович» надо маскировать
            # даже без слова «клиент» рядом. Для банка пропуск фамилии
            # дороже лишней маски. Без отчества известная фамилия считается
            # ПД только при явном клиентском контексте.
            if not has_client and not has_patronymic:
                return True
        return False

    @staticmethod
    def _context_flags(text: str, start: int, end: int) -> tuple[bool, bool]:
        """(есть клиентский маркер, есть публичный маркер) вокруг фрагмента."""
        window = text[max(0, start - 60) : min(len(text), end + 60)].lower()
        has_marker = any(marker in window for marker in CLIENT_MARKERS)
        has_public = any(marker in window for marker in PUBLIC_MARKERS)
        return has_marker, has_public

    def _score(self, tokens: list[str], has_marker: bool, has_public: bool) -> float:
        patronymic = [t for t in tokens if _is_patronymic(t)]
        given = [t for t in tokens if self._is_given(t)]
        surname = [t for t in tokens if _is_surname_shaped(t)]

        # Публичное упоминание без единого признака клиента — не ПД.
        # Работает и для тех, кого нет в стоп-листе: «Роман Льва Толстого».
        if not has_marker and has_public:
            return 0.0

        if len(tokens) == 3:
            if patronymic and (given or surname):
                return 0.97
            if given and surname:
                return 0.9
            if has_marker and (given or surname):
                return 0.8
            return 0.0

        if len(tokens) == 2:
            if patronymic and given:
                return 0.95
            if given and surname:
                return 0.92
            if surname and has_marker:
                return 0.85
            if given and has_marker:
                return 0.82
            return 0.0

        token = tokens[0]
        if not has_marker:
            return 0.0
        if self._is_given(token):
            return 0.75
        if _is_surname_shaped(token):
            return 0.7
        return 0.0

    def detect(self, text: str, threshold: float = 0.7) -> list[Entity]:
        found: list[Entity] = []
        taken: list[tuple[int, int]] = []
        quoted = _quoted_spans(text)

        def _add(start: int, end: int, confidence: float, detector: str) -> None:
            if any(start < e and s < end for s, e in taken):
                return
            found.append(Entity(PDType.FIO, start, end, text[start:end], confidence, detector))
            taken.append((start, end))

        for match in RE_INITIALS_AFTER.finditer(text):
            surname = match.group(1)
            if _is_surname_shaped(surname) and not self._is_stopped(
                [surname], text, match.start(), match.end()
            ):
                _add(match.start(), match.end(), 0.93, "fio_initials_after")

        for match in RE_INITIALS_BEFORE.finditer(text):
            surname = match.group(2)
            if _is_surname_shaped(surname) and not self._is_stopped(
                [surname], text, match.start(), match.end()
            ):
                _add(match.start(), match.end(), 0.93, "fio_initials_before")

        for match in RE_NAME_SEQ.finditer(text):
            spans = _trim_non_name_words(match.group(), match.start())
            best = self._best_window(spans, text, quoted, threshold, require_patronymic=False)
            if best is not None:
                confidence, size, start, end = best
                _add(start, end, confidence, f"fio_{size}w")

        for match in RE_LOWER_PATRONYMIC.finditer(text):
            start, end = match.start(), match.end()
            before = RE_LOWER_WORDS_BEFORE.search(text[max(0, start - LOWER_NEIGHBOUR_SPAN) : start])
            after = RE_LOWER_WORDS_AFTER.match(text[end : end + LOWER_NEIGHBOUR_SPAN])
            if before:
                start -= len(before.group(1))
            if after:
                end += len(after.group(1))
            spans = _trim_non_name_words(text[start:end], start)
            best = self._best_window(
                spans, text, quoted, LOWERCASE_THRESHOLD, require_patronymic=True
            )
            if best is not None:
                confidence, size, start, end = best
                _add(start, end, confidence, f"fio_lower_{size}w")

        return found

    def _best_window(
        self,
        spans: list[tuple[str, int, int]],
        text: str,
        quoted: list[tuple[int, int]],
        threshold: float,
        *,
        require_patronymic: bool,
    ) -> tuple[float, int, int, int] | None:
        """Лучшее окно токенов: (уверенность, длина, начало, конец) или None."""
        if not spans:
            return None
        # Окна одной группы различаются на пару слов, поэтому маркеры контекста
        # считаются один раз на группу, а не заново для каждого окна.
        has_marker, has_public = self._context_flags(text, spans[0][1], spans[-1][2])
        best: tuple[float, int, int, int] | None = None
        for window in _candidate_windows(spans):
            tokens = [token for token, _, _ in window]
            if require_patronymic and not any(_is_patronymic(token) for token in tokens):
                continue
            start, end = window[0][1], window[-1][2]
            # Названия в кавычках — «Евгений Онегин» — это не ПД.
            if any(qs <= start and end <= qe for qs, qe in quoted):
                continue
            if self._is_stopped(tokens, text, start, end):
                continue
            confidence = self._score(tokens, has_marker, has_public)
            if confidence <= 0:
                continue
            confidence = min(confidence + self._window_bonus(tokens), 1.0)
            candidate = (confidence, len(tokens), start, end)
            if best is None or candidate > best:
                best = candidate
        if best is None or best[0] < threshold:
            return None
        return best

    def _window_bonus(self, tokens: list[str]) -> float:
        """Премия окну, которое больше похоже на настоящее ФИО целиком.

        Без неё в «Вчера Петров Сергей Николаевич» побеждало бы левое окно
        «Вчера Петров Сергей», и отчество осталось бы незамаскированным.
        """
        bonus = 0.0
        if any(_is_patronymic(token) for token in tokens):
            bonus += 0.04
        if all(
            self._is_given(token) or _is_patronymic(token) or _is_surname_shaped(token)
            for token in tokens
        ):
            bonus += 0.06
        return bonus
