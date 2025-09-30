import json
import re

from pydantic import BaseModel

from core.agents.translate import create_agent
from core.agents.translate_style_analysis import translate_style_analysis_agent
from utlis.ini_lib import IniFile, generate_model_from_dict
from utlis.redis_lib import get_db

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
    OutputSchema: type[BaseModel] = generate_model_from_dict(tasks)
    response = create_agent(OutputSchema).run(translate_style)
    if isinstance(response, str):
        return OutputSchema.model_validate(json.loads(response))

    return response.content


def analysis_style(content: str) -> str:
    result: str | None = translate_style_analysis_agent.run(content).content
    if result is None:
        raise ValueError("Translation style analysis failed.")
    return result


def translate_inifile(
    inifile: IniFile,
    use_cache: bool = True,
    translate_style: str = "",
    mod_id: str = "",
) -> IniFile:
    redis = None
    if use_cache:
        redis = get_db()
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
        if use_cache and redis and redis.hget(translate_cache_key, text):
            translated_text = redis.hget(translate_cache_key, text).decode("utf-8")
            inifile.data[section][key] = translated_text
        else:
            translate_tasks_case[text] = "translation Key"

    if translate_tasks_case:
        response: BaseModel = translate_tasks(translate_tasks_case, translate_style)
        print(response)
        for original_text, translated_text in response.model_dump().items():
            for section, key in text_keys:
                if inifile.data[section][key] == original_text:
                    inifile.data[section][key] = translated_text
            if redis:
                redis.hset(translate_cache_key, original_text, translated_text)

    return inifile
