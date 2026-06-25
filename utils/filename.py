"""
Utilities for naming translated .rwmod output files.

The translated file is named after the mod's display title (read from
mod-info.txt inside the .rwmod) plus a short language suffix:

    <sanitized-mod-title>-<lang-suffix>.rwmod

Example: a "Zombie Survival | v2.1.2 Rebirth" mod translated to Japanese
becomes "Zombie-Survival-v2.1.2-Rebirth-jp.rwmod".
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Optional


# ISO code / locale / language name -> short suffix used in the filename.
# ja -> jp and zh-CN -> cn follow the user's request; unknown languages
# fall through to a 2-letter lowercase code derived from the input.
_LANG_SUFFIX_MAP: dict[str, str] = {
    "ja": "jp", "jp": "jp", "jpn": "jp", "japanese": "jp",
    "zh": "cn", "zh-cn": "cn", "zh-hans": "cn", "chinese": "cn",
    "zh-tw": "tw", "zh-hant": "tw", "traditional-chinese": "tw",
    "zh-hk": "hk", "zh-mo": "mo",
    "en": "en", "eng": "en", "english": "en",
    "ru": "ru", "rus": "ru", "russian": "ru",
    "ko": "kr", "kor": "kr", "korean": "kr",
    "fr": "fr", "fra": "fr", "french": "fr",
    "de": "de", "deu": "de", "german": "de",
    "es": "es", "spa": "es", "spanish": "es",
    "pt": "pt", "por": "pt", "portuguese": "pt",
    "it": "it", "ita": "it", "italian": "it",
    "tr": "tr", "tur": "tr", "turkish": "tr",
    "pl": "pl", "pol": "pl", "polish": "pl",
    "uk": "ua", "ukr": "ua", "ukrainian": "ua",
    "vi": "vn", "vie": "vn", "vietnamese": "vn",
    "th": "th", "tha": "th", "thai": "th",
    "ar": "ar", "ara": "ar", "arabic": "ar",
    "id": "id", "ind": "id", "indonesian": "id",
    "ms": "my", "msa": "my", "malay": "my",
    "hi": "in", "hin": "in", "hindi": "in",
}


# Characters illegal in filenames on Windows; also strip ASCII control chars.
_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
# Whitespace, hyphens, underscores in a row collapse to a single hyphen.
_MULTI_SEP = re.compile(r"[\s\-_]+")

_TITLE_LINE = re.compile(r"(?im)^\s*title\s*[:=]\s*(.+?)\s*$")


def language_to_suffix(target_language: Optional[str]) -> str:
    """Map a language code to the short suffix used in the output filename.

    Unknown / empty values fall through to a 2-char lowercase alnum prefix,
    or "xx" if nothing usable is left.
    """
    if not target_language:
        return "xx"
    key = target_language.strip().lower()
    if key in _LANG_SUFFIX_MAP:
        return _LANG_SUFFIX_MAP[key]
    # Try progressively shorter prefixes so e.g. "zh-CN-foo" still matches
    # the "zh-cn" / "zh" entries.
    for length in (5, 2):
        if len(key) >= length:
            prefix = key[:length]
            if prefix in _LANG_SUFFIX_MAP:
                return _LANG_SUFFIX_MAP[prefix]
    fallback = "".join(c for c in key if c.isalnum())[:2]
    return fallback or "xx"


def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Return a string safe to use as a filename on Windows / macOS / Linux.

    - NFKC-normalize (folds fullwidth characters, etc.)
    - Replace illegal characters with "-"
    - Collapse runs of whitespace / hyphens / underscores
    - Strip leading/trailing separators
    - Truncate to max_length while preserving the trailing portion
    """
    if not name:
        return ""
    cleaned = unicodedata.normalize("NFKC", name)
    cleaned = _INVALID_CHARS.sub("-", cleaned)
    cleaned = _MULTI_SEP.sub("-", cleaned)
    cleaned = cleaned.strip("-").strip()
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip("-")
    return cleaned


def extract_mod_title(extract_dir: str) -> Optional[str]:
    """Read mod-info.txt inside an extracted .rwmod and return its title.

    Returns None if no mod-info.txt is found or it has no title field.
    Case-insensitive filename match, and accepts "title: foo" or "title = foo".
    """
    if not extract_dir or not os.path.isdir(extract_dir):
        return None
    candidates: list[str] = []
    # Prefer the top-level mod-info.txt, but also accept a nested one level deep.
    for entry in os.listdir(extract_dir):
        if entry.lower() == "mod-info.txt":
            candidates.append(os.path.join(extract_dir, entry))
    for entry in os.listdir(extract_dir):
        full = os.path.join(extract_dir, entry)
        if os.path.isdir(full) and os.path.isfile(
            os.path.join(full, "mod-info.txt")
        ):
            candidates.append(os.path.join(full, "mod-info.txt"))
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        match = _TITLE_LINE.search(text)
        if match:
            return match.group(1).strip()
    return None


def build_output_filename(
    extract_dir: str,
    target_language: str,
    fallback_name: str = "mod",
) -> str:
    """Compose the final translated .rwmod filename.

    Priority: mod-info.txt title (sanitized) -> fallback_name (sanitized)
    -> "mod", all suffixed with the language code.
    """
    title = extract_mod_title(extract_dir)
    base = sanitize_filename(title) if title else ""
    if not base:
        base = sanitize_filename(fallback_name) or "mod"
    suffix = language_to_suffix(target_language)
    return f"{base}-{suffix}.rwmod"
