#!/usr/bin/env python3
"""Поиск из консоли + просмотр дерева тем.

  python scripts/cli_search.py "кондиционер ошибка e5" [--under climate] [-n 10] [--show ID]
  python scripts/cli_search.py --tree                 # дерево тем со slug
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db                  # noqa: E402
from app.search import search               # noqa: E402


def print_tree(conn, indent=0, parent=None):
    rows = conn.execute(
        "SELECT id,name,slug,kind FROM topics WHERE parent_id IS ? ORDER BY name",
        (parent,),
    ).fetchall()
    for r in rows:
        n_att = conn.execute(
            "SELECT COUNT(*) c FROM topic_attachments WHERE topic_id=?", (r["id"],)
        ).fetchone()["c"]
        n_art = conn.execute(
            "SELECT COUNT(*) c FROM article_topics WHERE topic_id=?", (r["id"],)
        ).fetchone()["c"]
        print("  " * indent + f"- {r['name']}  [{r['slug']}] ({r['kind']}, "
              f"{n_art} ст., {n_att} влож.)")
        print_tree(conn, indent + 1, r["id"])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("query", nargs="*", help="поисковый запрос")
    ap.add_argument("--under", default=None, help="filter: только подтема (slug)")
    ap.add_argument("-n", "--limit", type=int, default=10)
    ap.add_argument("--show", type=int, metavar="ID", help="показать полный текст статьи")
    ap.add_argument("--tree", action="store_true", help="показать дерево тем")
    args = ap.parse_args()

    conn = init_db()
    if args.tree:
        print_tree(conn)
        return 0
    if args.show:
        row = conn.execute("SELECT * FROM articles WHERE id=?", (args.show,)).fetchone()
        if not row:
            print("Нет такой статьи")
            return 1
        print(f"# {row['title']}\n\n{row['body']}")
        return 0
    if not args.query:
        ap.print_help()
        return 1

    q = " ".join(args.query)
    hits = search(conn, q, under_topic_slug=args.under, limit=args.limit)
    if not hits:
        print("Ничего не найдено.")
        return 0
    for h in hits:
        print(f"[{h.article_id:>4}] score={h.score:.2f}  {h.title}")
        print(f"       {h.snippet[:300]}")
        for t in h.topics:
            print(f"       тема: {t}")
        print()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
