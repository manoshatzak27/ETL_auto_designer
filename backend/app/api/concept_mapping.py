"""
API routes for the concept mapping step (Concepts step).

Endpoints:
  GET  /projects/concept-lookup              → look up domain for a concept_id in CONCEPT.csv
  GET  /projects/concept-matcher/health      → readiness of the bulk matching pipeline
  POST /projects/{id}/match-concepts         → map a list of source columns to concepts in bulk
  POST /projects/{id}/match-values           → map a column's distinct values, one domain per column
  POST /projects/{id}/suggest-value-concepts → suggest a concept per value within a known domain
  GET  /projects/{id}/search-concepts        → free-text concept search within one domain
  GET  /projects/{id}/column-descriptions    → the project's data dictionary
  PUT  /projects/{id}/column-descriptions    → replace it (also used for single-column edits)
  POST /projects/{id}/column-descriptions/upload   → merge in an uploaded CSV/Excel dictionary
  GET  /projects/{id}/column-descriptions/template → a pre-filled CSV to fill in and upload back
  GET  /projects/{id}/column-values          → unique values per source column
  GET  /projects/{id}/concept-decisions       → load saved decisions
  POST /projects/{id}/concept-decisions       → save decisions (full replace)
  POST /projects/{id}/generate-mapping-csvs  → generate the 3 CSVs from decisions
  GET  /projects/{id}/download-mapping-files → download the generated mapping CSVs as a zip
  GET  /projects/{id}/download-mapping-summary → download a human-readable Excel summary of all decisions
"""
import csv
import re
import io
import shutil
import tempfile
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import pandas as pd

from app.database import get_db
from app.models.project import Project
from app.schemas.project import ProjectResponse
from app.services.column_descriptions import parse_descriptions
from app.services.mapping_generator import generate_mapping_csvs, generate_mapping_summary_excel
from app.config import settings

router = APIRouter(prefix="/projects", tags=["concept-mapping"])

# ── Concept lookup (vocab.concept) ──────────────────────────────────────────

# Deliberately uncached: the vocabulary can be (re)loaded while the backend
# runs, and a cached answer would outlive it — a concept looked up before the
# load would stay "not found", and one deprecated by a newer vocabulary would
# stay standard/valid. A primary-key lookup is cheap enough to do every time.
def _get_concept_info(concept_id: int) -> "tuple[str, str, str | None, str | None] | None":
    """Look up (domain_id, concept_name, standard_concept, invalid_reason) for an OMOP concept by
    querying the loaded vocabulary in Postgres. Returns None when the concept
    genuinely isn't there. Raises on connection/query errors instead of
    swallowing them, so callers can tell "not found" from "couldn't check"."""
    if concept_id is None or concept_id <= 0:
        return None
    from app.services.db import connect
    from psycopg2 import sql as pgsql

    schema = settings.omop_vocab_schema or "vocab"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT domain_id, concept_name, standard_concept, invalid_reason FROM {schema}.concept WHERE concept_id = %s"
                ).format(schema=pgsql.Identifier(schema)),
                (int(concept_id),),
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return (
                    str(row[0]),
                    str(row[1]) if row[1] is not None else "",
                    str(row[2]) if row[2] is not None else None,
                    # Loader stores '' as NULL, but normalise in case a vocab was loaded another way.
                    (str(row[3]).strip() or None) if row[3] is not None else None,
                )
            return None


def _get_concept_names(concept_ids: "set[int]") -> dict[int, str]:
    """concept_id → concept_name for many concepts in one query. Ids missing
    from the vocabulary are simply absent. Raises on connection/query errors."""
    ids = sorted(cid for cid in concept_ids if cid > 0)
    if not ids:
        return {}
    from app.services.db import connect
    from psycopg2 import sql as pgsql

    schema = settings.omop_vocab_schema or "vocab"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT concept_id, concept_name FROM {schema}.concept WHERE concept_id = ANY(%s)"
                ).format(schema=pgsql.Identifier(schema)),
                (ids,),
            )
            return {int(cid): str(name) for cid, name in cur.fetchall() if name is not None}


@router.get("/concept-lookup/domain")
def concept_lookup(concept_id: int):
    """Return the domain_id, concept_name and standard_concept flag for a given
    concept_id by querying the loaded OMOP vocabulary in Postgres (vocab.concept).
    standard_concept is 'S' for standard concepts, 'C' for classification
    concepts, and null/None for non-standard concepts — per OMOP convention.
    invalid_reason is null for valid concepts, 'D' (deleted) or 'U' (upgraded)
    otherwise. vocab_available is False when the lookup couldn't run at all, so callers can
    tell "this concept doesn't exist" apart from "couldn't check".
    """
    try:
        info = _get_concept_info(concept_id)
    except Exception as exc:
        # Vocab schema/table missing or Postgres unreachable.
        print(f"[concept-lookup] vocab.concept query failed: {exc}")
        return {"concept_id": concept_id, "domain_id": None, "concept_name": None, "standard_concept": None, "invalid_reason": None, "found": False, "vocab_available": False}
    if info:
        domain, concept_name, standard_concept, invalid_reason = info
        return {"concept_id": concept_id, "domain_id": domain, "concept_name": concept_name, "standard_concept": standard_concept, "invalid_reason": invalid_reason, "found": True, "vocab_available": True}
    return {"concept_id": concept_id, "domain_id": None, "concept_name": None, "standard_concept": None, "invalid_reason": None, "found": False, "vocab_available": True}


# ── Column descriptions (the project's data dictionary) ─────────────────────


