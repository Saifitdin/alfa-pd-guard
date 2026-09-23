"""Детекторы структурированных типов ПД.

Все шаблоны компилируются один раз на импорте и работают без обращений во
внешние сервисы — это то, что даёт предсказуемую latency под нагрузкой.

Общий приём против ложных срабатываний: шаблон задаёт форму, а решение
принимается по контрольной сумме (карта, ИНН, СНИЛС) или по контексту —
словам-маркерам рядом с находкой.
"""

from __future__ import annotations

import re

from pdguard.core.entities import Entity, PDType

from .validators import (
    digits_only,
    inn_valid,
    luhn_valid,
    plausible_card_bin,
    plausible_phone,
    snils_valid,
    valid_calendar_date,
)

_FLAGS = re.IGNORECASE | re.UNICODE

#: Ширина окна (в символах) для поиска слов-маркеров вокруг находки.
CONTEXT_WINDOW = 60


def _context(text: str, start: int, end: int, window: int = CONTEXT_WINDOW) -> str:
    return text[max(0, start - window) : min(len(text), end + window)].lower()


def _has_marker(text: str, start: int, end: int, markers: tuple[str, ...]) -> bool:
    chunk = _context(text, start, end)
    return any(marker in chunk for marker in markers)


# --------------------------------------------------------------------------
# Email и телефон
# --------------------------------------------------------------------------

RE_EMAIL = re.compile(
    r"\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,24}\b",
    _FLAGS,
)

RE_PHONE = re.compile(
    r"(?<![\d\-])"
    r"(?:\+7|\b8|\b7)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"(?![\d\-])",
    _FLAGS,
)

#: Номер без кода страны: 9xx xxx-xx-xx — требует маркера рядом.
RE_PHONE_SHORT = re.compile(r"(?<![\d\-+])\(?9\d{2}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?![\d\-])", _FLAGS)

PHONE_MARKERS = ("тел", "телефон", "моб", "звон", "номер телефона", "phone", "контакт", "whatsapp")


def detect_email(text: str) -> list[Entity]:
    return [
        Entity(PDType.EMAIL, m.start(), m.end(), m.group(), 0.99, "email")
        for m in RE_EMAIL.finditer(text)
    ]


def detect_phone(text: str) -> list[Entity]:
    found: list[Entity] = []
    for match in RE_PHONE.finditer(text):
        if plausible_phone(match.group()):
            found.append(Entity(PDType.PHONE, match.start(), match.end(), match.group(), 0.97, "phone"))
    for match in RE_PHONE_SHORT.finditer(text):
        if any(match.start() < e.end and e.start < match.end() for e in found):
            continue
        if _has_marker(text, match.start(), match.end(), PHONE_MARKERS):
            found.append(
                Entity(PDType.PHONE, match.start(), match.end(), match.group(), 0.85, "phone_short")
            )
    return found


# --------------------------------------------------------------------------
# Карта, CVV, PIN, имя держателя, счёт
# --------------------------------------------------------------------------

RE_CARD = re.compile(r"(?<![\d])\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{1,7}(?![\d])", _FLAGS)
RE_CVV = re.compile(r"(?<![\d])\d{3}(?![\d])", _FLAGS)
RE_PIN = re.compile(r"(?<![\d])\d{4}(?![\d])", _FLAGS)
RE_ACCOUNT = re.compile(r"(?<![\d])\d{20}(?![\d])", _FLAGS)
#: Имя держателя на карте: «IVAN PETROV» капсом или «Ivan Petrov».
_LATIN_NAME_WORD = r"(?:[A-Z]{2,20}|[A-Z][a-z]{1,19})"
RE_CARD_HOLDER = re.compile(rf"\b{_LATIN_NAME_WORD}\s+{_LATIN_NAME_WORD}(?:\s+{_LATIN_NAME_WORD})?\b")

