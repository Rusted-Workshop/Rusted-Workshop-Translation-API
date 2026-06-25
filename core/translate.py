import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from utils.config import AI_API_KEY, AI_BASE_URL, AI_MODEL
from utils.ini_lib import IniFile, read_file
from utils.language import (
    normalize_language_suffix,
    resolve_target_language,
    resolve_target_language_suffixes,
)

# 基础文本键的正则（不包含语言后缀）
BASE_TEXT_KEYS_REGEX = re.compile(
    r"(?i)^(?:"
    r"description|"
    r"title|"
    r"displaydescription|"
    r"text|"
    r"displayText|"
    r"isLockedAltMessage|"
    r"cannotPlaceMessage|"
    r"displayName|"
    r"displayNameShort|"
    r"showMessageToPlayer|"
    r"showMessageToAllPlayers|"
    r"action_\d+_(?:text|displayName)"
    r")$",
    re.IGNORECASE,
)

LANGUAGE_SUFFIX_REGEX = re.compile(
    r"^[a-z]{2,3}(?:[-_][a-z0-9]{2,8})*$",
    re.IGNORECASE,
)

LINE_KV_REGEX = re.compile(
    r"^(?P<indent>\s*)(?P<key>[^:=\n]+?)(?P<pre>\s*)(?P<sep>[:=])(?P<post>\s*)(?P<value>.*)$"
)


def is_text_key_valid(key: str) -> bool:
    """检查是否为有效的基础文本键（不包含语言后缀）"""
    return BASE_TEXT_KEYS_REGEX.match(key) is not None


def split_localized_text_key(key: str) -> tuple[str, str] | None:
    """
    将本地化键拆分为 (基础键, 语言后缀)。
    例如：
    - text_ru -> ("text", "ru")
    - action_1_text_zh-CN -> ("action_1_text", "zh-cn")
    """
    if "_" not in key:
        return None

    key_parts = key.split("_")
    if len(key_parts) < 2:
        return None

    # 兼容 text_ru / text_zh-cn / text_zh_cn 等后缀形式
    for suffix_parts_count in range(1, len(key_parts)):
        base_key = "_".join(key_parts[:-suffix_parts_count])
        suffix = "_".join(key_parts[-suffix_parts_count:])
        if not base_key or not suffix:
            continue
        if not is_text_key_valid(base_key):
            continue
        if not LANGUAGE_SUFFIX_REGEX.fullmatch(suffix):
            continue
        return base_key, suffix.lower()

    return None


def is_localized_text_key(key: str) -> bool:
    """检查是否为带语言后缀的文本键"""
    return split_localized_text_key(key) is not None


def localized_to_base_key(key: str) -> str:
    """将带语言后缀的键名还原为基础键名。"""
    localized = split_localized_text_key(key)
    if not localized:
        return key
    return localized[0]


def _extract_json_array(text: str) -> list[str]:
    content = text.strip()
    if content.startswith("```"):
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline + 1:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
    parsed = json.loads(content)
    if not isinstance(parsed, list):
        raise ValueError(f"Expected list, got {type(parsed)}")
    return [str(item) for item in parsed]


def _sanitize_single_line_value(value: str) -> str:
    """将模型返回结果规范为单行 INI 值（保留 \\n 转义）。"""
    return value.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n")


def should_skip_localized_generation(key: str, value: str) -> bool:
    """
    判断某个基础文本键是否应跳过生成本地化后缀键。

    目前规则：
    - `i:` 开头的值是引擎内置文本资源键，不应再生成 `*_zh` / `*_ru`
    """
    _ = key
    return value.strip().startswith("i:")


# 目标语言 → 语言代码映射（用于 prompt 中引用）
_TARGET_LANG_CODE_MAP: dict[str, str] = {
    "中文": "Chinese",
    "汉语": "Chinese",
    "汉化": "Chinese",
    "俄文": "Russian",
    "俄语": "Russian",
    "英文": "English",
    "英语": "English",
    "日文": "Japanese",
    "日语": "Japanese",
    "韩文": "Korean",
    "韩语": "Korean",
}


