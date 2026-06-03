"""Database initialization and connection management."""

import aiosqlite
from pathlib import Path
from loguru import logger

from app.config import settings


async def init_db():
    """Initialize all database tables."""
    # Ensure data directory exists
    Path("./data").mkdir(parents=True, exist_ok=True)
    Path("./data/attachments").mkdir(parents=True, exist_ok=True)

    # Initialize audit database
    async with aiosqlite.connect(settings.audit.db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                phase TEXT NOT NULL,
                event_type TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_session
            ON audit_logs(session_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_timestamp
            ON audit_logs(timestamp)
        """)
        await db.commit()

    # Initialize knowledge database
    async with aiosqlite.connect(settings.knowledge.db_path) as db:
        from app.knowledge.store import ensure_knowledge_schema

        await ensure_knowledge_schema(db)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'active'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_attachments (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                message_id TEXT,
                input_type TEXT NOT NULL,
                filename TEXT NOT NULL,
                content_type TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                recognition_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE,
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE SET NULL
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_message_attachments_session
            ON message_attachments(session_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_message_attachments_message
            ON message_attachments(message_id)
        """)
        from app.agent.runbook_governance import ensure_runbook_schema
        from app.agent.context_manager import ensure_context_schema
        from app.agent.tool_execution_store import ensure_tool_execution_schema
        from app.incidents.store import ensure_incident_schema
        from app.api.health_report import ensure_health_report_schema
        from app.api.security_posture import ensure_security_posture_schema

        await ensure_runbook_schema(db)
        await ensure_context_schema(db)
        await ensure_tool_execution_schema(db)
        await ensure_incident_schema(db)
        await ensure_health_report_schema(db)
        await ensure_security_posture_schema(db)
        await db.commit()

    logger.info("Database initialized successfully")


def get_audit_db_path() -> str:
    """Get audit database file path."""
    return settings.audit.db_path


def get_knowledge_db_path() -> str:
    """Get knowledge database file path."""
    return settings.knowledge.db_path