def _project_columns(project: Project) -> list[tuple[str, str]]:
    """Every source column as (column, file), in file order, first spelling wins.

    Column names are treated as globally unique throughout the wizard —
    concept_decisions is keyed by them — so a name appearing in two files is one
    column here too, attributed to the first file that declares it.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in (project.source_files or []):
        filename = entry.get("filename") or ""
        for col in (entry.get("columns") or []):
            if col not in seen:
                seen.add(col)
                out.append((col, filename))
    for col in (project.source_columns or []):
        if col not in seen:
            seen.add(col)
            out.append((col, project.source_filename or ""))
    return out


class ColumnDescriptionsPayload(BaseModel):
    descriptions: dict[str, str]


@router.get("/{project_id}/column-descriptions")
def get_column_descriptions(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"descriptions": project.column_descriptions or {}}


@router.put("/{project_id}/column-descriptions")
def put_column_descriptions(
    project_id: str,
    payload: ColumnDescriptionsPayload,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Replace the whole dictionary. Also how a single description is edited or
    cleared — the map is small enough that a full replace is simpler than a
    patch, and it makes "clear all" the same code path as an empty upload."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    cleaned = {
        str(col): str(text).strip()
        for col, text in (payload.descriptions or {}).items()
        if str(text or "").strip()
    }
    project.column_descriptions = cleaned
    db.commit()
    return {"descriptions": cleaned}


@router.post("/{project_id}/column-descriptions/upload")
async def upload_column_descriptions(
    project_id: str,
    file: UploadFile = File(...),
    replace: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Merge an uploaded data dictionary (CSV/TSV or Excel) into the project.

    The file needs a header row naming a column and a description — several
    spellings of each are accepted, see `services/column_descriptions.py`. Rows
    whose column isn't in the project come back in `unmatched` rather than being
    dropped quietly, since a near-miss in a column name is the usual cause and
    the user is the only one who can resolve it.

    `replace=true` discards the existing dictionary first; the default merges,
    with the uploaded file winning on conflicts.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    known = [col for col, _ in _project_columns(project)]
    if not known:
        raise HTTPException(
            status_code=400,
            detail="This project has no source columns yet — upload a source file first.",
        )

    suffix = Path(file.filename or "upload.csv").suffix.lower() or ".csv"
    tmp_path: Path | None = None
    try:
        # Spooled to disk because delimiter/encoding sniffing and pandas both
        # want a real path, and a data dictionary is small.
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            shutil.copyfileobj(file.file, tmp)
            tmp_path = Path(tmp.name)
        parsed = parse_descriptions(tmp_path, known)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't read that file: {exc}")
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    existing: dict[str, str] = {} if replace else dict(project.column_descriptions or {})
    existing.update(parsed["descriptions"])
    project.column_descriptions = existing
    db.commit()

    described = set(existing)
    return {
        "descriptions": existing,
        "matched": parsed["matched"],
        "unmatched": parsed["unmatched"],
        "headers": parsed["headers"],
        # Columns still without a description, so the UI can say what's left.
        "missing": [col for col in known if col not in described],
    }


@router.get("/{project_id}/column-descriptions/template")
def column_descriptions_template(project_id: str, db: Session = Depends(get_db)):
    """A CSV of every source column, ready to fill in and upload back.

    Pre-filled with any description the project already has, so the template
    doubles as an export of the current dictionary.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    descriptions = project.column_descriptions or {}
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "table", "description"])
    for col, filename in _project_columns(project):
        writer.writerow([col, filename, descriptions.get(col, "")])

    # utf-8-sig: Excel opens a plain utf-8 CSV as mojibake, and this file exists
    # to be opened in Excel.
    data = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{project_id}_column_descriptions.csv"'},
    )


# ── Bulk concept matching (the "new concept finding" pipeline) ──────────────

# The matcher is the staged pipeline from the sibling project, behind HTTP
# (pipeline/service.py there, the `conceptmatcher` compose service here). It is
# a different thing from EntityLinker: EntityLinker answers "what concepts look
# like this phrase?" one query at a time for the AI Search box, while this maps
# a whole column list in one pass and returns a decision per column
# (auto_accept / review / unmapped) rather than a ranked list.


class MatchColumn(BaseModel):
    name: str
    description: str | None = None
    table: str | None = None


class MatchConceptsPayload(BaseModel):
    columns: list[MatchColumn]
    # Scopes any manual corrections recorded on the pipeline side; defaults to
    # the project id so one project's overrides never leak into another's.
    source_system: str | None = None
    candidates: int = 5
    # Restricts this run's candidates to these OMOP domains. None leaves the
    # matcher on whatever it was configured with; [] lifts the restriction.
    domains: list[str] | None = None


