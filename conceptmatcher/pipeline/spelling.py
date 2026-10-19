"""Spelling correction against the vocabulary's own words (Stage 0).

A misspelt word breaks every lexical stage at once: Stage 2 finds no lexeme,
Stage 3 counts no overlap, and when the rest of the input still matches
("Serum bolirubin level") the typo is silently ignored and the answer is
whatever "Serum ... level" concept ranks highest. Correcting the word up front
repairs all of them, and the stages downstream then score a correctly spelled
query instead of being taught one by one to tolerate typos.

The checker is SymSpell (`symspellpy`), an external library, but the
dictionary is not: it is ``pipeline.word_freq``, every word of every standard
concept name and English synonym with its frequency (sql/14_pipeline_spelling.sql).
A general English dictionary does not know 'certolizumab' or 'bilirubin' and
would "correct" them into something worse.

Correction only ever ADDS an interpretation — Stage 0 keeps the input as typed
alongside the corrected copy, and Stage 6 keeps the better-scoring concept
across them. A wrong correction therefore costs one extra search pass, never
the original reading.

Optional, like Stage 5: without `symspellpy`, or with an empty word_freq
table, the checker reports itself unavailable once and corrects nothing.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .config import PipelineConfig
from .db import Database

log = logging.getLogger(__name__)


class SpellChecker:
    """Lazy SymSpell over pipeline.word_freq. Thread-safe; share one."""

    def __init__(self, config: PipelineConfig, db: Database) -> None:
        self.config = config
        self.db = db
        self._symspell: Any = None
        self._unavailable_reason: str | None = None
        self._lock = threading.Lock()
        self._cache: dict[str, str | None] = {}

    @property
    def unavailable_reason(self) -> str | None:
        self._load()
        return self._unavailable_reason

    def _load(self) -> Any:
        # Deferred to the first word that actually needs checking: building the
        # index costs ~7 s and ~450 MB, which a run over correctly spelled
        # input — or a `--help` — should never pay.
        if self._symspell is not None or self._unavailable_reason is not None:
            return self._symspell
        with self._lock:
            if self._symspell is not None or self._unavailable_reason is not None:
                return self._symspell
            try:
                from symspellpy import SymSpell
            except ImportError:
                self._unavailable_reason = "symspellpy is not installed"
                log.warning("spelling correction disabled: %s", self._unavailable_reason)
                return None
            try:
                rows = self.db.fetch_all("SELECT word, freq FROM pipeline.word_freq")
            except Exception as exc:
                self._unavailable_reason = f"pipeline.word_freq unreadable ({exc}); run scripts/setup-conceptmatcher.sh"
                log.warning("spelling correction disabled: %s", self._unavailable_reason)
                return None
            if not rows:
                self._unavailable_reason = "pipeline.word_freq is empty; run scripts/setup-conceptmatcher.sh"
                log.warning("spelling correction disabled: %s", self._unavailable_reason)
                return None
            symspell = SymSpell(max_dictionary_edit_distance=2, prefix_length=7)
            for row in rows:
                symspell.create_dictionary_entry(row["word"], int(row["freq"]))
            self._symspell = symspell
        return self._symspell

    def correct(self, word: str) -> str | None:
        """The vocabulary word this one is a misspelling of, or None.

        None when the word is known, too short to judge, or has no word within
        the allowed edit distance.

        "Known" has an exception, because the vocabulary carries misspellings of
        its own: 'creatnine' occurs once and 'posphate' 8 times, so trusting the
        dictionary outright left both uncorrected. A known word that is rare
        (at most spelling_rare_max occurrences) and sits one edit from a word
        spelling_rare_ratio times more frequent is treated as that word's typo.
        Everything else known is left alone — the vocabulary is full of
        legitimate one-off names, and the original is kept as an
        interpretation either way.
        """
        word = word.lower()
        # Stage 0 passes letter runs only, so length is the one thing left to
        # judge: at 4 letters one edit already reaches dozens of real words
        # ('lipa' -> 'lipid', 'lisa', 'lima'...).
        if len(word) < self.config.spelling_min_length:
            return None
        if word in self._cache:
            return self._cache[word]

        # Ask the table first. Almost every input word is a common, correctly
        # spelled one, and a primary-key lookup answers that without building
        # the SymSpell index — so a one-off `match` on clean text never pays
        # the ~7 s load, and only input with a real suspect in it does.
        known = self._frequency(word)
        if known is not None and known > self.config.spelling_rare_max:
            self._remember(word, None)
            return None

        symspell = self._load()
        if symspell is None:
            return None

        from symspellpy import Verbosity

        result: str | None = None
        if known is not None:
            if known <= self.config.spelling_rare_max:
                neighbours = [s for s in symspell.lookup(word, Verbosity.ALL,
                                                         max_edit_distance=1)
                              if s.distance == 1]
                if neighbours:
                    best = max(neighbours, key=lambda s: s.count)
                    if best.count >= self.config.spelling_rare_ratio * known:
                        result = best.term
        else:
            # One edit up to 7 letters, two beyond: a second edit on a short
            # word reaches too many unrelated real words to mean anything.
            distance = 1 if len(word) < self.config.spelling_two_edit_length else 2
            suggestions = symspell.lookup(word, Verbosity.CLOSEST,
                                          max_edit_distance=distance)
            # CLOSEST returns every suggestion at the smallest distance found,
            # most frequent first. Frequency is the tiebreak SymSpell is built
            # on: among equally close words, the common one is the likely intent.
            if suggestions:
                result = suggestions[0].term
        self._remember(word, result)
        return result

    def _frequency(self, word: str) -> int | None:
        if self._unavailable_reason is not None:
            return None
        if self._symspell is not None:
            return self._symspell.words.get(word)
        try:
            value = self.db.fetch_value(
                "SELECT freq FROM pipeline.word_freq WHERE word = %s", (word,))
        except Exception:
            return None          # _load() will report the table problem
        return int(value) if value is not None else None

    def _remember(self, word: str, result: str | None) -> None:
        with self._lock:
            if len(self._cache) > 50_000:
                self._cache.clear()
            self._cache[word] = result
