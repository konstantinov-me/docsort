#!/usr/bin/env python3
"""Модерация: показать статьи без темы и назначить тему вручную.

  python scripts/review.py --list
  python scripts/review.py --assign ARTICLE_ID TOPIC_SLUG [--primary]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--assign", nargs=2, metavar=("ARTICLE_ID", "TOPIC_SLUG"))
    ap.add_argument("--primary", action="store_true")
    ap.add_argument("--preview-chars", type=int, default=200)
    args = ap.parse_args()

    conn = init_db()
    if args.assign:
        aid, slug = args.assign
        art = conn.execute("SELECT id,title FROM articles WHERE id=?", (aid,)).fetchone()
        top = conn.execute("SELECT id,name FROM topics WHERE slug=?", (slug,)).fetchone()
        if not art or not top:
            print(f"Не найдено: article={bool(art)}, topic={bool(top)}"); return 2
        conn.execute(
            "INSERT OR REPLACE INTO article_topics(article_id, topic_id, weight, is_primary)"
            " VALUES (?,?,1.0,?)", (aid, top["id"], 1 if args.primary else 0))
        conn.execute("UPDATE articles SET needs_review=0 WHERE id=?", (aid,))
        conn.commit()
        print(f"Статья «{art['title']}» → тема «{top['name']}»")
        return 0

    rows = conn.execute(
        """SELECT a.id, a.title, substr(a.body,1,?) AS prev FROM articles a
           WHERE a.needs_review=1 AND NOT EXISTS
             (SELECT 1 FROM article_topics t WHERE t.article_id=a.id)
           ORDER BY a.id""",
        (args.preview_chars,),
    ).fetchall()
    if not rows:
        print("Очередь модерации пуста.")
    for r in rows:
        print(f"[{r['id']:>4}] {r['title']}\n       {r['prev']!r}\n")
    print(f"Всего к модерации: {len(rows)}. Назначение: --assign ID SLUG")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