async def _post_match(
    columns: list[dict[str, Any]],
    source_system: str,
    note: str,
    candidates: int,
    domains: list[str] | None = None,
    shortlist: bool = False,
) -> dict[str, Any]:
    """One POST to the matcher's /match, with its errors translated to ours.

    `shortlist=True` asks the matcher to keep ranking alternatives behind an
    exact-name hit instead of returning that one concept alone — for callers
    that show a pick-list. The decision itself is unchanged by it. A matcher
    that predates the flag ignores the key and behaves as before.

    Shared by both matching endpoints so the domain-restricted value passes
    below reach the pipeline exactly the way a plain column run does.
    """
    import httpx

    body: dict[str, Any] = {
        "columns": columns,
        "source_system": source_system,
        "note": note,
        "candidates": candidates,
    }
    # Absent and empty mean different things to the matcher, so the key is only
    # sent when the caller actually asked for a restriction.
    if domains is not None:
        body["domains"] = domains
    if shortlist:
        body["shortlist"] = True

    try:
        async with httpx.AsyncClient(timeout=settings.concept_matcher_timeout) as client:
            resp = await client.post(
                f"{settings.concept_matcher_url.rstrip('/')}/match", json=body,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        # The matcher answers 400 for a malformed column list or an unknown
        # domain name, and 503 when the vocabulary database is down — pass its
        # own reason through rather than flattening both into a generic failure.
        detail = exc.response.text
        try:
            detail = exc.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Concept matcher unavailable: {exc}")


@router.get("/concept-matcher/health")
async def concept_matcher_health():
    """Whether the bulk matcher is reachable and its vocabulary is loaded.

    Never raises: the Concepts step calls this on mount purely to decide whether
    to enable its "Load concepts" button, so an unreachable matcher is a normal
    answer ("available": false), not an error.
    """
    if not settings.concept_matcher_url:
        return {"available": False, "detail": "CONCEPT_MATCHER_URL is not configured"}

    import httpx

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{settings.concept_matcher_url.rstrip('/')}/health")
            resp.raise_for_status()
            info = resp.json()
    except Exception as exc:
        return {"available": False, "detail": f"Concept matcher unavailable: {exc}"}

    return {
        "available": bool(info.get("ready")),
        "detail": "; ".join(info.get("problems") or []) or None,
        **{k: info.get(k) for k in (
            "vocabulary_version", "concepts", "embeddings", "embedding_model",
            "auto_accept_threshold", "review_threshold",
        )},
    }


@router.post("/{project_id}/match-concepts")
async def match_concepts(
    project_id: str,
    payload: MatchConceptsPayload,
    db: Session = Depends(get_db),
):
    """Map a list of source columns onto standard OMOP concepts in one pass.

    Any column that arrives without a description is given the project's, from
    the uploaded data dictionary. Filling it in here rather than at the caller
    makes the dictionary authoritative: every path into the matcher gets the
    same input, and the frontend cannot forget to send it.

    Results come back in request order and each one echoes `column_name` and
    `source_table`, so the caller can tie a result to its row even when the same
    column name appears in more than one source file.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not settings.concept_matcher_url:
        raise HTTPException(status_code=503, detail="CONCEPT_MATCHER_URL is not configured")
    if not payload.columns:
        return {"run_id": 0, "results": []}

    described = project.column_descriptions or {}
    columns = []
    for column in payload.columns:
        entry = column.model_dump()
        if not (entry.get("description") or "").strip():
            entry["description"] = described.get(entry["name"]) or None
        columns.append(entry)

    return await _post_match(
        columns,
        payload.source_system or project_id,
        f"ETL Auto-Designer project {project_id}",
        payload.candidates,
        payload.domains,
    )


# ── Bulk value matching (the same pipeline, one domain per column) ───────────

# A value concept is only ever routed through the stem table, which knows these
# five domains — so the domain a column's values are forced into has to be one
# of them for the result to be usable downstream (see ValueConceptRow in the
# Concepts step, which rejects anything else when a value is mapped by hand).
STEM_DOMAINS = ("Measurement", "Observation", "Drug", "Procedure", "Condition")

# Values of one column per matcher request. The matcher caps a request at 2000
# columns, and a narrower batch also keeps its corpus-adaptive stopwords (which
# are computed over the whole batch) from being derived from an unwieldy one.
VALUE_CHUNK = 500


def _value_domain(result: dict[str, Any]) -> str | None:
    """The OMOP domain a value's selected concept sits in, if one was selected."""
    if result.get("error") or not result.get("concept_id"):
        return None
    return result.get("domain_id") or None


class MatchValuesPayload(BaseModel):
    # The columns whose distinct values should be matched. The values themselves
    # are read from the source files here rather than posted, so the caller
    # doesn't have to have loaded every file's values to run this.
    columns: list[MatchColumn]
    source_system: str | None = None
    candidates: int = 5
    # Values per column, in source order. A column with more distinct values
    # than this is a poor "map values" candidate anyway, and matching thousands
    # of them would dominate the run.
    max_values: int = 500


@router.post("/{project_id}/match-values")
async def match_values(
    project_id: str,
    payload: MatchValuesPayload,
    db: Session = Depends(get_db),
):
    """Map each listed column's distinct values onto concepts, one domain per column.

    Two passes per column, which is what a column mapped by value needs and what
    a single /match-concepts run cannot express: every value of one variable has
    to land in the same OMOP domain (they all become rows of the same stem-table
    domain), but which domain that is only becomes apparent once the values have
    been looked up. So pass 1 matches every value unrestricted and counts the
    domains that come back; the most frequent one — among the five stem domains
    if any of them appear at all — wins. Pass 2 then discards every concept
    outside that domain and searches those values again with the matcher
    restricted to it, so a value whose best overall match was off-domain still
    gets the best in-domain one instead of being dropped.

    Per column the response carries the chosen domain, the vote it came from, and
    one result per value in the matcher's own shape, keyed by the source value.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not settings.concept_matcher_url:
        raise HTTPException(status_code=503, detail="CONCEPT_MATCHER_URL is not configured")
    if not payload.columns:
        return {"columns": []}

    described = project.column_descriptions or {}
    all_values = _all_distinct_values(project, max_values=payload.max_values)
    source_system = payload.source_system or project_id
    note = f"ETL Auto-Designer project {project_id} (values)"

    async def match_values_of(
        column: MatchColumn, values: list[str], domains: list[str] | None,
    ) -> list[dict[str, Any]]:
        """One pass over `values`, in order, chunked. The value is the term to
        match; the column's description (or its name) rides along as the context
        that tells the pipeline what kind of value it is looking at."""
        context = (column.description or "").strip() or described.get(column.name) or column.name
        results: list[dict[str, Any]] = []
        for start in range(0, len(values), VALUE_CHUNK):
            chunk = values[start:start + VALUE_CHUNK]
            answer = await _post_match(
                [{"name": v, "description": context, "table": column.table} for v in chunk],
                source_system, note, payload.candidates, domains,
            )
            # Results come back in request order, so they are paired positionally
            # rather than by name — the matcher strips and normalizes what it is
            # given, and a value is not guaranteed to survive that unchanged.
            answered = answer.get("results") or []
            if len(answered) != len(chunk):
                # One result per term is the contract pairing relies on, so a
                # mismatch is reported rather than silently misaligning values.
                raise HTTPException(
                    status_code=502,
                    detail=(f"Matcher returned {len(answered)} results for {len(chunk)} "
                            f"values of {column.name}"),
                )
            results.extend(answered)
        return results

    columns_out: list[dict[str, Any]] = []

    for column in payload.columns:
        values = all_values.get(column.name) or []
        entry: dict[str, Any] = {
            "column_name": column.name,
            "source_table": column.table,
            "values_requested": len(values),
            "domain": None,
            "domain_votes": {},
            "rematched": 0,
            "results": {},
        }
        if not values:
            columns_out.append(entry)
            continue

        first = await match_values_of(column, values, None)
        votes = Counter(d for d in (_value_domain(r) for r in first) if d)
        entry["domain_votes"] = dict(votes)
        stem_votes = Counter({d: n for d, n in votes.items() if d in STEM_DOMAINS})
        # most_common is insertion-ordered on ties, and insertion order here is
        # the order the values appear in the source — so a tie goes to whichever
        # domain the column's first values pointed at.
        winner = (stem_votes or votes).most_common(1)[0][0] if votes else None
        entry["domain"] = winner

        results = dict(zip(values, first))
        if winner:
            # Pass 2: everything that didn't land in the winning domain is thrown
            # away and searched again with the matcher confined to that domain.
            stale = [v for v, r in results.items() if _value_domain(r) != winner]
            if stale:
                results.update(zip(stale, await match_values_of(column, stale, [winner])))
                entry["rematched"] = len(stale)
            # A candidate outside the chosen domain is not an option for this
            # column, so it is dropped from the shortlists the caller offers.
            for result in results.values():
                result["candidates"] = [
                    c for c in (result.get("candidates") or []) if c.get("domain_id") == winner
                ]

        entry["results"] = results
        columns_out.append(entry)

    return {"columns": columns_out}


# ── Domain-bound value suggestions and search (table steps) ─────────────────

# The table steps (person's gender/race/ethnicity, and later the others) map a
# column's values into one domain that is known up front, unlike /match-values
# which has to discover it. These two endpoints are that case: the value is
# normalized for its domain (concept_normalizer), then matched with the matcher
# confined to the domain. No description is sent — for short demographic terms
# the column's description only drags the score down ("Greek" alone is an exact
# match; "Greek" described as "ethnicity" is not).


# OHDSI convention for person.race_concept_id: one of these five top-level
# categories, with the original value kept in race_source_value. The Race
# domain has ~1400 standard concepts, and a text match on "Black" lands on the
# narrower 38003598 rather than 8516 — valid, but a `race_concept_id = 8516`
# filter would miss it. So auto-fill rolls matches up to these five.
TOP_LEVEL_RACES = {
    8527: "White",
    8516: "Black or African American",
    8515: "Asian",
    8657: "American Indian or Alaska Native",
    8557: "Native Hawaiian or Other Pacific Islander",
}


@lru_cache(maxsize=1)
def _race_rollup() -> dict[int, int]:
    """Descendant concept_id → its top-level race, from vocab.concept_ancestor.

    Only ~45 concepts sit under the five categories; the rest of the Race
    domain (e.g. "Black African", "White Roma") has no top-level ancestor at
    all. Raises when the vocabulary isn't loaded, so the failure isn't cached.
    """
    from app.services.db import connect
    from psycopg2 import sql as pgsql

    schema = settings.omop_vocab_schema or "vocab"
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT descendant_concept_id, ancestor_concept_id FROM {schema}.concept_ancestor "
                    "WHERE ancestor_concept_id = ANY(%s) AND descendant_concept_id <> ancestor_concept_id"
                ).format(schema=pgsql.Identifier(schema)),
                (list(TOP_LEVEL_RACES),),
            )
            return {int(d): int(a) for d, a in cur.fetchall()}


def _roll_up_race(entry: dict[str, Any]) -> None:
    """Move a race suggestion onto its top-level category, in place.

    A concept with no top-level ancestor (or when the ancestry can't be read)
    is demoted to a suggestion: still offered, never filled in on its own.
    """
    concept_id = entry.get("concept_id")
    if not concept_id or concept_id in TOP_LEVEL_RACES:
        return
    try:
        top = _race_rollup().get(concept_id)
    except Exception as exc:
        print(f"[suggest-value-concepts] concept_ancestor query failed: {exc}")
        top = None
    if top:
        entry["detailed"] = {"concept_id": concept_id, "concept_name": entry.get("concept_name")}
        entry["concept_id"] = top
        entry["concept_name"] = TOP_LEVEL_RACES[top]
    elif entry.get("status") == "auto_accept":
        entry["status"] = "review"


def _direct_parents(concept_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """concept_id → its direct parents (one level up in concept_ancestor) that
    are valid standard concepts in the same domain, as search candidates."""
    if not concept_ids:
        return {}
    from app.services.db import connect
    from psycopg2 import sql as pgsql

    schema = pgsql.Identifier(settings.omop_vocab_schema or "vocab")
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT a.descendant_concept_id, p.concept_id, p.concept_name, p.domain_id, "
                    "p.vocabulary_id, p.concept_class_id, p.concept_code "
                    "FROM {s}.concept_ancestor a "
                    "JOIN {s}.concept c ON c.concept_id = a.descendant_concept_id "
                    "JOIN {s}.concept p ON p.concept_id = a.ancestor_concept_id "
                    "WHERE a.descendant_concept_id = ANY(%s) AND a.min_levels_of_separation = 1 "
                    "AND p.standard_concept = 'S' AND p.invalid_reason IS NULL AND p.domain_id = c.domain_id "
                    "ORDER BY a.descendant_concept_id, p.concept_id"
                ).format(s=schema),
                ([int(c) for c in concept_ids],),
            )
            out: dict[int, list[dict[str, Any]]] = {}
            for child, *parent in cur.fetchall():
                out.setdefault(int(child), []).append(dict(zip(
                    ("concept_id", "concept_name", "domain_id", "vocabulary_id", "concept_class_id", "concept_code"),
                    parent,
                )))
            return out


def _parents_first(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Arrange search results as a tree of broader → narrower concepts.

    Flat list, in display order. Every direct parent of a result joins the
    tree (added if the search didn't find it), and chains nest as deep as they
    go — Ambulance Visit › Ambulance › Ambulance - Land — so each concept sits
    once under the concept it belongs to. Each row carries `depth` (0 = top),
    `parent_id` (the row it is listed under, absent at the top) and
    `is_parent` (it has rows under it). Trees come first, in the order of the
    first result they contain, then the results with no parent or child among
    them. A concept with two unrelated parents is listed under each; of two
    parents where one is above the other, only the more specific is used.
    Unchanged when the ancestry can't be read.
    """
    try:
        parents = _direct_parents([c["concept_id"] for c in candidates if c.get("concept_id")])
    except Exception as exc:
        print(f"[search-concepts] concept_ancestor query failed: {exc}")
        return candidates

    def ancestors(cid: int) -> set[int]:
        found: set[int] = set()
        stack = [p["concept_id"] for p in parents.get(cid, [])]
        while stack:
            up = stack.pop()
            if up not in found:
                found.add(up)
                stack.extend(p["concept_id"] for p in parents.get(up, []))
        return found

    # A parent that is itself an ancestor of another of the same child's
    # parents adds nothing: Air Ambulance sits under both Ambulance and
    # Ambulance - Air or Water, which is under Ambulance — list it once, under
    # the more specific one.
    for cid, ups in list(parents.items()):
        ids = {p["concept_id"] for p in ups}
        redundant = {a for u in ids for a in ancestors(u)} & ids
        if redundant:
            parents[cid] = [p for p in ups if p["concept_id"] not in redundant]

    nodes: dict[int, dict[str, Any]] = {c["concept_id"]: c for c in candidates}
    children: dict[int, list[int]] = {}
    has_parent: set[int] = set()
    for child in candidates:
        for parent in parents.get(child["concept_id"], []):
            nodes.setdefault(parent["concept_id"], {**parent, "score": None})
            children.setdefault(parent["concept_id"], []).append(child["concept_id"])
            has_parent.add(child["concept_id"])

    def root_of(cid: int, seen: frozenset = frozenset()) -> int:
        ups = parents.get(cid) or []
        if not ups or cid in seen:
            return cid
        return root_of(ups[0]["concept_id"], seen | {cid})

    out: list[dict[str, Any]] = []

    def emit(cid: int, depth: int, under: int | None, path: frozenset) -> None:
        row = dict(nodes[cid], depth=depth, is_parent=bool(children.get(cid)))
        if under is not None:
            row["parent_id"] = under
        out.append(row)
        for kid in children.get(cid, []):
            if kid not in path:      # a cycle in the ancestry would never end
                emit(kid, depth + 1, cid, path | {kid})

    emitted_roots: set[int] = set()
    lone: list[int] = []
    for c in candidates:
        root = root_of(c["concept_id"])
        if root in emitted_roots:
            continue
        emitted_roots.add(root)
        if children.get(root):
            emit(root, 0, None, frozenset({root}))
        elif root not in has_parent:
            lone.append(root)
    for cid in lone:
        emit(cid, 0, None, frozenset({cid}))
    return out


def _suggestion(value: str, term: str, result: dict[str, Any] | None) -> dict[str, Any]:
    """One value's answer, in the shape both endpoints below return."""
    result = result or {}
    return {
        "value": value,
        "term": term,
        "source": "matcher",
        "status": result.get("status") or "unmapped",
        "confidence": result.get("confidence") or 0,
        "concept_id": result.get("concept_id"),
        "concept_name": result.get("concept_name"),
        "candidates": result.get("candidates") or [],
    }


class ConceptPreference(BaseModel):
    """Which concepts in the domain the field wants, when the domain alone is
    too broad — e.g. visit_concept_id wants the Visit vocabulary (9201
    Inpatient Visit), place of service wants CMS Place of Service (8717
    Inpatient Hospital), and both are Visit-domain concepts that score within a
    few points of each other for the same text."""
    vocabularies: list[str] | None = None
    concept_classes: list[str] | None = None


class SuggestValueConceptsPayload(BaseModel):
    domain: str
    values: list[str]
    candidates: int = 5
    prefer: ConceptPreference | None = None


# The matcher's own auto-accept threshold, applied to a preferred candidate
# that replaces its pick.
AUTO_ACCEPT_SCORE = 90.0


def _exact_preferred(term: str, domain: str, prefer: ConceptPreference) -> dict[str, Any] | None:
    """The single valid standard concept in `domain` named exactly `term`
    (case-insensitively) that satisfies `prefer`, from the vocab schema.

    The fallback for exact-name ties the matcher collapses to one arbitrary
    concept: "Canada" comes back as the OSM place "Canadá", and the country
    concept 41915371 Canada never reaches the shortlist. Returns None when
    there is no such concept, or more than one.
    """
    from app.services.db import connect
    from psycopg2 import sql as pgsql

    conditions = [pgsql.SQL("lower(concept_name) = lower(%s) AND domain_id = %s "
                            "AND standard_concept = 'S' AND invalid_reason IS NULL")]
    params: list[Any] = [term, domain]
    if prefer.vocabularies:
        conditions.append(pgsql.SQL("vocabulary_id = ANY(%s)"))
        params.append(list(prefer.vocabularies))
    if prefer.concept_classes:
        conditions.append(pgsql.SQL("concept_class_id = ANY(%s)"))
        params.append(list(prefer.concept_classes))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT concept_id, concept_name, domain_id, vocabulary_id, concept_class_id, concept_code "
                    "FROM {s}.concept WHERE {where} LIMIT 2"
                ).format(s=pgsql.Identifier(settings.omop_vocab_schema or "vocab"),
                         where=pgsql.SQL(" AND ").join(conditions)),
                params,
            )
            rows = cur.fetchall()
    if len(rows) != 1:
        return None
    keys = ("concept_id", "concept_name", "domain_id", "vocabulary_id", "concept_class_id", "concept_code")
    return {**dict(zip(keys, rows[0])), "score": 100.0}