#: Латинские слова, которые стоят рядом с картой, но именем не являются:
#: платёжные системы, продуктовые линейки, надписи на самой карте.
CARD_BRAND_WORDS: frozenset[str] = frozenset(
    """
    visa mastercard maestro mir unionpay amex american express jcb diners club
    classic gold platinum black infinite signature world elite standard business
    premium debit credit card cards bank valid thru exp expires date cvv cvc cvv2
    cvc2 pin code number name holder cardholder member since alfa sber vtb tinkoff
    raiffeisen gazprombank otkritie rosbank
    """.split()
)

CARD_MARKERS = ("карт", "card", "pan", "visa", "mastercard", "мир", "maestro", "счёт", "счет")
CVV_MARKERS = ("cvv", "cvc", "cvv2", "cvc2", "код безопасности", "секретный код", "три цифры")
PIN_MARKERS = ("пин", "pin", "пин-код", "пинкод")
HOLDER_MARKERS = ("держател", "holder", "карт", "card", "на имя")


def detect_card_number(text: str) -> list[Entity]:
    found: list[Entity] = []
    for match in RE_CARD.finditer(text):
        raw = match.group()
        if not luhn_valid(raw):
            continue
        # 20 цифр подряд — это счёт, а не карта.
        if len(digits_only(raw)) > 19:
            continue
        if not plausible_card_bin(raw) and not _has_marker(
            text, match.start(), match.end(), CARD_MARKERS
        ):
            continue
        found.append(Entity(PDType.CARD_NUMBER, match.start(), match.end(), raw, 0.98, "card_luhn"))
    return found


def detect_bank_account(text: str) -> list[Entity]:
    return [
        Entity(PDType.BANK_ACCOUNT, m.start(), m.end(), m.group(), 0.9, "account")
        for m in RE_ACCOUNT.finditer(text)
        if _has_marker(text, m.start(), m.end(), CARD_MARKERS)
    ]


def detect_cvv(text: str) -> list[Entity]:
    return [
        Entity(PDType.CVV, m.start(), m.end(), m.group(), 0.9, "cvv")
        for m in RE_CVV.finditer(text)
        if _has_marker(text, m.start(), m.end(), CVV_MARKERS)
    ]


def detect_pin(text: str) -> list[Entity]:
    return [
        Entity(PDType.PIN, m.start(), m.end(), m.group(), 0.9, "pin")
        for m in RE_PIN.finditer(text)
        if _has_marker(text, m.start(), m.end(), PIN_MARKERS)
    ]


def detect_card_holder(text: str) -> list[Entity]:
    """Имя держателя — латиница рядом с карточным контекстом.

    «VISA CLASSIC» или «VALID THRU» стоят рядом с картой ещё чаще, чем имя,
    поэтому слова платёжных систем и надписей на карте исключаются явно.
    """
    found: list[Entity] = []
    for match in RE_CARD_HOLDER.finditer(text):
        if any(word.lower() in CARD_BRAND_WORDS for word in match.group().split()):
            continue
        if _has_marker(text, match.start(), match.end(), HOLDER_MARKERS):
            found.append(
                Entity(PDType.CARD_HOLDER, match.start(), match.end(), match.group(), 0.88, "holder")
            )
    return found


# --------------------------------------------------------------------------
# ИНН, СНИЛС
# --------------------------------------------------------------------------

RE_INN = re.compile(r"(?<![\d])\d{10}(?:\d{2})?(?![\d])", _FLAGS)
RE_SNILS = re.compile(r"(?<![\d])\d{3}[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{2}(?![\d])", _FLAGS)

INN_MARKERS = ("инн", "налогоплательщ", "n.n.", "idnumber")


def detect_inn(text: str) -> list[Entity]:
    found: list[Entity] = []
    for match in RE_INN.finditer(text):
        raw = match.group()
        checksum_ok = inn_valid(raw)
        marker = _has_marker(text, match.start(), match.end(), INN_MARKERS)
        if not checksum_ok and not marker:
            continue
        confidence = 0.97 if checksum_ok else 0.75
        found.append(Entity(PDType.INN, match.start(), match.end(), raw, confidence, "inn"))
    return found


