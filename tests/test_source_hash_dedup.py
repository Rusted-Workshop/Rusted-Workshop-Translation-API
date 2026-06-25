"""
Tests for source_hash-based dedup logic on TranslationTask + TaskManager.
No live DB required — uses an in-memory fake asyncpg connection.
"""

from __future__ import annotations

import asyncio
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
# Avoids needing a live Postgres for unit tests; mimics the subset of
# methods TaskManager uses (connect, execute, fetchrow, transaction).

_SENTINEL = object()


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
        if "ALTER TABLE" in sql or "CREATE" in sql:
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
                # one-shot flag
                self.unique_violation = False
                raise _UniqueViolationError("uq_translation_tasks_source_hash")
            row = self._build_row(args)
            key = row["task_id"]
            self.store[key] = row
            return _FakeRecord(row)
        if sql_up.startswith("SELECT * FROM TRANSLATION_TASKS WHERE TASK_ID"):
            target = args[0]
            row = self.store.get(target)
            return _FakeRecord(row) if row else None
        if "FROM TRANSLATION_TASKS WHERE SOURCE_HASH" in sql_up:
            return self._hash_lookup(args[0])
        if sql_up.startswith("UPDATE TRANSLATION_TASKS"):
            target = args[0]
            row = self.store.get(target)
            if not row:
                return None
            row["status"] = args[1]
            row["progress"] = args[2]
            row["total_files"] = args[3]
            row["processed_files"] = args[4]
            row["error_message"] = args[5]
            row["completed_at"] = args[6]
            row["updated_at"] = "now"
            return _FakeRecord(row)
        return None

    async def fetch(self, sql: str, *args):
        if "ORDER BY CREATED_AT DESC" in sql.upper():
            return [_FakeRecord(r) for r in self.store.values()]
        return []

    def _hash_lookup(self, target_hash: str):
        candidates = [r for r in self.store.values() if r["source_hash"] == target_hash]
        if not candidates:
            return None
        # Sort by created_at DESC, return the most recent.
        candidates.sort(key=lambda r: r["created_at"], reverse=True)
        return _FakeRecord(candidates[0])

    def transaction(self):
        return _NullCtx()

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


# Inject a fake asyncpg module so the import in task_manager works.
asyncpg_stub = types.ModuleType("asyncpg")


async def _fake_connect(url: str):
    return None  # unused — TaskManager._connect patched per test


asyncpg_stub.connect = _fake_connect
asyncpg_stub.UniqueViolationError = _UniqueViolationError
sys.modules["asyncpg"] = asyncpg_stub

from models.task import TaskStatus, TranslationTask  # noqa: E402
from services import task_manager as tm  # noqa: E402


def _make_task(source_hash: Optional[str]) -> TranslationTask:
    return TranslationTask(
        task_id=str(uuid.uuid4()),
        s3_source_url="s3://b/k",
        s3_dest_bucket="b",
        s3_dest_key="k",
        target_language="zh-CN",
        translate_style="auto",
        source_hash=source_hash,
        status=TaskStatus.PENDING,
    )


class HashDeduplicationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store: dict = {}
        self.conn = _FakeConnection(self.store)

        # Patch asyncpg.connect used by TaskManager
        self._orig_connect = tm.asyncpg.connect
        tm.asyncpg.connect = lambda url: _async_return(self.conn)

    async def asyncTearDown(self) -> None:
        tm.asyncpg.connect = self._orig_connect

    async def test_create_task_persists_source_hash(self) -> None:
        mgr = tm.TaskManager()
        task = _make_task("a" * 64)
        created = await mgr.create_task(task)
        self.assertEqual(created.source_hash, "a" * 64)
        self.assertIn(uuid.UUID(task.task_id), self.store)

    async def test_find_latest_by_hash_returns_match(self) -> None:
        mgr = tm.TaskManager()
        task = _make_task("b" * 64)
        await mgr.create_task(task)

        found = await mgr.find_latest_by_hash("b" * 64)
        self.assertIsNotNone(found)
        self.assertEqual(found.task_id, task.task_id)

    async def test_find_latest_by_hash_returns_none_for_unknown(self) -> None:
        mgr = tm.TaskManager()
        self.assertIsNone(await mgr.find_latest_by_hash("c" * 64))

    async def test_find_latest_by_hash_returns_most_recent(self) -> None:
        mgr = tm.TaskManager()
        older = _make_task("d" * 64)
        await mgr.create_task(older)
        # Mutate created_at on the older row to be earlier
        self.store[uuid.UUID(older.task_id)]["created_at"] = "2023-01-01T00:00:00Z"
        newer = _make_task("d" * 64)
        await mgr.create_task(newer)
        self.store[uuid.UUID(newer.task_id)]["created_at"] = "2025-01-01T00:00:00Z"

        found = await mgr.find_latest_by_hash("d" * 64)
        self.assertIsNotNone(found)
        self.assertEqual(found.task_id, newer.task_id)

    async def test_unique_violation_can_be_caught_via_asyncpg(self) -> None:
        # Simulate a concurrent insert that violates the unique hash index.
        self.conn.unique_violation = True
        mgr = tm.TaskManager()
        with self.assertRaises(_UniqueViolationError):
            await mgr.create_task(_make_task("e" * 64))

    async def test_task_model_accepts_omitted_hash(self) -> None:
        t = _make_task(None)
        self.assertIsNone(t.source_hash)
        # hash field is optional, max 64 chars
        t2 = _make_task("f" * 64)
        self.assertEqual(t2.source_hash, "f" * 64)


async def _async_return(value):
    return value


if __name__ == "__main__":
    unittest.main()