def _apply_preference(entry: dict[str, Any], prefer: ConceptPreference | None, domain: str) -> None:
    """Swap a suggestion onto the best-scoring preferred candidate, in place.

    With none of the candidates preferred, the matcher's pick stands but is
    only offered, never filled in on its own.
    """
    if prefer is None or not entry.get("term"):
        return
    vocabularies = set(prefer.vocabularies or [])
    classes = set(prefer.concept_classes or [])

    def wanted(c: dict[str, Any]) -> bool:
        return ((not vocabularies or c.get("vocabulary_id") in vocabularies)
                and (not classes or c.get("concept_class_id") in classes))

    preferred = [c for c in entry.get("candidates") or [] if wanted(c)]
    if not preferred:
        try:
            exact = _exact_preferred(entry["term"], domain, prefer)
        except Exception as exc:
            print(f"[suggest-value-concepts] exact-name lookup failed: {exc}")
            exact = None
        if exact is None:
            if entry.get("status") == "auto_accept":
                entry["status"] = "review"
            return
        entry["candidates"] = [exact] + list(entry.get("candidates") or [])
        preferred = [exact]
    best = max(preferred, key=lambda c: c.get("score") or 0)
    # The status is re-decided even when the matcher's pick is the preferred
    # one: its "review" is often ambiguity between vocabularies (8717 Inpatient
    # Hospital vs 9201 Inpatient Visit), which the preference has just settled.
    entry.update(
        concept_id=best["concept_id"], concept_name=best.get("concept_name"),
        confidence=best.get("score") or 0,
        status="auto_accept" if (best.get("score") or 0) >= AUTO_ACCEPT_SCORE else "review",
    )
    # Keep the chosen concept first, as the matcher's pick was.
    entry["candidates"] = [best] + [c for c in entry["candidates"] if c is not best]


