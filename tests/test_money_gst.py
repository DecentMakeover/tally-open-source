import pytest

from bahi.gst import gstin_checksum, make_gstin, validate_gstin
from bahi.money import (
    amount_in_words,
    div_round,
    fmt_money,
    fmt_qty,
    fmt_rate,
    line_amount,
    tax_on,
    to_bp,
    to_milli,
    to_paise,
)


def test_parsing_and_formatting():
    assert to_paise("1,234.505") == 123451
    assert to_paise(10) == 1000
    assert to_paise("") == 0
    assert fmt_money(-123451) == "-1234.51"
    assert fmt_money(5) == "0.05"
    assert to_milli("2.5") == 2500
    assert fmt_qty(2500) == "2.500"
    assert to_bp("18") == 1800
    assert fmt_rate(1800) == "18"
    assert fmt_rate(25) == "0.25"
    with pytest.raises(ValueError):
        to_paise("abc")


def test_rounding_is_half_away_from_zero():
    assert div_round(5, 10) == 1
    assert div_round(4, 10) == 0
    assert div_round(-5, 10) == -1
    assert line_amount(2500, 1999) == 4998  # 2.5 x 19.99 = 49.975 -> 49.98
    assert tax_on(10000, 1800) == 1800
    assert tax_on(333, 500) == 17  # 16.65 paise -> 17


def test_amount_in_words_uses_indian_numbering():
    assert amount_in_words(0) == "Rupees Zero Only"
    assert amount_in_words(12345678950) == (
        "Rupees Twelve Crore Thirty Four Lakh Fifty Six Thousand Seven Hundred Eighty Nine and Fifty Paise Only"
    )
    assert amount_in_words(10000000) == "Rupees One Lakh Only"


def test_gstin_validation():
    good = make_gstin("29", "AAAPD1234K")
    assert validate_gstin(good) is None
    assert validate_gstin(good.lower()) is None
    bad_check = good[:-1] + ("0" if good[-1] != "0" else "1")
    assert "checksum" in validate_gstin(bad_check)
    assert "state code" in validate_gstin("99" + good[2:])
    assert validate_gstin("123") is not None
    # A published sample GSTIN with a known checksum digit.
    assert gstin_checksum("27AAPFU0939F1Z") == "V"
