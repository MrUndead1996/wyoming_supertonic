import tempfile
import unittest
from pathlib import Path
from wyoming_supertonic.normalization import NormalizationConfig, StreamingCleaner, TextNormalizer


def stream(source, size):
    cleaner = StreamingCleaner(max_buffer=64)
    return "".join(cleaner.add(source[i:i + size]) for i in range(0, len(source), size)) + cleaner.finish()


class NormalizationCoreTests(unittest.TestCase):
    def test_number_date_time_and_identifiers(self):
        n = TextNormalizer(NormalizationConfig(profile="enhanced"))
        self.assertEqual(n.normalize("-0.50"), "минус ноль целых пятьдесят сотых")
        self.assertEqual(n.normalize("+2.03"), "плюс две целых три сотых")
        self.assertEqual(n.normalize("192.168.1.1 v1.2.3"), "192.168.1.1 v1.2.3")
        self.assertEqual(n.normalize("2026-02-30"), "2026-02-30")
        self.assertEqual(n.normalize("2026-11-10"), "десятое ноября две тысячи двадцать шестого года")
        self.assertEqual(n.normalize("01:01"), "один час одна минута")
        self.assertEqual(n.normalize("00:00"), "ноль часов")

    def test_units_are_case_sensitive(self):
        n = TextNormalizer(NormalizationConfig(profile="enhanced"))
        self.assertEqual(n.normalize("2 kW"), "два киловатта")
        self.assertEqual(n.normalize("2 KW"), "два KW")

    def test_english_mixed_and_unicode_stress(self):
        n = TextNormalizer(NormalizationConfig(profile="enhanced", numbers=False))
        self.assertEqual(n.normalize("Home Assistant привет"), "Home Assistant привет")
        self.assertEqual(n.normalize("за́мок з+амок ё́лка Ё́лка"), "за́мок за́мок ёлка Ёлка")

    def test_accentor_does_not_receive_latin_or_expression_tags(self):
        seen = []
        def accent(text):
            seen.append(text)
            return text.upper()
        n = TextNormalizer(NormalizationConfig(profile="enhanced", stress="off"))
        n.accentor = accent
        result = n.normalize("hello привет <laugh> мир </sigh> world")
        self.assertEqual(result, "hello ПРИВЕТ <laugh> МИР </sigh> world")
        self.assertTrue(all("hello" not in x and "world" not in x and "<laugh>" not in x for x in seen))

    def test_accentor_never_sees_private_placeholders_or_protected_values(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dict.yml"
            path.write_text("pronunciations:\n  alias: словарь\n", encoding="utf-8")
            n = TextNormalizer(NormalizationConfig(
                profile="enhanced", stress="off", dictionary_path=str(path),
            ))
            calls = []

            def hostile_accentor(text):
                calls.append(text)
                # Simulates an external model that drops non-letter symbols.
                return "".join(char for char in text if char.isalpha() or char.isspace())

            n.accentor = hostile_accentor
            result = n.normalize("alias за́мок v1.2.3 обычный 2")

            self.assertEqual(result, "словарь за́мок v1.2.3 обычный два")
            self.assertTrue(calls)
            self.assertTrue(all("\U000f0000" not in text for text in calls))
            self.assertTrue(all(not any("A" <= char <= "Z" or "a" <= char <= "z" for char in text)
                                for text in calls))

    def test_dictionary_longest_literal_and_nonrecursive(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "dict.yml"
            path.write_text('pronunciations:\n  Home: "Дом MQTT"\n  "Home Assistant": "Хоум"\n  MQTT: "эм-кью-ти-ти"\n', encoding="utf-8")
            n = TextNormalizer(NormalizationConfig(profile="enhanced", dictionary_path=str(path)))
            self.assertEqual(n.normalize("Home Assistant Home"), "Хоум Дом MQTT")

    def test_dictionary_validation(self):
        for yaml in ("[]", "unknown: {}", "stress: []", "pronunciations:\n  x: 1"):
            with self.subTest(yaml=yaml), tempfile.TemporaryDirectory() as td:
                path = Path(td) / "bad.yml"
                path.write_text(yaml, encoding="utf-8")
                with self.assertRaises(ValueError):
                    TextNormalizer(NormalizationConfig(dictionary_path=str(path)))

    def test_independent_stage_flags(self):
        cases = [
            ({"numbers": False}, "5", "5"),
            ({"dates": False}, "2026-11-10", "2026-11-10"),
            ({"units": False}, "2 kW", "два kW"),
            ({"abbreviations": False}, "т. е.", "т. е."),
            ({"dictionary": False}, "alias", "alias"),
            ({"cleanup": False}, "**a**", "**a**"),
        ]
        for flag, source, expected in cases:
            with self.subTest(flag=flag):
                path = None
                if "dictionary" in flag:
                    td = tempfile.TemporaryDirectory()
                    path = Path(td.name) / "d.yml"
                    path.write_text("pronunciations:\n  alias: замена\n", encoding="utf-8")
                cfg = NormalizationConfig(profile="enhanced", dictionary_path=str(path) if path else None)
                for key, value in flag.items():
                    setattr(cfg, key, value)
                n = TextNormalizer(cfg)
                actual = n.clean(source) if "cleanup" in flag else n.normalize(source)
                self.assertEqual(actual, expected)
                if path:
                    td.cleanup()

    def test_tags_only_exact_known_tokens(self):
        self.assertEqual(StreamingCleaner().add("<laugh>x</laugh><laugh onload='x'>y</laugh>"),
                         "<laugh>x</laugh>y</laugh>")

    def test_cleanup_every_boundary_and_characterwise(self):
        cases = [
            ("[label](https://a.test/x(b)) tail", "label tail"),
            ("```secret``` visible", "  visible"),
            ("<!--secret--> visible", "  visible"),
            ("before <think foo='bar'>secret</think> after", "before   after"),
        ]
        for source, expected in cases:
            for size in range(1, len(source) + 1):
                with self.subTest(size=size, source=source):
                    self.assertEqual(stream(source, size), expected)

    def test_long_split_think_opener_has_bounded_state_and_discards_body(self):
        cleaner = StreamingCleaner(max_buffer=32)
        cleaner.add("<think ")
        for _ in range(200):
            cleaner.add("x" * 200)
            self.assertLessEqual(len(cleaner._pending), 32)
        self.assertEqual(cleaner.add(">secret</think>visible"), " visible")
        self.assertEqual(cleaner.finish(), "")

    def test_think_opener_closes_and_drains_remainder_in_same_add(self):
        cleaner = StreamingCleaner()
        self.assertEqual(cleaner.add("<think "), "")
        self.assertEqual(cleaner.add("attributes>secret</think>visible"), " visible")

        unfinished = StreamingCleaner()
        self.assertEqual(unfinished.add("<think "), "")
        self.assertEqual(unfinished.finish(), "")


if __name__ == "__main__":
    unittest.main()
