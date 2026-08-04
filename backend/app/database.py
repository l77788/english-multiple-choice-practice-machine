from __future__ import annotations

import sqlite3
from collections.abc import Generator

from .config import DATABASE_PATH, ensure_directories


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL UNIQUE,
    subject TEXT NOT NULL DEFAULT '英语一',
    title TEXT NOT NULL,
    source_file TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id INTEGER NOT NULL,
    unit_type TEXT NOT NULL,
    subtype TEXT,
    title TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    passage TEXT NOT NULL DEFAULT '',
    shared_data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE,
    UNIQUE (paper_id, sequence)
);

CREATE TABLE IF NOT EXISTS questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id INTEGER NOT NULL,
    number INTEGER NOT NULL,
    stem TEXT NOT NULL DEFAULT '',
    question_type TEXT NOT NULL DEFAULT 'single_choice',
    answer TEXT NOT NULL,
    score REAL NOT NULL,
    sequence INTEGER NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE,
    UNIQUE (unit_id, number)
);

CREATE TABLE IF NOT EXISTS options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    stable_key TEXT NOT NULL,
    original_label TEXT NOT NULL,
    content TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    UNIQUE (question_id, stable_key)
);

CREATE TABLE IF NOT EXISTS practice_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mode TEXT NOT NULL,
    paper_id INTEGER,
    unit_ids TEXT NOT NULL,
    shuffle_options INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    submitted_at TEXT,
    score REAL,
    max_score REAL,
    FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS practice_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    user_answer TEXT NOT NULL,
    option_order TEXT NOT NULL DEFAULT '[]',
    is_correct INTEGER,
    answered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES practice_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE,
    UNIQUE (session_id, question_id)
);

