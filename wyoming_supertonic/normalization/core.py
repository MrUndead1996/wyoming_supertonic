"""Small, local TTS front end. Resources are shared; request state is not."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

import yaml
from num2words import num2words

from .cleanup import StreamingCleaner


@dataclass
class NormalizationConfig:
    profile: str = "legacy"
    cleanup: bool = True
    dictionary: bool = True
    abbreviations: bool = True
    numbers: bool = True
    dates: bool = True
    units: bool = True
    stress: str = "model"
    dictionary_path: Optional[str] = None

    def __post_init__(self):
        if self.profile not in {"legacy", "enhanced"}:
            raise ValueError("profile must be 'legacy' or 'enhanced'")
        if self.stress not in {"model", "off", "silero-stress"}:
            raise ValueError("stress must be model, off, or silero-stress")
        if self.profile == "legacy" and self.stress == "silero-stress":
            raise ValueError("silero-stress requires the enhanced normalization profile")


_ABBREVIATIONS = {
    "т. е.": "то есть", "т.е.": "то есть", "т. д.": "так далее",
    "т.д.": "так далее", "т. п.": "тому подобное", "т.п.": "тому подобное",
    "напр.": "например", "т.к.": "так как", "т. к.": "так как",
}


def _dictionary(path):
    sections = {"pronunciations", "abbreviations", "stress"}
    if not path:
        return {name: {} for name in sections}
    try:
        with Path(path).open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid pronunciation dictionary YAML: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("dictionary YAML root must be a mapping")
    if set(data) - sections:
        raise ValueError("unknown dictionary section(s): " + ", ".join(map(str, set(data) - sections)))
    result = {}
    for section in sections:
        values = data.get(section, {})
        if not isinstance(values, dict) or any(
            not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
            for k, v in values.items()
        ):
            raise ValueError(f"dictionary section '{section}' must map non-empty strings to strings")
        result[section] = values
    return result


class _Replacer:
    def __init__(self, mapping):
        self.mapping = {k.casefold(): v for k, v in mapping.items()}
        keys = sorted(self.mapping, key=len, reverse=True)
        self.pattern = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, keys)) + r")(?!\w)", re.I) if keys else None

    def apply(self, text, protect):
        if self.pattern is None:
            return text
        return self.pattern.sub(lambda m: protect(self.mapping.get(m[0].casefold(), m[0])), text)


def _acute(text):
    return re.sub(r"([аеёиоуыэюя])\u0301", r"+\1", text, flags=re.I)


def _to_unicode_stress(text):
    text = re.sub(r"\+([ёЁ])", r"\1", text)
    return re.sub(r"\+([аеёиоуыэюяАЕИОУЫЭЮЯ])", lambda m: m[1] + "\u0301", text)


def _num(value, gender="m"):
    value = value.replace("−", "-").replace(",", ".")
    try:
        # Passing a string preserves decimal scale, including trailing zeroes.
        Decimal(value)
        # num2words mishandles negative fractions (including -0.5), so spell
        # the magnitude first and preserve the explicit sign separately.
        sign = "минус " if value.startswith("-") else "плюс " if value.startswith("+") else ""
        return sign + num2words(value.lstrip("+-"), lang="ru", gender=gender)
    except (InvalidOperation, ValueError, OverflowError):
        return value


_VALUE = r"[−+\-]?\d+(?:[.,]\d+)?"
_NUMBER = re.compile(r"(?<![\w.,+−-])" + _VALUE + r"(?![\w]|[.,]\d)")
_DATE = re.compile(r"(?<![\w.])(?:\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})(?!\w|\.\d)")
_TIME = re.compile(r"(?<![\w.])(\d{1,2}):(\d{2})(?!\w|:\d)")
_MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
# Count forms (one/few/many), gender, fractional genitive singular.
_UNITS = {
    "°C": ("градус Цельсия", "градуса Цельсия", "градусов Цельсия", "m", "градуса Цельсия"),
    "°С": ("градус Цельсия", "градуса Цельсия", "градусов Цельсия", "m", "градуса Цельсия"),
    "°F": ("градус Фаренгейта", "градуса Фаренгейта", "градусов Фаренгейта", "m", "градуса Фаренгейта"),
    "°": ("градус", "градуса", "градусов", "m", "градуса"),
    "%": ("процент", "процента", "процентов", "m", "процента"),
    "мин": ("минута", "минуты", "минут", "f", "минуты"),
    "сек": ("секунда", "секунды", "секунд", "f", "секунды"),
    "ч": ("час", "часа", "часов", "m", "часа"),
    "час": ("час", "часа", "часов", "m", "часа"),
    "м²": ("квадратный метр", "квадратных метра", "квадратных метров", "m", "квадратного метра"),
}
for _symbol, _noun, _few, _many in (
    ("В", "вольт", "вольта", "вольт"), ("Вт", "ватт", "ватта", "ватт"),
    ("кВт", "киловатт", "киловатта", "киловатт"), ("МВт", "мегаватт", "мегаватта", "мегаватт"),
    ("Гц", "герц", "герца", "герц"), ("кГц", "килогерц", "килогерца", "килогерц"),
    ("кг", "килограмм", "килограмма", "килограммов"), ("г", "грамм", "грамма", "граммов"),
    ("мг", "миллиграмм", "миллиграмма", "миллиграммов"), ("л", "литр", "литра", "литров"),
    ("мл", "миллилитр", "миллилитра", "миллилитров"), ("м", "метр", "метра", "метров"),
    ("км", "километр", "километра", "километров"), ("см", "сантиметр", "сантиметра", "сантиметров"),
    ("мм", "миллиметр", "миллиметра", "миллиметров"),
    ("кВт·ч", "киловатт-час", "киловатт-часа", "киловатт-часов"),
):
    _UNITS[_symbol] = (_noun, _few, _many, "m", _few)
_UNITS["км/ч"] = ("километр в час", "километра в час", "километров в час", "m", "километра в час")
_UNITS["м/с"] = ("метр в секунду", "метра в секунду", "метров в секунду", "m", "метра в секунду")
for _alias, _canonical in {
    "W": "Вт", "kW": "кВт", "MW": "МВт", "kWh": "кВт·ч", "kW·h": "кВт·ч",
    "V": "В", "Hz": "Гц", "kHz": "кГц", "kg": "кг", "g": "г", "mg": "мг",
    "L": "л", "l": "л", "ml": "мл", "m": "м", "km": "км", "cm": "см", "mm": "мм",
    "m²": "м²", "km/h": "км/ч", "m/s": "м/с", "min": "мин", "s": "сек", "h": "ч",
    "минута": "мин", "минуты": "мин", "минут": "мин",
    "секунда": "сек", "секунды": "сек", "секунд": "сек",
    "часа": "ч", "часов": "ч",
}.items():
    _UNITS[_alias] = _UNITS[_canonical]
for _symbol, _one, _few, _many in (
    ("А", "ампер", "ампера", "ампер"), ("A", "ампер", "ампера", "ампер"),
    ("mA", "миллиампер", "миллиампера", "миллиампер"),
    ("Pa", "паскаль", "паскаля", "паскалей"), ("hPa", "гектопаскаль", "гектопаскаля", "гектопаскалей"),
    ("kPa", "килопаскаль", "килопаскаля", "килопаскалей"),
    ("lx", "люкс", "люкса", "люкс"), ("dB", "децибел", "децибела", "децибел"),
):
    _UNITS[_symbol] = (_one, _few, _many, "m", _few)
_UNIT = re.compile(
    r"(?<![\w.,])(" + _VALUE + r")\s*(" + "|".join(
        re.escape(k).replace("°", r"°\s*") for k in sorted(_UNITS, key=len, reverse=True)
    ) + r")(?!\w)"
)
# Preserve grouped numeric identifiers rather than partially reading their suffixes.
_IDENTIFIER = re.compile(r"(?<!\w)(?:[A-Za-zА-Яа-я]+[\w]*[-_]\d[\w.-]*|v?\d+(?:\.\d+){2,}|\d+(?:,\d+){2,})(?!\w)")
_MARKED_WORD = re.compile(r"[А-Яа-яЁё]*(?:\+[А-Яа-яЁё]+)+")


def _date_words(literal):
    try:
        if "." in literal:
            day, month, year = map(int, literal.split("."))
        else:
            year, month, day = map(int, literal.split("-"))
        date(year, month, day)
    except ValueError:
        return literal
    day_words = num2words(day, lang="ru", to="ordinal", gender="n")
    year_words = num2words(year, lang="ru", to="ordinal", case="genitive")
    return f"{day_words} {_MONTHS[month - 1]} {year_words} года"


def _quantity(value, symbol):
    forms = _UNITS[re.sub(r"\s", "", symbol)]
    magnitude = abs(Decimal(value.replace("−", "-").replace(",", ".")))
    fractional = "." in value or "," in value
    if fractional:
        noun = forms[4]
    else:
        n = int(magnitude)
        index = 0 if n % 10 == 1 and n % 100 != 11 else 1 if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else 2
        noun = forms[index]
    return f"{_num(value, forms[3])} {noun}"


_ACCENTOR = None
_ACCENT_LOCK = threading.Lock()


def _get_accentor():
    global _ACCENTOR
    with _ACCENT_LOCK:
        if _ACCENTOR is None:
            try:
                import torch
                from silero_stress import load_accentor
            except ImportError as exc:
                raise ValueError("silero-stress mode requires `pip install .[ru]`") from exc
            threads = torch.get_num_threads()
            try:
                _ACCENTOR = load_accentor()
            finally:
                torch.set_num_threads(threads)
    return _ACCENTOR


def _format_time(hour, minute):
    def numeral(n, gender=None):
        word = num2words(n, lang="ru", gender=gender) if gender else num2words(n, lang="ru")
        if gender == "f" and n % 10 == 1 and n % 100 != 11 and word.endswith("один"):
            return word[:-4] + "одна"
        if gender == "f" and n % 10 == 2 and n % 100 != 12 and word.endswith("два"):
            return word[:-3] + "две"
        return word

    def form(n, one, few, many):
        if n % 10 == 1 and n % 100 != 11:
            return one
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return few
        return many

    hours = f"{numeral(hour)} {form(hour, 'час', 'часа', 'часов')}"
    if minute == 0:
        return hours
    minutes = f"{numeral(minute, 'f')} {form(minute, 'минута', 'минуты', 'минут')}"
    return f"{hours} {minutes}"


class TextNormalizer:
    def __init__(self, config=None):
        self.config = config or NormalizationConfig()
        local = _dictionary(self.config.dictionary_path)
        mapping = {}
        if self.config.dictionary:
            mapping.update(local["stress"])
            mapping.update(local["pronunciations"])
        if self.config.abbreviations:
            mapping.update(local["abbreviations"])
        self.user = _Replacer(mapping)
        self.abbreviations = _Replacer(_ABBREVIATIONS)
        self.accentor = _get_accentor() if self.config.profile == "enhanced" and self.config.stress == "silero-stress" else None

    def clean(self, text):
        if self.config.profile != "enhanced" or not self.config.cleanup:
            return text
        cleaner = StreamingCleaner()
        return cleaner.add(text) + cleaner.finish()

    def normalize(self, text, lang="ru"):
        cfg = self.config
        if cfg.profile == "legacy" or lang.split("-")[0].split("_")[0].lower() != "ru":
            return text
        protected = {}

        def protect(value):
            marker = chr(0xF0000 + len(protected))
            while marker in text or marker in protected:
                marker = chr(ord(marker) + 1)
            protected[marker] = _acute(value)
            return marker

        text = _acute(text)
        accentable_markers = set()

        def protect_accentable(value):
            marker = protect(value)
            accentable_markers.add(marker)
            return marker

        text = _MARKED_WORD.sub(lambda m: protect(m[0]), text)
        text = self.user.apply(text, protect)
        if cfg.abbreviations:
            text = self.abbreviations.apply(text, protect)
        # Date-looking and identifier tokens are opaque to ordinary number rules,
        # even if date expansion is disabled or the calendar date is invalid.
        def protect_date(match):
            value = _date_words(match[0]) if cfg.dates else match[0]
            return protect_accentable(value) if value != match[0] else protect(value)

        text = _DATE.sub(protect_date, text)
        text = _IDENTIFIER.sub(lambda m: protect(m[0]), text)
        if cfg.units:
            text = _UNIT.sub(lambda m: protect_accentable(_quantity(m[1], m[2])), text)
        if cfg.numbers:
            def clock(m):
                h, minute = int(m[1]), int(m[2])
                if h < 24 and minute < 60:
                    return protect_accentable(_format_time(h, minute))
                return protect(m[0])

            text = _TIME.sub(clock, text)
            text = _NUMBER.sub(lambda m: protect_accentable(_num(m[0])), text)
        # Ordinary numeric/date expansions still need stress. Protected values
        # with no manual accent are included, while manual exceptions stay exact.
        if self.accentor:
            accent_pattern = re.compile(r"[А-Яа-яЁё]+(?:\s+[А-Яа-яЁё]+)*")

            def accent_russian(value):
                return accent_pattern.sub(lambda m: self.accentor(m[0]), value)

            text = re.split("(" + "|".join(map(re.escape, protected)) + ")", text) if protected else [text]
            text = "".join(
                accent_russian(protected[part]) if part in accentable_markers
                else protected[part] if part in protected
                else accent_russian(part)
                for part in text
            )
            protected = {}
        result = "".join(protected.get(char, char) for char in text)
        return _to_unicode_stress(result)


def clean_text(text, cleaner=None):
    return (cleaner or TextNormalizer()).clean(text)


def normalize_sentence(text, lang="ru", config=None):
    return TextNormalizer(config).normalize(text, lang)
