# Configuration reference

Normalization settings can be supplied as CLI options or environment
variables. CLI options take precedence over the corresponding environment
variable. Environment booleans are case-insensitive, but only `true` and
`false` are accepted. An invalid environment value is an error unless the
corresponding CLI option overrides it.

## Normalization options

| Setting | CLI option | Environment variable | Default | Effect |
| --- | --- | --- | --- | --- |
| Profile | `--normalization-profile {legacy,enhanced}` | `SUPERTONIC_NORMALIZATION_PROFILE` | `legacy` | Select the Russian normalizer. |
| Cleanup | `--normalize-cleanup` / `--no-normalize-cleanup` | `SUPERTONIC_NORMALIZE_CLEANUP` | `true` | Enhanced Russian streaming cleanup before sentence detection. |
| Dictionary | `--normalize-dictionary` / `--no-normalize-dictionary` | `SUPERTONIC_NORMALIZE_DICTIONARY` | `true` | Apply the configured YAML dictionary. |
| Abbreviations | `--normalize-abbreviations` / `--no-normalize-abbreviations` | `SUPERTONIC_NORMALIZE_ABBREVIATIONS` | `true` | Expand built-in and dictionary abbreviations. |
| Numbers | `--normalize-numbers` / `--no-normalize-numbers` | `SUPERTONIC_NORMALIZE_NUMBERS` | `true` | Speak Russian numbers and valid clock times. |
| Dates | `--normalize-dates` / `--no-normalize-dates` | `SUPERTONIC_NORMALIZE_DATES` | `true` | Speak valid supported dates. |
| Units | `--normalize-units` / `--no-normalize-units` | `SUPERTONIC_NORMALIZE_UNITS` | `true` | Speak supported quantities and units. |
| Stress mode | `--stress-mode {model,off,silero-stress}` | `SUPERTONIC_STRESS_MODE` | `model` | Select optional enhanced-profile external accenting. |
| Dictionary path | `--dictionary-path PATH` | `SUPERTONIC_DICTIONARY_PATH` | unset | Load a YAML pronunciation dictionary at startup. |

The stage switches, dictionary path, and non-default stress modes are enhanced
profile controls. If they are changed while the effective profile is `legacy`,
argument parsing fails instead of silently implying that they affect legacy
`ru_norm.py`. Default-valued switches are harmless in legacy mode.

Enhanced normalization is applied only to Russian requests. English words and
mixed-script text remain in their original scripts; there is no Latin
transliteration or brand-name conversion.

## Stress behavior

* **Legacy:** uses the existing `ru_norm.py` behavior unchanged. Its automatic
  Russian accenting uses the external Silero accentor when the optional `ru`
  extra is installed.
* **Enhanced `model` (default):** does not load or call an external accentor.
* **Enhanced `off`:** also does not load or call an external accentor. It does
  not disable Supertonic's built-in model behavior.
* **Enhanced `silero-stress`:** explicitly loads and calls the optional
  external accentor. Install it with `python -m pip install '.[ru]'`.

The `model`/`off` names are configuration modes for the optional external
accenting stage; neither switches off Supertonic's speech model.

## Other command-line options

These server/runtime options do not have normalization environment-variable
counterparts:

| CLI option | Default | Meaning |
| --- | --- | --- |
| `--uri URI` | `tcp://0.0.0.0:10209` | Wyoming TCP listen address. |
| `--data-dir PATH` | unset | Supertonic model directory. |
| `--language LANG` | `en` | Default request language; individual Wyoming requests may select another language. |
| `--crop-silence MS` | `260` | Silence to crop at audio edges. |
| `--steps N` | `5` | Supertonic denoising steps. |
| `--speed RATE` | `1.0` | Speech speed multiplier. |
| `--threads N` | `4` | CPU thread setting. |
| `--target-db DB` | disabled | Target peak level in dBFS. |
| `--no-streaming` | disabled | Disable sentence-by-sentence output. |
| `--debug` | disabled | Enable debug logging. |
| `--log-format FORMAT` | Python logging basic format | Logging format string. |

## Example: opt in with environment variables

```bash
SUPERTONIC_NORMALIZATION_PROFILE=enhanced \
SUPERTONIC_STRESS_MODE=off \
SUPERTONIC_DICTIONARY_PATH=./supertonic-dictionary.yaml \
python -m wyoming_supertonic
```

Keep `--language=en` (the default) if English is the default request language;
enhanced processing is selected per Russian request.
