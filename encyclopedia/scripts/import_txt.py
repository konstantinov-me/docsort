#!/usr/bin/env python3
"""Импорт .txt/.md файлов (файл или директория) → источники, статьи, темы.

  python scripts/import_txt.py ./texts --taxonomy data/taxonomy.yaml [--dry-run]

Логика: decode → split_sections → classify → если max confidence >= порога,
создаём отсутствующие в БД темы из таксономии и привязываем статью; иначе —
статья помечается needs_review=1 (команда review назначит тему вручную).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.chunker import decode, split_sections          # noqa: E402
from app.classifier import classify                     # noqa: E402
from app.config import AUTO_ASSIGN_THRESHOLD            # noqa: E402
from app.db import init_db                              # noqa: E402
from app.storage import sha256_of                       # noqa: E402
from app.taxonomy import load_taxonomy, ensure_topics   # noqa: E402


def topic_id_for_slug(conn, slug: str):
    r = conn.execute("SELECT id FROM topics WHERE slug=?", (slug,)).fetchone()
    return r["id"] if r else None


def node_path_slug(node, roots) -> str:
    """Найти полный slug узла (parent/child/…) обходом дерева."""
    def walk(ns, prefix):
        for n in ns:
            s = f"{prefix}{n.slug}" if prefix else n.slug
            if n is node:
                yield s
            yield from walk(n.children, f"{s}/")

    return next(walk(roots, ""), node.slug)


def import_file(conn, path: Path, roots, dry_run: bool) -> tuple[int, int]:
    raw = path.read_bytes()
    text, enc = decode(raw)
    sections = split_sections(text)
    src_sha = sha256_of(path)

    imported = reviewed = 0
    if dry_run:
        for s in sections:
            scores = classify(s.title, s.body, roots)
            print(f"  [{path.name}] раздел «{s.title[:60]}» → "
                  + (", ".join(f"{sc.node.name}:{sc.confidence:.2f}" for sc in scores) or "нет тем"))
        return len(sections), 0

    cur = conn.execute(
        "INSERT OR IGNORE INTO sources(path, sha256, title, meta) VALUES (?,?,?,?)",
        (str(path), src_sha, path.stem, f'{{"encoding":"{enc}","sections":{len(sections)}}}'),
    )
    row = conn.execute("SELECT id FROM sources WHERE path=? AND sha256=?",
                       (str(path), src_sha)).fetchone()
    source_id = row["id"] if row else cur.lastrowid

    for s in sections:
        scores = classify(s.title, s.body, roots)
        acur = conn.execute(
            "INSERT INTO articles(source_id, title, body, topic_hint, needs_review) VALUES (?,?,?,?,?)",
            (source_id, s.title, s.body, s.title, 1 if not scores else 0),
        )
        aid = acur.lastrowid
        imported += 1
        top = [sc for sc in scores if sc.confidence >= AUTO_ASSIGN_THRESHOLD]
        if not top:
            reviewed += 1
            continue
        conn.execute("UPDATE articles SET needs_review=0 WHERE id=?", (aid,))
        for i, sc in enumerate(top):
            slug = node_path_slug(sc.node, roots)
            tid = topic_id_for_slug(conn, slug)
            if tid is None:
                # тема есть в таксономии, но ещё не в БД — досоздать
                ensure_topics(conn, roots)
                tid = topic_id_for_slug(conn, slug)
                if tid is None:
                    continue
            conn.execute(
                "INSERT OR REPLACE INTO article_topics(article_id, topic_id, weight, is_primary)"
                " VALUES (?,?,?,?)",
                (aid, tid, round(sc.confidence, 4), 1 if i == 0 else 0),
            )
    conn.commit()
    return imported, reviewed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target", help="txt/md файл или директория")
    ap.add_argument("--taxonomy", default=None)
    ap.add_argument("--glob", default="*.[tT][mM][dD]*", help="шаблон внутри директории")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from app.config import TAXONOMY_PATH

    tax_path = Path(args.taxonomy or TAXONOMY_PATH)
    roots = load_taxonomy(tax_path) if tax_path.exists() else []
    if not roots:
        print("Таксономия пуста — всё уйдёт в needs_review.")

    target = Path(args.target)
    files = sorted([target] if target.is_file() else list(target.rglob("*")))
    files = [f for f in files if f.suffix.lower() in (".txt", ".md", ".text", ".rst")]

    conn = init_db()
    total = rev_total = 0
    for f in files:
        n, r = import_file(conn, f, roots, args.dry_run)
        total += n
        rev_total += r
        print(f"{f}: разделов {n}, на модерацию {r}")
    conn.close()
    print(f"Итого: статей {total}, требует модерации {rev_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