def _get_target_lang_english(prompt_target_language: str) -> str:
    """将目标语言的人类可读名称转为英文（用于 prompt）。"""
    return _TARGET_LANG_CODE_MAP.get(prompt_target_language, prompt_target_language)


def _build_translate_rules(target_lang_english: str) -> str:
    """根据目标语言生成翻译规则（英文，避免中文偏见）。"""
    return (
        f"Translation rules:\n"
        f"1. Preserve all variables, placeholders, expressions, escapes, and tags exactly as-is, "
        f"e.g. `${{...}}`, `%{{...}}`, `\\n`, `[tag]`\n"
        f"2. Variable names are case-sensitive; preserve them character-for-character. "
        f"Do NOT rename, correct case, complete, simplify, or translate variable names\n"
        f"3. Only translate natural language text; do NOT alter code-style content, "
        f"numeric formulas, key references, or content inside placeholders\n"
        f"4. Output must be in {target_lang_english}"
    )


def _build_system_prompt(target_lang_english: str) -> str:
    """构建 system prompt（英文，避免中文偏见）。"""
    return (
        f"You are a professional Rusted Warfare mod unit translation assistant. "
        f"You strictly output a JSON array of translated strings in {target_lang_english}. "
        f"Preserve all placeholders and variable references exactly; variable names are case-sensitive and must not be altered."
    )


def analysis_style(content: str, target_language: str = "中文") -> str:
    """
    分析原文风格，返回翻译风格指令。

    Args:
        content: 原文样本内容
        target_language: 目标语言的人类可读名称（如"中文""俄文"）
    """
    if not content.strip():
        return ""
    target_lang_english = _get_target_lang_english(target_language)
    # 生成与目标语言匹配的风格指令（英文，避免中文偏见）
    return (
        f"Maintain the original's concise, technical style. "
        f"Keep terminology consistent and avoid verbose explanations. "
        f"All translated output must be in {target_lang_english}."
    )


# ---- 批量调优：默认每次 LLM 请求可容纳更多文本（多文件批量化后明显受益） ----
LLM_BATCH_ITEMS = int(os.environ.get("LLM_BATCH_ITEMS", "100"))
LLM_BATCH_CHARS = int(os.environ.get("LLM_BATCH_CHARS", "16000"))


async def translate_tasks(
    tasks: dict,
    translate_style: str = "",
    target_language: str = "中文",
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> dict[str, str]:
    """
    翻译任务字典，返回原文到译文的映射。

    支持智能批处理：当 tasks 较小时，合并为一个请求；
    当 tasks 较大时，按合理大小分批发送，减少 API 调用次数。
    """
    original_texts = list(tasks.keys())
    if not original_texts:
        return {}

    prompt_target_language, _ = resolve_target_language(target_language)
    target_lang_english = _get_target_lang_english(prompt_target_language)

    if not AI_API_KEY:
        return {text: text for text in original_texts}

    # 智能分批：控制每个请求的文本数量，避免单次请求过短或过长
    max_items = LLM_BATCH_ITEMS
    max_chars = LLM_BATCH_CHARS

    batches: list[list[str]] = []
    current_batch: list[str] = []
    current_chars = 0

    for text in original_texts:
        current_batch.append(text)
        current_chars += len(text)
        if len(current_batch) >= max_items or current_chars >= max_chars:
            batches.append(current_batch)
            current_batch = []
            current_chars = 0

    if current_batch:
        batches.append(current_batch)

    client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)
    try:
        all_translations: list[str] = []
        last_error: Exception | None = None

        for batch_texts in batches:
            batch_numbered = "\n".join(
                [f"{i + 1}. {text}" for i, text in enumerate(batch_texts)]
            )

            translate_rules = _build_translate_rules(target_lang_english)
            system_prompt = _build_system_prompt(target_lang_english)

            prompt = f"""{translate_style}
You are a Rusted Warfare mod unit translation expert.
Target language: {target_lang_english}

{translate_rules}

Translate the following texts in order (maintain item order, do not explain):
{batch_numbered}

Return requirements:
1. Return ONLY a JSON array
2. Array length must equal {len(batch_texts)}
3. Do not return any extra text
"""

            for attempt in range(max_retries):
                try:
                    response = await client.chat.completions.create(
                        model=AI_MODEL,
                        temperature=0.2,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": prompt},
                        ],
                    )

                    message = (
                        response.choices[0].message.content
                        if response.choices
                        else None
                    )
                    if not message:
                        raise ValueError("Empty translation response content")

                    translations = _extract_json_array(message)
                    if len(translations) != len(batch_texts):
                        raise ValueError(
                            f"Translation count mismatch: expected {len(batch_texts)}, got {len(translations)}"
                        )

                    all_translations.extend(translations)
                    break
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                    else:
                        print(f"LLM translation failed after retries: {e}")

        if len(all_translations) == len(original_texts):
            return {
                original_texts[idx]: all_translations[idx]
                for idx in range(len(original_texts))
            }
    finally:
        await client.close()

    # 回退：失败时保留原文，确保流水线可完成并暴露错误日志
    if last_error is not None:
        print(f"translate_tasks 全量失败，回退原文: {last_error}")
    return {text: text for text in original_texts}


