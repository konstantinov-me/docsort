"""Таксономия тем: загрузка YAML-словаря, матчинг тем по ключевым словам/моделям.

Формат taxonomy.yaml:

  topics:
    - name: Климатическое оборудование
      slug: climate
      description: Всё про heating/cooling
      keywords: [кондиционер, вентилятор, обогреватель, сплит]
      children:
        - name: Кондиционеры
          slug: air-conditioners
          keywords: [кондиционер, сплит-система, инвертор, фреон, хладагент, r32]
          children:
            - name: Haier DC1052
              slug: haier-dc1052
              kind: device_model
              model_re: "haier\\s*dc?\\s*1052"
        - name: Вентиляторы
          slug: fans
          keywords: [вентилятор, крыльчатка]

Зависимости не вводились ради MVP: если PyYAML установлен — читаем YAML;
иначе используем встроенный минимальный парсер подмножества (отступы + списки в []),
чего достаточно для файлов такого формата выше.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TaxonNode:
    name: str
    slug: str
    kind: str = "category"                # category|device_class|device_model
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    model_re: str | None = None
    children: list["TaxonNode"] = field(default_factory=list)

    def path_parts(self) -> list[str]:
        """Для матчинга: все префиксные пути темы (родитель/…/сама)."""
        return [self.slug]


# ---------------------------------------------------------------- loading

def _parse_minimal_yaml(text: str) -> dict:
    """Крошечный парсер подмножества YAML (dict/list/scalar, отступы)."""
    lines = [ln.rstrip("\n") for ln in text.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]

    def indent(ln: str) -> int:
        return len(ln) - len(ln.lstrip(" "))

    def parse_block(i: int, ind: int):
        # возвращает (value, next_index)
        if i >= len(lines):
            return None, i
        cur = lines[i]
        if cur.strip().startswith("- "):
            items = []
            while i < len(lines) and indent(lines[i]) == ind and lines[i].strip().startswith("- "):
                item_text = lines[i].strip()[2:]
                if ":" in item_text and not item_text.startswith("["):
                    # inline mapping start; may continue on deeper lines
                    d, i = parse_map_from_item(i, ind + 2, item_text)
                    items.append(d)
                else:
                    items.append(_scalar(item_text))
                    i += 1
            return items, i
        return parse_map(i, ind)

    def parse_map_from_item(i: int, ind: int, first: str):
        d: dict = {}
        k, _, v = first.partition(":")
        v = v.strip()
        if v:
            d[k.strip()] = _scalar(v)
            i += 1
        else:
            d[k.strip()] = None
            i += 1
            nv, ni = parse_block(i, ind) if i < len(lines) and indent(lines[i]) >= ind else (None, i)
            d[k.strip()] = nv
            i = ni
        # continuation keys of the same mapping at indent `ind`
        while i < len(lines) and indent(lines[i]) == ind and not lines[i].strip().startswith("- "):
            key_line = lines[i].strip()
            k2, _, v2 = key_line.partition(":")
            v2 = v2.strip()
            if v2:
                d[k2.strip()] = _scalar(v2)
                i += 1
            else:
                i += 1
                nv, ni = parse_block(i, indent(lines[i]) if i < len(lines) else ind)
                d[k2.strip()] = nv
                i = ni
        return d, i

    def parse_map(i: int, ind: int):
        d: dict = {}
        while i < len(lines) and indent(lines[i]) == ind and not lines[i].strip().startswith("- "):
            key_line = lines[i].strip()
            k, _, v = key_line.partition(":")
            v = v.strip()
            if v:
                d[k.strip()] = _scalar(v)
                i += 1
            else:
                i += 1
                if i < len(lines) and indent(lines[i]) > ind:
                    nv, ni = parse_block(i, indent(lines[i]))
                    d[k.strip()] = nv
                    i = ni
                else:
                    d[k.strip()] = None
        return d, i

    def _scalar(s: str):
        s = s.strip()
        if s.startswith("[") and s.endswith("]"):
            inner = s[1:-1].strip()
            return [_scalar(x) for x in inner.split(",")] if inner else []
        if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
            return s[1:-1]
        return s

    val, _ = parse_block(0, indent(lines[0]) if lines else 0)
    return val if isinstance(val, dict) else {"topics": val}


def load_taxonomy(path: str | Path) -> list[TaxonNode]:
    text = Path(path).read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        raw = yaml.safe_load(text)
    except ImportError:
        raw = _parse_minimal_yaml(text)

    def to_node(d: dict) -> TaxonNode:
        return TaxonNode(
            name=str(d.get("name", "")).strip(),
            slug=str(d.get("slug") or _slugify(str(d.get("name", "")))),
            kind=str(d.get("kind", "category")),
            description=str(d.get("description", "")),
            keywords=[str(k).lower() for k in (d.get("keywords") or [])],
            model_re=d.get("model_re"),
            children=[to_node(c) for c in (d.get("children") or [])],
        )

    return [to_node(t) for t in (raw.get("topics") or raw if isinstance(raw, list) else raw.get("topics", []))]


def _slugify(name: str) -> str:
    s = re.sub(r"[^\w]+", "-", name.lower().strip())
    return s.strip("-") or "topic"


# ---------------------------------------------------------------- DB sync

def ensure_topics(conn: sqlite3.Connection, nodes: list[TaxonNode],
                  parent_id: int | None = None, prefix: str = "") -> None:
    """Рекурсивно создать/обновить темы в таблице topics."""
    for node in nodes:
        slug = f"{prefix}{node.slug}" if prefix else node.slug
        row = conn.execute("SELECT id FROM topics WHERE slug=?", (slug,)).fetchone()
        if row:
            tid = row["id"]
            conn.execute(
                "UPDATE topics SET name=?, kind=?, description=? WHERE id=?",
                (node.name, node.kind, node.description, tid),
            )
        else:
            cur = conn.execute(
                "INSERT INTO topics(parent_id, name, slug, kind, description) VALUES (?,?,?,?,?)",
                (parent_id, node.name, slug, node.kind, node.description),
            )
            tid = cur.lastrowid
        ensure_topics(conn, node.children, tid, prefix=f"{slug}/")
    conn.commit()


def flatten(nodes: list[TaxonNode]) -> list[tuple[TaxonNode, list[str], list[str]]]:
    """[(узел, ключéвики узла+предков, регулярки моделей узла+предков)] — для скоринга раздела."""
    out: list[tuple[TaxonNode, list[str], list[str]]] = []

    def walk(ns, inherited_kw, inherited_re):
        for n in ns:
            kw = inherited_kw + n.keywords
            res = inherited_re + ([n.model_re] if n.model_re else [])
            out.append((n, kw, res))
            walk(n.children, kw, res)

    walk(nodes, [], [])
    return out
