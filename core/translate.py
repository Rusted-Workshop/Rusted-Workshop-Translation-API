import re

from pydantic import BaseModel

from core.agents.translate import create_agent
from core.agents.translate_style_analysis import translate_style_analysis_agent
from utlis.ini_lib import IniFile, generate_model_from_dict

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
    tasks: dict, translate_style: str, target_language: str = "中文"
) -> dict[str, str]:
    """
    翻译任务字典，返回原文到译文的映射
    """
    translate_style += f"\n\n目标语言: {target_language}"

    # 创建字段名到原文的映射
    field_to_original: dict[str, str] = {}
    sanitized_tasks: dict[str, str] = {}

    for idx, (original_text, description) in enumerate(tasks.items()):
        # 使用索引作为字段名，确保是有效的 Python 标识符
        field_name = f"field_{idx}"
        field_to_original[field_name] = original_text
        sanitized_tasks[field_name] = description

    OutputSchema: type[BaseModel] = generate_model_from_dict(sanitized_tasks)

    response = create_agent(OutputSchema).run(translate_style)

    if response is None or response.content is None:
        raise ValueError("Translation failed.")

    # 验证响应
    validated_response = OutputSchema.model_validate(response.content)

    # 将结果映射回原文
    result: dict[str, str] = {}
    for field_name, translated_text in validated_response.model_dump().items():
        original_text = field_to_original[field_name]
        result[original_text] = translated_text

    return result


def analysis_style(content: str) -> str:
    result: str | None = translate_style_analysis_agent.run(content).content
    if result is None:
        raise ValueError("Translation style analysis failed.")
    return result


async def translate_inifile(
    inifile: IniFile,
    use_cache: bool = True,
    translate_style: str = "",
    mod_id: str = "",
    cache_dict: dict[str, str] | None = None,
) -> IniFile:
    """
    翻译单个ini文件

    Args:
        inifile: INI文件对象
        use_cache: 是否使用缓存
        translate_style: 翻译风格
        mod_id: 模组ID
        cache_dict: 缓存字典，用于批量翻译时的缓存查询

    Returns:
        翻译后的INI文件对象
    """
    text_keys: list[tuple[str, str]] = []

    for section in inifile.data.keys():
        for key in inifile.data[section]:
            if is_text_key_valid(key):
                text_keys.append((section, key))

    if not text_keys:
        return inifile

    translate_tasks_case = {}

    # 收集需要翻译的文本，优先使用缓存
    for section, key in text_keys:
        text: str = inifile.data[section][key]

        # 如果提供了缓存字典，先查缓存
        if use_cache and cache_dict and text in cache_dict:
            inifile.data[section][key] = cache_dict[text]
        else:
            translate_tasks_case[text] = "translation Key"

    # 如果有需要翻译的文本
    if translate_tasks_case:
        response: dict[str, str] = await translate_tasks(
            translate_tasks_case, translate_style
        )

        # 应用翻译结果
        for original_text, translated_text in response.items():
            for section, key in text_keys:
                if inifile.data[section][key] == original_text:
                    inifile.data[section][key] = translated_text

            # 更新缓存字典
            if cache_dict is not None:
                cache_dict[original_text] = translated_text

    return inifile


async def translate_inifiles_batch(
    inifiles: list[IniFile],
    translate_style: str = "",
    mod_id: str = "",
    target_language: str = "中文",
    batch_size: int = 10,
    cache_dict: dict[str, str] | None = None,
) -> list[IniFile]:
    """
    批量翻译多个ini文件

    Args:
        inifiles: INI文件对象列表
        translate_style: 翻译风格
        mod_id: 模组ID
        target_language: 目标语言
        batch_size: 批量大小，每次翻译多少个文件
        cache_dict: 缓存字典，用于跨文件的缓存查询和更新

    Returns:
        翻译后的INI文件对象列表
    """
    if cache_dict is None:
        cache_dict = {}

    # 第一步：预处理 - 如果基础键为空，从其他语言版本复制
    for inifile in inifiles:
        for section in inifile.data.keys():
            keys_to_check = list(inifile.data[section].keys())

            for key in keys_to_check:
                if is_text_key_valid(key):
                    # 基础键
                    base_value = inifile.data[section].get(key, "").strip()

                    # 如果基础键为空，尝试从其他语言版本获取
                    if not base_value:
                        # 查找所有可能的语言后缀版本
                        for localized_key in keys_to_check:
                            if localized_key.lower().startswith(key.lower() + "_"):
                                localized_value = inifile.data[section].get(localized_key, "").strip()
                                if localized_value:
                                    inifile.data[section][key] = localized_value
                                    break

    # 第二步：收集所有需要翻译的文本
    all_translate_tasks = {}
    file_text_keys: list[tuple[int, list[tuple[str, str]]]] = []

    for file_idx, inifile in enumerate(inifiles):
        text_keys: list[tuple[str, str]] = []

        for section in inifile.data.keys():
            for key in inifile.data[section]:
                if is_text_key_valid(key):
                    text_keys.append((section, key))

        file_text_keys.append((file_idx, text_keys))

        # 收集需要翻译的文本
        for section, key in text_keys:
            text: str = inifile.data[section][key]

            # 跳过空文本和已缓存的文本
            if text and text not in cache_dict:
                all_translate_tasks[text] = "translation Key"

    # 第三步：批量翻译所有新文本
    if all_translate_tasks:
        # 将任务分批处理
        tasks_items = list(all_translate_tasks.items())

        for i in range(0, len(tasks_items), batch_size):
            batch_tasks = dict(tasks_items[i:i + batch_size])

            response: dict[str, str] = await translate_tasks(
                batch_tasks, translate_style, target_language
            )

            # 更新缓存字典
            cache_dict.update(response)

    # 第四步：应用翻译结果到所有文件
    for file_idx, text_keys in file_text_keys:
        inifile = inifiles[file_idx]

        for section, key in text_keys:
            original_text = inifile.data[section][key]
            if original_text in cache_dict:
                inifile.data[section][key] = cache_dict[original_text]

    # 第五步：删除所有带语言后缀的键
    for inifile in inifiles:
        for section in inifile.data.keys():
            keys_to_delete = []

            for key in inifile.data[section]:
                if is_localized_text_key(key):
                    keys_to_delete.append(key)

            # 删除这些键
            for key in keys_to_delete:
                del inifile.data[section][key]

    return inifiles
