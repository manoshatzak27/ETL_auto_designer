"""Normalization, tokenization and lemmatization helpers for Stage 0.

The one invariant worth calling out: :func:`normalize_for_exact_match` must
stay byte-for-byte equivalent to ``public.pipeline_normalize()`` in
``sql/10_pipeline_extensions.sql``. Stage 1 compares the Python-normalized
input against a Postgres index built on the SQL-normalized concept name; if the
two drift apart, exact matching silently stops finding anything.
"""

from __future__ import annotations

import re
import unicodedata

from . import resources

# ── character-level cleanup ─────────────────────────────────────────────

# Unicode variants that show up in exported column descriptions and would
# otherwise survive as their own tokens.
_CHAR_FOLD = {
    "µ": "u",   # MICRO SIGN
    "μ": "u",   # GREEK SMALL LETTER MU
    "°": " degree ",
    "≤": " <= ",
    "≥": " >= ",
    "≠": " != ",
    "²": "2",
    "³": "3",
    "–": "-", "—": "-", "−": "-",
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    " ": " ",
}

_DOTTED_ACRONYM = re.compile(r"\b(?:[a-z]\.){2,}", re.IGNORECASE)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+(?:[_][a-z0-9]+)*")


def fold_characters(text: str) -> str:
    """Replace unicode look-alikes with ASCII equivalents."""
    for src, dst in _CHAR_FOLD.items():
        text = text.replace(src, dst)
    return text


