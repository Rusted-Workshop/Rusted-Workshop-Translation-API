import os
from pathlib import Path

prompt_dir = Path(__file__).parent

translate_style_prompt = open(
    os.path.join(prompt_dir, "translate_style.md"), encoding="utf-8"
).read()

translate_prompt = open(
    os.path.join(prompt_dir, "translate.md"), encoding="utf-8"
).read()
