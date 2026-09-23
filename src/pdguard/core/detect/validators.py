"""Контрольные суммы и валидаторы.

Проверка контрольных сумм — главный инструмент борьбы с ложными
срабатываниями: она отделяет реальный номер карты или ИНН от любой другой
последовательности цифр той же длины.
"""

from __future__ import annotations

_INN10_WEIGHTS = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_WEIGHTS_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def luhn_valid(number: str) -> bool:
    """Алгоритм Луна для номеров платёжных карт."""
    digits = digits_only(number)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = ord(char) - 48
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def plausible_card_bin(number: str) -> bool:
    """Проверяет первую цифру по мажорным платёжным системам.

    Алгоритма Луна недостаточно: примерно каждый десятый случайный набор цифр
    проходит его. Диапазон BIN отсекает номера заказов и внутренние
    идентификаторы, которые совпали с контрольной суммой случайно.
    МИР=2, AmEx/Diners=3, Visa=4, Mastercard=5, Maestro/UnionPay/МИР=6.
    """
    digits = digits_only(number)
    return bool(digits) and digits[0] in "23456"


def inn_valid(number: str) -> bool:
    """ИНН физлица (12 знаков) и юрлица (10 знаков)."""
    digits = digits_only(number)
    if len(digits) == 10:
        checksum = sum(w * (ord(d) - 48) for w, d in zip(_INN10_WEIGHTS, digits)) % 11 % 10
        return checksum == ord(digits[9]) - 48
    if len(digits) == 12:
        first = sum(w * (ord(d) - 48) for w, d in zip(_INN12_WEIGHTS_1, digits)) % 11 % 10
        second = sum(w * (ord(d) - 48) for w, d in zip(_INN12_WEIGHTS_2, digits)) % 11 % 10
        return first == ord(digits[10]) - 48 and second == ord(digits[11]) - 48
    return False


def snils_valid(number: str) -> bool:
    """СНИЛС: контрольное число по первым 9 цифрам."""
    digits = digits_only(number)
    if len(digits) != 11:
        return False
    payload, control = digits[:9], int(digits[9:])
    total = sum((9 - i) * (ord(d) - 48) for i, d in enumerate(payload))
    if total < 100:
        expected = total
    elif total in (100, 101):
        expected = 0
    else:
        expected = total % 101
        if expected in (100, 101):
            expected = 0
    return expected == control


def plausible_phone(number: str) -> bool:
    """Отсекает наборы цифр, не похожие на российский номер."""
    digits = digits_only(number)
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    if len(digits) != 10:
        return False
    # Мобильные (9xx) и городские коды начинаются не с 0 и не с 1.
    return digits[0] not in "01"


def plausible_year(year: int) -> bool:
    """Годы вне этого диапазона в паспортных сценариях не встречаются."""
    return 1900 <= year <= 2100


def valid_calendar_date(day: int, month: int, year: int) -> bool:
    if not plausible_year(year) or not 1 <= month <= 12:
        return False
    days_in_month = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    if day < 1 or day > days_in_month[month - 1]:
        return False
    if month == 2 and day == 29:
        return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return True
