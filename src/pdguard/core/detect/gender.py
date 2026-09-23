"""Определение грамматического рода по ФИО.

Нужно не для идентификации, а наоборот: при токенизации модель получает
«[FIO_1]» и не может выбрать «Уважаемый» или «Уважаемая». Подсказка
«[FIO_1:F]» ничего не раскрывает — пол не сужает круг людей сколько-нибудь
заметно, — но даёт модели правильно склонять обращение.

Порядок сигналов — от надёжного к слабому: отчество, фамилия, личное имя.
Если ни один не сработал, рода нет, и плейсхолдер остаётся без суффикса.
"""

from __future__ import annotations

FEMALE = "F"
MALE = "M"

_PATRONYMIC_FEMALE = ("овна", "евна", "ична", "инична", "овны", "евны", "ичны", "овне", "евне",
                      "овну", "евну", "овной", "евной", "кызы")
_PATRONYMIC_MALE = ("ович", "евич", "ьич", "ич", "овича", "евича", "овичу", "евичу",
                    "овичем", "евичем", "оглы", "угли")

_SURNAME_FEMALE = ("ова", "ева", "ёва", "ина", "ына", "ская", "цкая", "ая", "овой", "евой",
                   "иной", "ской", "цкой", "ову", "еву", "ину")
_SURNAME_MALE = ("ов", "ев", "ёв", "ин", "ын", "ский", "цкий", "ой", "ый", "ского", "цкого",
                 "ову", "еву", "ину", "овым", "евым", "иным")

#: Мужские имена на -а/-я: по окончанию они выглядят женскими.
_MALE_NAMES_ENDING_A = frozenset(
    "никита илья данила савва фома лука кузьма мустафа муса иса гаврила добрыня ерёма "
    "миша саша паша дима лёша гоша женя валя".split()
)
#: Женские имена на согласный или мягкий знак.
_FEMALE_NAMES_CONSONANT = frozenset("любовь нинель рахиль эстер гульнар айгуль гузель асель".split())


def _from_patronymic(token: str) -> str | None:
    if len(token) < 6:
        return None
    if token.endswith(_PATRONYMIC_FEMALE):
        return FEMALE
    if token.endswith(_PATRONYMIC_MALE):
        return MALE
    return None


def _from_surname(token: str) -> str | None:
    if len(token) < 4:
        return None
    # Женские окончания проверяются первыми: «ова» длиннее «ов» и содержит его.
    if token.endswith(_SURNAME_FEMALE) and not token.endswith(("ову", "еву", "ину")):
        return FEMALE
    if token.endswith(_SURNAME_MALE):
        return MALE
    return None


def _from_given(token: str, female_names: frozenset[str], male_names: frozenset[str]) -> str | None:
    if token in female_names or token in _FEMALE_NAMES_CONSONANT:
        return FEMALE
    if token in male_names or token in _MALE_NAMES_ENDING_A:
        return MALE
    if token.endswith(("а", "я")) and len(token) > 2:
        return FEMALE
    return None


def infer_gender(
    fio: str,
    female_names: frozenset[str] = frozenset(),
    male_names: frozenset[str] = frozenset(),
) -> str | None:
    """Род по строке ФИО в любом порядке слов и регистре, либо None."""
    tokens = [t.strip(".,") for t in fio.lower().replace("ё", "ё").split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return None

    for token in tokens:
        gender = _from_patronymic(token)
        if gender:
            return gender

    for token in tokens:
        gender = _from_given(token, female_names, male_names)
        if gender and (token in female_names or token in male_names
                       or token in _FEMALE_NAMES_CONSONANT or token in _MALE_NAMES_ENDING_A):
            return gender

    for token in tokens:
        gender = _from_surname(token)
        if gender:
            return gender

    for token in tokens:
        gender = _from_given(token, female_names, male_names)
        if gender:
            return gender
    return None
