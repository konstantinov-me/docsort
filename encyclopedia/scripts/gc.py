#!/usr/bin/env python3
"""Сборщик мусора: удалить из хранилища файлы без ссылок. По умолчанию dry-run."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import storage        # noqa: E402
from app.db import init_db     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--delete", action="store_true", help="реально удалять (иначе только список)")
    args = ap.parse_args()

    conn = init_db()
    removed = storage.gc_orphans(conn, dry_run=not args.delete)
    if removed:
        print(("Удалено: " if args.delete else "Осиротевшие файлы (dry-run): ") + str(len(removed)))
        for r in removed:
            print("  ", r)
    else:
        print("Осиротевших файлов нет.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
