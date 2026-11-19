"""Exact money and quantity arithmetic.

Everything is stored as integers so totals never drift:

* money      -> paise (1 rupee = 100)
* quantities -> thousandths of a unit (1 unit = 1000)
* tax rates  -> basis points (18% = 1800)
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

PAISE = 100
QTY_SCALE = 1000
BP = 100  # basis points per percent


def _dec(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal(0)
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc


def _scale(value: object, factor: int) -> int:
    return int((_dec(value) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_paise(value: object) -> int:
    return _scale(value, PAISE)


def to_milli(value: object) -> int:
    return _scale(value, QTY_SCALE)


def to_bp(value: object) -> int:
    return _scale(value, BP)


def div_round(numerator: int, denominator: int) -> int:
    """Integer division rounding half away from zero."""
    if denominator == 0:
        raise ZeroDivisionError("division by zero")
    sign = -1 if (numerator < 0) != (denominator < 0) else 1
    n, d = abs(numerator), abs(denominator)
    return sign * ((2 * n + d) // (2 * d))


def _fmt(value: int, factor: int, places: int) -> str:
    sign = "-" if value < 0 else ""
    whole, frac = divmod(abs(value), factor)
    return f"{sign}{whole}.{frac:0{places}d}"


def fmt_money(paise: int) -> str:
    return _fmt(paise, PAISE, 2)


def fmt_qty(milli: int) -> str:
    return _fmt(milli, QTY_SCALE, 3)


def fmt_rate(bp: int) -> str:
    text = _fmt(bp, BP, 2)
    return text.rstrip("0").rstrip(".") if "." in text else text


def line_amount(qty_milli: int, rate_paise: int) -> int:
    return div_round(qty_milli * rate_paise, QTY_SCALE)


def tax_on(taxable_paise: int, rate_bp: int) -> int:
    return div_round(taxable_paise * rate_bp, 100 * BP)


# --- amount in words, Indian numbering (lakh / crore) ---------------------

_ONES = ["Zero", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["_", "_", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_hundred(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f" {_ONES[ones]}" if ones else "")


def _below_thousand(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_below_hundred(rest))
    return " ".join(parts)


def number_in_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10_000_000)
    if crore:
        parts.append(f"{number_in_words(crore)} Crore")
    lakh, n = divmod(n, 100_000)
    if lakh:
        parts.append(f"{_below_hundred(lakh)} Lakh")
    thousand, n = divmod(n, 1000)
    if thousand:
        parts.append(f"{_below_hundred(thousand)} Thousand")
    if n:
        parts.append(_below_thousand(n))
    return " ".join(parts)


def amount_in_words(paise: int) -> str:
    rupees, p = divmod(abs(paise), PAISE)
    text = f"Rupees {number_in_words(rupees)}"
    if p:
        text += f" and {_below_hundred(p)} Paise"
    return ("Minus " if paise < 0 else "") + text + " Only"
