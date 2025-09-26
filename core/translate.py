import re
from typing import Type

from pydantic import BaseModel

from core.agents.translate import create_agent
from core.agents.translate_style_analysis import translate_style_analysis_agent
from utlis.ini_lib import generate_model_from_dict, IniFile

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


def translate_tasks(
    tasks: dict, translate_style: str, target_language: str = "中文"
) -> BaseModel:
    translate_style += f"\n\n目标语言: {target_language}"
    OutputSchema: Type[BaseModel] = generate_model_from_dict(tasks)
    response = create_agent(OutputSchema).run(translate_style)
    return response.content


def analysis_style(content: str) -> str:
    return translate_style_analysis_agent.run(content).content


def translate_inifile(inifile: IniFile):
    text_keys: list[tuple[str, str]] = []

    for section in inifile.data.keys():
        for key in inifile.data[section]:
            if is_text_key_valid(key):
                text_keys.append((section, key))

    if not text_keys:
        return inifile

    translate_tasks_case = {}
    style_analysis_case = []

    # 提取部分当作风格案例
    for section, key in text_keys[:30]:
        style_analysis_case.append(inifile.data[section][key])

    # 翻译任务
    for section, key in text_keys:
        translate_tasks_case[f"{section}:{key}"] = inifile.data[section][key]

    style_analysis_case_text = "\n----------\n".join(style_analysis_case)[:500]

    translate_style = analysis_style(style_analysis_case_text)

    response = translate_tasks(translate_tasks_case, translate_style)

    for key, value in response.model_dump().items():
        section = key.split(":")[0]
        key = key.split(":")[1]
        inifile.data[section][key] = value

    return inifile
