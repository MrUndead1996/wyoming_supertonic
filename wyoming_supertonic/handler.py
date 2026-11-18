import argparse
import asyncio
import logging
import re

from sentence_stream import SentenceBoundaryDetector
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.error import Error
from wyoming.event import Event
from wyoming.info import Describe, Info
from wyoming.server import AsyncEventHandler
from wyoming.tts import (
    Synthesize,
    SynthesizeChunk,
    SynthesizeStart,
    SynthesizeStop,
    SynthesizeStopped,
)

from .supertonic_engine import SupertonicEngine
from .normalization import StreamingCleaner

_LOGGER = logging.getLogger(__name__)
_ABBREVIATION_SHIELD = re.compile(
    r"т\.\s*е\.|т\.е\.|т\.\s*д\.|т\.д\.|т\.\s*п\.|т\.п\.|т\.\s*к\.|т\.к\.|напр\.",
    re.I,
)
_ABBREVIATION_TAIL = 12


class _AbbreviationShield:
    """Keep Russian dotted abbreviations intact until the detector sees them."""

    def __init__(self):
        self.pending = ""

    @staticmethod
    def _shield(text):
        return _ABBREVIATION_SHIELD.sub(lambda m: m[0].replace(".", "\ue000"), text)

    def add(self, text):
        self.pending += text
        shielded = self._shield(self.pending)
        safe = max(0, len(shielded) - _ABBREVIATION_TAIL)
        emitted, self.pending = shielded[:safe], shielded[safe:]
        return emitted

    def finish(self):
        result = self._shield(self.pending)
        self.pending = ""
        return result

    @staticmethod
    def restore(text):
        return text.replace("\ue000", ".")


