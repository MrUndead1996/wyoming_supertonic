# Text normalizers

The engine selects a normalizer by the request language. Russian has two
profiles: `legacy` delegates to the existing `ru_norm.py`, while `enhanced`
uses the independent `wyoming_supertonic.normalization` package. The default
is legacy. See [ENV.md](ENV.md) for all profile controls.

The enhanced profile is Russian-only. It does not transliterate Latin words or
brand names; mixed-script content is left intact except for explicitly enabled
normalization stages. It supports streaming cleanup before sentence detection,
a literal YAML pronunciation dictionary, abbreviation expansion, and Russian
numbers, dates, times, and units. It does not add SSML parsing.

## Add a normalizer for another language

`de` and `cs` are registered in
[`wyoming_supertonic/supertonic_engine.py`](wyoming_supertonic/supertonic_engine.py).
To add another language:

1. Add a module in `wyoming_supertonic/` with a class exposing
   `normalize(text) -> str`.
2. Register its module and class in `NORMALIZERS_REGISTRY`.
3. Add `unittest` coverage under `tests/`, including expression-tag handling
   and identifiers relevant to the language.
4. Run `python -m unittest discover -s tests`.

Do not change the existing `ru_norm.py` as part of unrelated normalizer work.
The enhanced Russian implementation is deliberately isolated so it can evolve
without changing the legacy behavior. Keep `de` and `cs` behavior unchanged
unless a separate change explicitly targets those languages.

## Russian pronunciation dictionaries

The optional dictionary is a UTF-8 YAML mapping with `pronunciations`,
`stress`, and `abbreviations` sections. See
[`supertonic-dictionary.example.yaml`](supertonic-dictionary.example.yaml)
for a small example. Replacements are literal and non-recursive: replacement
values are not looked up again or transliterated. Matching is case-insensitive
and longest-first.

Set `SUPERTONIC_DICTIONARY_PATH` or `--dictionary-path` to use a file. Invalid
YAML or a missing configured file fails startup clearly. The dictionary is
loaded once when the enhanced Russian normalizer is initialized.