@router.post("/{project_id}/suggest-value-concepts")
async def suggest_value_concepts(
    project_id: str,
    payload: SuggestValueConceptsPayload,
    db: Session = Depends(get_db),
):
    """Suggest a standard concept in `domain` for each source value.

    `status` is the matcher's own verdict ("auto_accept" / "review" /
    "unmapped"), or "auto_accept" with `source: "rule"` for a value the
    normalizer resolved without it (gender). The caller decides what to fill in;
    the intended use is auto_accept filled, review offered, unmapped left alone.

    Race matches are rolled up to the five top-level OHDSI categories
    (`detailed` then keeps the concept actually matched); a race concept
    outside that hierarchy comes back as "review" at best. `prefer` narrows the
    domain to the vocabularies / concept classes the field wants (see
    ConceptPreference).
    """
    from app.services.concept_normalizer import GENDER_FEMALE, GENDER_MALE, gender_concept, normalize_term

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    values = list(dict.fromkeys(v for v in payload.values if str(v).strip()))
    out: dict[str, dict[str, Any]] = {}
    pending: dict[str, list[str]] = {}   # term → the source values it came from

    for value in values:
        if payload.domain.lower() == "gender":
            concept_id = gender_concept(value)
            if concept_id:
                entry = _suggestion(value, value, None)
                entry.update(
                    source="rule", status="auto_accept", confidence=100.0, concept_id=concept_id,
                    concept_name="MALE" if concept_id == GENDER_MALE else "FEMALE",
                )
                out[value] = entry
                continue
        pending.setdefault(normalize_term(value, payload.domain), []).append(value)

    if pending:
        if not settings.concept_matcher_url:
            raise HTTPException(status_code=503, detail="CONCEPT_MATCHER_URL is not configured")
        terms = list(pending)
        for start in range(0, len(terms), VALUE_CHUNK):
            chunk = terms[start:start + VALUE_CHUNK]
            answer = await _post_match(
                [{"name": t} for t in chunk], project_id,
                f"ETL Auto-Designer project {project_id} ({payload.domain} values)",
                # A preference picks from the shortlist, so it needs a longer one.
                max(payload.candidates, 10) if payload.prefer else payload.candidates,
                [payload.domain],
                # Alternatives behind an exact hit: they are what the
                # "Suggested" list and a vocabulary preference choose from.
                shortlist=True,
            )
            answered = answer.get("results") or []
            if len(answered) != len(chunk):
                raise HTTPException(
                    status_code=502,
                    detail=f"Matcher returned {len(answered)} results for {len(chunk)} terms",
                )
            for term, result in zip(chunk, answered):
                for value in pending[term]:
                    out[value] = _suggestion(value, term, result)
                    _apply_preference(out[value], payload.prefer, payload.domain)
                    if payload.domain.lower() == "race":
                        _roll_up_race(out[value])

    return {"domain": payload.domain, "results": {v: out[v] for v in values}}


