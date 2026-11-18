import argparse
import asyncio
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import numpy as np
from wyoming.info import Info
from wyoming.tts import Synthesize, SynthesizeChunk, SynthesizeStart, SynthesizeStop

from wyoming_supertonic.__main__ import parse_args
from wyoming_supertonic.handler import SupertonicEventHandler
from wyoming_supertonic.normalization import NormalizationConfig
from wyoming_supertonic.supertonic_engine import SupertonicEngine


class QuestionMarksTests(unittest.TestCase):
    def make_engine(self, count=1):
        with patch.object(SupertonicEngine, "_load_normalizers"):
            engine = SupertonicEngine(question_marks=count, crop_silence_ms=0)
        engine.tts = Mock()
        engine.tts.synthesize = Mock(
            return_value=(np.zeros(8, dtype=np.float32), None)
        )
        return engine

    def test_cli_environment_default_and_precedence(self):
        self.assertEqual(parse_args([], env={}).question_marks, 1)
        self.assertEqual(parse_args([], env={"SUPERTONIC_QUESTION_MARKS": "3"}).question_marks, 3)
        self.assertEqual(
            parse_args(["--question-marks=2"], env={"SUPERTONIC_QUESTION_MARKS": "invalid"}).question_marks,
            2,
        )
        for value in ("0", "-1", "1.5", "invalid", ""):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    parse_args([], env={"SUPERTONIC_QUESTION_MARKS": value})
                with self.assertRaises(SystemExit):
                    parse_args(["--question-marks", value], env={})

    def test_engine_rejects_invalid_counts(self):
        for value in (0, -1, 1.5, "3", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SupertonicEngine(question_marks=value)

    def test_default_preserves_punctuation(self):
        engine = self.make_engine()
        source = "Да? Правда?? Точно???? Что?! Нет!"
        engine.synthesize(source, "M1", "ru")
        self.assertEqual(engine.tts.synthesize.call_args.args[0], source)

    def test_expansion_after_normalization_for_all_languages(self):
        engine = self.make_engine(3)
        for language in ("ru", "en", "de", "cs"):
            normalizer = Mock()
            normalizer.normalize.return_value = "Да? Правда?? Точно???? Что?! Нет!"
            engine.normalizers = {language: normalizer}
            engine.synthesize("input", "M1", language)
            normalizer.normalize.assert_called_once_with("input")
            self.assertEqual(
                engine.tts.synthesize.call_args.args[0],
                "Да??? Правда??? Точно???? Что???! Нет!",
            )

    def test_full_and_streaming_requests_reach_model_with_hint(self):
        async def run():
            for profile in ("legacy", "enhanced"):
                engine = self.make_engine(3)
                config = NormalizationConfig(profile=profile)
                args = argparse.Namespace(language="ru", no_streaming=False,
                                          normalization_config=config)
                handler = SupertonicEventHandler(
                    Info(tts=[]), args, engine, asyncio.StreamReader(), Mock()
                )

                async def ignore_event(_event):
                    pass

                handler.write_event = ignore_event
                await handler.handle_event(Synthesize(text="Ты дома?").event())
                full = [call.args[0] for call in engine.tts.synthesize.call_args_list]
                self.assertEqual(full, ["Ты дома???"])
                engine.tts.synthesize.reset_mock()
                await handler.handle_event(SynthesizeStart().event())
                for chunk in ("Ты ", "дома", "?"):
                    await handler.handle_event(SynthesizeChunk(chunk).event())
                await handler.handle_event(SynthesizeStop().event())
                streamed = [call.args[0] for call in engine.tts.synthesize.call_args_list]
                self.assertEqual(streamed, full)

        asyncio.run(run())