# ----------------------------------------------------------------------------
# 单文件解析 / 写回（同时被单文件和批量翻译复用）
# ----------------------------------------------------------------------------


@dataclass
class ParsedFile:
    """单文件解析结果：包含 write-back 所需的全部状态。"""

    file_path: str
    parsed_lines: list[dict[str, Any]]
    existing_suffixes_by_pair: dict[tuple[str, str], set[str]] = field(default_factory=dict)
    localized_values: dict[tuple[str, str], str] = field(default_factory=dict)
    line_ending: str = "\n"
    had_trailing_newline: bool = False


def _parse_ini_file(file_path: str) -> ParsedFile:
    """解析单个 ini/template/un/fx 文件为可翻译的中间结构。"""
    content = read_file(file_path)
    line_ending = "\r\n" if "\r\n" in content else "\n"
    had_trailing_newline = content.endswith("\n") or content.endswith("\r")

    parsed_lines: list[dict[str, Any]] = []
    section = ""
    in_triple_quote_block = False

    for raw_line in content.splitlines():
        stripped = raw_line.strip()

        if in_triple_quote_block:
            parsed_lines.append({"kind": "raw", "raw": raw_line})
            if raw_line.count('"""') % 2 == 1:
                in_triple_quote_block = False
            continue

        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            parsed_lines.append({"kind": "raw", "raw": raw_line})
            continue

        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip()
            parsed_lines.append({"kind": "raw", "raw": raw_line})
            continue

        match = LINE_KV_REGEX.match(raw_line)
        if not match:
            parsed_lines.append({"kind": "raw", "raw": raw_line})
            continue

        key = match.group("key").strip()
        value = match.group("value")

        parsed_lines.append(
            {
                "kind": "kv",
                "raw": raw_line,
                "section": section,
                "indent": match.group("indent"),
                "key": key,
                "pre": match.group("pre"),
                "sep": match.group("sep"),
                "post": match.group("post"),
                "value": value,
            }
        )

        if value.count('"""') % 2 == 1:
            in_triple_quote_block = True

    localized_values: dict[tuple[str, str], str] = {}
    existing_suffixes_by_pair: dict[tuple[str, str], set[str]] = {}

    for item in parsed_lines:
        if item["kind"] != "kv":
            continue
        key = item["key"]
        localized = split_localized_text_key(key)
        if not localized:
            continue
        base_key, suffix = localized
        pair = (item["section"], base_key)
        existing_suffixes_by_pair.setdefault(pair, set()).add(suffix)
        localized_value = item["value"].strip()
        if localized_value:
            localized_values.setdefault((item["section"], base_key), localized_value)

    return ParsedFile(
        file_path=file_path,
        parsed_lines=parsed_lines,
        existing_suffixes_by_pair=existing_suffixes_by_pair,
        localized_values=localized_values,
        line_ending=line_ending,
        had_trailing_newline=had_trailing_newline,
    )