def detect_snils(text: str) -> list[Entity]:
    return [
        Entity(PDType.SNILS, m.start(), m.end(), m.group(), 0.96, "snils")
        for m in RE_SNILS.finditer(text)
        if snils_valid(m.group())
    ]


# --------------------------------------------------------------------------
# Паспорт, код подразделения, орган выдачи, водительское удостоверение
# --------------------------------------------------------------------------

#: «серия 4509 номер 123456», «серия 45 09 № 123456» — разделяющие слова.
RE_PASSPORT_VERBOSE = re.compile(
    r"сер(?:ия|\.|ии)?\s*[:№#]?\s*(\d{2}\s?\d{2})"
    r"[\s,;]*(?:номер|ном\.?|№|#|n)?\s*[:№#]?\s*(\d{6})",
    _FLAGS,
)
#: Голые «4509 123456» / «4509123456» — только рядом с паспортным контекстом.
RE_PASSPORT_PLAIN = re.compile(r"(?<![\d])(\d{4})[\s\-]?(\d{6})(?![\d])", _FLAGS)

RE_DEPARTMENT_CODE = re.compile(r"(?<![\d])\d{3}[\s\-]\d{3}(?![\d])", _FLAGS)

RE_DRIVER_LICENSE = re.compile(r"(?<![\d])(\d{2}\s?\d{2})\s?(\d{6})(?![\d])", _FLAGS)

RE_PASSPORT_ISSUER = re.compile(
    r"(?:выдан[аоы]?|выдачи органом|кем выдан)\s*[:\-]?\s*"
    # Точки внутри разрешены: «ГУ МВД России по г. Москве» — одна сущность.
    r"((?:[^,;\n]{3,140}?))"
    r"(?=\s*(?:,|;|\.|\n|$|\d{2}[./-]\d{2}[./-]\d{2,4}|код\s+подразделения))",
    _FLAGS,
)

PASSPORT_MARKERS = ("паспорт", "passport", "удостовер", "серия", "документ")
DEPARTMENT_MARKERS = ("код подразделения", "подразделен", "к/п", "кп ")
DRIVER_MARKERS = ("водительск", "в/у", "ву ", "вод. удост", "driver", "права")


def detect_passport_rf(text: str) -> list[Entity]:
    found: list[Entity] = []
    consumed: list[tuple[int, int]] = []

    for match in RE_PASSPORT_VERBOSE.finditer(text):
        found.append(
            Entity(PDType.PASSPORT_RF, match.start(), match.end(), match.group(), 0.96, "passport_verbose")
        )
        consumed.append((match.start(), match.end()))

    for match in RE_PASSPORT_PLAIN.finditer(text):
        if any(match.start() < e and s < match.end() for s, e in consumed):
            continue
        if not _has_marker(text, match.start(), match.end(), PASSPORT_MARKERS):
            continue
        found.append(
            Entity(PDType.PASSPORT_RF, match.start(), match.end(), match.group(), 0.92, "passport_plain")
        )
    return found


def detect_department_code(text: str) -> list[Entity]:
    return [
        Entity(PDType.DEPARTMENT_CODE, m.start(), m.end(), m.group(), 0.93, "department_code")
        for m in RE_DEPARTMENT_CODE.finditer(text)
        if _has_marker(text, m.start(), m.end(), DEPARTMENT_MARKERS + PASSPORT_MARKERS)
    ]


def detect_driver_license(text: str) -> list[Entity]:
    return [
        Entity(PDType.DRIVER_LICENSE, m.start(), m.end(), m.group(), 0.93, "driver_license")
        for m in RE_DRIVER_LICENSE.finditer(text)
        if _has_marker(text, m.start(), m.end(), DRIVER_MARKERS)
    ]