def _name_search(term: str, domain: str, limit: int) -> list[dict[str, Any]]:
    """Valid standard concepts in `domain` whose name contains every word of
    `term`, from the vocab schema — exact name first, then earliest match, then
    shortest name.

    Tops up the matcher's shortlist, which is deliberately short: an exact
    concept name ends its search at Stage 1 with that one concept, so searching
    "ambulance" returned NUCC "Ambulance" and never "Ambulance - Land" (8668),
    the CMS Place of Service concept a place-of-service field actually wants.
    A partial word ("hisp") gets nothing from the matcher at all.
    """
    from app.services.db import connect
    from psycopg2 import sql as pgsql

    words = [w for w in re.split(r"\s+", term.strip()) if w]
    if not words:
        return []
    # LIKE wildcards in the user's text are dropped rather than escaped.
    like = ["%" + re.sub(r"[%_\\]", "", w) + "%" for w in words]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                pgsql.SQL(
                    "SELECT concept_id, concept_name, domain_id, vocabulary_id, concept_class_id, concept_code "
                    "FROM {s}.concept WHERE domain_id = %s AND standard_concept = 'S' "
                    "AND invalid_reason IS NULL AND concept_name ILIKE ALL(%s) "
                    "ORDER BY lower(concept_name) = lower(%s) DESC, "
                    "strpos(lower(concept_name), lower(%s)) = 0, strpos(lower(concept_name), lower(%s)), "
                    "length(concept_name), concept_id LIMIT %s"
                ).format(s=pgsql.Identifier(settings.omop_vocab_schema or "vocab")),
                (domain, like, term, words[0], words[0], limit),
            )
            keys = ("concept_id", "concept_name", "domain_id", "vocabulary_id", "concept_class_id", "concept_code")
            return [{**dict(zip(keys, row)), "score": None} for row in cur.fetchall()]


