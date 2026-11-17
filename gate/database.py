import aiosqlite
import os
import json
from datetime import datetime, timezone

DB_PATH = os.getenv("GATE_DB_PATH", "/data/gate.db")

CREATE_TABLES_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS conversation_mode (
    conversation_id  TEXT PRIMARY KEY,
    subject_line     TEXT NOT NULL,
    mode             TEXT NOT NULL CHECK(mode IN ('ungated', 'gated')),
    updated_at       TEXT NOT NULL,
    updated_by       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id               TEXT PRIMARY KEY,
    conversation_id  TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL UNIQUE,
    state            TEXT NOT NULL CHECK(state IN ('held', 'dispatching', 'done', 'cancelled', 'uncertain')),
    engine           TEXT NOT NULL CHECK(engine IN ('n8n', 'calnode', 'windmill')),
    payload          TEXT NOT NULL,
    outcome          TEXT,
    actor_guid       TEXT NOT NULL,
    permission_proof TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    decided_at       TEXT,
    FOREIGN KEY(conversation_id) REFERENCES conversation_mode(conversation_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id          TEXT PRIMARY KEY,
    action_id   TEXT NOT NULL,
    decision    TEXT NOT NULL CHECK(decision IN ('released', 'cancelled')),
    decided_by  TEXT NOT NULL,
    decided_at  TEXT NOT NULL,
    FOREIGN KEY(action_id) REFERENCES actions(id)
);

CREATE TABLE IF NOT EXISTS permissions (
    user_id     TEXT PRIMARY KEY,
    role        TEXT NOT NULL,
    is_revoked  INTEGER NOT NULL DEFAULT 0
);
"""

async def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_TABLES_SQL)
        now = datetime.now(timezone.utc).isoformat()

        # Seed 3 mandatory conversations (with intentional Subject Line ambiguity for test)
        await db.execute(
            """INSERT OR IGNORE INTO conversation_mode (conversation_id, subject_line, mode, updated_at, updated_by)
               VALUES (?, ?, 'ungated', ?, 'system')""",
            ("conv-customer-a-enquiry", "Customer A – Website Enquiry", now)
        )
        await db.execute(
            """INSERT OR IGNORE INTO conversation_mode (conversation_id, subject_line, mode, updated_at, updated_by)
               VALUES (?, ?, 'ungated', ?, 'system')""",
            ("conv-customer-b-pricing", "Customer B – Pricing Question", now)
        )
        await db.execute(
            """INSERT OR IGNORE INTO conversation_mode (conversation_id, subject_line, mode, updated_at, updated_by)
               VALUES (?, ?, 'ungated', ?, 'system')""",
            ("conv-customer-a-demo", "Customer A – Demo Request", now)
        )

        # Seed test users for test A09
        await db.execute(
            "INSERT OR IGNORE INTO permissions (user_id, role, is_revoked) VALUES (?, 'owner', 0)",
            ("u-owner-guid",)
        )
        await db.execute(
            "INSERT OR IGNORE INTO permissions (user_id, role, is_revoked) VALUES (?, 'viewer', 0)",
            ("u-viewer-guid",)
        )
        await db.execute(
            "INSERT OR IGNORE INTO permissions (user_id, role, is_revoked) VALUES (?, 'revoked_user', 1)",
            ("u-revoked-guid",)
        )

        await db.commit()

async def get_db():
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db
