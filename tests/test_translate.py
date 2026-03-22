import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")

    class AsyncOpenAI:  # pragma: no cover - test stub
        pass

    openai_stub.AsyncOpenAI = AsyncOpenAI
    sys.modules["openai"] = openai_stub

from core.translate import (
    should_skip_localized_generation,
    translate_file_preserve_structure,
    translate_inifile,
    translate_tasks,
)
from utils.ini_lib import IniFile


class TranslateRulesTest(unittest.IsolatedAsyncioTestCase):
    async def test_translate_tasks_prompt_mentions_case_sensitive_variables(self) -> None:
        captured_messages: list[dict[str, str]] = []

        class FakeResponseMessage:
            content = '["translated"]'

        class FakeChoice:
            message = FakeResponseMessage()

        class FakeResponse:
            choices = [FakeChoice()]

        class FakeCompletions:
            async def create(self, **kwargs):
                captured_messages.extend(kwargs["messages"])
                return FakeResponse()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.chat = FakeChat()

            async def close(self):
                return None

        with (
            patch("core.translate.AI_API_KEY", "test-key"),
            patch("core.translate.AsyncOpenAI", FakeClient),
        ):
            result = await translate_tasks({"value ${antiTeleportRange}": "x"})

        self.assertEqual(result, {"value ${antiTeleportRange}": "translated"})
        self.assertEqual(len(captured_messages), 2)
        self.assertIn("变量名区分大小写", captured_messages[0]["content"])
        self.assertIn("变量名区分大小写", captured_messages[1]["content"])
        self.assertIn("${antiTeleportRange}", captured_messages[1]["content"])

    async def test_translate_file_preserve_structure_skips_i18n_resource_keys(self) -> None:
        content = "\n".join(
            [
                "[action_buildAntiNuke]",
                "text: i:gui.actions.buildAntiNuke",
                "description: Build anti nuke missile",
                "",
            ]
        )

        async def fake_translate_tasks(tasks, translate_style="", target_language="中文"):
            return {
                source: "建造后自动拦截战略导弹"
                for source in tasks
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "sample.ini"
            file_path.write_text(content, encoding="utf-8")

            with patch("core.translate.translate_tasks", fake_translate_tasks):
                await translate_file_preserve_structure(str(file_path))

            output = file_path.read_text(encoding="utf-8")

        self.assertIn("text: i:gui.actions.buildAntiNuke", output)
        self.assertNotIn("text_zh:", output)
        self.assertIn("description_zh: 建造后自动拦截战略导弹", output)

    async def test_translate_inifile_skips_i18n_resource_keys(self) -> None:
        content = "\n".join(
            [
                "[action_buildAntiNuke]",
                "text: i:gui.actions.buildAntiNuke",
                "description: Build anti nuke missile",
                "",
            ]
        )

        async def fake_translate_tasks(tasks, translate_style="", target_language="中文"):
            return {
                source: "建造后自动拦截战略导弹"
                for source in tasks
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "sample.ini"
            file_path.write_text(content, encoding="utf-8")
            inifile = IniFile(str(file_path))

            with patch("core.translate.translate_tasks", fake_translate_tasks):
                translated = await translate_inifile(inifile)

        section = translated.data["action_buildAntiNuke"]
        self.assertEqual(section["text"], "i:gui.actions.buildAntiNuke")
        self.assertNotIn("text_zh", section)
        self.assertEqual(section["description_zh"], "建造后自动拦截战略导弹")


class TranslationHelpersTest(unittest.TestCase):
    def test_should_skip_localized_generation_for_i18n_resource_key(self) -> None:
        self.assertTrue(
            should_skip_localized_generation("text", "i:gui.actions.buildAntiNuke")
        )
        self.assertFalse(
            should_skip_localized_generation(
                "displayDescription", "Range: ${antiTeleportRange}"
            )
        )


if __name__ == "__main__":
    unittest.main()
