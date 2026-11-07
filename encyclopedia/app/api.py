"""REST API + минимальный Web UI (FastAPI).

Запуск:  uvicorn app.api:app --port 8080
Документация: /docs (Swagger)
UI: /  — поиск, дерево тем, страница темы со статьями и вложениями (фото/видео inline).
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from starlette.responses import FileResponse

from . import storage
from .config import FILES_DIR, KINDS
from .db import init_db
from .search import article_topics, get_article, search

app = FastAPI(title="Энциклопедия", version="0.1")


def db() -> sqlite3.Connection:
    conn = init_db()          # идемпотентно: создаёт схему при первом вызове
    return conn


# ------------------------------------------------------------------ REST

@app.get("/api/search")
def api_search(q: str, topic: Optional[str] = None, limit: int = 20, offset: int = 0):
    conn = db()
    try:
        hits = search(conn, q, under_topic_slug=topic, limit=limit, offset=offset)
        return [
            {"article_id": h.article_id, "title": h.title, "snippet": h.snippet,
             "score": round(h.score, 3), "topics": h.topics}
            for h in hits
        ]
    finally:
        conn.close()


@app.get("/api/articles/{article_id}")
def api_article(article_id: int):
    conn = db()
    try:
        row = get_article(conn, article_id)
        if not row:
            raise HTTPException(404, "Статья не найдена")
        topics = [{"slug": t["slug"], "name": t["name"], "weight": t["weight"]}
                  for t in article_topics(conn, article_id)]
        return {"id": row["id"], "title": row["title"], "body": row["body"],
                "needs_review": bool(row["needs_review"]), "topics": topics}
    finally:
        conn.close()


@app.get("/api/tree")
def api_tree():
    conn = db()
    try:
        rows = conn.execute("SELECT id,parent_id,name,slug,kind FROM topics ORDER BY id").fetchall()
        nodes = {r["id"]: {"id": r["id"], "name": r["name"], "slug": r["slug"],
                           "kind": r["kind"], "children": []} for r in rows}
        roots = []
        for r in rows:
            node = nodes[r["id"]]
            if r["parent_id"] and r["parent_id"] in nodes:
                nodes[r["parent_id"]]["children"].append(node)
            else:
                roots.append(node)
        return roots
    finally:
        conn.close()


@app.post("/api/topics/{slug}/attachments")
async def api_attach(slug: str, file: UploadFile = File(...),
                     kind: str = Form("other"), role: str = Form("file"),
                     note: str = Form("")):
    """Загрузить архив/медиа/код и привязать к теме по slug."""
    if kind not in KINDS:
        raise HTTPException(400, f"kind должен быть одним из {KINDS}")
    conn = db()
    try:
        trow = conn.execute("SELECT id FROM topics WHERE slug=?", (slug,)).fetchone()
        if not trow:
            raise HTTPException(404, "Тема не найдена")
        # сохраняем upload во временный файл → в контент-хранилище
        tmp_dir = FILES_DIR / "_upload_tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / (file.filename or "upload.bin")
        with tmp.open("wb") as out:
            while chunk := await file.read(1 << 20):
                out.write(chunk)
        att = storage.add_file(conn, tmp, kind=None if kind == "other" else kind, note=note)
        storage.attach_to_topic(conn, trow["id"], att.id, role=role)
        tmp.unlink(missing_ok=True)
        return {"attachment_id": att.id, "sha256": att.sha256, "kind": att.kind,
                "filename": att.filename, "size": att.size}
    finally:
        conn.close()


@app.get("/api/attachments/{att_id}/download")
def api_download(att_id: int):
    conn = db()
    try:
        att = storage.get_attachment(conn, att_id)
        if not att:
            raise HTTPException(404, "Вложение не найдено")
        p = storage.abs_path(att)
        if not p.exists():
            raise HTTPException(410, "Файл отсутствует в хранилище")
        return FileResponse(p, media_type=att.mime, filename=att.filename,
                            headers={"X-Content-Type-Options": "nosniff"})
    finally:
        conn.close()


@app.get("/api/files/{relpath:path}")
def api_raw(relpath: str):
    """Отдача медиа для inline-просмотра (<img src>, <video src>)."""
    p = (FILES_DIR / relpath).resolve()
    if not str(p).startswith(str(FILES_DIR.resolve())) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p)


@app.post("/api/admin/reindex")
def api_reindex():
    from .db import reindex

    conn = db()
    try:
        reindex(conn)
        return {"ok": True}
    finally:
        conn.close()


# ------------------------------------------------------------------ SSR UI

PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Энциклопедия</title>
<style>
 body{font-family:system-ui;max-width:960px;margin:2rem auto;padding:0 1rem}
 nav a{margin-right:.75rem} .hit{border-bottom:1px solid #ddd;padding:.75rem 0}
 .crumb{color:#666;font-size:.85rem} pre{background:#f6f6f6;padding:.5rem;overflow:auto}
 .att{display:inline-block;border:1px solid #ccc;border-radius:6px;padding:.5rem;margin:.3rem}
 img.thumb{max-height:120px} video{max-height:240px}
</style></head><body>
<h1><a href="/" style="text-decoration:none;color:inherit">📚 Энциклопедия</a></h1>
<form action="/search"><input name="q" size="40" placeholder="полнотекстовый поиск…"
 value="{q}"> <input name="topic" size="20" placeholder="slug темы (опц.)" value="{topic}">
<button>Искать</button></form>
<div>{content}</div>
</body></html>"""


