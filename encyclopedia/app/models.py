"""Доменные модели (лёгкие dataclass-обёртки над строками БД)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Topic:
    id: int
    parent_id: Optional[int]
    name: str
    slug: str
    kind: str = "category"
    description: str = ""
    children: list["Topic"] = field(default_factory=list)

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Topic":
        return cls(r["id"], r["parent_id"], r["name"], r["slug"],
                   r["kind"], r["description"] or "")


@dataclass
class Article:
    id: int
    source_id: Optional[int]
    title: str
    body: str
    topic_hint: str = ""
    needs_review: bool = False

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Article":
        return cls(r["id"], r["source_id"], r["title"], r["body"],
                   r["topic_hint"] or "", bool(r["needs_review"]))


@dataclass
class Attachment:
    id: int
    filename: str
    sha256: str
    size: int
    mime: str
    kind: str
    storage_path: str
    note: str = ""

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Attachment":
        return cls(r["id"], r["filename"], r["sha256"], r["size"], r["mime"],
                   r["kind"], r["storage_path"], r["note"] or "")


@dataclass
class SearchHit:
    article_id: int
    title: str
    snippet: str          # подсвеченный фрагмент с <b>…</b>
    score: float          # чем меньше (bm25), тем релевантнее; инвертирован для удобства
    topics: list[str] = field(default_factory=list)   # хлебные крошки тем
