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
) -> IniFile:
    redis = None

    translate_cache_key = f"translate_cache:{mod_id}"

    text_keys: list[tuple[str, str]] = []

    for section in inifile.data.keys():
        for key in inifile.data[section]:
            if is_text_key_valid(key):
                text_keys.append((section, key))

    if not text_keys:
        return inifile

    translate_tasks_case = {}

    # 翻译任务
    for section, key in text_keys:
        text: str = inifile.data[section][key]

        translate_tasks_case[text] = "translation Key"

    if translate_tasks_case:
        response: dict[str, str] = await translate_tasks(
            translate_tasks_case, translate_style
        )

        for original_text, translated_text in response.items():
            for section, key in text_keys:
                if inifile.data[section][key] == original_text:
                    inifile.data[section][key] = translated_text
            if redis:
                redis.hset(translate_cache_key, original_text, translated_text)

    return inifile
