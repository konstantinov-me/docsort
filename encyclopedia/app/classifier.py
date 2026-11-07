"""Классификация раздела текста → темы таксономии с оценкой уверенности.

Алгоритм:
  1. Токенизируем заголовок и тело (нижний регистр).
  2. Для каждой темы считаем score = Σ вхождений её ключевиков (в теле ×1,
     в заголовке ×5) + бонус за совпадение model_re (+10).
  3. Нормализуем в уверенность conf = score / (score + k), k=8 → асимптотически к 1.
  4. Возвращаем топ-N тем; main тема — самая глубокая в дереве из тех, что выше порога
     (подтема-устройство предпочитается родительской категории).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .taxonomy import TaxonNode, flatten

WORD_RE = re.compile(r"[\w]+", re.UNICODE)


@dataclass
class TopicScore:
    node: TaxonNode
    raw: float
    confidence: float


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in WORD_RE.findall(text)]


def classify(title: str, body: str, roots: list[TaxonNode],
             top_n: int = 3, k: float = 8.0) -> list[TopicScore]:
    low_title = title.lower()
    low_body = body.lower()
    title_toks = _tokens(title)      # считаем один раз, а не на каждый ключевик
    body_toks = _tokens(body)
    results: list[TopicScore] = []

    for node, keywords, model_res in flatten(roots):
        score = 0.0
        for kw in keywords:
            # точное совпадение токенов (после unicode61-токенизации дефис не является
            # разделителем, поэтому "сплит-система" остаётся одним токеном)
            toks = _tokens(kw)
            if not toks:
                continue
            t0 = toks[0]
            score += 5 * sum(1 for t in title_toks if t == t0)
            score += 1 * sum(1 for t in body_toks if t == t0)
        for rex in model_res:
            try:
                if re.search(rex, low_body) or re.search(rex, low_title):
                    score += 10
            except re.error:
                pass
        if score > 0:
            results.append(TopicScore(node=node, raw=score, confidence=min(score / (score + k), 1.0)))

    results.sort(key=lambda r: (-r.confidence, -_depth(r.node, roots)))
    return results[:top_n]


def _depth(node: TaxonNode, roots: list[TaxonNode]) -> int:
    def walk(ns, d):
        for n in ns:
            if n is node:
                yield d
            yield from walk(n.children, d + 1)

    return max(walk(roots, 0), default=0)
