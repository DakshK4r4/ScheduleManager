from __future__ import annotations

import os
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.domain.models import Base

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    try:
        import psycopg2  # check if driver available
        DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/primavera"
    except ImportError:
        DATABASE_URL = "sqlite:///./primavera.db"

# SQLite test compatibility
connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create tables if they do not already exist and apply pending schema migrations."""
    Base.metadata.create_all(bind=engine)

    # Automatically ensure execution_events schema is up to date on PostgreSQL
    if not DATABASE_URL.startswith("sqlite"):
        try:
            from sqlalchemy import text
            with engine.begin() as conn:
                conn.execute(text("""
                    ALTER TABLE execution_events ALTER COLUMN artifact_id DROP NOT NULL;
                    ALTER TABLE execution_events ALTER COLUMN source_report_id DROP NOT NULL;
                    ALTER TABLE execution_events ALTER COLUMN source_document_name DROP NOT NULL;
                    ALTER TABLE execution_events ALTER COLUMN storage_key DROP NOT NULL;
                    ALTER TABLE execution_events ALTER COLUMN file_sha256 DROP NOT NULL;
                    ALTER TABLE execution_events ALTER COLUMN page_number DROP NOT NULL;
                    ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) NOT NULL DEFAULT 'ARTIFACT';
                    ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS conversation_id VARCHAR(36);
                    ALTER TABLE execution_events ADD COLUMN IF NOT EXISTS message_id VARCHAR(36);
                    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS title VARCHAR(255);
                    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN NOT NULL DEFAULT FALSE;
                    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS language VARCHAR(20);
                    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS language_style VARCHAR(50);
                    ALTER TABLE conversations ADD COLUMN IF NOT EXISTS language_locked BOOLEAN NOT NULL DEFAULT FALSE;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS early_start TIMESTAMP;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS early_finish TIMESTAMP;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS late_start TIMESTAMP;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS late_finish TIMESTAMP;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS total_float FLOAT;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS free_float FLOAT;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS is_critical BOOLEAN DEFAULT FALSE;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS driving_predecessor_id VARCHAR(36);
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS constraint_type VARCHAR(50);
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS constraint_date TIMESTAMP;
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS activity_codes JSON DEFAULT '{}';
                    ALTER TABLE activities ADD COLUMN IF NOT EXISTS notes TEXT;
                """))
        except Exception as e:
            import logging
            logging.getLogger("database").warning(f"Note on running schema migrations: {e}")


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
