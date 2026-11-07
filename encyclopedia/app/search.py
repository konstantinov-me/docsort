"""Полнотекстовый поиск: FTS5 MATCH + bm25-ранжирование + фильтр по поддереву темы."""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

from .db import topic_descendants
from .models import SearchHit

TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def build_match_query(q: str) -> str:
    """'кондиционер haier e5' → 'кондиц* OR ...': каждое слово → токен с префикс-*
    (ловим окончания), склеиваем AND'ом. Кавычки/спецсимволы FTS вырезаем."""
    toks = [t.lower() for t in TOKEN_RE.findall(q)]
    if not toks:
        return ""
    parts = []
    for t in toks:
        esc = t.replace('"', '""')
        parts.append(f'"{esc}" OR "{esc}*"')
    return " AND ".join(f"({p})" for p in parts)


def search(conn: sqlite3.Connection, q: str,
           under_topic_slug: Optional[str] = None,
           limit: int = 20, offset: int = 0) -> list[SearchHit]:
    match = build_match_query(q)
    if not match:
        return []

    params: list = [match]
    topic_filter = ""
    if under_topic_slug:
        row = conn.execute("SELECT id FROM topics WHERE slug=?", (under_topic_slug,)).fetchone()
        if not row:
            return []
        ids = topic_descendants(conn, row["id"])
        topic_filter = f"AND a.id IN (SELECT article_id FROM article_topics WHERE topic_id IN ({','.join('?' * len(ids))}))"
        params.extend(ids)

    sql = f"""
        SELECT a.id, a.title,
               snippet(articles_fts, 1, '<b>', '</b>', '…', 16) AS snip,
               bm25(articles_fts, 5.0, 1.0) AS score
        FROM articles_fts f
        JOIN articles a ON a.id = f.rowid
        WHERE articles_fts MATCH ? {topic_filter}
        ORDER BY score
        LIMIT ? OFFSET ?
    """
    rows = conn.execute(sql, params + [limit, offset]).fetchall()

    hits: list[SearchHit] = []
    for r in rows:
        # хлебные крошки тем статьи: для каждой связанной темы — путь от корня
        crumbs: list[str] = []
        for trow in article_topics(conn, r["id"]):
            path: list[str] = []
            node = trow
            while node is not None:
                path.append(node["name"])
                pid = node["parent_id"]
                node = conn.execute("SELECT * FROM topics WHERE id=?", (pid,)).fetchone() if pid else None
            crumbs.append(" › ".join(reversed(path)))
        hits.append(SearchHit(article_id=r["id"], title=r["title"],
                              snippet=r["snip"], score=-r["score"], topics=crumbs))
    return hits


def get_article(conn: sqlite3.Connection, article_id: int):
    return conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()


def article_topics(conn: sqlite3.Connection, article_id: int):
    return conn.execute(
        """SELECT t.*, at.weight, at.is_primary FROM article_topics at
           JOIN topics t ON t.id = at.topic_id
           WHERE at.article_id=? ORDER BY at.is_primary DESC, at.weight DESC""",
        (article_id,),
    ).fetchall()
