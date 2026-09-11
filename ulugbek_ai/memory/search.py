"""Memory retrieval strategies.

Retrieval is pluggable on purpose. Today the default is deterministic keyword
scoring, which needs no extra infrastructure and is exactly reproducible in
tests. A future phase can add an embedding-backed strategy by implementing
:class:`MemorySearchStrategy` — nothing above this module has to change.
"""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from ulugbek_ai.core.utils import ensure_utc, utcnow
from ulugbek_ai.memory.models import Memory

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)

#: Extremely common words carry no retrieval signal.
_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "if", "then", "of", "to", "in",
        "on", "for", "with", "is", "are", "was", "were", "be", "been", "do",
        "does", "did", "it", "this", "that", "these", "those", "as", "at", "by",
        "from", "my", "me", "you", "we", "i",
        # Uzbek function words, since the operator writes in Uzbek.
        "va", "yoki", "uchun", "bilan", "ham", "bu", "shu", "men", "sen", "biz",
        "u", "qanday", "nima", "kerak",
    }
)

#: Ranking weights.
_TERM_WEIGHT = 1.0
#: Credit for a prefix match ("invoice" vs "invoices", "Railway" vs "Railwayda").
#: Uzbek is agglutinative, so suffixed forms are the common case, not an edge one.
_PREFIX_MATCH_CREDIT = 0.6
#: Shortest token length at which a prefix match is trustworthy rather than noise.
_MIN_PREFIX_LENGTH = 4
_TAG_WEIGHT = 1.5
_SUMMARY_WEIGHT = 0.5
_IMPORTANCE_WEIGHT = 2.0
_RECENCY_WEIGHT = 1.0
#: A memory scoring below this is not worth spending context on.
MIN_RELEVANCE_SCORE = 0.5
#: Half-life used for recency decay.
_RECENCY_HALFLIFE_DAYS = 30.0


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stopwords and 1-character noise removed."""
    return [
        token
        for token in (match.group(0).lower() for match in _WORD_RE.finditer(text))
        if len(token) > 1 and token not in _STOPWORDS
    ]


@dataclass(slots=True, order=True)
class ScoredMemory:
    """A memory with its relevance score (sorts by score)."""

    score: float
    memory: Memory


class MemorySearchStrategy(ABC):
    """Ranks candidate memories against a query."""

    @abstractmethod
    def rank(
        self, query: str, candidates: Sequence[Memory], *, limit: int
    ) -> list[ScoredMemory]:
        """Return the *limit* most relevant candidates, best first."""

    @abstractmethod
    def query_terms(self, query: str) -> list[str]:
        """Terms the repository should pre-filter on."""


class KeywordSearchStrategy(MemorySearchStrategy):
    """Term-overlap scoring with importance and recency boosts."""

    def query_terms(self, query: str) -> list[str]:
        # De-duplicated, order preserved, capped so the SQL stays small.
        seen: dict[str, None] = {}
        for token in tokenize(query):
            seen.setdefault(token, None)
        return list(seen)[:20]

    def rank(
        self, query: str, candidates: Sequence[Memory], *, limit: int
    ) -> list[ScoredMemory]:
        terms = set(self.query_terms(query))
        if not terms:
            return []

        now = utcnow()
        scored: list[ScoredMemory] = []

        for memory in candidates:
            content_tokens = set(tokenize(memory.content))
            score = _TERM_WEIGHT * _overlap(terms, content_tokens)

            if memory.summary:
                score += _SUMMARY_WEIGHT * _overlap(terms, set(tokenize(memory.summary)))

            tags = {tag.lower() for tag in memory.tags or []}
            score += _TAG_WEIGHT * _overlap(terms, tags)

            if score <= 0:
                continue

            score += _IMPORTANCE_WEIGHT * float(memory.importance or 0.0)
            score += _RECENCY_WEIGHT * self._recency_boost(memory, now)

            if score >= MIN_RELEVANCE_SCORE:
                scored.append(ScoredMemory(score=score, memory=memory))

        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[:limit]

    @staticmethod
    def _recency_boost(memory: Memory, now) -> float:
        """Exponential decay: a fresh memory scores ~1.0, an old one ~0."""
        created = ensure_utc(memory.created_at)
        if created is None:
            return 0.0
        age_days = max((now - created).total_seconds() / 86_400.0, 0.0)
        return math.exp(-age_days / _RECENCY_HALFLIFE_DAYS)


def _overlap(terms: set[str], tokens: set[str]) -> float:
    """Weighted count of *terms* present in *tokens*.

    An exact token match scores in full; a prefix match in either direction
    scores partially, so "invoice" still finds "invoices" and "Railway" still
    finds "Railwayda". This mirrors the ``ILIKE`` pre-filter the repository
    applies — without it the SQL would hand the ranker candidates it then scored
    at zero.
    """
    if not terms or not tokens:
        return 0.0

    total = 0.0
    for term in terms:
        if term in tokens:
            total += 1.0
            continue
        if any(_is_prefix_match(term, token) for token in tokens):
            total += _PREFIX_MATCH_CREDIT
    return total


def _is_prefix_match(term: str, token: str) -> bool:
    if min(len(term), len(token)) < _MIN_PREFIX_LENGTH:
        return False
    return token.startswith(term) or term.startswith(token)