def _render_topic_page(conn: sqlite3.Connection, slug: str) -> str:
    trow = conn.execute("SELECT * FROM topics WHERE slug=?", (slug,)).fetchone()
    if not trow:
        return "<p>Тема не найдена</p>"
    kids = conn.execute("SELECT name,slug FROM topics WHERE parent_id=? ORDER BY name",
                        (trow["id"],)).fetchall()
    arts = conn.execute(
        """SELECT a.id, a.title FROM article_topics at JOIN articles a ON a.id=at.article_id
           WHERE at.topic_id=? ORDER BY at.weight DESC LIMIT 100""", (trow["id"],)).fetchall()
    atts = storage.topic_attachments(conn, trow["id"])
    parts = [f"<h2>{trow['name']} <small>({trow['kind']})</small></h2>"]
    if trow["description"]:
        parts.append(f"<p>{trow['description']}</p>")
    if kids:
        parts.append("<h3>Подтемы:</h3><ul>" + "".join(
            f'<li><a href="/topic/{k["slug"]}">{k["name"]}</a></li>' for k in kids) + "</ul>")
    parts.append("<h3>Статьи:</h3><ul>" + ("".join(
        f'<li><a href="/article/{a["id"]}">{a["title"]}</a></li>' for a in arts) or "<li>нет</li>") + "</ul>")
    if atts:
        blocks = []
        for a in atts:
            url = f"/api/files/{a['storage_path']}"
            if a["kind"] == "photo":
                inner = f'<a href="{url}"><img class="thumb" src="{url}" alt="{a["filename"]}"></a>'
            elif a["kind"] == "video":
                inner = f'<video controls src="{url}"></video>'
            else:
                inner = ""
            blocks.append(
                f'<div class="att">{inner}<br><a href="/api/attachments/{a["id"]}/download">'
                f'⬇ {a["filename"]}</a> <small>[{a["kind"]}, {a["size"]} байт, роль: {a["role"]}]</small>'
                f'<br><small>{a["note"] or ""}</small></div>')
        parts.append("<h3>Вложения (архивы/медиа):</h3>" + "".join(blocks))
    return "".join(parts)


def _render_article_page(conn: sqlite3.Connection, aid: int) -> str:
    row = get_article(conn, aid)
    if not row:
        return "<p>Статья не найдена</p>"
    tp = "".join(f'<a href="/topic/{t["slug"]}">{t["name"]}</a> ({t["weight"]:.2f}) '
                 for t in article_topics(conn, aid))
    return (f"<h2>{row['title']}</h2><p><b>Темы:</b> {tp or '—'}</p>"
            f"<pre>{row['body'].replace('<','&lt;')}</pre>")


@app.get("/", response_class=HTMLResponse)
def ui_index():
    return PAGE.format(q="", topic="", content="<p>Введите запрос или откройте тему: "
                       '<a href="/topic/climate">climate</a></p>')


@app.get("/search", response_class=HTMLResponse)
def ui_search(q: str = "", topic: str = ""):
    conn = db()
    try:
        hits = search(conn, q, under_topic_slug=topic or None)
        body = []
        for h in hits:
            crumbs = " | ".join(f'<a href="/topic/{c.split(" › ")[-1]}">{c}</a>' for c in h.topics)
            body.append(f'<div class="hit"><a href="/article/{h.article_id}"><b>{h.title}</b></a>'
                        f'<br>{h.snippet}<br><span class="crumb">{crumbs}</span></div>')
        content = "".join(body) or "<p>Ничего не найдено.</p>"
        return PAGE.format(q=q, topic=topic, content=content)
    finally:
        conn.close()


@app.get("/topic/{slug}", response_class=HTMLResponse)
def ui_topic(slug: str):
    conn = db()
    try:
        return PAGE.format(q="", topic=slug, content=_render_topic_page(conn, slug))
    finally:
        conn.close()


@app.get("/article/{aid}", response_class=HTMLResponse)
def ui_article(aid: int):
    conn = db()
    try:
        return PAGE.format(q="", topic="", content=_render_article_page(conn, aid))
    finally:
        conn.close()
