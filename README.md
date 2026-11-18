# Wyoming [Supertonic](https://github.com/supertone-inc/supertonic)

Wyoming server for Supertonic TTS (V3).

## Installation

Clone the repository and set up a virtual environment:

```bash
git clone https://github.com/mitrokun/wyoming_supertonic.git
cd wyoming_supertonic

python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

Installing the project pulls in all runtime dependencies, including PyYAML.
Russian legacy auto-stress can optionally use the external Silero accentor:

```bash
python -m pip install '.[ru]'
```

## Usage

Run the server pointing to your model directory:

```bash
python3 -m wyoming_supertonic --uri 'tcp://0.0.0.0:10209'
```

### Arguments

*   `--language` Default voice language (default: `en`).      
*   `--uri`: Server URI (default: `tcp://0.0.0.0:10209`).
*   `--speed`: Speech speed, 0.5 to 2.0 (default: `1.0`).
*   `--steps`: Denoising steps. Higher is better quality but slower (default: `5`).
*   `--threads`: Number of CPU threads to use (default: `4`).
*   `--target-db`: Peak volume normalization target in dBFS, e.g. `-3.0` (disabled by default).
*   `--no-streaming`: Disable sentence-by-sentence streaming.
*   `--debug`: Enable debug logging.

The initial synthesized audio for each sentence is framed by silence on both sides. I chose a default trim value of 260ms; if the pauses between sentences are unsatisfactory for you, try adjusting `--crop-silence` parameter.

### Supported Languages:
| Code | Language | Code | Language | Code | Language |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `ar` | Arabic | `fr` | French | `pt` | Portuguese |
| `bg` | Bulgarian | `hi` | Hindi | `ro` | Romanian |
| `cs` | Czech | `hr` | Croatian | `ru` | Russian |
| `da` | Danish | `hu` | Hungarian | `sk` | Slovak |
| `de` | German | `id` | Indonesian | `sl` | Slovenian |
| `el` | Greek | `it` | Italian | `sv` | Swedish |
| `en` | English | `ja` | Japanese | `tr` | Turkish |
| `es` | Spanish | `ko` | Korean | `uk` | Ukrainian |
| `et` | Estonian | `lt` | Lithuanian | `vi` | Vietnamese |
| `fi` | Finnish | `lv` | Latvian | `nl` | Dutch |

### Text normalization

Russian normalization defaults to the **legacy** profile, which preserves the
existing `ru_norm` behavior. With the `ru` extra installed, legacy continues to
use its existing automatic Silero stress placement.

The opt-in **enhanced** profile adds cleanup, a YAML pronunciation dictionary,
abbreviation expansion, and Russian numbers, dates, times, and units. English
words and mixed-script text are not transliterated; for example, `Home Assistant`
is preserved as written.

```bash
python -m wyoming_supertonic --normalization-profile enhanced
```

In enhanced mode, `--stress-mode model` and `--stress-mode off` do not load an
external accentor; `off` disables only that optional external accentor and does
**not** disable Supertonic's built-in model behavior. External accenting is enabled
only by explicit `--stress-mode silero-stress`, which requires `.[ru]`.

`--language` remains `en` by default. Enhanced Russian normalization applies
when an individual Wyoming request selects Russian. Settings and environment
variable names are listed in [ENV.md](ENV.md); dictionary format is shown in
[`supertonic-dictionary.example.yaml`](supertonic-dictionary.example.yaml).
See [ADD_NORM.md](ADD_NORM.md) for normalizer integration notes.

## Quick start with uv

```
git clone https://github.com/mitrokun/wyoming_supertonic.git
cd wyoming_supertonic
uv sync
uv run -m wyoming_supertonic --uri 'tcp://0.0.0.0:10209'
```
For legacy Russian auto-stress, install the optional extra with
`uv sync --extra ru`. To use enhanced normalization with the external accentor, select
`--normalization-profile enhanced --stress-mode silero-stress`.

## Run using Docker

```
git clone https://github.com/mitrokun/wyoming_supertonic.git
cd wyoming_supertonic
sudo docker build . -t "wyoming-supertonic:latest" --build-arg WITH_RU=false # for auto-stress for the Russian language, set this to true
mkdir data # models will be downloaded here
sudo docker compose up
```
Edit [docker-compose-yml](docker-compose.yml) as needed.

---

### Tip: Multi-Language Setup

Due to a [limitation](https://github.com/OHF-Voice/wyoming/blob/main/wyoming/tts.py#L39) of the Wyoming standard library, passing the `voice` parameter disables the `language` parameter.

If you are setting up a multi-language assistant with different voice pipelines and need dynamic language switching, it is recommended to use this alternative client: **[Streaming tts proxy](https://github.com/mitrokun/streaming_tts_proxy)**

It automatically forwards the language of the currently active voice assistant pipeline, allowing the Wyoming-Supertonic server to dynamically apply the correct language normalizer and synthesis settings on the fly. The server-side fix has already been implemented.
