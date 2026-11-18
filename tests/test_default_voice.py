import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from wyoming.info import Info
from wyoming.tts import Synthesize, SynthesizeChunk, SynthesizeStart, SynthesizeStop

from wyoming_supertonic.__main__ import main, parse_args
from wyoming_supertonic.handler import SupertonicEventHandler


class DefaultVoiceTests(unittest.TestCase):
    def test_cli_default_and_explicit_voice(self):
        self.assertIsNone(parse_args([], env={}).default_voice)
        self.assertEqual(parse_args(["--default-voice", "F2"], env={}).default_voice, "F2")

    def test_unavailable_default_prevents_server_start(self):
        engine = Mock(available_voices=["M1", "F2"])
        args = parse_args(["--default-voice", "missing"], env={})
        with patch("wyoming_supertonic.__main__.parse_args", return_value=args), \
             patch("wyoming_supertonic.__main__.SupertonicEngine", return_value=engine), \
             patch("wyoming_supertonic.__main__.AsyncTcpServer") as server, \
             self.assertLogs("wyoming_supertonic.__main__", level="CRITICAL") as logs:
            asyncio.run(main())
        server.assert_not_called()
        self.assertIn("M1, F2", logs.output[0])

    def test_batch_and_streaming_voice_selection(self):
        async def run():
            for default, requested, expected in (
                (None, None, "M1"),
                ("F2", None, "F2"),
                ("F2", "M1", "M1"),
                ("F2", "missing", "F2"),
                (None, "missing", "M1"),
            ):
                with self.subTest(default=default, requested=requested):
                    args = parse_args([], env={})
                    args.default_voice = default
                    engine = Mock(available_voices=["M1", "F2"])
                    engine.synthesize.return_value = (b"\x00\x00", 24000, None)
                    handler = SupertonicEventHandler(
                        Info(tts=[]), args, engine, asyncio.StreamReader(), Mock()
                    )
                    handler.write_event = AsyncMock()

                    # Reuse the connection to verify that a previous client
                    # selection does not leak into the next request.
                    for streaming in (False, True):
                        for voice in ("M1", requested):
                            data = {"voice": {"name": voice}} if voice else {}
                            if streaming:
                                event = SynthesizeStart().event()
                                event.data.update(data)
                                await handler.handle_event(event)
                                await handler.handle_event(SynthesizeChunk("Hello.").event())
                                await handler.handle_event(SynthesizeStop().event())
                            else:
                                event = Synthesize(text="Hello.").event()
                                event.data.update(data)
                                await handler.handle_event(event)
                            self.assertEqual(
                                engine.synthesize.call_args.args[1],
                                "M1" if voice == "M1" else expected,
                            )

        asyncio.run(run())
