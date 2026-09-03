"""Loaders for the Stage 0 dictionaries in ``pipeline/resources/``.

Everything is read once and cached at module level; the dictionaries are small
(a few hundred KB) and are shared by every worker thread in a batch run.

Each shipped file may be shadowed by a ``*_local.*`` sibling that is merged
over the top, so site-specific abbreviations and phrases can be added without
touching the defaults or fighting a future update.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import RESOURCE_DIR


def _read_json(name: str) -> dict:
    path = RESOURCE_DIR / name
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _strip_comments(data: dict) -> dict:
    return {k: v for k, v in data.items() if not k.startswith("_")}


@lru_cache(maxsize=1)
def abbreviations() -> dict[str, list[str]]:
    """Step 0.2: abbreviation -> list of expansions (>1 entry == ambiguous)."""
    merged: dict[str, list[str]] = {}
    for name in ("abbreviations.json", "abbreviations_local.json"):
        for key, value in _strip_comments(_read_json(name)).items():
            expansions = value if isinstance(value, list) else [value]
            merged[key.lower()] = [str(e).lower() for e in expansions]
    return merged


@lru_cache(maxsize=1)
def unit_table() -> dict[str, dict[str, str]]:
    """Step 0.3: normalized unit token -> {type, expansion}."""
    merged: dict[str, dict[str, str]] = {}
    for name in ("units.json", "units_local.json"):
        data = _read_json(name)
        merged.update(_strip_comments(data.get("units", {})))
    return merged


@lru_cache(maxsize=1)
def expected_unit_types() -> dict[str, list[str]]:
    """Step 7.2: concept-name keyword -> plausible unit types."""
    merged: dict[str, list[str]] = {}
    for name in ("units.json", "units_local.json"):
        data = _read_json(name)
        merged.update(_strip_comments(data.get("expected_unit_types", {})))
    return merged


@lru_cache(maxsize=1)
def protected_phrases() -> tuple[str, ...]:
    """Step 0.6: multi-word phrases, longest first so the longest match wins."""
    phrases: set[str] = set()
    for name in ("phrases.txt", "phrases_local.txt"):
        path = RESOURCE_DIR / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip().lower()
            if line and not line.startswith("#"):
                phrases.add(" ".join(line.split()))
    return tuple(sorted(phrases, key=lambda p: (-len(p.split()), -len(p), p)))


@lru_cache(maxsize=1)
def stopwords() -> dict[str, frozenset[str]]:
    """Step 0.7: the four stopword buckets, already de-duplicated."""
    buckets: dict[str, set[str]] = {
        "generic": set(), "metadata": set(),
        "structural": set(), "corpus_adaptive": set(), "keep_always": set(),
    }
    for name in ("stopwords.json", "stopwords_local.json"):
        for key, values in _strip_comments(_read_json(name)).items():
            if key in buckets:
                buckets[key].update(str(v).lower() for v in values)

    # keep_always vetoes every removal list — the spec is explicit that
    # domain-bearing words must survive even if someone lists them as noise.
    keep = buckets["keep_always"]
    for key in ("generic", "metadata", "structural", "corpus_adaptive"):
        buckets[key] -= keep
    return {k: frozenset(v) for k, v in buckets.items()}


@lru_cache(maxsize=1)
def irregular_lemmas() -> tuple[dict[str, str], frozenset[str]]:
    """Step 0.8: (irregular form -> lemma, words that must not be de-pluralized)."""
    forms: dict[str, str] = {}
    invariant: set[str] = set()
    for name in ("irregular_lemmas.json", "irregular_lemmas_local.json"):
        data = _read_json(name)
        forms.update({k.lower(): str(v).lower()
                      for k, v in data.get("forms", {}).items()})
        invariant.update(str(w).lower() for w in data.get("invariant", []))
    return forms, frozenset(invariant)


def reload_all() -> None:
    """Drop the caches — used after the feedback loop writes new entries."""
    for fn in (abbreviations, unit_table, expected_unit_types,
               protected_phrases, stopwords, irregular_lemmas):
        fn.cache_clear()


def resource_path(name: str) -> Path:
    return RESOURCE_DIR / name