def strip_accents(text: str) -> str:
    """Match Postgres unaccent(): decompose, then drop combining marks."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_for_exact_match(text: str) -> str:
    """Mirror of SQL ``public.pipeline_normalize()``.

    lower -> unaccent -> every non-[a-z0-9] run becomes one space -> trim.
    """
    if not text:
        return ""
    lowered = strip_accents(fold_characters(text)).lower()
    return _NON_ALNUM.sub(" ", lowered).strip()


def split_identifier(name: str) -> str:
    """Turn a column identifier into words.

    ``systolic_bp_mmhg`` -> ``systolic bp mmhg``
    ``SystolicBPValue``  -> ``Systolic BP Value``

    Done before lowercasing, because camelCase boundaries are the only signal
    the original casing carries.
    """
    if not name:
        return ""
    spaced = _CAMEL_BOUNDARY.sub(" ", name)
    spaced = spaced.replace("_", " ").replace("-", " ")
    return _WHITESPACE.sub(" ", spaced).strip()


def collapse_dotted_acronyms(text: str) -> str:
    """``W.B.C.`` -> ``WBC`` so Step 0.2 can look the abbreviation up."""
    return _DOTTED_ACRONYM.sub(lambda m: m.group(0).replace(".", ""), text)


def tokenize(text: str) -> list[str]:
    """Split on anything that is not alphanumeric or a phrase-joining underscore."""
    return _TOKEN.findall(text.lower())


# ── lemmatization (Step 0.8) ────────────────────────────────────────────

class Lemmatizer:
    """Medical-leaning lemmatizer, not a stemmer.

    Uses spaCy when it is installed (``pip install spacy`` plus a model, or
    ``scispacy`` for the clinical pipeline). Otherwise falls back to an
    irregular-form table plus conservative suffix rules.

    The fallback is deliberately timid: over-stemming is far more damaging here
    than under-stemming, because a mangled rare token ("sepsi") destroys the
    IDF weight that makes Stage 3 work.
    """

    def __init__(self, spacy_model: str | None = None) -> None:
        self._forms, self._invariant = resources.irregular_lemmas()
        self._cache: dict[str, str] = {}
        self._nlp = None
        if spacy_model:
            try:  # pragma: no cover - depends on optional install
                import spacy

                self._nlp = spacy.load(spacy_model, disable=["parser", "ner"])
            except Exception:
                self._nlp = None

    @property
    def backend(self) -> str:
        return "spacy" if self._nlp is not None else "rules"

    def lemmatize(self, token: str) -> str:
        if self._nlp is not None:  # pragma: no cover - optional path
            doc = self._nlp(token)
            if len(doc):
                return doc[0].lemma_.lower()
        return self._rule_lemma(token)

    def _rule_lemma(self, token: str) -> str:
        # Instance-level cache rather than @lru_cache on a method, which would
        # keep every Lemmatizer alive for the life of the process.
        hit = self._cache.get(token)
        if hit is None:
            hit = self._rule_lemma_uncached(token)
            self._cache[token] = hit
        return hit

    def _rule_lemma_uncached(self, token: str) -> str:
        word = token.lower()

        # A joined protected phrase ("heart_failure") is never lemmatized:
        # Step 0.8 says protected phrases stay exactly as they are.
        if "_" in word:
            return word

        if word in self._forms:
            return self._forms[word]
        if word in self._invariant or len(word) <= 3 or word.isdigit():
            return word

        if word.endswith("ies") and len(word) > 4:
            return word[:-3] + "y"
        if word.endswith("sses") or word.endswith("shes") or word.endswith("ches"):
            return word[:-2]
        if word.endswith("xes") or word.endswith("zes"):
            return word[:-2]
        if word.endswith("oes") and len(word) > 4:
            return word[:-2]
        # Plain -s plural, but never -ss (mass), -us (corpus), -is (analysis).
        if word.endswith("s") and not word.endswith(("ss", "us", "is", "as", "os")):
            return word[:-1]
        return word

    def lemmatize_all(self, tokens: list[str]) -> list[str]:
        return [self.lemmatize(t) for t in tokens]


_DEFAULT_LEMMATIZER: Lemmatizer | None = None


def default_lemmatizer(spacy_model: str | None = None) -> Lemmatizer:
    """Process-wide lemmatizer, so a spaCy model is loaded at most once."""
    global _DEFAULT_LEMMATIZER
    if _DEFAULT_LEMMATIZER is None:
        _DEFAULT_LEMMATIZER = Lemmatizer(spacy_model)
    return _DEFAULT_LEMMATIZER


# ── misc ────────────────────────────────────────────────────────────────

def join_phrase(phrase: str) -> str:
    """``heart failure`` -> ``heart_failure`` (a single token downstream)."""
    return "_".join(phrase.split())


def split_phrase(token: str) -> str:
    """Inverse of :func:`join_phrase`."""
    return token.replace("_", " ")


def squash(text: str) -> str:
    """Collapse runs of whitespace and trim."""
    return _WHITESPACE.sub(" ", text).strip()


# ── similarity primitives (Stage 4) ─────────────────────────────────────

def trigrams(text: str) -> set[str]:
    """Trigram set, compatible with pg_trgm's ``show_trgm()``.

    pg_trgm splits on non-alphanumerics, pads each word with two leading and
    one trailing space, and takes every 3-character window. Reproducing that
    exactly means a similarity computed here and one computed by Postgres are
    the same number, so Stage 4 can mix the two without a scale mismatch.
    """
    grams: set[str] = set()
    for word in _NON_ALNUM.sub(" ", strip_accents(text).lower()).split():
        padded = f"  {word} "
        for i in range(len(padded) - 2):
            grams.add(padded[i:i + 3])
    return grams


def trigram_similarity(left: str, right: str) -> float:
    """pg_trgm ``similarity()``: |A ∩ B| / |A ∪ B| over trigram sets."""
    a, b = trigrams(left), trigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def levenshtein(left: str, right: str) -> int:
    """Edit distance. Two-row DP; the inputs here are short by construction."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, lch in enumerate(left, start=1):
        current = [i]
        for j, rch in enumerate(right, start=1):
            current.append(min(
                previous[j] + 1,           # deletion
                current[j - 1] + 1,        # insertion
                previous[j - 1] + (lch != rch),   # substitution
            ))
        previous = current
    return previous[-1]


def levenshtein_similarity(left: str, right: str) -> float:
    """Edit distance rescaled to 0..1 against the longer string."""
    longest = max(len(left), len(right))
    if longest == 0:
        return 1.0
    return 1.0 - levenshtein(left, right) / longest


def acronym_of(text: str) -> str:
    """Initials of a phrase: 'Heart rate' -> 'hr'.

    Step 4.2's real mechanism. Trigram similarity between "hr" and "heart
    rate" is near zero — an acronym shares almost no character windows with
    what it stands for — so catching an unexpanded abbreviation needs this
    rather than a string-similarity threshold.
    """
    words = _NON_ALNUM.sub(" ", strip_accents(text).lower()).split()
    return "".join(word[0] for word in words if word)