class SupertonicEventHandler(AsyncEventHandler):
    """Event handler for Supertonic TTS."""

    def __init__(
        self,
        wyoming_info: Info,
        cli_args: argparse.Namespace,
        engine: SupertonicEngine,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *args,
        **kwargs,
    ) -> None:
        super().__init__(reader=reader, writer=writer, *args, **kwargs)

        self.cli_args = cli_args
        self.wyoming_info_event = wyoming_info.event()
        self.engine = engine

        self.sbd = SentenceBoundaryDetector()
        self._is_streaming = False
        self._audio_started = False
        self._current_voice = None
        self._current_language = self.cli_args.language
        self._session_scale = None
        self._cleaner = None
        self._abbreviation_shield = None

    def _new_request_pipeline(self):
        config = getattr(self.cli_args, "normalization_config", None)
        use_cleanup = bool(
            config and config.profile == "enhanced" and config.cleanup
            and self._current_language.split("-")[0].split("_")[0].lower() == "ru"
        )
        self._cleaner = StreamingCleaner() if use_cleanup else None
        self._abbreviation_shield = (
            _AbbreviationShield()
            if config and config.profile == "enhanced" and config.abbreviations
            and self._current_language.split("-")[0].split("_")[0].lower() == "ru"
            else None
        )

    def _preprocess_for_detector(self, text, final=False):
        if self._cleaner is not None:
            if final:
                text = self._cleaner.add(text) + self._cleaner.finish()
                self._cleaner = None
            else:
                text = self._cleaner.add(text)
        if self._abbreviation_shield is not None:
            shield = self._abbreviation_shield
            text = shield.add(text) + (shield.finish() if final else "")
        return text

    async def handle_event(self, event: Event) -> bool:
        """Handle incoming Wyoming event."""
        if Describe.is_type(event.type):
            await self.write_event(self.wyoming_info_event)
            return True

        try:
            # --- 1. Synthesize (Single batch request) ---
            if Synthesize.is_type(event.type):
                if self._is_streaming:
                    return True

                syn = Synthesize.from_event(event)
                self._current_voice = None
                self._current_language = self.cli_args.language
                voice_data = event.data.get("voice", {})
                lang = voice_data.get("language")

                if syn.voice and syn.voice.name:
                    self._current_voice = syn.voice.name

                if lang:
                    self._current_language = lang

                # Reset session scale before standalone batch synthesis
                self._session_scale = None

                _LOGGER.debug(
                    f"Synthesize request. Voice: {self._current_voice}, Lang: {self._current_language}"
                )
                return await self._handle_synthesize_full(syn.text)

            # --- 2. Start Streaming ---
            if SynthesizeStart.is_type(event.type):
                if self.cli_args.no_streaming:
                    return True

                start = SynthesizeStart.from_event(event)
                self._current_voice = None
                self._current_language = self.cli_args.language
                voice_data = event.data.get("voice", {})
                lang = voice_data.get("language")

                self._is_streaming = True
                self._audio_started = False
                self.sbd = SentenceBoundaryDetector()
                # Reset session scale at the start of a streaming response
                self._session_scale = None

                if start.voice and start.voice.name:
                    self._current_voice = start.voice.name

                if lang:
                    self._current_language = lang
                self._new_request_pipeline()

                _LOGGER.debug(
                    f"Stream started. Voice: {self._current_voice}, Language: {self._current_language}"
                )
                return True

            # --- 3. Chunk ---
            if SynthesizeChunk.is_type(event.type):
                if not self._is_streaming:
                    return True
                chunk = SynthesizeChunk.from_event(event)
                detector_text = self._preprocess_for_detector(chunk.text)
                for sentence in self.sbd.add_chunk(detector_text):
                    await self._process_sentence(sentence)
                return True

            # --- 4. Stop ---
            if SynthesizeStop.is_type(event.type):
                if not self._is_streaming:
                    return True

                detector_text = self._preprocess_for_detector("", final=True)
                for sentence in self.sbd.add_chunk(detector_text):
                    await self._process_sentence(sentence)
                remaining = self.sbd.finish()
                if remaining:
                    await self._process_sentence(remaining)

                if self._audio_started:
                    await self.write_event(AudioStop().event())

                await self.write_event(SynthesizeStopped().event())
                self._is_streaming = False
                self._session_scale = None
                self._cleaner = None
                self._abbreviation_shield = None
                return True

        except Exception as err:
            _LOGGER.exception("Handler error")
            await self.write_event(Error(text=str(err), code=err.__class__.__name__).event())
            self._is_streaming = False
            self._session_scale = None
            self._cleaner = None
            self._abbreviation_shield = None

        return True

    async def _handle_synthesize_full(self, text: str) -> bool:
        """Process a complete text synthesis request."""
        self._audio_started = False
        self.sbd = SentenceBoundaryDetector()
        self._new_request_pipeline()

        detector_text = self._preprocess_for_detector(text, final=True)
        sentences = list(self.sbd.add_chunk(detector_text))
        remaining = self.sbd.finish()
        if remaining:
            sentences.append(remaining)

        for s in sentences:
            await self._process_sentence(s)

        self._cleaner = None
        self._abbreviation_shield = None
        if self._audio_started:
            await self.write_event(AudioStop().event())
        return True

    async def _process_sentence(self, sentence: str):
        """Process a single sentence detected by the boundary detector."""
        s = sentence.strip()
        if not s:
            return

        shield = self._abbreviation_shield
        if shield is not None:
            s = shield.restore(s)
        await self._synthesize_text(s)

    async def _synthesize_text(self, text: str):
        """Synthesize text to audio and stream chunks."""
        voice_name = "M1"
        if self._current_voice and self._current_voice in self.engine.available_voices:
            voice_name = self._current_voice
        elif getattr(self.cli_args, "default_voice", None) in self.engine.available_voices:
            voice_name = self.cli_args.default_voice
        elif self.engine.available_voices:
            voice_name = self.engine.available_voices[0]

        _LOGGER.debug(
            f"Requesting synthesis for: '{text[:40]}...' [Lang: {self._current_language}]"
        )

        loop = asyncio.get_running_loop()
        try:
            pcm_bytes, rate, self._session_scale = await loop.run_in_executor(
                None,
                self.engine.synthesize,
                text,
                voice_name,
                self._current_language,
                self._session_scale,
            )
        except Exception as e:
            _LOGGER.error(f"Engine error: {e}")
            return

        if not self._audio_started:
            await self.write_event(AudioStart(rate=rate, width=2, channels=1).event())
            self._audio_started = True

        chunk_size = 2048
        for i in range(0, len(pcm_bytes), chunk_size):
            await self.write_event(
                AudioChunk(audio=pcm_bytes[i : i + chunk_size], rate=rate, width=2, channels=1).event()
            )
