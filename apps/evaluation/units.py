"""Deterministic parsing of quantities, units, money and tax/freight wording.

Nothing here calls an LLM. Every function returns None (or "unknown") when the text is not understood,
so callers can report "Requires manual verification" instead of guessing.
"""
import re
from decimal import Decimal, InvalidOperation

# unit -> (dimension, factor to the dimension's base unit)
UNITS = {
    "mm": ("length", Decimal("1")), "millimetre": ("length", Decimal("1")), "millimeter": ("length", Decimal("1")),
    "cm": ("length", Decimal("10")), "m": ("length", Decimal("1000")), "metre": ("length", Decimal("1000")),
    "meter": ("length", Decimal("1000")), "in": ("length", Decimal("25.4")), "inch": ("length", Decimal("25.4")),
    "inches": ("length", Decimal("25.4")), '"': ("length", Decimal("25.4")),
    "um": ("length", Decimal("0.001")), "µm": ("length", Decimal("0.001")), "micron": ("length", Decimal("0.001")),
    "microns": ("length", Decimal("0.001")),
    "day": ("time", Decimal("1")), "days": ("time", Decimal("1")), "week": ("time", Decimal("7")),
    "weeks": ("time", Decimal("7")), "month": ("time", Decimal("30")), "months": ("time", Decimal("30")),
    "year": ("time", Decimal("365")), "years": ("time", Decimal("365")),
    "kg": ("mass", Decimal("1000")), "g": ("mass", Decimal("1")), "t": ("mass", Decimal("1000000")),
    "mpa": ("pressure", Decimal("1")), "bar": ("pressure", Decimal("0.1")), "psi": ("pressure", Decimal("0.00689476")),
    "%": ("percent", Decimal("1")),
    "nos": ("count", Decimal("1")), "no": ("count", Decimal("1")), "pcs": ("count", Decimal("1")),
    "units": ("count", Decimal("1")), "unit": ("count", Decimal("1")), "pieces": ("count", Decimal("1")),
    "ea": ("count", Decimal("1")),
}

NUM = r"\d[\d,]*(?:\.\d+)?|\.\d+"
_QTY = re.compile(rf"(?P<num>{NUM})\s*(?P<unit>[a-zA-Zµ%\"]+)?")
_MONEY_MULT = {"lakh": Decimal("100000"), "lakhs": Decimal("100000"), "lac": Decimal("100000"), "lacs": Decimal("100000"),
               "crore": Decimal("10000000"), "crores": Decimal("10000000"), "cr": Decimal("10000000"),
               "k": Decimal("1000"), "million": Decimal("1000000"), "mn": Decimal("1000000")}


def to_decimal(text):
    try:
        return Decimal(str(text).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def parse_quantity(text):
    """First number in `text` with its unit -> {number, unit, dimension, base, abs_tol} or None.

    `base` is the value converted to the dimension's base unit so values can be compared directly.
    """
    if text is None:
        return None
    text = str(text)
    m = _QTY.search(text.replace("±", " ").replace("+/-", " "))
    if not m:
        return None
    number = to_decimal(m.group("num"))
    if number is None:
        return None
    unit = (m.group("unit") or "").lower()
    info = UNITS.get(unit) or UNITS.get(unit.rstrip("."))
    if not info and unit:
        # "mm." / "days," etc. are handled above; anything else is an unknown unit.
        return {"number": number, "unit": unit, "dimension": None, "base": None}
    if not info:
        return {"number": number, "unit": "", "dimension": None, "base": None}
    return {"number": number, "unit": unit, "dimension": info[0], "base": number * info[1]}


def parse_money(text):
    """Amount in rupees from text such as "Rs. 5,00,000/-", "₹4.5 lakh", "INR 1.2 crore". None if absent."""
    if text is None:
        return None
    s = str(text).lower()
    if not re.search(r"\d", s):
        return None
    m = re.search(rf"(?P<num>{NUM})\s*(?P<mult>lakhs?|lacs?|crores?|cr\b|k\b|million|mn)?", s)
    if not m:
        return None
    value = to_decimal(m.group("num"))
    if value is None:
        return None
    if m.group("mult"):
        value *= _MONEY_MULT[m.group("mult")]
    return value


NEGATIVE = re.compile(r"^\s*(no|none|nil|n/?a|not\s+(available|applicable|certified|provided|compliant|offered|supported|possible)|nah)\b", re.I)
POSITIVE = re.compile(r"^\s*(yes|available|provided|certified|compliant|confirmed|offered|complied|applicable|y)\b", re.I)


def is_negative(text):
    return bool(text and NEGATIVE.search(str(text)))


def is_positive(text):
    return bool(text and POSITIVE.search(str(text)))


def parse_tax(text):
    """-> {"mode": included|excluded|unknown, "rate": Decimal|None}. Never assumes a mode without wording."""
    if not text:
        return {"mode": "unknown", "rate": None}
    s = str(text).lower()
    rate = None
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
    if m:
        rate = Decimal(m.group(1))
    if re.search(r"\b(inclusive|included|incl\.?|including)\b", s) and not re.search(r"\b(exclusive|excluding|excl\.?|extra)\b", s):
        return {"mode": "included", "rate": rate}
    if re.search(r"\b(extra|exclusive|excluding|excl\.?|additional|over and above|plus)\b", s):
        return {"mode": "excluded", "rate": rate}
    return {"mode": "unknown", "rate": rate}


def parse_freight(text):
    """-> {"mode": included|amount|unknown, "amount": Decimal|None}."""
    if text is None or not str(text).strip():
        return {"mode": "unknown", "amount": None}
    s = str(text).lower()
    if re.search(r"\b(included|inclusive|free|nil|not applicable|ex-?works? included)\b", s) or s.strip() in ("0", "zero"):
        return {"mode": "included", "amount": Decimal("0")}
    if re.search(r"\b(actual|at actuals|to be (advised|confirmed)|tbd|as applicable)\b", s):
        return {"mode": "unknown", "amount": None}
    amount = parse_money(s)
    if amount is not None:
        return {"mode": "amount", "amount": amount}
    return {"mode": "unknown", "amount": None}


def normalise(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def slug(text):
    return normalise(text).replace(" ", "_")