def _collect_translatable_texts(parsed: ParsedFile) -> dict[str, str]:
    """收集单文件内待翻译的源文本（去重），同时把 source_text 写回到 parsed_lines 上。"""
    translate_tasks_dict: dict[str, str] = {}
    for item in parsed.parsed_lines:
        if item["kind"] != "kv":
            continue
        key = item["key"]
        if not is_text_key_valid(key):
            continue

        source_text = item["value"].strip()
        if not source_text:
            source_text = parsed.localized_values.get((item["section"], key), "")
        if not source_text:
            continue
        if should_skip_localized_generation(key, source_text):
            continue
        item["source_text"] = source_text
        translate_tasks_dict[source_text] = "translation key"
    return translate_tasks_dict


def _write_translated_file(
    parsed: ParsedFile,
    translations: dict[str, str],
    target_language: str,
) -> None:
    """将翻译结果写回到磁盘文件，保留原始结构。"""
    prompt_target_language, target_suffix = resolve_target_language(target_language)
    target_suffixes = resolve_target_language_suffixes(target_language)

    translated_by_pair: dict[tuple[str, str], str] = {}
    for item in parsed.parsed_lines:
        if item["kind"] != "kv":
            continue
        key = item["key"]
        if not is_text_key_valid(key):
            continue
        source_text = item.get("source_text")
        if not source_text:
            continue
        translated_value = translations.get(source_text, source_text)
        translated_by_pair[(item["section"], key)] = _sanitize_single_line_value(
            translated_value
        )

    output_lines: list[str] = []
    inserted_target_key_suffixes: set[tuple[tuple[str, str], str]] = set()

    for item in parsed.parsed_lines:
        if item["kind"] != "kv":
            output_lines.append(item["raw"])
            continue

        key = item["key"]
        section = item["section"]

        localized = split_localized_text_key(key)
        if localized:
            base_key, suffix = localized
            suffix_primary = normalize_language_suffix(suffix, default_suffix=suffix)
            if suffix_primary == target_suffix:
                translated_value = translated_by_pair.get((section, base_key))
                if translated_value:
                    output_lines.append(
                        f"{item['indent']}{key}{item['pre']}{item['sep']}{item['post']}{translated_value}"
                    )
                else:
                    output_lines.append(item["raw"])
            else:
                output_lines.append(item["raw"])
            continue

        output_lines.append(item["raw"])

        if not is_text_key_valid(key):
            continue

        pair = (section, key)
        translated_value = translated_by_pair.get(pair)
        if not translated_value:
            continue

        existing_suffixes = parsed.existing_suffixes_by_pair.get(pair, set())
        for suffix in target_suffixes:
            if suffix in existing_suffixes:
                continue
            suffix_key = (pair, suffix)
            if suffix_key in inserted_target_key_suffixes:
                continue

            localized_key = f"{key}_{suffix}"
            output_lines.append(
                f"{item['indent']}{localized_key}{item['pre']}{item['sep']}{item['post']}{translated_value}"
            )
            inserted_target_key_suffixes.add(suffix_key)

    new_content = parsed.line_ending.join(output_lines)
    if parsed.had_trailing_newline:
        new_content += parsed.line_ending

    with open(parsed.file_path, "w", encoding="utf-8", newline="") as file:
        file.write(new_content)


# ----------------------------------------------------------------------------
# 对外 API
# ----------------------------------------------------------------------------


