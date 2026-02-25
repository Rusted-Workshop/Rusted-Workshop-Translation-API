"""
独立集成测试：解析 rwmod 并执行汉化替换（不依赖 PG / MQ / HTTP）。

流程：
1. 解压 rwmod
2. 扫描 .ini/.template/.un/.fx/mod-info.txt
3. 执行结构保留式翻译替换
4. 校验语法安全（避免 <= / >= 被破坏、三引号不闭合）
5. 重新打包为 translated.rwmod
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import core.translate as translate_module
from core.translate import translate_file_preserve_structure
from services.s3_service import create_archive, extract_archive
from utils.ini_lib import found_ini_files, read_file


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def contains_chinese(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def mock_zh_translation(text: str) -> str:
    # 仅用于集成测试，确保文本发生“汉化替换”且不依赖外部模型
    if contains_chinese(text):
        return text
    return f"汉化:{text}"


async def run(args: argparse.Namespace) -> int:
    input_file = Path(args.file).resolve()
    if not input_file.exists():
        print(f"File not found: {input_file}")
        return 1

    run_id = time.strftime("%Y%m%d_%H%M%S")
    run_root = PROJECT_ROOT / "artifacts" / "integration" / f"rwmod_localize_{run_id}"
    src_dir = run_root / "source"
    out_rwmod = run_root / "translated.rwmod"

    run_root.mkdir(parents=True, exist_ok=True)
    src_dir.mkdir(parents=True, exist_ok=True)

    print(f"RUN_ROOT={run_root}")
    print(f"INPUT_RWMOD={input_file}")

    extract_archive(str(input_file), str(src_dir))
    all_files = [Path(p) for p in found_ini_files(str(src_dir))]
    if args.max_files > 0:
        all_files = all_files[: args.max_files]

    if not all_files:
        print("No translatable files found.")
        return 2

    before_hash = {str(p): sha256_file(p) for p in all_files}

    original_translate_tasks = translate_module.translate_tasks
    if args.mock_translation:

        async def _mock_translate_tasks(
            tasks: dict,
            translate_style: str,
            target_language: str = "中文",
            max_retries: int = 3,
            retry_delay: float = 1.0,
        ) -> dict[str, str]:
            return {src: mock_zh_translation(src) for src in tasks.keys()}

        translate_module.translate_tasks = _mock_translate_tasks
        print("TRANSLATION_MODE=mock")
    else:
        print("TRANSLATION_MODE=real")

    try:
        sem = asyncio.Semaphore(max(1, args.concurrency))

        async def _translate_one(file_path: Path) -> None:
            async with sem:
                await translate_file_preserve_structure(
                    str(file_path),
                    translate_style=args.translate_style,
                    target_language=args.target_language,
                )

        await asyncio.gather(*[_translate_one(path) for path in all_files])
    finally:
        translate_module.translate_tasks = original_translate_tasks

    changed = 0
    syntax_break_files: list[str] = []
    triple_quote_files: list[str] = []

    for path in all_files:
        current_hash = sha256_file(path)
        if current_hash != before_hash[str(path)]:
            changed += 1

        content = read_file(str(path))
        if "<:" in content or ">:" in content:
            syntax_break_files.append(str(path))
        if content.count('"""') % 2 != 0:
            triple_quote_files.append(str(path))

    # 针对已知高风险文件追加定向校验
    torpedo_files = [p for p in all_files if p.name == "反潜鱼雷.ini"]
    for p in torpedo_files:
        c = read_file(str(p))
        if "self.height<=1.4" not in c or "self.timeAlive>=" not in c:
            syntax_break_files.append(f"{p} (known-check)")

    tank_files = [p for p in all_files if p.name == "超时空坦克.ini"]
    for p in tank_files:
        c = read_file(str(p))
        if 'setUnitMemory:"""' not in c:
            triple_quote_files.append(f"{p} (known-check)")

    create_archive(str(src_dir), str(out_rwmod), format="zip")

    print(f"TOTAL_FILES={len(all_files)}")
    print(f"CHANGED_FILES={changed}")
    print(f"SYNTAX_BREAK_FILES={len(syntax_break_files)}")
    print(f"TRIPLE_QUOTE_ISSUES={len(triple_quote_files)}")
    print(f"OUTPUT_RWMOD={out_rwmod}")

    if syntax_break_files:
        print("SYNTAX_BREAK_SAMPLE:")
        for path in syntax_break_files[:10]:
            print(path)
    if triple_quote_files:
        print("TRIPLE_QUOTE_SAMPLE:")
        for path in triple_quote_files[:10]:
            print(path)

    if changed == 0:
        print("Integration failed: no file changed after localization.")
        return 3
    if syntax_break_files or triple_quote_files:
        print("Integration failed: found structural break risks.")
        return 4

    print("INTEGRATION_RESULT=PASSED")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--file",
        default="tmp7cgm8rh1.rwmod",
        help="待测试 rwmod 文件路径",
    )
    parser.add_argument(
        "--target-language",
        default="zh-CN",
        help="目标语言",
    )
    parser.add_argument(
        "--translate-style",
        default="auto",
        help="翻译风格",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=15,
        help="并发翻译文件数",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=0,
        help="仅测试前 N 个文件（0 表示全量）",
    )
    parser.add_argument(
        "--mock-translation",
        action="store_true",
        help="使用内置 mock 汉化，不调用外部模型接口",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    raise SystemExit(asyncio.run(run(args)))
