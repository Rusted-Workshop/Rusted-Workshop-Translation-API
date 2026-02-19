import json
import re
from typing import Any

from openai import AsyncOpenAI

from utils.config import AI_API_KEY, AI_BASE_URL, AI_MODEL
from utils.ini_lib import IniFile, read_file

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

# 带语言后缀的文本键正则
LOCALIZED_TEXT_KEYS_REGEX = re.compile(
    r"(?i)^(?:"
    r"description_[a-z]+|"
    r"title_[a-z]+|"
    r"displaydescription_[a-z]+|"
    r"displayDescription_[a-z]+|"
    r"text_[a-z]+|"
    r"displayText_[a-z]+|"
    r"isLockedAltMessage_[a-z]+|"
    r"cannotPlaceMessage_[a-z]+|"
    r"displayName_[a-z]+|"
    r"displayNameShort_[a-z]+|"
    r"showMessageToPlayer_[a-z]+|"
    r"showMessageToAllPlayers_[a-z]+|"
    r"action_\d+_(?:text|displayName)_[a-z]+"
    r")$",
    re.IGNORECASE,
)

LINE_KV_REGEX = re.compile(
    r"^(?P<indent>\s*)(?P<key>[^:=\n]+?)(?P<pre>\s*)(?P<sep>[:=])(?P<post>\s*)(?P<value>.*)$"
)


def is_text_key_valid(key: str) -> bool:
    """检查是否为有效的基础文本键（不包含语言后缀）"""
    return BASE_TEXT_KEYS_REGEX.match(key) is not None


def is_localized_text_key(key: str) -> bool:
    """检查是否为带语言后缀的文本键"""
    return LOCALIZED_TEXT_KEYS_REGEX.match(key) is not None


def localized_to_base_key(key: str) -> str:
    """将带语言后缀的键名还原为基础键名。"""
    return re.sub(r"_[a-z]+$", "", key, flags=re.IGNORECASE)


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

    if not AI_API_KEY:
        return {text: text for text in original_texts}

    texts_numbered = "\n".join(
        [f"{i + 1}. {text}" for i, text in enumerate(original_texts)]
    )

    prompt = f"""{translate_style}
你是铁锈战争 mod 单位翻译专家。
目标语言: {target_language}

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
    在保留文件结构的前提下，仅翻译允许的文本键。
    关键点：
    1. 不使用 configparser 重写全文件，避免破坏逻辑表达式。
    2. 三引号多行块原样保留。
    3. 删除 *_xx 语言后缀键。
    """
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
    for item in parsed_lines:
        if item["kind"] != "kv":
            continue
        key = item["key"]
        if not is_localized_text_key(key):
            continue
        localized_value = item["value"].strip()
        if not localized_value:
            continue
        base_key = localized_to_base_key(key)
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
            target_language=target_language,
        )

    output_lines: list[str] = []
    for item in parsed_lines:
        if item["kind"] != "kv":
            output_lines.append(item["raw"])
            continue

        key = item["key"]

        # 删除语言后缀键，避免同义多语言字段污染
        if is_localized_text_key(key):
            continue

        if is_text_key_valid(key):
            source_text = item.get("source_text")
            if source_text:
                translated_value = translations.get(source_text, source_text)
                new_value = _sanitize_single_line_value(translated_value)
            else:
                new_value = item["value"]

            output_lines.append(
                f"{item['indent']}{key}{item['pre']}{item['sep']}{item['post']}{new_value}"
            )
            continue

        output_lines.append(item["raw"])

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
    翻译单个ini文件
    """
    # 第一步：预处理 - 如果基础键为空，从其他语言版本复制
    for section in inifile.data.keys():
        keys_to_check = list(inifile.data[section].keys())

        for key in keys_to_check:
            if is_text_key_valid(key):
                base_value = inifile.data[section].get(key, "").strip()

                # 如果基础键为空，尝试从其他语言版本获取
                if not base_value:
                    for localized_key in keys_to_check:
                        if localized_key.lower().startswith(key.lower() + "_"):
                            localized_value = (
                                inifile.data[section].get(localized_key, "").strip()
                            )
                            if localized_value:
                                inifile.data[section][key] = localized_value
                                break

    # 第二步：收集需要翻译的文本
    text_keys: list[tuple[str, str]] = []
    translate_tasks_dict = {}

    for section in inifile.data.keys():
        for key in inifile.data[section]:
            if is_text_key_valid(key):
                text = inifile.data[section][key].strip()
                if text:
                    text_keys.append((section, key))
                    translate_tasks_dict[text] = "translation key"

    # 第三步：翻译所有文本
    if translate_tasks_dict:
        translations = await translate_tasks(
            translate_tasks_dict, translate_style, target_language
        )

        for section, key in text_keys:
            original_text = inifile.data[section][key]
            if original_text in translations:
                inifile.data[section][key] = translations[original_text]

    # 第四步：删除所有带语言后缀的键
    for section in inifile.data.keys():
        keys_to_delete = []
        for key in inifile.data[section]:
            if is_localized_text_key(key):
                keys_to_delete.append(key)

        for key in keys_to_delete:
            del inifile.data[section][key]

    return inifile
