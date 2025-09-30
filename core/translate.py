import re

from agno.agent import Agent
from agno.models.openai import OpenAILike

from core.agents.translate_style_analysis import translate_style_analysis_agent
from utlis.config import AI_API_KEY, AI_BASE_URL, AI_MODEL
from utlis.ini_lib import IniFile

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


def is_text_key_valid(key: str) -> bool:
    """检查是否为有效的基础文本键（不包含语言后缀）"""
    return BASE_TEXT_KEYS_REGEX.match(key) is not None


def is_localized_text_key(key: str) -> bool:
    """检查是否为带语言后缀的文本键"""
    return LOCALIZED_TEXT_KEYS_REGEX.match(key) is not None


async def translate_tasks(
    tasks: dict,
    translate_style: str,
    target_language: str = "中文",
    max_retries: int = 3,
    retry_delay: float = 1.0,
) -> dict[str, str]:
    """
    翻译任务字典，返回原文到译文的映射
    使用数组格式节省 token，按顺序翻译

    Args:
        tasks: 翻译任务字典 {原文: 描述}
        translate_style: 翻译风格
        target_language: 目标语言
        max_retries: 最大重试次数
        retry_delay: 重试延迟（秒）

    Returns:
        原文到译文的映射
    """
    import asyncio
    import json

    # 将原文转为列表，保持顺序
    original_texts = list(tasks.keys())

    # 构建提示词 - 使用数组格式节省 token
    texts_numbered = "\n".join(
        [f"{i + 1}. {text}" for i, text in enumerate(original_texts)]
    )

    prompt = f"""{translate_style}

目标语言: {target_language}

请将以下文本翻译为{target_language}。按顺序翻译每一条，保持原文的格式和风格。

待翻译文本：
{texts_numbered}

返回格式：
请返回 JSON 数组格式，只包含译文，按照原文顺序：["译文1", "译文2", "译文3", ...]

注意：
1. 必须严格按照原文顺序返回
2. 返回的数组长度必须等于原文数量 ({len(original_texts)} 条)
3. 不要添加任何额外的文本，只返回纯 JSON 数组
"""

    # 创建不使用结构化输出的 Agent
    agent = Agent(
        model=OpenAILike(id=AI_MODEL, api_key=AI_API_KEY, base_url=AI_BASE_URL),
        instructions="你是一个专业的翻译助手。请严格按照要求翻译文本，并以 JSON 数组格式返回结果。",
        use_json_mode=True,
        structured_outputs=False,
    )

    # 重试逻辑
    last_error = None
    for attempt in range(max_retries):
        try:
            # 使用异步调用
            response = await agent.arun(prompt)

            if response is None:
                raise ValueError("Translation failed: response is None")

            if response.content is None:
                raise ValueError("Translation failed: response.content is None")

            # 解析 JSON 响应
            try:
                # 调试输出
                print(f"响应类型: {type(response.content)}")
                if isinstance(response.content, str):
                    print(f"响应内容前100字符: {response.content[:100]}")

                # response.content 可能是字符串或列表
                if isinstance(response.content, str):
                    if not response.content.strip():
                        raise ValueError("Response content is empty string")

                    content = response.content.strip()

                    # 移除 markdown 代码块标记（AI 可能返回 ```json ... ```）
                    if content.startswith("```"):
                        # 找到第一个换行符（跳过 ```json）
                        first_newline = content.find("\n")
                        if first_newline != -1:
                            content = content[first_newline + 1 :]

                        # 移除结尾的 ```
                        if content.endswith("```"):
                            content = content[:-3]

                        content = content.strip()
                        print(f"清理后的内容前100字符: {content[:100]}")

                    translations = json.loads(content)
                elif isinstance(response.content, list):
                    translations = response.content
                else:
                    raise ValueError(
                        f"Unexpected response type: {type(response.content)}"
                    )

                # 验证返回的是数组
                if not isinstance(translations, list):
                    raise ValueError(f"Expected list, got {type(translations)}")

                # 验证数组长度
                if len(translations) != len(original_texts):
                    raise ValueError(
                        f"Translation count mismatch: expected {len(original_texts)}, got {len(translations)}"
                    )

                # 构建结果字典
                result = {}
                for i, original_text in enumerate(original_texts):
                    result[original_text] = translations[i]

                return result

            except json.JSONDecodeError as e:
                print(f"JSON 解析失败，响应内容: {response.content}")
                raise ValueError(f"Invalid JSON response: {e}")

        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                print(
                    f"翻译失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}, {retry_delay}秒后重试..."
                )
                await asyncio.sleep(retry_delay)
            else:
                print(f"翻译失败，已达到最大重试次数 ({max_retries}): {str(e)}")

    # 所有重试都失败
    raise Exception(f"Translation failed after {max_retries} attempts: {last_error}")


def analysis_style(content: str) -> str:
    result: str | None = translate_style_analysis_agent.run(content).content
    if result is None:
        raise ValueError("Translation style analysis failed.")
    return result


async def translate_inifile(
    inifile: IniFile,
    translate_style: str = "",
    target_language: str = "中文",
) -> IniFile:
    """
    翻译单个ini文件（简化版，一次翻译一个文件的所有文本）

    Args:
        inifile: INI文件对象
        translate_style: 翻译风格
        target_language: 目标语言

    Returns:
        翻译后的INI文件对象
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
                if text:  # 只翻译非空文本
                    text_keys.append((section, key))
                    translate_tasks_dict[text] = "translation key"

    # 第三步：翻译所有文本
    if translate_tasks_dict:
        translations = await translate_tasks(
            translate_tasks_dict, translate_style, target_language
        )

        # 应用翻译结果
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