CREATE TABLE IF NOT EXISTS practice_answer_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    question_id INTEGER NOT NULL,
    user_answer TEXT NOT NULL,
    option_order TEXT NOT NULL DEFAULT '[]',
    changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES practice_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS practice_unit_submissions (
    session_id INTEGER NOT NULL,
    unit_id INTEGER NOT NULL,
    submitted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    score REAL NOT NULL DEFAULT 0,
    max_score REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, unit_id),
    FOREIGN KEY (session_id) REFERENCES practice_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wrong_stats (
    question_id INTEGER PRIMARY KEY,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    wrong_count INTEGER NOT NULL DEFAULT 0,
    recent_results TEXT NOT NULL DEFAULT '[]',
    consecutive_correct INTEGER NOT NULL DEFAULT 0,
    manually_frequent INTEGER NOT NULL DEFAULT 0,
    last_wrong_at TEXT,
    last_attempt_at TEXT,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vocabulary_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    term TEXT NOT NULL,
    normalized_term TEXT NOT NULL UNIQUE,
    lemma TEXT NOT NULL DEFAULT '',
    phonetic TEXT NOT NULL DEFAULT '',
    part_of_speech TEXT NOT NULL DEFAULT '',
    contextual_meaning TEXT NOT NULL DEFAULT '',
    common_meaning TEXT NOT NULL DEFAULT '',
    memory_hint TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    translation_status TEXT NOT NULL DEFAULT 'pending',
    translation_error TEXT NOT NULL DEFAULT '',
    encounter_count INTEGER NOT NULL DEFAULT 1,
    study_status TEXT NOT NULL DEFAULT 'learning',
    manually_frequent INTEGER NOT NULL DEFAULT 0,
    user_edited INTEGER NOT NULL DEFAULT 0,
    next_review_at TEXT,
    last_reviewed_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS vocabulary_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    surface_form TEXT NOT NULL,
    context_sentence TEXT NOT NULL DEFAULT '',
    context_before TEXT NOT NULL DEFAULT '',
    context_after TEXT NOT NULL DEFAULT '',
    unit_id INTEGER,
    question_id INTEGER,
    year INTEGER,
    unit_title TEXT NOT NULL DEFAULT '',
    unit_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entry_id) REFERENCES vocabulary_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE SET NULL,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS vocabulary_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL,
    rating TEXT NOT NULL,
    reviewed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    next_review_at TEXT,
    FOREIGN KEY (entry_id) REFERENCES vocabulary_entries(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    detected_year INTEGER,
    detected_format TEXT,
    status TEXT NOT NULL DEFAULT 'analyzing',
    draft_data TEXT NOT NULL DEFAULT '{}',
    warnings TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS revision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_job_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_ref TEXT NOT NULL,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    source TEXT NOT NULL,
    model_name TEXT,
    approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (import_job_id) REFERENCES import_jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL DEFAULT '本地模型',
    base_url TEXT NOT NULL DEFAULT 'http://127.0.0.1:11434/v1',
    api_key_encrypted TEXT,
    model TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.2,
    max_tokens INTEGER NOT NULL DEFAULT 1200,
    system_prompt TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO ai_settings (id) VALUES (1);

CREATE TABLE IF NOT EXISTS ai_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key_encrypted TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    is_default INTEGER NOT NULL DEFAULT 0,
    default_model TEXT NOT NULL DEFAULT '',
    temperature REAL NOT NULL DEFAULT 0.2,
    max_tokens INTEGER NOT NULL DEFAULT 1200,
    system_prompt TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_profile_models (
    profile_id INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    owned_by TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    is_visible INTEGER NOT NULL DEFAULT 1,
    is_available INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (profile_id, model_id),
    FOREIGN KEY (profile_id) REFERENCES ai_profiles(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '新对话',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    profile_id INTEGER,
    model_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (conversation_id) REFERENCES ai_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id) REFERENCES ai_profiles(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS question_ai_labels (
    question_id INTEGER PRIMARY KEY,
    primary_skill TEXT NOT NULL DEFAULT '',
    secondary_skills TEXT NOT NULL DEFAULT '[]',
    trap_types TEXT NOT NULL DEFAULT '[]',
    attention_points TEXT NOT NULL DEFAULT '[]',
    vocabulary_demand TEXT NOT NULL DEFAULT 'medium',
    context_dependency TEXT NOT NULL DEFAULT 'medium',
    grammar_dependency TEXT NOT NULL DEFAULT 'medium',
    confidence REAL NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 0,
    user_edited INTEGER NOT NULL DEFAULT 0,
    model_name TEXT NOT NULL DEFAULT '',
    label_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_label_run_items (
    run_id TEXT NOT NULL,
    question_id INTEGER NOT NULL,
    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, question_id),
    FOREIGN KEY (question_id) REFERENCES questions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wrong_analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope_key TEXT NOT NULL DEFAULT '',
    unit_ids TEXT NOT NULL DEFAULT '[]',
    input_snapshot TEXT NOT NULL DEFAULT '{}',
    scope_title TEXT NOT NULL DEFAULT '',
    question_count INTEGER NOT NULL DEFAULT 0,
    aggregate_data TEXT NOT NULL DEFAULT '{}',
    report TEXT NOT NULL,
    model_name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS wrong_analysis_states (
    unit_id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL,
    analyzed_session_id INTEGER NOT NULL DEFAULT 0,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (unit_id) REFERENCES units(id) ON DELETE CASCADE,
    FOREIGN KEY (report_id) REFERENCES wrong_analysis_reports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS question_bank_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    content_version TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    publisher TEXT NOT NULL DEFAULT '',
    manifest_data TEXT NOT NULL DEFAULT '{}',
    source_file TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'published',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (package_id, content_version)
);

CREATE TABLE IF NOT EXISTS question_bank_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    content_version TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (package_id, content_version, asset_id)
);

CREATE TABLE IF NOT EXISTS question_bank_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id TEXT NOT NULL,
    content_version TEXT NOT NULL,
    paper_external_key TEXT NOT NULL,
    action TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO ai_profiles
    (name, base_url, api_key_encrypted, enabled, is_default, default_model,
     temperature, max_tokens, system_prompt)
SELECT
    name, base_url, api_key_encrypted, 1, 1, model,
    temperature, max_tokens, system_prompt
FROM ai_settings
WHERE NOT EXISTS (SELECT 1 FROM ai_profiles);

INSERT OR IGNORE INTO ai_profile_models
    (profile_id, model_id, display_name, is_visible, is_available)
SELECT id, default_model, default_model, 1, 1
FROM ai_profiles
WHERE default_model <> '';

CREATE INDEX IF NOT EXISTS idx_units_paper ON units(paper_id);
CREATE INDEX IF NOT EXISTS idx_questions_unit ON questions(unit_id);
CREATE INDEX IF NOT EXISTS idx_answers_session ON practice_answers(session_id);
CREATE INDEX IF NOT EXISTS idx_answer_events_question
    ON practice_answer_events(question_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_unit_submissions_session
    ON practice_unit_submissions(session_id);
CREATE INDEX IF NOT EXISTS idx_wrong_count ON wrong_stats(wrong_count DESC);
CREATE INDEX IF NOT EXISTS idx_vocab_priority
    ON vocabulary_entries(encounter_count DESC, next_review_at, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_vocab_occurrences_entry
    ON vocabulary_occurrences(entry_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_vocab_translation_queue
    ON vocabulary_entries(translation_status, user_edited, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_profiles_single_default
    ON ai_profiles(is_default) WHERE is_default = 1;
CREATE INDEX IF NOT EXISTS idx_ai_profile_models_selector
    ON ai_profile_models(profile_id, is_visible, is_available);
CREATE INDEX IF NOT EXISTS idx_ai_conversations_updated
    ON ai_conversations(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation
    ON ai_messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_question_ai_labels_locked
    ON question_ai_labels(locked, updated_at);
CREATE INDEX IF NOT EXISTS idx_question_label_run_items_question
    ON question_label_run_items(question_id, run_id);
CREATE INDEX IF NOT EXISTS idx_wrong_analysis_created
    ON wrong_analysis_reports(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_question_bank_assets_lookup
    ON question_bank_assets(package_id, content_version, asset_id);
CREATE INDEX IF NOT EXISTS idx_question_bank_revisions_paper
    ON question_bank_revisions(paper_external_key, created_at DESC);
"""


def connect() -> sqlite3.Connection:
    ensure_directories()
    # FastAPI may enter and resume a synchronous dependency on different
    # worker threads. SQLite's default thread check would then intermittently
    # reject an otherwise valid request with a 500 response.
    connection = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        row["name"]
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
        )


def _run_migrations(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "papers", "external_key", "TEXT")
    _ensure_column(connection, "papers", "package_id", "TEXT")
    _ensure_column(connection, "papers", "content_version", "TEXT")
    _ensure_column(connection, "papers", "source_metadata", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "units", "external_key", "TEXT")
    _ensure_column(connection, "questions", "external_key", "TEXT")
    _ensure_column(connection, "questions", "content_hash", "TEXT")
    _ensure_column(connection, "options", "metadata", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "wrong_analysis_reports", "scope_key", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "wrong_analysis_reports", "unit_ids", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(connection, "wrong_analysis_reports", "input_snapshot", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(connection, "wrong_analysis_states", "analyzed_session_id", "INTEGER NOT NULL DEFAULT 0")
    connection.execute(
        """
        UPDATE vocabulary_entries
        SET translation_status = 'queued'
        WHERE translation_status = 'pending'
        """
    )
    connection.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_units_external_key
            ON units(paper_id, external_key)
            WHERE external_key IS NOT NULL AND external_key <> '';
        CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_external_key
            ON questions(unit_id, external_key)
            WHERE external_key IS NOT NULL AND external_key <> '';
        CREATE INDEX IF NOT EXISTS idx_papers_external_key
            ON papers(external_key);
        """
    )


def initialize_database() -> None:
    with connect() as connection:
        connection.executescript(SCHEMA)
        _run_migrations(connection)


def get_db() -> Generator[sqlite3.Connection, None, None]:
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()
