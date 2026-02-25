import re

# 支持常见 BCP-47 形式（如 zh-CN / pt_BR），并提取主语言码。
LANGUAGE_TAG_REGEX = re.compile(
    r"^(?P<primary>[a-z]{2,3})(?:[-_][a-z0-9]{2,8})*$",
    re.IGNORECASE,
)

LANGUAGE_ALIASES: dict[str, str] = {
    # Chinese
    "zh": "zh",
    "zh-cn": "zh",
    "zh_cn": "zh",
    "zh-hans": "zh",
    "zh_hans": "zh",
    "chinese": "zh",
    "simplifiedchinese": "zh",
    "traditionalchinese": "zh",
    "cn": "zh",
    "中文": "zh",
    "汉语": "zh",
    "汉化": "zh",
    "简体中文": "zh",
    "繁体中文": "zh",
    # Russian
    "ru": "ru",
    "ru-ru": "ru",
    "ru_ru": "ru",
    "russian": "ru",
    "русский": "ru",
    "俄文": "ru",
    "俄语": "ru",
    # English
    "en": "en",
    "en-us": "en",
    "en_us": "en",
    "english": "en",
    "英文": "en",
    "英语": "en",
    # Japanese
    "ja": "ja",
    "ja-jp": "ja",
    "ja_jp": "ja",
    "japanese": "ja",
    "日文": "ja",
    "日语": "ja",
    # Korean
    "ko": "ko",
    "ko-kr": "ko",
    "ko_kr": "ko",
    "korean": "ko",
    "韩文": "ko",
    "韩语": "ko",
}

DEFAULT_PROMPT_LANGUAGE_BY_SUFFIX: dict[str, str] = {
    "zh": "中文",
    "ru": "俄文",
    "en": "英文",
    "ja": "日文",
    "ko": "韩文",
}

LANGUAGE_SUFFIX_VARIANTS: dict[str, list[str]] = {
    "zh": ["zh", "zh_cn", "cn"],
    "ru": ["ru", "ru_ru"],
    "en": ["en", "en_us"],
    "ja": ["ja", "ja_jp"],
    "ko": ["ko", "ko_kr"],
}


def _normalize_token(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def normalize_language_suffix(language: str, default_suffix: str = "zh") -> str:
    """将目标语言输入规范为可用于键名后缀的短码（如 zh/ru/en）。"""
    if not language:
        return default_suffix

    token = _normalize_token(language.strip())
    if not token:
        return default_suffix

    alias = LANGUAGE_ALIASES.get(token)
    if alias:
        return alias

    # 对中俄语做宽松中文别名兜底，兼容“中文汉化”“俄文翻译”等描述。
    if "中文" in language or "汉化" in language:
        return "zh"
    if "俄" in language:
        return "ru"

    match = LANGUAGE_TAG_REGEX.fullmatch(token)
    if match:
        return match.group("primary").lower()

    return default_suffix


def resolve_target_language(target_language: str) -> tuple[str, str]:
    """
    解析目标语言：
    - 返回用于提示词的语言描述
    - 返回用于键名后缀的主语言码
    """
    language = (target_language or "").strip()
    suffix = normalize_language_suffix(language)
    prompt_language = language or DEFAULT_PROMPT_LANGUAGE_BY_SUFFIX.get(suffix, "中文")
    return prompt_language, suffix


def resolve_target_language_suffixes(target_language: str) -> list[str]:
    """
    返回目标语言可写入的后缀列表（按优先级，去重）。
    例如：
    - zh-CN -> ["zh", "zh_cn", "cn"]
    - ru -> ["ru", "ru_ru"]
    - pt-BR -> ["pt"]
    """
    _prompt_language, suffix = resolve_target_language(target_language)
    variants = LANGUAGE_SUFFIX_VARIANTS.get(suffix, [suffix])

    ordered: list[str] = []
    seen: set[str] = set()
    for item in variants:
        normalized = item.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered
