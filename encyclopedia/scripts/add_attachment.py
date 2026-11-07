#!/usr/bin/env python3
"""Прикрепить файл (архив/бекап/драйвер/фото/видео) к теме.

  python scripts/add_attachment.py --topic climate/air-conditioners/haier-dc1052 \
      --file ./haier_err_E5.mp4 --kind video --role error_video --note "видео ошибки E5"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import storage                       # noqa: E402
from app.config import KINDS                  # noqa: E402
from app.db import init_db                    # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--topic", required=True, help="slug темы (см. /api/tree или scripts/cli_tree)")
    ap.add_argument("--file", required=True, nargs="+", help="один или несколько файлов")
    ap.add_argument("--kind", default=None, choices=list(KINDS),
                    help="тип вложения; по умолчанию определяется по расширению")
    ap.add_argument("--role", default="file", help="роль: manual|firmware|photo|error_video|backup…")
    ap.add_argument("--note", default="")
    args = ap.parse_args()

    conn = init_db()
    trow = conn.execute("SELECT id,name FROM topics WHERE slug=?", (args.topic,)).fetchone()
    if not trow:
        print(f"Тема '{args.topic}' не найдена. Доступные:")
        for r in conn.execute("SELECT slug,name FROM topics ORDER BY slug").fetchall():
            print(f"  {r['slug']}  ({r['name']})")
        return 2

    rc = 0
    for f in args.file:
        p = Path(f)
        if not p.is_file():
            print(f"Пропущен (не файл): {p}")
            rc = 1
            continue
        att = storage.add_file(conn, p, kind=args.kind, note=args.note)
        storage.attach_to_topic(conn, trow["id"], att.id, role=args.role)
        print(f"OK: {p.name} → тема «{trow['name']}» [attachment id={att.id}, "
              f"kind={att.kind}, sha256={att.sha256[:12]}…, size={att.size}]")
    conn.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
