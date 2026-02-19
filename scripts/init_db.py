"""
初始化 Postgres 表结构。

用法:
  python scripts/init_db.py
"""

import asyncio

import asyncpg

from utils.config import DATABASE_URL


DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS translation_tasks (
        task_id UUID PRIMARY KEY,
        s3_source_url TEXT NOT NULL,
        s3_dest_bucket TEXT NOT NULL,
        s3_dest_key TEXT NOT NULL,
        target_language VARCHAR(32) NOT NULL DEFAULT 'zh-CN',
        translate_style VARCHAR(64) NOT NULL DEFAULT 'auto',
        status VARCHAR(32) NOT NULL,
        progress DOUBLE PRECISION NOT NULL DEFAULT 0,
        total_files INT NOT NULL DEFAULT 0,
        processed_files INT NOT NULL DEFAULT 0,
        error_message TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS translation_files (
        file_id UUID PRIMARY KEY,
        task_id UUID NOT NULL REFERENCES translation_tasks(task_id) ON DELETE CASCADE,
        file_path TEXT NOT NULL,
        status VARCHAR(32) NOT NULL,
        retry_count INT NOT NULL DEFAULT 0,
        error_message TEXT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        completed_at TIMESTAMPTZ NULL,
        UNIQUE(task_id, file_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS task_events (
        event_id UUID PRIMARY KEY,
        task_id UUID NOT NULL REFERENCES translation_tasks(task_id) ON DELETE CASCADE,
        event_type VARCHAR(64) NOT NULL,
        event_payload JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS outbox_events (
        event_id UUID PRIMARY KEY,
        aggregate_type VARCHAR(32) NOT NULL,
        aggregate_id UUID NOT NULL,
        event_type VARCHAR(64) NOT NULL,
        schema_version INT NOT NULL DEFAULT 1,
        payload JSONB NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        published_at TIMESTAMPTZ NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_keys (
        key TEXT PRIMARY KEY,
        request_hash TEXT NOT NULL,
        response_payload JSONB NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMPTZ NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_translation_tasks_created_at
    ON translation_tasks (created_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_translation_files_task_id
    ON translation_files (task_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_outbox_events_status_created_at
    ON outbox_events (status, created_at)
    """,
]


async def init_db() -> None:
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        for ddl in DDL_STATEMENTS:
            await conn.execute(ddl)
        print("Database schema initialized.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(init_db())