def detect_passport_issuer(text: str) -> list[Entity]:
    found: list[Entity] = []
    for match in RE_PASSPORT_ISSUER.finditer(text):
        value = match.group(1).strip()
        if len(value) < 4:
            continue
        start = match.start(1)
        found.append(
            Entity(PDType.PASSPORT_ISSUER, start, start + len(value), value, 0.85, "passport_issuer")
        )
    return found


# --------------------------------------------------------------------------
# Даты
# --------------------------------------------------------------------------

MONTHS_RU: dict[str, int] = {}
for _index, _stems in enumerate(
    (
        ("янв", "jan"),
        ("фев", "feb"),
        ("мар", "mar"),
        ("апр", "apr"),
        ("ма", "мая", "май", "may"),
        ("июн", "jun"),
        ("июл", "jul"),
        ("авг", "aug"),
        ("сен", "sep"),
        ("окт", "oct"),
        ("ноя", "nov"),
        ("дек", "dec"),
    ),
    start=1,
):
    for _stem in _stems:
        MONTHS_RU[_stem] = _index

_MONTH_WORDS = (
    r"январ\w*|феврал\w*|март\w*|апрел\w*|ма[йяе]\w*|июн\w*|июл\w*|"
    r"август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*|"
    # Английские названия: встречаются в анкетах и выписках.
    r"jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*|nov\w*|dec\w*"
)

#: Числовые форматы: dd.mm.yyyy, yyyy-mm-dd, mm/dd/yyyy — порядок определяется
#: календарной валидацией, поэтому «гггг.дд.мм» тоже распознаётся.
RE_DATE_NUMERIC = re.compile(r"(?<![\d])(\d{1,4})[./\-](\d{1,2})[./\-](\d{2,4})(?![\d])", _FLAGS)

#: «15 марта 1985», «15 марта 1985 г.»
RE_DATE_WORD = re.compile(
    rf"(?<![\w])(\d{{1,2}})\s+({_MONTH_WORDS})\s+(\d{{4}})(?:\s*(?:г\.?|года?))?",
    _FLAGS,
)

#: «марта 15, 1985» — англоязычный порядок.
RE_DATE_WORD_FIRST = re.compile(rf"(?<![\w])({_MONTH_WORDS})\s+(\d{{1,2}}),?\s+(\d{{4}})", _FLAGS)

ORDINAL_DAYS = {
    "первого": 1, "второго": 2, "третьего": 3, "четвертого": 4, "четвёртого": 4,
    "пятого": 5, "шестого": 6, "седьмого": 7, "восьмого": 8, "девятого": 9,
    "десятого": 10, "одиннадцатого": 11, "двенадцатого": 12, "тринадцатого": 13,
    "четырнадцатого": 14, "пятнадцатого": 15, "шестнадцатого": 16,
    "семнадцатого": 17, "восемнадцатого": 18, "девятнадцатого": 19,
    "двадцатого": 20, "двадцать первого": 21, "двадцать второго": 22,
    "двадцать третьего": 23, "двадцать четвертого": 24, "двадцать четвёртого": 24,
    "двадцать пятого": 25, "двадцать шестого": 26, "двадцать седьмого": 27,
    "двадцать восьмого": 28, "двадцать девятого": 29, "тридцатого": 30,
    "тридцать первого": 31,
}
# Именительный падеж: «пятнадцатое марта» встречается не реже родительного.
ORDINAL_DAYS.update(
    {
        (key[:-4] + "ье" if key.endswith("ьего") else key[:-3] + "ое"): day
        for key, day in list(ORDINAL_DAYS.items())
    }
)

#: «пятнадцатого марта 1985 года» — дата прописью.
RE_DATE_ORDINAL = re.compile(
    rf"(?<![\w])({'|'.join(sorted(ORDINAL_DAYS, key=len, reverse=True))})\s+({_MONTH_WORDS})"
    rf"(?:\s+(\d{{4}})\s*(?:г\.?|года?)?)?",
    _FLAGS,
)