@router.get("/{project_id}/search-concepts")
async def search_concepts(
    project_id: str,
    query: str,
    domain: str,
    limit: int = 15,
    db: Session = Depends(get_db),
):
    """Ranked standard concepts in `domain` for a free-text query — the search
    box beside every concept field, so the user never has to go to Athena.

    The matcher's ranked shortlist comes first, topped up to `limit` with a
    plain name search over the vocabulary (see _name_search); both are grouped
    under their direct parents (see _parents_first). The name search runs
    alongside the matcher call, and if the vocab schema isn't loaded the
    matcher's results are returned on their own.
    """
    import asyncio
    from app.services.concept_normalizer import normalize_term

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not settings.concept_matcher_url:
        raise HTTPException(status_code=503, detail="CONCEPT_MATCHER_URL is not configured")
    if not query.strip():
        return {"term": "", "results": []}

    term = normalize_term(query, domain)
    limit = max(1, min(limit, 25))

    async def by_name() -> list[dict[str, Any]]:
        try:
            return await asyncio.to_thread(_name_search, term, domain, limit)
        except Exception as exc:
            print(f"[search-concepts] vocabulary name search failed: {exc}")
            return []

    answer, named = await asyncio.gather(
        _post_match(
            [{"name": term}], project_id, f"ETL Auto-Designer project {project_id} (search)",
            limit, [domain], shortlist=True,
        ),
        by_name(),
    )
    result = (answer.get("results") or [{}])[0]
    candidates = list(result.get("candidates") or [])
    # The selected concept normally heads the candidate list; keep it first
    # even if the matcher trimmed it out.
    if result.get("concept_id") and all(c.get("concept_id") != result["concept_id"] for c in candidates):
        candidates.insert(0, {
            k: result.get(k) for k in (
                "concept_id", "concept_name", "domain_id", "vocabulary_id", "concept_class_id", "concept_code",
            )
        } | {"score": result.get("confidence")})
    seen = {c.get("concept_id") for c in candidates}
    candidates += [c for c in named if c["concept_id"] not in seen][:max(0, limit - len(candidates))]
    return {"term": term, "results": _parents_first(candidates)}


# ── Column unique values ────────────────────────────────────────────────────

@router.get("/{project_id}/column-values")
def get_column_values(
    project_id: str,
    max_values: int = 1000,
    filename: str | None = None,
    db: Session = Depends(get_db),
):
    """
    Return per-column stats and distinct values for the source dataset.
    Response shape:
      { col: { distinct_values, distinct_count, null_count, total_rows, completion_rate } }
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Multi-file: read from the specified file when provided
    if filename and project.source_files:
        file_entry = next((f for f in project.source_files if f.get("filename") == filename), None)
        if file_entry and Path(file_entry["path"]).exists():
            df = pd.read_csv(
                file_entry["path"],
                sep=file_entry.get("delimiter", ","),
                encoding=file_entry.get("encoding", "utf-8"),
                dtype=str,
                on_bad_lines="skip",
            )
        else:
            return {}
    else:
        if not project.source_path or not Path(project.source_path).exists():
            return {}
        df = pd.read_csv(
            project.source_path,
            sep=project.source_delimiter or ",",
            encoding=project.source_encoding or "utf-8",
            dtype=str,
            on_bad_lines="skip",
        )

    total_rows = len(df)
    result: dict[str, dict] = {}

    for col in df.columns:
        null_count = int(df[col].isna().sum())
        all_vals = df[col].dropna().unique().tolist()
        distinct_count = len(all_vals)
        completion_rate = round(((total_rows - null_count) / total_rows * 100), 1) if total_rows else 0.0

        result[col] = {
            "distinct_values": [str(v) for v in all_vals[:max_values]],
            "distinct_count": distinct_count,
            "null_count": null_count,
            "total_rows": total_rows,
            "completion_rate": completion_rate,
        }

    return result


# ── Concept decisions ───────────────────────────────────────────────────────

class ConceptDecisionsPayload(BaseModel):
    decisions: dict[str, Any]


@router.get("/{project_id}/concept-decisions")
def get_concept_decisions(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project.concept_decisions or {}


@router.post("/{project_id}/concept-decisions", response_model=ProjectResponse)
def save_concept_decisions(
    project_id: str,
    payload: ConceptDecisionsPayload,
    db: Session = Depends(get_db),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    project.concept_decisions = payload.decisions
    db.commit()
    db.refresh(project)
    return project


# ── Generate mapping CSVs ───────────────────────────────────────────────────

@router.post("/{project_id}/generate-mapping-csvs", response_model=ProjectResponse)
def generate_csvs(project_id: str, db: Session = Depends(get_db)):
    """
    Generate variable_mapping.csv, value_mapping.csv, variable_value_mapping.csv
    (and custom_mappings.csv) from the saved concept decisions.
    Stores file paths in project.mapping_files.
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.concept_decisions:
        raise HTTPException(status_code=400, detail="No concept decisions saved yet")

    output_dir = str(settings.get_upload_path() / project_id / "mappings")
    files = generate_mapping_csvs(
        project.concept_decisions,
        output_dir,
        custom_vocabulary_id=project.custom_vocabulary_id or "CUSTOM",
    )

    if not files:
        raise HTTPException(
            status_code=400,
            detail="No mapping rows generated. Make sure at least one variable is mapped.",
        )

    project.mapping_files = files
    db.commit()
    db.refresh(project)
    return project


