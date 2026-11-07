"""Схема SQLite + соединения + FTS5-триггеры.

Таблицы:
  topics            — дерево тем/подтем (parent_id)
  sources           — исходные txt-файлы
  articles          — разделы/статьи, извлечённые из источников
  article_topics    — M:N статей и тем с весом уверенности
  attachments       — физические файлы (архивы/медиа), контент-адресуемые
  topic_attachments — M:N тем и вложений
  tags/article_tags — свободные метки
  articles_fts      — FTS5-индекс (синхронизируется триггерами)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS topics (
    id          INTEGER PRIMARY KEY,
    parent_id   INTEGER REFERENCES topics(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    slug        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL DEFAULT 'category',   -- category|device_class|device_model
    description TEXT DEFAULT '',
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_topics_parent ON topics(parent_id);

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    title       TEXT DEFAULT '',
    imported_at TEXT DEFAULT (datetime('now')),
    meta        TEXT DEFAULT '{}',                  -- JSON: encoding, кол-во разделов...
    UNIQUE (sha256, path)
);

CREATE TABLE IF NOT EXISTS articles (
    id          INTEGER PRIMARY KEY,
    source_id   INTEGER REFERENCES sources(id) ON DELETE CASCADE,
    title       TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    topic_hint  TEXT DEFAULT '',                    -- сырая подсказка из текста (заголовок раздела)
    needs_review INTEGER NOT NULL DEFAULT 0,        -- 1 = классификатор не уверен, ждёт модерацию
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_id);

-- FTS5-индекс по статьям. content='' — внешняя таблица: содержимое хранится
-- в articles, а индекс синхронизируется триггерами (unindexed content table).
CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title, body,
    content='articles', content_rowid='id',
    tokenize = 'porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS articles_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;
CREATE TRIGGER IF NOT EXISTS articles_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
END;
CREATE TRIGGER IF NOT EXISTS articles_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, body)
    VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO articles_fts(rowid, title, body) VALUES (new.id, new.title, new.body);
END;

CREATE TABLE IF NOT EXISTS article_topics (
    article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    weight      REAL NOT NULL DEFAULT 1.0,          -- уверенность классификатора 0..1
    is_primary  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (article_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_at_topic ON article_topics(topic_id);

CREATE TABLE IF NOT EXISTS attachments (
    id           INTEGER PRIMARY KEY,
    filename     TEXT NOT NULL,
    sha256       TEXT NOT NULL UNIQUE,
    size         INTEGER NOT NULL,
    mime         TEXT DEFAULT 'application/octet-stream',
    kind         TEXT NOT NULL DEFAULT 'other',     -- archive|backup|software|driver|photo|video|doc|other
    storage_path TEXT NOT NULL,                     -- относительный путь в FILES_DIR
    note         TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS topic_attachments (
    topic_id     INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    attachment_id INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    role         TEXT DEFAULT 'file',               -- manual|firmware|photo|error_video|backup...
    PRIMARY KEY (topic_id, attachment_id, role)
);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS article_tags (
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (article_id, tag_id)
);
"""


def connect(db_path=None) -> sqlite3.Connection:
    """Открыть соединение с БД (создать схему при первом запуске)."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def session(db_path=None) -> Iterator[sqlite3.Connection]:
    """Транзакционный контекст: commit при успехе, rollback при ошибке."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None) -> sqlite3.Connection:
    """Создать каталоги и применить схему. Возвращает открытое соединение."""
    from .config import DATA_DIR, FILES_DIR

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def reindex(conn: sqlite3.Connection) -> None:
    """Полная пересборка FTS-индекса (на случай ручных правок вне триггеров)."""
    conn.execute("DELETE FROM articles_fts")
    conn.execute(
        "INSERT INTO articles_fts(rowid, title, body) SELECT id, title, body FROM articles"
    )
    conn.commit()


def topic_descendants(conn: sqlite3.Connection, topic_id: int) -> list[int]:
    """Рекурсивно вернуть [topic_id, ...все id поддеревa] (для фильтра поиска по теме)."""
    rows = conn.execute(
        """
        WITH RECURSIVE subtree(id) AS (
            SELECT id FROM topics WHERE id = ?
            UNION ALL
            SELECT t.id FROM topics t JOIN subtree s ON t.parent_id = s.id
        )
        SELECT id FROM subtree
        """,
        (topic_id,),
    ).fetchall()
    return [r["id"] for r in rows]