BIRTH_MARKERS = ("рожд", "родил", "г.р", "гр.", "date of birth", "dob", "появился на свет")
ISSUE_MARKERS = ("выдан", "выдачи", "issued", "дата выдачи")


def _month_from_word(word: str) -> int | None:
    lowered = word.lower()
    for stem, number in MONTHS_RU.items():
        if lowered.startswith(stem):
            return number
    return None


#: Маркер справа от даты («15.03.1985 — дата рождения») встречается реже,
#: чем слева, поэтому получает штраф при сравнении расстояний.
_RIGHT_MARKER_PENALTY = 6


def _nearest_marker(text: str, start: int, end: int, markers: tuple[str, ...]) -> int | None:
    """Расстояние в символах до ближайшего маркера, или None если его нет."""
    left = text[max(0, start - CONTEXT_WINDOW) : start].lower()
    right = text[end : end + CONTEXT_WINDOW].lower()
    best: int | None = None
    for marker in markers:
        index = left.rfind(marker)
        if index != -1:
            distance = len(left) - (index + len(marker))
            best = distance if best is None else min(best, distance)
        index = right.find(marker)
        if index != -1:
            distance = index + _RIGHT_MARKER_PENALTY
            best = distance if best is None else min(best, distance)
    return best


def _classify_date(text: str, start: int, end: int) -> PDType:
    """Тип даты определяется ближайшим словом-маркером.

    Сравнение именно по расстоянию, а не по факту наличия: в строке
    «дата рождения 15.03.1985, паспорт выдан 20.01.2015» оба маркера попадают
    в окно каждой даты, и только близость разводит их по разным типам.
    """
    birth = _nearest_marker(text, start, end, BIRTH_MARKERS)
    issue = _nearest_marker(text, start, end, ISSUE_MARKERS)
    if birth is None and issue is None:
        return PDType.DATE_GENERIC
    if issue is None:
        return PDType.BIRTH_DATE
    if birth is None:
        return PDType.PASSPORT_ISSUE_DATE
    return PDType.BIRTH_DATE if birth <= issue else PDType.PASSPORT_ISSUE_DATE


def detect_dates(text: str) -> list[Entity]:
    found: list[Entity] = []
    taken: list[tuple[int, int]] = []

    def _add(start: int, end: int, value: str, confidence: float, detector: str, fmt: str) -> None:
        if any(start < e and s < end for s, e in taken):
            return
        found.append(
            Entity(_classify_date(text, start, end), start, end, value, confidence, detector, {"format": fmt})
        )
        taken.append((start, end))

    for match in RE_DATE_WORD.finditer(text):
        month = _month_from_word(match.group(2))
        if month and valid_calendar_date(int(match.group(1)), month, int(match.group(3))):
            _add(match.start(), match.end(), match.group(), 0.95, "date_word", "dd month yyyy")

    for match in RE_DATE_WORD_FIRST.finditer(text):
        month = _month_from_word(match.group(1))
        if month and valid_calendar_date(int(match.group(2)), month, int(match.group(3))):
            _add(match.start(), match.end(), match.group(), 0.92, "date_word_first", "month dd, yyyy")

    for match in RE_DATE_ORDINAL.finditer(text):
        month = _month_from_word(match.group(2))
        if month is None:
            continue
        _add(match.start(), match.end(), match.group(), 0.9, "date_ordinal", "ordinal")

    for match in RE_DATE_NUMERIC.finditer(text):
        first, second, third = (int(g) for g in match.groups())
        fmt = _resolve_numeric_date(first, second, third)
        if fmt is None:
            continue
        _add(match.start(), match.end(), match.group(), 0.9, "date_numeric", fmt)

    return found