async def translate_file_preserve_structure(
    file_path: str,
    translate_style: str = "",
    target_language: str = "中文",
) -> None:
    """
    在保留文件结构的前提下，为目标语言写入/更新本地化键（单文件）。
    """
    parsed = _parse_ini_file(file_path)
    translate_tasks_dict = _collect_translatable_texts(parsed)
    translations: dict[str, str] = {}
    if translate_tasks_dict:
        translations = await translate_tasks(
            translate_tasks_dict,
            translate_style=translate_style,
            target_language=target_language,
        )
    _write_translated_file(parsed, translations, target_language)


async def translate_files_batch(
    file_paths: list[str],
    work_dir: str,
    translate_style: str = "",
    target_language: str = "中文",
) -> dict[str, str]:
    """
    批量翻译多个文件，所有文件共用一次（或少数几次）LLM 请求。

    Args:
        file_paths: 相对 work_dir 的文件路径列表
        work_dir: 工作目录（已解压的 rwmod 根目录）
        translate_style: 翻译风格指令
        target_language: 目标语言

    Returns:
        {file_path: error_message} 失败的文件映射；成功的文件不会出现在结果中
    """
    if not file_paths:
        return {}

    parsed_files: list[ParsedFile] = []
    all_translate_tasks: dict[str, str] = {}

    for rel_path in file_paths:
        rel_norm = rel_path.replace("/", os.sep)
        full_path = os.path.join(work_dir, rel_norm)
        try:
            parsed = _parse_ini_file(full_path)
        except Exception as e:
            print(f"[batch] 解析文件失败 {rel_path}: {e}")
            return {rel_path: str(e)}

        per_file_texts = _collect_translatable_texts(parsed)
        if per_file_texts:
            parsed_files.append(parsed)
            for text in per_file_texts:
                all_translate_tasks.setdefault(text, "translation key")
        else:
            # 无可翻译文本，但仍记入列表以便后续决策
            parsed_files.append(parsed)

    if not all_translate_tasks:
        return {}

    translations = await translate_tasks(
        all_translate_tasks,
        translate_style=translate_style,
        target_language=target_language,
    )

    errors: dict[str, str] = {}
    for parsed in parsed_files:
        try:
            _write_translated_file(parsed, translations, target_language)
        except Exception as e:
            errors[parsed.file_path] = str(e)
    return errors


async def translate_inifile(
    inifile: IniFile,
    translate_style: str = "",
    target_language: str = "中文",
) -> IniFile:
    """
    翻译单个 ini 文件：
    - 保留基础键原文
    - 仅新增/更新目标语言后缀键（如 text_zh, description_ru）
    """
    prompt_target_language, _target_suffix = resolve_target_language(target_language)
    target_suffixes = resolve_target_language_suffixes(target_language)

    # 先汇总各基础键可回退使用的本地化文本
    localized_values: dict[tuple[str, str], str] = {}
    for section in inifile.data.keys():
        for key, value in inifile.data[section].items():
            localized = split_localized_text_key(key)
            if not localized:
                continue
            base_key, _ = localized
            localized_value = value.strip()
            if localized_value:
                localized_values.setdefault((section, base_key), localized_value)

    # 收集待翻译文本（按基础键），并计算目标语言键名
    text_keys: list[tuple[str, str, str]] = []
    translate_tasks_dict: dict[str, str] = {}

    for section in inifile.data.keys():
        for key in inifile.data[section]:
            if not is_text_key_valid(key):
                continue
            source_text = inifile.data[section][key].strip()
            if not source_text:
                source_text = localized_values.get((section, key), "")
            if not source_text:
                continue
            if should_skip_localized_generation(key, source_text):
                continue

            text_keys.append((section, key, source_text))
            translate_tasks_dict[source_text] = "translation key"

    # 翻译并写入目标语言键
    if translate_tasks_dict:
        translations = await translate_tasks(
            translate_tasks_dict,
            translate_style,
            target_language,
        )

        for section, base_key, source_text in text_keys:
            translated = translations.get(source_text)
            if translated is not None:
                for suffix in target_suffixes:
                    localized_key = f"{base_key}_{suffix}"
                    inifile.data[section][localized_key] = translated

    return inifile
