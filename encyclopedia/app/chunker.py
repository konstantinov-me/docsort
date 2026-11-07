"""Разбиение текстовых файлов на разделы (будущие статьи) + детект кодировки.

Эвристики заголовков раздела:
  * Markdown ATX:        "# Заголовок", "## Подзаголовок"
  * Выделители:          "=== текст ===", "--- ниже", "***"
  * Ключевые слова:      "Тема:", "Глава N.", "Раздел:", "Topic:", "Статья:"
  * Строка-заголовок: короткая (<80 симв.), без точки в конце, следующая строка — абзац.

Если файл не разбивается — весь файл становится одним разделом.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HEAD_RE = re.compile(
    r"^\s*(?:#{1,4}\s+|(?:={2,}|-{3,}|\*{3,})\s*)?"
    r"(?:Тема|Глава\s*\d+|Раздел|Статья|Topic|Chapter|Section)\s*[:.\s]\s*(?P<t1>.+)$"
    r"|^\s*(?:={3,}|_{3,}|\*{3,})\s*$",
    re.IGNORECASE | re.MULTILINE,
)

ATX_RE = re.compile(r"^#{1,4}\s+(?P<t>\S.*?)\s*$", re.MULTILINE)
# "=== Заголовок ===" (равные или больше с обеих сторон), а также одиночные
# строки-разделители "=====" без заголовка.
BANNER_RE = re.compile(r"^={2,}\s*(?P<t>[^=\n]{1,120}?)\s*={2,}$", re.MULTILINE)
SEP_LINE_RE = re.compile(r"^(?:={3,}|_{3,}|\*{3,}|-{3,})\s*$", re.MULTILINE)


@dataclass
class Section:
    title: str
    body: str


def decode(raw: bytes) -> tuple[str, str]:
    """Подобрать кодировку: BOM → utf-8-sig, затем utf-8, cp1251, latin-1."""
    for enc in ("utf-8-sig", "utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _looks_like_heading(line: str, nxt: str) -> bool:
    s = line.strip()
    if not (2 <= len(s) <= 90):
        return False
    if s.endswith((".", ",", ";", ":")) and not re.match(r"^(Тема|Глава|Раздел)", s, re.I):
        return False
    if not nxt.strip():
        return False
    # заголовок обычно без знаков препинания внутри и с большой буквы/цифры
    return bool(re.match(r"^[А-ЯA-Z0-9]", s)) and len(nxt.strip()) > 20


def split_sections(text: str) -> list[Section]:
    """Вернуть список разделов (title, body). Пустой ввод → []."""
    marks: list[tuple[int, str]] = []   # (позиция начала тела, заголовок)

    def add(m_iter, group):
        for m in m_iter:
            t = (m.group(group) or "").strip() if isinstance(m.group(group), str) else ""
            pos = m.start()
            # тело начинается после строки заголовка
            eol = text.find("\n", pos)
            marks.append((eol + 1 if eol != -1 else len(text), t or "Без названия"))

    add(ATX_RE.finditer(text), "t")
    add(BANNER_RE.finditer(text), "t")
    for m in HEAD_RE.finditer(text):
        t = (m.groupdict().get("t1") or "").strip()
        eol = text.find("\n", m.start())
        marks.append((eol + 1 if eol != -1 else len(text), t or "Без названия"))

    # эвристика коротких строк-заголовков (только если в файле ещё нет явных маркеров)
    if not marks:
        lines = text.splitlines(keepends=True)
        offset = 0
        for i, ln in enumerate(lines):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if _looks_like_heading(ln, nxt):
                end = offset + len(ln)
                marks.append((end, ln.strip()))
            offset += len(ln)

    # убрать дубликаты/пересечения: если заголовок «покрывается» предыдущим
    # (начинается раньше, а тело стартует позже или там же) — отбросить слабый.
    marks = sorted(set(marks), key=lambda x: (x[0], len(x[1])))
    deduped: list[tuple[int, str]] = []
    for pos, title in marks:
        while deduped and deduped[-1][0] <= pos:
            deduped.pop()          # предыдущий маркер «съедает» этот
        deduped.append((pos, title))
    marks = deduped
    if not marks:
        body = text.strip()
        return [Section("Без названия", body)] if body else []

    sections: list[Section] = []
    first_pos = marks[0][0]
    preamble = text[:first_pos].strip()
    if preamble:
        sections.append(Section("Введение", preamble))
    for idx, (pos, title) in enumerate(marks):
        end = marks[idx + 1][0] if idx + 1 < len(marks) else len(text)
        body = text[pos:end].strip()
        if body or title:
            sections.append(Section(title, body))
    # склеить соседние пустые
    return [s for s in sections if s.body] or [Section("Без названия", text.strip())]
