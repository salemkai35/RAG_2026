"""Analyse robuste des montants et dates au format français."""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_SPACES = re.compile(r"[\s  ']")


def normalize_text(value: str) -> str:
    """Minuscules, sans accents, espaces normalisés (comparaisons, recherche)."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value).strip().lower()


def parse_decimal(value) -> Decimal | None:
    """'1 234 567,89' / '1.234.567,89' / '1234567.89' / 1234.5 -> Decimal."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = _SPACES.sub("", str(value))
    text = re.sub(r"(?i)(da|dzd|dinars?|eur|€|usd|\$|ht|ttc|%)", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        # Le dernier séparateur rencontré est le séparateur décimal.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d/%m/%y")


def parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_int(value) -> int | None:
    number = parse_decimal(value)
    return int(number) if number is not None else None
