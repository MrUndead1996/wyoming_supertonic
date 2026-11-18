import argparse
import asyncio
import unittest
from unittest.mock import patch

import numpy as np
from sentence_stream import SentenceBoundaryDetector
from wyoming.info import Info
from wyoming.tts import SynthesizeStart, SynthesizeChunk, SynthesizeStop, SynthesizeVoice

from wyoming_supertonic.__main__ import parse_args
from wyoming_supertonic.handler import SupertonicEventHandler, _AbbreviationShield
from wyoming_supertonic.normalization import NormalizationConfig
from wyoming_supertonic.supertonic_engine import SupertonicEngine


class FakeWriter:
    def write(self, _data):
        pass

    async def drain(self):
        pass


class IntegrationTests(unittest.TestCase):
    def test_cli_env_strict_values_and_cli_precedence(self):
        args = parse_args(
            ["--normalization-profile", "enhanced", "--no-normalize-cleanup"],
            env={"SUPERTONIC_NORMALIZATION_PROFILE": "legacy",
                 "SUPERTONIC_NORMALIZE_CLEANUP": "true"},
        )
        self.assertEqual(args.normalization_config.profile, "enhanced")
        self.assertFalse(args.normalization_config.cleanup)
        self.assertTrue(parse_args([], env={"SUPERTONIC_NORMALIZATION_PROFILE": "enhanced",
                                           "SUPERTONIC_NORMALIZE_NUMBERS": "TRUE"}).normalization_config.numbers)
        overridden = parse_args(
            ["--normalization-profile", "enhanced", "--stress-mode", "off", "--no-normalize-cleanup"],
            env={
                "SUPERTONIC_NORMALIZATION_PROFILE": "not-a-profile",
                "SUPERTONIC_STRESS_MODE": "not-a-mode",
                "SUPERTONIC_NORMALIZE_CLEANUP": "not-a-bool",
            },
        )
        self.assertEqual(overridden.normalization_config.profile, "enhanced")
        self.assertEqual(overridden.normalization_config.stress, "off")
        self.assertFalse(overridden.normalization_config.cleanup)
        with self.assertRaises(SystemExit):
            parse_args([], env={"SUPERTONIC_NORMALIZE_UNITS": "maybe"})
        with self.assertRaises(SystemExit):
            parse_args(["--no-normalize-dates"], env={})
        with self.assertRaises(SystemExit):
            parse_args(["--stress-mode", "silero-stress"], env={})

    def test_enhanced_engine_does_not_import_legacy_ru_normalizer(self):
        original_import = __import__("importlib").import_module
        imported = []

        def track(name, package=None):
            imported.append(name)
            return original_import(name, package=package)

        with patch("wyoming_supertonic.supertonic_engine.importlib.import_module", side_effect=track):
            engine = SupertonicEngine(crop_silence_ms=0,
                                      normalization_config=NormalizationConfig(profile="enhanced", stress="off"))
        self.assertIn("ru", engine.normalizers)
        self.assertNotIn(".ru_norm", imported)

    def test_request_cleanup_is_only_enabled_for_enhanced_russian(self):
        async def check():
            config = NormalizationConfig(profile="enhanced")
            handler, _ = self.make_handler(config)
            handler._current_language = "de-DE"
            handler._new_request_pipeline()
            self.assertIsNone(handler._cleaner)
            handler._current_language = "ru-RU"
            handler._new_request_pipeline()
            self.assertIsNotNone(handler._cleaner)
            handler.cli_args.normalization_config = NormalizationConfig(profile="legacy")
            handler._new_request_pipeline()
            self.assertIsNone(handler._cleaner)
        asyncio.run(check())

    def test_legacy_engine_still_constructs_existing_ru_normalizer(self):
        with patch("wyoming_supertonic.ru_norm.RussianTextNormalizer") as legacy_normalizer:
            engine = SupertonicEngine(crop_silence_ms=0,
                                      normalization_config=NormalizationConfig(profile="legacy"))
        legacy_normalizer.assert_called_once_with()
        self.assertIs(engine.normalizers["ru"], legacy_normalizer.return_value)

    def test_enhanced_missing_explicit_stress_fails_engine_startup(self):
        config = NormalizationConfig(profile="enhanced", stress="silero-stress")
        with patch("wyoming_supertonic.normalization.core._get_accentor",
                   side_effect=ValueError("requires install extra")):
            with self.assertRaisesRegex(ValueError, "requires install extra"):
                SupertonicEngine(normalization_config=config)

    def test_engine_sends_normalized_mixed_text_to_fake_tts(self):
        engine = SupertonicEngine(crop_silence_ms=0,
                                  normalization_config=NormalizationConfig(profile="enhanced", stress="off"))
        observed = []

        class FakeTTS:
            def get_voice_style(self, voice_name):
                return voice_name

            def synthesize(self, text, voice_style=None, lang=None, speed=None, total_step=None):
                observed.append((text, lang))
                return np.ones(8, dtype=np.float32) * 0.1, None

        engine.tts = FakeTTS()
        engine.synthesize("Home Assistant привет 2 за́мок", "M1", "ru")
        self.assertEqual(observed, [("Home Assistant привет два за́мок", "ru")])

    def make_handler(self, normalization_config=None):
        if normalization_config is None:
            normalization_config = NormalizationConfig(profile="enhanced", stress="off")
        args = argparse.Namespace(language="en", no_streaming=False,
                                  normalization_config=normalization_config)
        engine = SupertonicEngine(crop_silence_ms=0, normalization_config=normalization_config)
        handler = SupertonicEventHandler(Info(tts=[]), args, engine,
                                         asyncio.StreamReader(), FakeWriter())
        return handler, engine

    def test_stream_cleanup_chunk_boundaries_and_no_cross_request_leak(self):
        async def run():
            handler, _ = self.make_handler(NormalizationConfig(profile="enhanced", stress="off"))
            seen = []

            async def capture(text):
                seen.append((text, handler._current_voice, handler._current_language))

            handler._synthesize_text = capture
            async def ignore_event(_event):
                pass
            handler.write_event = ignore_event
            await handler.handle_event(SynthesizeStart(SynthesizeVoice(name="F2", language="ru")).event())
            for part in ("visible <thi", "nk>secret</think> т.", " е. дальше."):
                await handler.handle_event(SynthesizeChunk(part).event())
            await handler.handle_event(SynthesizeStop().event())
            await handler.handle_event(SynthesizeStart().event())
            await handler.handle_event(SynthesizeChunk("new request.").event())
            await handler.handle_event(SynthesizeStop().event())
            self.assertEqual(seen[0][1:], ("F2", "ru"))
            self.assertNotIn("secret", seen[0][0])
            self.assertIn("т. е.", seen[0][0])
            self.assertEqual(seen[-1][1:], (None, "en"))
            self.assertIn("new request.", seen[-1][0])
            full, _ = self.make_handler(NormalizationConfig(profile="enhanced", stress="off"))
            full.cli_args.language = "ru"
            full._current_language = "ru"
            full_seen = []

            async def capture_full(text):
                full_seen.append(text)
            full._synthesize_text = capture_full
            await full._handle_synthesize_full(
                "visible <think>secret</think> т. е. дальше."
            )
            self.assertEqual(full_seen, [seen[0][0]])
        asyncio.run(run())

    def test_audio_start_stop_events_remain_in_stream_protocol(self):
        async def run():
            handler, engine = self.make_handler()

            def fake_synthesize(*_args):
                return (np.ones(128, dtype=np.int16).tobytes(), 44100, None)

            engine.synthesize = fake_synthesize
            written = []

            async def record(event):
                written.append(event.type)

            handler.write_event = record
            await handler.handle_event(SynthesizeStart().event())
            await handler.handle_event(SynthesizeChunk("hello.").event())
            await handler.handle_event(SynthesizeStop().event())
            self.assertEqual(written[0], "audio-start")
            self.assertEqual(written[-2:], ["audio-stop", "synthesize-stopped"])
            self.assertIn("audio-chunk", written)
        asyncio.run(run())

    def test_abbreviation_shield_handles_chunk_split(self):
        shield = _AbbreviationShield()
        output = shield.add("сказал т.")
        output += shield.add(" е. дальше")
        output += shield.finish()
        self.assertNotIn(".", output)
        self.assertEqual(shield.restore(output), "сказал т. е. дальше")

    def test_abbreviations_at_every_offset_and_chunk_size_reach_engine_normalized(self):
        engine = SupertonicEngine(
            crop_silence_ms=0,
            normalization_config=NormalizationConfig(profile="enhanced", stress="off"),
        )
        synthesized = []

        class FakeTTS:
            def get_voice_style(self, voice_name):
                return voice_name

            def synthesize(self, text, voice_style=None, lang=None, speed=None, total_step=None):
                synthesized.append(text)
                return np.ones(4, dtype=np.float32), None

        engine.tts = FakeTTS()
        for abbreviation, expected in (("т. к.", "так как"), ("т. е.", "то есть")):
            for prefix_len in range(13):
                source = ("x" * prefix_len) + " " + abbreviation + " дальше."
                partitions = [
                    [source[i:i + size] for i in range(0, len(source), size)]
                    for size in range(1, len(source) + 1)
                ]
                partitions.extend([[source[:offset], source[offset:]] for offset in range(len(source) + 1)])
                for chunks in partitions:
                    shield = _AbbreviationShield()
                    detector = SentenceBoundaryDetector()
                    sentences = []
                    for chunk in chunks:
                        sentences.extend(detector.add_chunk(shield.add(chunk)))
                    sentences.extend(detector.add_chunk(shield.finish()))
                    remainder = detector.finish()
                    if remainder:
                        sentences.append(remainder)
                    before_count = len(synthesized)
                    for sentence in sentences:
                        engine.synthesize(shield.restore(sentence), "M1", "ru")
                    spoken = synthesized[-1]
                    self.assertIn(expected, spoken, (abbreviation, prefix_len, chunks, spoken))
                    self.assertEqual(len(synthesized), before_count + 1)

    def test_private_use_character_is_not_restored_without_active_shield(self):
        async def run():
            for config, language in (
                (NormalizationConfig(profile="enhanced", stress="off"), "en"),
                (NormalizationConfig(profile="legacy"), "ru"),
            ):
                patcher = None
                if config.profile == "legacy":
                    patcher = patch("wyoming_supertonic.ru_norm.RussianTextNormalizer")
                    patcher.start()
                try:
                    handler, _ = self.make_handler(config)
                    handler._current_language = language
                    handler._new_request_pipeline()
                    seen = []

                    async def capture(text):
                        seen.append(text)

                    handler._synthesize_text = capture
                    await handler._process_sentence("text\ue000 remains.")
                    self.assertEqual(seen, ["text\ue000 remains."])
                finally:
                    if patcher is not None:
                        patcher.stop()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
