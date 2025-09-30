"""
文件级别的翻译缓存服务
"""

import hashlib
import json
from typing import Optional

from utlis.redis_lib import get_redis_connection


class TranslationCache:
    """文件级别的翻译缓存"""

    def __init__(self):
        self.redis = get_redis_connection()
        self.cache_prefix = "file_translation_cache:"

    def _get_file_hash(self, file_path: str, content: str) -> str:
        """
        生成文件内容的哈希值

        Args:
            file_path: 文件路径
            content: 文件内容

        Returns:
            文件内容的MD5哈希值
        """
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        return f"{file_path}:{content_hash}"

    def _get_cache_key(self, file_hash: str) -> str:
        """获取缓存键"""
        return f"{self.cache_prefix}{file_hash}"

    async def get_cached_translation(
        self, file_path: str, original_content: str
    ) -> Optional[dict[str, str]]:
        """
        获取文件的缓存翻译

        Args:
            file_path: 文件路径
            original_content: 原始文件内容

        Returns:
            翻译映射字典，如果没有缓存则返回None
        """
        file_hash = self._get_file_hash(file_path, original_content)
        cache_key = self._get_cache_key(file_hash)

        cached_data = await self.redis.get(cache_key)
        if cached_data:
            return json.loads(cached_data)

        return None

    async def save_translation(
        self,
        file_path: str,
        original_content: str,
        translations: dict[str, str],
        ttl: int = 30 * 24 * 60 * 60,  # 30天
    ) -> None:
        """
        保存文件的翻译结果到缓存

        Args:
            file_path: 文件路径
            original_content: 原始文件内容
            translations: 翻译映射字典 (原文 -> 译文)
            ttl: 缓存过期时间（秒）
        """
        file_hash = self._get_file_hash(file_path, original_content)
        cache_key = self._get_cache_key(file_hash)

        cache_data = json.dumps(translations, ensure_ascii=False)
        await self.redis.setex(cache_key, ttl, cache_data)

    async def get_field_translation(
        self, file_path: str, original_content: str, field_key: str
    ) -> Optional[str]:
        """
        获取单个字段的翻译

        Args:
            file_path: 文件路径
            original_content: 原始文件内容
            field_key: 字段键（原文）

        Returns:
            翻译后的文本，如果没有缓存则返回None
        """
        translations = await self.get_cached_translation(file_path, original_content)
        if translations:
            return translations.get(field_key)
        return None

    async def clear_cache(self, pattern: Optional[str] = None) -> int:
        """
        清理缓存

        Args:
            pattern: 匹配模式，如果为None则清理所有翻译缓存

        Returns:
            删除的缓存数量
        """
        if pattern:
            keys = await self.redis.keys(f"{self.cache_prefix}{pattern}")
        else:
            keys = await self.redis.keys(f"{self.cache_prefix}*")

        if keys:
            return await self.redis.delete(*keys)
        return 0