def _resolve_numeric_date(first: int, second: int, third: int) -> str | None:
    """Определяет раскладку числовой даты, пробуя варианты по валидности.

    Порядок проб задаёт приоритет: российский dd.mm.yyyy выигрывает у
    американского mm.dd.yyyy, когда подходят оба.
    """
    year_third = third if third > 99 else (1900 + third if third > 30 else 2000 + third)
    candidates = (
        ("dd.mm.yyyy", first, second, year_third),
        ("mm.dd.yyyy", second, first, year_third),
        ("yyyy.mm.dd", third, second, first),
        ("yyyy.dd.mm", second, third, first),
    )
    for fmt, day, month, year in candidates:
        if valid_calendar_date(day, month, year):
            return fmt
    return None


# --------------------------------------------------------------------------
# Гражданство, место рождения
# --------------------------------------------------------------------------

RE_CITIZENSHIP = re.compile(
    r"(?:гражданств[оаеи]|гражданин|гражданка|citizenship|nationality)\s*[:\-]?\s*"
    r"([А-ЯЁA-Z][\w\- ]{1,40}?)(?=[,.;\n]|\s+(?:паспорт|инн|дата|прожива)|$)",
    _FLAGS,
)

RE_BIRTH_PLACE = re.compile(
    r"(?:мест[оа] рождения|родил[аяс]{1,3}ь? в|уроженец|уроженка|place of birth|born in)"
    r"\s*[:\-]?\s*((?:г\.|город|пос\.|село|с\.|д\.|дер\.)?\s*[А-ЯЁA-Z][\w\- ]{1,60}?)"
    r"(?=[,.;\n]|$)",
    _FLAGS,
)


def detect_citizenship(text: str) -> list[Entity]:
    found: list[Entity] = []
    for match in RE_CITIZENSHIP.finditer(text):
        value = match.group(1).strip()
        if len(value) < 2:
            continue
        start = match.start(1)
        found.append(Entity(PDType.CITIZENSHIP, start, start + len(value), value, 0.88, "citizenship"))
    return found


#: «Родился в Москве» про историческую фигуру — не персональные данные клиента.
#: Те же маркеры, что подавляют ФИО: держим их в одном месте с детектором имён.
PUBLIC_CONTEXT_MARKERS = (
    "поэт", "писател", "композитор", "художник", "актёр", "актер", "режиссёр",
    "режиссер", "учёный", "ученый", "академик", "космонавт", "полководец",
    "император", "царь", "философ", "певец", "певица", "персонаж", "написал",
    "памятник", "в честь", "музей", "биограф",
)
CLIENT_CONTEXT_MARKERS = (
    "клиент", "на имя", "фио", "паспорт", "анкет", "заявлен", "заемщик",
    "заёмщик", "получател", "плательщик", "гражданин", "гражданка",
)


def _is_public_context(text: str, start: int, end: int) -> bool:
    window = _context(text, start, end, window=90)
    if any(marker in window for marker in CLIENT_CONTEXT_MARKERS):
        return False
    return any(marker in window for marker in PUBLIC_CONTEXT_MARKERS)


def detect_birth_place(text: str) -> list[Entity]:
    found: list[Entity] = []
    for match in RE_BIRTH_PLACE.finditer(text):
        value = match.group(1).strip()
        if len(value) < 2:
            continue
        start = match.start(1)
        if _is_public_context(text, start, start + len(value)):
            continue
        found.append(Entity(PDType.BIRTH_PLACE, start, start + len(value), value, 0.85, "birth_place"))
    return found


#: Реестр детекторов этого модуля. Порядок не важен — пересечения разрешает
#: pipeline по confidence и длине спана.
STRUCTURED_DETECTORS = (
    detect_email,
    detect_phone,
    detect_card_number,
    detect_bank_account,
    detect_cvv,
    detect_pin,
    detect_card_holder,
    detect_inn,
    detect_snils,
    detect_passport_rf,
    detect_department_code,
    detect_driver_license,
    detect_passport_issuer,
    detect_dates,
    detect_citizenship,
    detect_birth_place,
)
