"""Контент-адресуемое хранилище файлов (архивы, бекапы, драйверы, фото, видео).

data/files/<sha256[0:2]>/<sha256>  — одинаковый файл хранится один раз при
многократном прикреплении к разным темам.
"""
from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
import shutil
import tempfile
from pathlib import Path

from .config import FILES_DIR, PREVIEW_MAX_BYTES
from .models import Attachment

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg"}
VIDEO_EXT = {".mp4", ".webm", ".ogg", ".mov", ".avi", ".mkv"}
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"}
CODE_EXT = {".c", ".h", ".py", ".sh", ".bat", ".inf", ".sys.txt", ".asm", ".ini", ".cfg", ".json", ".yaml", ".yml"}


def guess_kind(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXT:
        return "photo"
    if ext in VIDEO_EXT:
        return "video"
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext in CODE_EXT:
        return "driver" if ext in (".inf", ".sys.txt", ".asm") else "software"
    mime, _ = mimetypes.guess_type(filename)
    if mime:
        if mime.startswith("image/"):
            return "photo"
        if mime.startswith("video/"):
            return "video"
        if mime in ("application/zip", "application/x-rar-compressed",
                    "application/x-7z-compressed", "application/gzip"):
            return "archive"
        if mime.startswith("text/"):
            return "doc"
    return "other"


def sha256_of(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def store_path(digest: str) -> Path:
    return Path(digest[:2]) / digest


def add_file(conn: sqlite3.Connection, src: Path | str, *,
             kind: str | None = None, note: str = "") -> Attachment:
    """Положить файл в хранилище и зарегистрировать (или переиспользовать запись)."""
    src = Path(src)
    digest = sha256_of(src)
    rel = store_path(digest)
    dest = FILES_DIR / rel
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        # атомарно: во временный файл в той же ФС, затем rename
        tmp = Path(tempfile.mkstemp(dir=str(dest.parent))[1])
        try:
            shutil.copyfile(src, tmp)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)

    size = src.stat().st_size
    mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
    k = kind or guess_kind(src.name)

    row = conn.execute("SELECT id FROM attachments WHERE sha256=?", (digest,)).fetchone()
    if row:
        att_id = row["id"]
        conn.execute("UPDATE attachments SET filename=?, mime=?, kind=?, note=COALESCE(NULLIF(?,''), note)"
                     " WHERE id=?", (src.name, mime, k, note, att_id))
    else:
        cur = conn.execute(
            """INSERT INTO attachments(filename, sha256, size, mime, kind, storage_path, note)
               VALUES (?,?,?,?,?,?,?)""",
            (src.name, digest, size, mime, k, str(rel), note),
        )
        att_id = cur.lastrowid
    conn.commit()
    r = conn.execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
    return Attachment.from_row(r)


def attach_to_topic(conn: sqlite3.Connection, topic_id: int, attachment_id: int,
                    role: str = "file") -> None:
    conn.execute(
        "INSERT OR IGNORE INTO topic_attachments(topic_id, attachment_id, role) VALUES (?,?,?)",
        (topic_id, attachment_id, role),
    )
    conn.commit()


def get_attachment(conn: sqlite3.Connection, att_id: int) -> Attachment | None:
    r = conn.execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
    return Attachment.from_row(r) if r else None


def topic_attachments(conn: sqlite3.Connection, topic_id: int):
    return conn.execute(
        """SELECT a.*, ta.role FROM attachments a
           JOIN topic_attachments ta ON ta.attachment_id = a.id
           WHERE ta.topic_id=? ORDER BY a.kind, a.filename""",
        (topic_id,),
    ).fetchall()


def abs_path(att: Attachment) -> Path:
    return FILES_DIR / att.storage_path


def text_preview(att: Attachment) -> str | None:
    """Инлайновый предпросмотр для кода/текста (не больше PREVIEW_MAX_BYTES)."""
    p = abs_path(att)
    if att.mime.startswith("text/") or Path(att.filename).suffix.lower() in CODE_EXT:
        if p.exists() and p.stat().st_size <= PREVIEW_MAX_BYTES:
            return p.read_text(encoding="utf-8", errors="replace")
    return None


def gc_orphans(conn: sqlite3.Connection, dry_run: bool = True) -> list[str]:
    """Удалить файлы хранилища, на которые не ссылается ни одна запись attachments."""
    referenced = {r["storage_path"] for r in conn.execute("SELECT storage_path FROM attachments")}
    removed: list[str] = []
    for p in FILES_DIR.rglob("*"):
        if p.is_file():
            rel = str(p.relative_to(FILES_DIR))
            if rel not in referenced:
                removed.append(rel)
                if not dry_run:
                    p.unlink()
    # и записи attachments без ссылок тем → удаляем строку и файл
    dead = conn.execute(
        "SELECT a.* FROM attachments a LEFT JOIN topic_attachments ta ON ta.attachment_id=a.id "
        "WHERE ta.attachment_id IS NULL").fetchall()
    for a in dead:
        removed.append(a["storage_path"])
        if not dry_run:
            fp = FILES_DIR / a["storage_path"]
            fp.unlink(missing_ok=True)
            conn.execute("DELETE FROM attachments WHERE id=?", (a["id"],))
    if not dry_run:
        conn.commit()
    return removed
