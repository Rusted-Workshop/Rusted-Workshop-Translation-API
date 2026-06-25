"""
Tests for composite-signature dedup logic on TranslationTask + TaskManager.
No live DB required — uses an in-memory fake asyncpg connection.

Dedup key is (source_hash, target_language, translate_style), so a re-upload
of the same .rwmod into a different target language must NOT be treated as
a duplicate.
"""

from __future__ import annotations

import sys
import types
import unittest
import uuid
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---- asyncpg stub ---------------------------------------------------------

class _FakeRecord(dict):
    def __getitem__(self, k):
        return super().__getitem__(k)

    def get(self, k, default=None):
        return super().get(k, default)


class _UniqueViolationError(Exception):
    pass


class _FakeConnection:
    def __init__(self, store: dict[Any, dict]):
        self.store = store
        self.unique_violation = False

    async def close(self) -> None:
        return None

    async def execute(self, sql: str, *args):
        if "ALTER TABLE" in sql or "CREATE" in sql or "DROP INDEX" in sql:
            return "OK"
        if sql.strip().upper().startswith("DELETE FROM TRANSLATION_TASKS"):
            target = uuid.UUID(str(args[0]))
            if target in self.store:
                del self.store[target]
                return "DELETE 1"
            return "DELETE 0"
        return "OK"

    async def fetchrow(self, sql: str, *args):
        sql_up = " ".join(sql.split()).upper()
        if sql_up.startswith("INSERT INTO TRANSLATION_TASKS"):
            if self.unique_violation:
                self.unique_violation = False
                raise _UniqueViolationError("uq_translation_tasks_signature")
            row = self._build_row(args)
            key = row["task_id"]
            self.store[key] = row
            return _FakeRecord(row)
        if sql_up.startswith("SELECT * FROM TRANSLATION_TASKS WHERE TASK_ID"):
            target = args[0]
            row = self.store.get(target)
            return _FakeRecord(row) if row else None
        if "FROM TRANSLATION_TASKS WHERE SOURCE_HASH" in sql_up:
            return self._signature_lookup(args)
        return None

    async def fetch(self, sql: str, *args):
        if "ORDER BY CREATED_AT DESC" in sql.upper():
            return [_FakeRecord(r) for r in self.store.values()]
        return []

    def transaction(self):
        return _NullCtx()

    def _signature_lookup(self, args):
        if len(args) == 1:
            target_hash, = args
            candidates = [r for r in self.store.values() if r["source_hash"] == target_hash]
        else:
            target_hash, target_lang, target_style = args[:3]
            candidates = [
                r for r in self.store.values()
                if r["source_hash"] == target_hash
                and r["target_language"] == target_lang
                and r["translate_style"] == target_style
            ]
        if not candidates:
            return None
        candidates.sort(key=lambda r: r["created_at"], reverse=True)
        return _FakeRecord(candidates[0])

    def _build_row(self, args):
        return {
            "task_id": args[0],
            "s3_source_url": args[1],
            "s3_dest_bucket": args[2],
            "s3_dest_key": args[3],
            "target_language": args[4],
            "translate_style": args[5],
            "source_hash": args[6],
            "status": args[7],
            "progress": args[8],
            "total_files": args[9],
            "processed_files": args[10],
            "error_message": args[11],
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "completed_at": None,
        }


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


asyncpg_stub = types.ModuleType("asyncpg")


async def _fake_connect(url: str):
    return None


asyncpg_stub.connect = _fake_connect
asyncpg_stub.UniqueViolationError = _UniqueViolationError
sys.modules["asyncpg"] = asyncpg_stub

from models.task import TaskStatus, TranslationTask  # noqa: E402
from services import task_manager as tm  # noqa: E402


def _make_task(
    source_hash: Optional[str],
    target_language: str = "zh-CN",
    translate_style: str = "auto",
) -> TranslationTask:
    return TranslationTask(
        task_id=str(uuid.uuid4()),
        s3_source_url="s3://b/k",
        s3_dest_bucket="b",
        s3_dest_key="k",
        target_language=target_language,
        translate_style=translate_style,
        source_hash=source_hash,
        status=TaskStatus.PENDING,
    )


async def _async_return(value):
    return value


class HashSignatureDeduplicationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store: dict = {}
        self.conn = _FakeConnection(self.store)
        self._orig_connect = tm.asyncpg.connect
        tm.asyncpg.connect = lambda url: _async_return(self.conn)

    async def asyncTearDown(self) -> None:
        tm.asyncpg.connect = self._orig_connect

    async def test_create_task_persists_signature(self) -> None:
        mgr = tm.TaskManager()
        task = _make_task("a" * 64, target_language="ru", translate_style="auto")
        created = await mgr.create_task(task)
        self.assertEqual(created.source_hash, "a" * 64)
        self.assertEqual(created.target_language, "ru")

    async def test_same_signature_returns_match(self) -> None:
        mgr = tm.TaskManager()
        task = _make_task("b" * 64, "ja", "auto")
        await mgr.create_task(task)
        found = await mgr.find_latest_by_signature("b" * 64, "ja", "auto")
        self.assertIsNotNone(found)
        self.assertEqual(found.task_id, task.task_id)

    async def test_same_hash_different_language_returns_none(self) -> None:
        # Regression: the user uploaded a previously-seen .rwmod with a new
        # target language and the API returned the OLD (wrong-language) task.
        mgr = tm.TaskManager()
        await mgr.create_task(_make_task("c" * 64, "zh-CN", "auto"))

        found = await mgr.find_latest_by_signature("c" * 64, "ru", "auto")
        self.assertIsNone(found)

    async def test_same_hash_same_language_different_style_returns_none(self) -> None:
        mgr = tm.TaskManager()
        await mgr.create_task(_make_task("d" * 64, "ja", "auto"))
        found = await mgr.find_latest_by_signature("d" * 64, "ja", "official")
        self.assertIsNone(found)

    async def test_same_hash_same_signature_different_tasks_keeps_most_recent(self) -> None:
        mgr = tm.TaskManager()
        older = _make_task("e" * 64, "ru", "auto")
        await mgr.create_task(older)
        self.store[uuid.UUID(older.task_id)]["created_at"] = "2023-01-01T00:00:00Z"
        newer = _make_task("e" * 64, "ru", "auto")
        await mgr.create_task(newer)
        self.store[uuid.UUID(newer.task_id)]["created_at"] = "2025-01-01T00:00:00Z"

        found = await mgr.find_latest_by_signature("e" * 64, "ru", "auto")
        self.assertEqual(found.task_id, newer.task_id)

    async def test_empty_hash_returns_none(self) -> None:
        mgr = tm.TaskManager()
        self.assertIsNone(await mgr.find_latest_by_signature("", "ru", "auto"))

    async def test_unique_violation_can_be_caught(self) -> None:
        self.conn.unique_violation = True
        mgr = tm.TaskManager()
        with self.assertRaises(_UniqueViolationError):
            await mgr.create_task(_make_task("f" * 64, "ru"))

    async def test_task_model_accepts_omitted_hash(self) -> None:
        t = _make_task(None)
        self.assertIsNone(t.source_hash)
        t2 = _make_task("f" * 64)
        self.assertEqual(t2.source_hash, "f" * 64)


if __name__ == "__main__":
    unittest.main()
