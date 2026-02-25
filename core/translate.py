import json
import re
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
            content = content[first_newline + 1 :]
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


async def translate_tasks(
    tasks: dict,
    translate_style: str,
    target_language: str = "中文",
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> dict[str, str]:
    """
    翻译任务字典，返回原文到译文的映射。
    """
    import asyncio

    original_texts = list(tasks.keys())
    if not original_texts:
        return {}

    prompt_target_language, _ = resolve_target_language(target_language)

    if not AI_API_KEY:
        return {text: text for text in original_texts}

    texts_numbered = "\n".join(
        [f"{i + 1}. {text}" for i, text in enumerate(original_texts)]
    )

    prompt = f"""{translate_style}
你是铁锈战争 mod 单位翻译专家。
目标语言: {prompt_target_language}

请按顺序翻译以下文本（保持条目顺序，不要解释）：
{texts_numbered}

返回要求：
1. 只返回 JSON 数组
2. 数组长度必须等于 {len(original_texts)}
3. 不要返回额外文本
"""

    client = AsyncOpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)
    try:
        last_error: Exception | None = None
        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(
                    model=AI_MODEL,
                    temperature=0.2,
                    messages=[
                        {
                            "role": "system",
                            "content": "你是专业翻译助手，严格输出 JSON 数组。",
                        },
                        {"role": "user", "content": prompt},
                    ],
                )

                message = (
                    response.choices[0].message.content if response.choices else None
                )
                if not message:
                    raise ValueError("Empty translation response content")

                translations = _extract_json_array(message)
                if len(translations) != len(original_texts):
                    raise ValueError(
                        f"Translation count mismatch: expected {len(original_texts)}, got {len(translations)}"
                    )

                return {
                    original_texts[idx]: translations[idx]
                    for idx in range(len(original_texts))
                }
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                else:
                    print(f"LLM translation failed after retries: {e}")
    finally:
        await client.close()

    # 回退：失败时保留原文，确保流水线可完成并暴露错误日志
    return {text: text for text in original_texts}


async def translate_file_preserve_structure(
    file_path: str,
    translate_style: str = "",
    target_language: str = "中文",
) -> None:
    """
    在保留文件结构的前提下，为目标语言写入/更新本地化键。
    关键点：
    1. 不使用 configparser 重写全文件，避免破坏逻辑表达式。
    2. 三引号多行块原样保留。
    3. 保留基础键（如 text/description）与其他语种键，仅新增或更新目标语种键。
    """
    prompt_target_language, target_suffix = resolve_target_language(target_language)
    target_suffixes = resolve_target_language_suffixes(target_language)

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

        # 开始进入三引号多行块（当前行为起始行，后续行全部原样保留）
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
        if not localized_value:
            pass
        else:
            localized_values.setdefault((item["section"], base_key), localized_value)

    translate_tasks_dict: dict[str, str] = {}
    for item in parsed_lines:
        if item["kind"] != "kv":
            continue
        key = item["key"]
        if not is_text_key_valid(key):
            continue

        source_text = item["value"].strip()
        if not source_text:
            source_text = localized_values.get((item["section"], key), "")
        if source_text:
            item["source_text"] = source_text
            translate_tasks_dict[source_text] = "translation key"

    translations: dict[str, str] = {}
    if translate_tasks_dict:
        translations = await translate_tasks(
            translate_tasks_dict,
            translate_style=translate_style,
            target_language=prompt_target_language,
        )

    translated_by_pair: dict[tuple[str, str], str] = {}
    for item in parsed_lines:
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

    for item in parsed_lines:
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

        existing_suffixes = existing_suffixes_by_pair.get(pair, set())
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

    new_content = line_ending.join(output_lines)
    if had_trailing_newline:
        new_content += line_ending

    with open(file_path, "w", encoding="utf-8", newline="") as file:
        file.write(new_content)


def analysis_style(content: str) -> str:
    # 当前策略：风格分析失败不阻断流程
    if not content.strip():
        return ""
    return "保持原文简洁、技术化风格，术语一致，避免冗长解释。"


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

            text_keys.append((section, key, source_text))
            translate_tasks_dict[source_text] = "translation key"

    # 翻译并写入目标语言键
    if translate_tasks_dict:
        translations = await translate_tasks(
            translate_tasks_dict,
            translate_style,
            prompt_target_language,
        )

        for section, base_key, source_text in text_keys:
            translated = translations.get(source_text)
            if translated is not None:
                for suffix in target_suffixes:
                    localized_key = f"{base_key}_{suffix}"
                    inifile.data[section][localized_key] = translated

    return inifile
