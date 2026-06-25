"""
Tests for the output-filename helpers in utils.filename.
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.filename import (  # noqa: E402
    build_output_filename,
    extract_mod_title,
    language_to_suffix,
    sanitize_filename,
)


class LanguageSuffixTest(unittest.TestCase):
    def test_known_mappings(self) -> None:
        self.assertEqual(language_to_suffix("ja"), "jp")
        self.assertEqual(language_to_suffix("JA"), "jp")
        self.assertEqual(language_to_suffix("japanese"), "jp")
        self.assertEqual(language_to_suffix("zh-CN"), "cn")
        self.assertEqual(language_to_suffix("zh-TW"), "tw")
        self.assertEqual(language_to_suffix("zh"), "cn")
        self.assertEqual(language_to_suffix("en"), "en")
        self.assertEqual(language_to_suffix("ru"), "ru")
        self.assertEqual(language_to_suffix("ko"), "kr")
        self.assertEqual(language_to_suffix("fr"), "fr")
        self.assertEqual(language_to_suffix("de"), "de")

    def test_unknown_falls_back_to_lowercase_prefix(self) -> None:
        self.assertEqual(language_to_suffix("xx-foo"), "xx")
        self.assertEqual(language_to_suffix("zz"), "zz")
        self.assertEqual(language_to_suffix(""), "xx")
        self.assertEqual(language_to_suffix(None), "xx")


class SanitizeFilenameTest(unittest.TestCase):
    def test_replaces_invalid_chars(self) -> None:
        self.assertEqual(
            sanitize_filename("a/b\\c:d*e?f\"g<h>i|j"),
            "a-b-c-d-e-f-g-h-i-j",
        )

    def test_collapses_separators(self) -> None:
        self.assertEqual(sanitize_filename("foo   bar__baz--qux"), "foo-bar-baz-qux")

    def test_strips_edges(self) -> None:
        self.assertEqual(sanitize_filename("  ---hello---  "), "hello")

    def test_truncates_long_names(self) -> None:
        out = sanitize_filename("a" * 200, max_length=10)
        self.assertEqual(len(out), 10)
        self.assertFalse(out.endswith("-"))

    def test_normalizes_fullwidth(self) -> None:
        # Fullwidth digits / letters fold to ASCII under NFKC.
        self.assertEqual(sanitize_filename("\uff27\uff4f"), "Go")

    def test_empty_input(self) -> None:
        self.assertEqual(sanitize_filename(""), "")
        self.assertEqual(sanitize_filename(None or ""), "")


class ExtractModTitleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel_path: str, text: str) -> None:
        full = os.path.join(self.tmp, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def test_reads_title_with_colon(self) -> None:
        self._write(
            "mod-info.txt",
            textwrap.dedent(
                """\
                # A mod
                [mod]
                title: Zombie Survival | v2.1.2 Rebirth
                description: ...
                """
            ),
        )
        self.assertEqual(
            extract_mod_title(self.tmp),
            "Zombie Survival | v2.1.2 Rebirth",
        )

    def test_reads_title_with_equals(self) -> None:
        self._write("mod-info.txt", "title = Some Mod\n")
        self.assertEqual(extract_mod_title(self.tmp), "Some Mod")

    def test_case_insensitive(self) -> None:
        self._write("MOD-INFO.TXT", "Title: Case\n")
        self.assertEqual(extract_mod_title(self.tmp), "Case")

    def test_missing_returns_none(self) -> None:
        self.assertIsNone(extract_mod_title(self.tmp))

    def test_nested_modinfo_fallback(self) -> None:
        self._write(
            "somefolder/mod-info.txt",
            "title: Nested\n",
        )
        self.assertEqual(extract_mod_title(self.tmp), "Nested")


class BuildOutputFilenameTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_uses_mod_title_for_ja(self) -> None:
        with open(os.path.join(self.tmp, "mod-info.txt"), "w", encoding="utf-8") as f:
            f.write("title: Zombie Survival | v2.1.2 Rebirth\n")
        self.assertEqual(
            build_output_filename(self.tmp, "ja"),
            "Zombie-Survival-v2.1.2-Rebirth-jp.rwmod",
        )

    def test_uses_mod_title_for_zh_cn(self) -> None:
        with open(os.path.join(self.tmp, "mod-info.txt"), "w", encoding="utf-8") as f:
            f.write("title: 末日生存\n")
        self.assertEqual(
            build_output_filename(self.tmp, "zh-CN"),
            "末日生存-cn.rwmod",
        )

    def test_falls_back_when_no_modinfo(self) -> None:
        self.assertEqual(
            build_output_filename(self.tmp, "ru", fallback_name="test"),
            "test-ru.rwmod",
        )

    def test_falls_back_to_default_when_nothing_available(self) -> None:
        self.assertEqual(
            build_output_filename(self.tmp, "ru"),
            "mod-ru.rwmod",
        )


if __name__ == "__main__":
    unittest.main()
