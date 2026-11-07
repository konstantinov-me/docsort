#!/usr/bin/env python3
"""Инициализация БД и загрузка таксономии из YAML."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db                      # noqa: E402
from app.taxonomy import load_taxonomy, ensure_topics  # noqa: E402
from app.config import TAXONOMY_PATH            # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--taxonomy", default=str(TAXONOMY_PATH), help="путь к taxonomy.yaml")
    args = ap.parse_args()

    conn = init_db()
    print(f"БД готова: {conn.execute('PRAGMA integrity_check').fetchone()[0]}")
    p = Path(args.taxonomy)
    if p.exists():
        nodes = load_taxonomy(p)
        ensure_topics(conn, nodes)
        n = conn.execute("SELECT COUNT(*) c FROM topics").fetchone()["c"]
        print(f"Таксономия загружена из {p}, тем в БД: {n}")
    else:
        print(f"Таксономия не найдена ({p}) — создана пустая БД.")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
