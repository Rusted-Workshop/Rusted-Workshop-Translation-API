import re

from pydantic import BaseModel

from core.agents.translate import create_agent
from core.agents.translate_style_analysis import translate_style_analysis_agent
from utlis.ini_lib import IniFile, generate_model_from_dict

TEXT_KEYS_REGEX = re.compile(
    r"(?i)^(?:"
    r"description|"
    r"description_[a-z]+|"
    r"title|"
    r"title_[a-z]+|"
    r"displaydescription|"
    r"displayDescription_[a-z]+|"
    r"text|"
    r"text_[a-z]+|"
    r"displayText|"
    r"displayText_[a-z]+|"
    r"isLockedAltMessage|"
    r"isLockedAltMessage_[a-z]+|"
    r"cannotPlaceMessage|"
    r"cannotPlaceMessage_[a-z]+|"
    r"displayName|"
    r"displayName_[a-z]+|"
    r"displayNameShort|"
    r"displayNameShort_[a-z]+|"
    r"showMessageToPlayer|"
    r"showMessageToPlayer_[a-z]+|"
    r"showMessageToAllPlayers|"
    r"showMessageToAllPlayers_[a-z]+|"
    r"action_\d+_(?:text|displayName)(?:_[a-z]+)?"
    r")$",
    re.IGNORECASE,
)


def is_text_key_valid(key: str) -> bool:
    return TEXT_KEYS_REGEX.match(key) is not None


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
    batch_size: int = 10,
    cache_dict: dict[str, str] | None = None,
) -> list[IniFile]:
    """
    批量翻译多个ini文件

    Args:
        inifiles: INI文件对象列表
        translate_style: 翻译风格
        mod_id: 模组ID
        batch_size: 批量大小，每次翻译多少个文件
        cache_dict: 缓存字典，用于跨文件的缓存查询和更新

    Returns:
        翻译后的INI文件对象列表
    """
    if cache_dict is None:
        cache_dict = {}

    # 第一步：收集所有需要翻译的文本
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

            # 跳过已缓存的文本
            if text not in cache_dict:
                all_translate_tasks[text] = "translation Key"

    # 第二步：批量翻译所有新文本
    if all_translate_tasks:
        # 将任务分批处理
        tasks_items = list(all_translate_tasks.items())

        for i in range(0, len(tasks_items), batch_size):
            batch_tasks = dict(tasks_items[i:i + batch_size])

            response: dict[str, str] = await translate_tasks(
                batch_tasks, translate_style
            )

            # 更新缓存字典
            cache_dict.update(response)

    # 第三步：应用翻译结果到所有文件
    for file_idx, text_keys in file_text_keys:
        inifile = inifiles[file_idx]

        for section, key in text_keys:
            original_text = inifile.data[section][key]
            if original_text in cache_dict:
                inifile.data[section][key] = cache_dict[original_text]

    return inifiles