# ── Download mapping CSVs as a zip ──────────────────────────────────────────

@router.get("/{project_id}/download-mapping-files")
def download_mapping_files(project_id: str, db: Session = Depends(get_db)):
    """Bundle the CSVs listed in project.mapping_files into a zip for download."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    files: dict = project.mapping_files or {}
    existing = [Path(p) for p in files.values() if p and Path(p).is_file()]
    if not existing:
        raise HTTPException(
            status_code=404,
            detail="No mapping files generated yet. Generate them first.",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in existing:
            zf.write(path, arcname=path.name)
    buf.seek(0)

    zip_name = f"{project_id}_mapping_files.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


# ── Download mapping summary as Excel ───────────────────────────────────────

# Last answer from _all_distinct_values, as (key, values). One entry is enough:
# the callers that repeat (the Concepts step matches one column's values per
# request, so a run makes one call per column) all ask about the same project
# back to back, and re-reading every source CSV per column would dominate the run.
_distinct_values_cache: "tuple[tuple, dict[str, list[str]]] | None" = None


def _all_distinct_values(project: Project, max_values: int = 1000) -> dict[str, list[str]]:
    """Distinct source values for every column across every uploaded source file
    (unlike get_column_values above, which only reads the one currently-selected
    file). Lets the mapping summary list every value a column actually has, not
    just the ones the user happened to assign a concept to."""
    global _distinct_values_cache

    file_entries = project.source_files or (
        [{"path": project.source_path, "delimiter": project.source_delimiter, "encoding": project.source_encoding}]
        if project.source_path else []
    )

    # Keyed on what the answer actually depends on, mtime and size included, so a
    # re-uploaded source is never served from a stale entry.
    def stat_key(path: str | None) -> tuple:
        try:
            st = Path(path).stat() if path else None
        except OSError:
            return (path, None, None)
        return (path, st.st_mtime_ns, st.st_size) if st else (path, None, None)

    key = (project.id, max_values, tuple(stat_key(e.get("path")) for e in file_entries))
    if _distinct_values_cache and _distinct_values_cache[0] == key:
        return _distinct_values_cache[1]

    result: dict[str, list[str]] = {}
    for entry in file_entries:
        path = entry.get("path")
        if not path or not Path(path).exists():
            continue
        df = pd.read_csv(
            path,
            sep=entry.get("delimiter") or ",",
            encoding=entry.get("encoding") or "utf-8",
            dtype=str,
            on_bad_lines="skip",
        )
        for col in df.columns:
            if col in result:
                continue
            result[col] = [str(v) for v in df[col].dropna().unique().tolist()[:max_values]]

    _distinct_values_cache = (key, result)
    return result


def _ordered_decisions(project: Project) -> dict:
    """Re-key project.concept_decisions to follow the column order of the
    uploaded source file(s), instead of dict/insertion order — which is
    whatever order the user happened to touch columns in, not a meaningful
    order to read a report in. Any decision whose column isn't in a known
    file (e.g. its file was later removed) is appended at the end, in its
    original order, so nothing gets silently dropped."""
    decisions = project.concept_decisions or {}
    order: list[str] = []
    seen: set[str] = set()
    for entry in (project.source_files or []):
        for col in (entry.get("columns") or []):
            if col not in seen:
                order.append(col)
                seen.add(col)
    for col in (project.source_columns or []):
        if col not in seen:
            order.append(col)
            seen.add(col)

    ordered = {col: decisions[col] for col in order if col in decisions}
    for col, d in decisions.items():
        if col not in ordered:
            ordered[col] = d
    return ordered


@router.get("/{project_id}/download-mapping-summary")
def download_mapping_summary(project_id: str, db: Session = Depends(get_db)):
    """Build and download an Excel summary of every variable's mapping decisions
    (included or not, mapped or skipped) — variable/value concept ids and names,
    unit/route/type concepts, and start/end datetime columns. For human review,
    unlike generate-mapping-csvs which only emits ETL-ready rows."""
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if not project.concept_decisions:
        raise HTTPException(status_code=400, detail="No concept decisions saved yet")

    # The summary only needs names for bare ids — the per-column unit/route
    # mappings, which keep just {source value: concept_id}. Fetch them all at once.
    def as_id(cid: Any) -> int | None:
        try:
            return int(cid)
        except (TypeError, ValueError):
            return None

    bare_ids: set[int] = set()
    for decision in project.concept_decisions.values():
        decision = decision or {}
        for mapping_key, concepts_key in (("unit_mapping", "unit_concepts"), ("route_mapping", "route_concepts")):
            for cid in ((decision.get(mapping_key) or {}).get(concepts_key) or {}).values():
                if (i := as_id(cid)) is not None:
                    bare_ids.add(i)
    try:
        names = _get_concept_names(bare_ids)
    except Exception as exc:
        # Vocab not loaded / Postgres unreachable: export without those names.
        print(f"[mapping-summary] concept name lookup failed: {exc}")
        names = {}

    def lookup_name(cid: Any) -> str:
        i = as_id(cid)
        return names.get(i, "") if i is not None else ""

    column_values = _all_distinct_values(project)
    buf = generate_mapping_summary_excel(_ordered_decisions(project), lookup_name, column_values)

    file_name = f"{project_id}_mapping_summary.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
