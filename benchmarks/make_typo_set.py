"""Generate typo_set.csv: misspelt copies of the gold-set and drug columns.

    .venv/bin/python benchmarks/make_typo_set.py

The output is committed rather than generated on the fly, so a change to this
script shows up as a reviewable diff of the cases instead of silently moving
the benchmark. Generation is deterministic (seeded per case and typo type).

Sources: the `literal` rows of gold_set.csv (the `semantic` rows already miss
on the clean text, so a typo in them measures nothing) and drug_set.csv.

Each case gets one copy per typo type, always in the longest word its
`require` rule names (falling back to the longest word) — that is the word
carrying the meaning ("creatinine", not "concentration"), and a typo in a
filler word would mostly measure whether the filler mattered. The
first letter is never touched: people almost always get it right, and keeping
it fixed is what lets a misspelling stay recognisable.

Typo types, one edit each unless named otherwise:
  transpose   two adjacent letters swapped           methotrexate -> metohtrexate
  delete      one letter dropped                     etanercept   -> etanrcept
  substitute  one letter -> a QWERTY neighbour       naproxen     -> naprocen
  double      one letter doubled                     abatacept    -> abattacept
  vowel       one vowel -> another vowel (phonetic)  certolizumab -> certolezumab
  two_edits   a transpose plus a substitute          infliximab   -> inflximiab
"""

from __future__ import annotations

import csv
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = [HERE / "gold_set.csv", HERE / "drug_set.csv"]
OUTPUT = HERE / "typo_set.csv"

TYPO_TYPES = ("transpose", "delete", "substitute", "double", "vowel", "two_edits")
VOWELS = "aeiou"
MIN_WORD = 5

_QWERTY = ["qwertyuiop", "asdfghjkl", "zxcvbnm"]


def _neighbours() -> dict[str, str]:
    """Keys physically adjacent on a QWERTY keyboard, same row and the rows
    above and below at the same and next position."""
    where = {ch: (r, c) for r, row in enumerate(_QWERTY) for c, ch in enumerate(row)}
    out: dict[str, str] = {}
    for ch, (r, c) in where.items():
        near = [other for other, (r2, c2) in where.items()
                if other != ch and abs(r2 - r) <= 1 and abs(c2 - c) <= 1]
        out[ch] = "".join(near)
    return out


NEIGHBOURS = _neighbours()


def load_rows(path: Path) -> list[dict]:
    lines = [l for l in path.read_text(encoding="utf-8").splitlines()
             if not l.startswith("#")]
    return list(csv.DictReader(lines))


def target_word(description: str, require: str) -> str | None:
    """The longest alphabetic word of MIN_WORD+ letters that is not an
    all-caps abbreviation, preferring words the `require` rule names.

    Longest alone picks "concentration" out of "Serum creatinine
    concentration" — a filler word. The require terms are by construction the
    words the right answer must contain, so they are the ones worth breaking.
    """
    words = [w for w in re.findall(r"[A-Za-z]+", description)
             if len(w) >= MIN_WORD and not w.isupper()]
    required = set(re.findall(r"[a-z]+", require.lower()))
    meaningful = [w for w in words if w.lower() in required]
    pool = meaningful or words
    return max(pool, key=len) if pool else None


# Each edit works on positions 1..n-1 so the first letter survives, and
# returns None when the word offers no position where it makes a change.
def transpose(word: str, rng: random.Random) -> str | None:
    spots = [i for i in range(1, len(word) - 1) if word[i] != word[i + 1]]
    if not spots:
        return None
    i = rng.choice(spots)
    return word[:i] + word[i + 1] + word[i] + word[i + 2:]


def delete(word: str, rng: random.Random) -> str | None:
    i = rng.randrange(1, len(word))
    return word[:i] + word[i + 1:]


def substitute(word: str, rng: random.Random) -> str | None:
    spots = [i for i in range(1, len(word)) if word[i].lower() in NEIGHBOURS]
    if not spots:
        return None
    i = rng.choice(spots)
    return word[:i] + rng.choice(NEIGHBOURS[word[i].lower()]) + word[i + 1:]


def double(word: str, rng: random.Random) -> str | None:
    i = rng.randrange(1, len(word))
    return word[:i] + word[i] + word[i:]


def vowel(word: str, rng: random.Random) -> str | None:
    spots = [i for i in range(1, len(word)) if word[i].lower() in VOWELS]
    if not spots:
        return None
    i = rng.choice(spots)
    return word[:i] + rng.choice(VOWELS.replace(word[i].lower(), "")) + word[i + 1:]


def two_edits(word: str, rng: random.Random) -> str | None:
    first = transpose(word, rng)
    return substitute(first, rng) if first else None


EDITS = {"transpose": transpose, "delete": delete, "substitute": substitute,
         "double": double, "vowel": vowel, "two_edits": two_edits}


def misspell(word: str, typo_type: str, seed: str) -> str | None:
    rng = random.Random(seed)
    for _ in range(20):             # retry the rare edit that is a no-op
        typo = EDITS[typo_type](word, rng)
        if typo and typo.lower() != word.lower():
            return typo
    return None


def main() -> int:
    cases = []
    for path in SOURCES:
        for row in load_rows(path):
            kind = (row.get("kind") or "literal").strip()
            if kind == "semantic":
                continue
            case_id = row["name"] or row["description"].lower().replace(" ", "_")
            cases.append({**row, "case": case_id,
                          "source": "drug" if kind == "drug" else "lab"})

    out_rows = []
    skipped = []
    for case in cases:
        word = target_word(case["description"], case["require"])
        if not word:
            skipped.append(case["case"])
            continue
        for typo_type in TYPO_TYPES:
            typo = misspell(word, typo_type, f"{case['case']}:{typo_type}")
            if not typo:
                skipped.append(f"{case['case']}:{typo_type}")
                continue
            out_rows.append({
                "case": case["case"],
                "source": case["source"],
                "typo_type": typo_type,
                "word": word,
                "typo": typo,
                "clean_description": case["description"],
                "description": case["description"].replace(word, typo, 1),
                "require": case["require"],
                "forbid": case.get("forbid") or "",
            })

    header = ("# Generated by make_typo_set.py — edit that script, not this file.\n"
              "# One misspelling per (case, typo_type), in the longest word of the\n"
              "# description. Graded by the same name rule as gold_set.csv.\n")
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        handle.write(header)
        writer = csv.DictWriter(handle, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"{len(cases)} cases -> {len(out_rows)} typo rows in {OUTPUT.name}")
    if skipped:
        print(f"skipped (no eligible word or edit): {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
