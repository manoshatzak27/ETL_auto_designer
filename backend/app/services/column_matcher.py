"""Match source columns to the fields of a wizard step.

Every table step (location, care site, provider, person, visit, observation
period, death) asks the user to pick, field by field, which source column holds
what. This module makes that first pass for them:

1. **Name** — each column's name (and its data-dictionary description, when the
   project has one) is compared with a list of the spellings a field tends to
   go by: `dob`, `birth_date`, `PatientBirthDt` all point at date of birth.
2. **Values** — a sample of the column is checked against the kind of data the
   field holds. A column called `admission_type` scores well on name for an
   admission *date*, and the values are what rule it out. For a field whose
   values are later mapped to concepts (gender, race, ethnicity, visit
   concept...), the check is how many of the column's values the concept
   matcher maps in that field's domain: `DEMOGR_NATIONALITY` (PRT, GR, NL) maps
   to Ethnicity concepts, a free-text treatment column maps to no Visit concept.
3. **Assignment** — each column fills at most one field, best scores first, so
   `city` goes to the person's city rather than to the care site's as well.
4. **LLM fallback** — fields still without a confident match are sent, with the
   columns nobody claimed and a few sample values of each, to the OpenAI model
   the project already uses for code generation. Its answer is held to the same
   value checks: a pick that fails them is offered, not applied.
5. **Value scan** — a concept field still without a confident match has every
   unclaimed short-list column's values put through the concept matcher; one
   whose values clearly are the field's concepts is offered, never applied.

The scores are not probabilities; they are only meant to be compared against
`AUTO_THRESHOLD` and `SUGGEST_THRESHOLD`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd
from openai import AsyncOpenAI

from app.config import settings
from app.services.column_descriptions import _normalize
from app.services.concept_normalizer import is_no_value

logger = logging.getLogger(__name__)

AUTO_THRESHOLD = 0.75
SUGGEST_THRESHOLD = 0.45
# Below this a column's name has nothing to do with the field; its values alone
# never earn it a place (every text column "looks like" a city).
MIN_NAME_SCORE = 0.35
# The values can cost a column up to this share of its name score: they confirm
# or weaken a name, they never stand in for one.
VALUE_WEIGHT = 0.35
# Multiplier for a column whose values clearly are not the field's kind.
VETO_FACTOR = 0.3
PROFILE_ROWS = 5000
SAMPLE_VALUES = 5
# A `related` synonym names a neighbouring idea (nationality for ethnicity): it
# brings the column in for the values to decide, at a discount.
RELATED_FACTOR = 0.9


# ── Field specifications ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class ConceptTarget:
    """The concepts a field's values are mapped to — the domain and preference
    its step's "Auto-fill concept IDs" asks the concept matcher for."""
    domain: str
    vocabularies: tuple[str, ...] = ()
    concept_classes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FieldSpec:
    label: str
    kind: str
    synonyms: tuple[str, ...]
    description: str
    related: tuple[str, ...] = ()
    concept: ConceptTarget | None = None


def _spec(label: str, kind: str, synonyms: list[str], description: str,
          related: list[str] | None = None, concept: ConceptTarget | None = None) -> FieldSpec:
    return FieldSpec(label, kind, tuple(synonyms), description, tuple(related or ()), concept)


GENDER_CONCEPTS = ConceptTarget("Gender")
COUNTRY_CONCEPTS = ConceptTarget("Geography", concept_classes=("Location", "2nd level"))
VISIT_CONCEPTS = ConceptTarget("Visit", vocabularies=("Visit",))
PLACE_OF_SERVICE_CONCEPTS = ConceptTarget("Visit", vocabularies=("CMS Place of Service",))


def _prefixed(prefixes: list[str], synonyms: list[str]) -> list[str]:
    return [f"{p} {s}" for p in prefixes for s in synonyms]


PATIENT_ID = ["patient id", "person id", "subject id", "participant id", "pid", "patient",
              "mrn", "medical record number", "patient number", "record id", "subject",
              "case id", "patient no", "pat id", "pt id", "patient code", "id patient"]

ADDRESS_1 = ["address", "address 1", "address line 1", "street", "street address", "addr",
             "home address", "residence address", "adress", "address street"]
ADDRESS_2 = ["address 2", "address line 2", "addr 2", "adress 2", "apartment", "apt", "suite"]
CITY = ["city", "town", "municipality", "residence city", "village", "locality", "place of residence"]
STATE = ["state", "province", "region", "prefecture", "state code", "state province"]
ZIP = ["zip", "zip code", "zipcode", "postal code", "postcode", "post code", "postal", "plz"]
COUNTY = ["county", "district", "borough", "parish"]
COUNTRY = ["country", "country code", "country of residence", "residence country"]
LATITUDE = ["latitude", "lat", "geo lat", "geolocation latitude"]
LONGITUDE = ["longitude", "lon", "lng", "long", "geo lon", "geolocation longitude"]

SITE_WORDS = ["care site", "site", "facility", "hospital", "hosp", "clinic", "center",
              "centre", "institution", "practice", "ward", "organization"]
PROVIDER_WORDS = ["provider", "doctor", "physician", "clinician", "practitioner", "md", "dr",
                  "attending", "gp", "prov", "doc"]

BIRTH = ["date of birth", "dob", "birth date", "birthdate", "birthday", "birth", "year of birth",
         "birth year", "yob", "born", "birth dt", "date birth", "dt birth",
         "fecha nacimiento", "fechanac", "fecha nac", "geburtsdatum", "data nascita",
         "date naissance", "imerominia gennisis"]
GENDER = ["gender", "sex", "biological sex", "gender code", "sex code", "gender identity"]


def _location_fields() -> dict[str, FieldSpec]:
    person = {
        "address_1_col": _spec("Address line 1", "text", ADDRESS_1, "Street address of the patient's residence"),
        "address_2_col": _spec("Address line 2", "text", ADDRESS_2, "Second address line (apartment, suite) of the patient's residence"),
        "city_col": _spec("City", "text", CITY, "City or town the patient lives in"),
        "state_col": _spec("State", "category", STATE, "State, province or region the patient lives in"),
        "zip_col": _spec("Zip code", "zip", ZIP, "Postal code of the patient's residence"),
        "county_col": _spec("County", "category", COUNTY, "County or district the patient lives in"),
        "country_col": _spec("Country", "country", COUNTRY, "Country the patient lives in",
                             concept=COUNTRY_CONCEPTS),
        "latitude_col": _spec("Latitude", "latitude", LATITUDE, "Latitude of the patient's residence"),
        "longitude_col": _spec("Longitude", "longitude", LONGITUDE, "Longitude of the patient's residence"),
    }
    fields: dict[str, FieldSpec] = {
        "person_id_col": _spec("Patient ID", "id", PATIENT_ID, "Identifier of the patient a row belongs to"),
        **person,
    }
    for key, spec in person.items():
        fields[f"cs_{key}"] = _spec(
            f"Care site {spec.label.lower()}", spec.kind,
            _prefixed(SITE_WORDS, list(spec.synonyms)),
            spec.description.replace("the patient's residence", "the care site")
                            .replace("the patient lives in", "the care site is in"),
            concept=spec.concept,
        )
    return fields


FIELD_SPECS: dict[str, dict[str, FieldSpec]] = {
    "location": _location_fields(),
    "care_site": {
        "person_id_col": _spec("Patient ID", "id", PATIENT_ID, "Identifier of the patient a row belongs to"),
        "care_site_name_col": _spec(
            "Care site name", "name",
            ["care site", "care site name", "site", "site name", "facility", "facility name",
             "hospital", "hospital name", "clinic", "clinic name", "center", "centre",
             "institution", "department", "ward", "practice", "organization", "site id",
             "hospital id", "center name", "study site", "recruitment site"],
            "Name or code of the hospital, clinic, ward or site where care was delivered"),
        "place_of_service_col": _spec(
            "Place of service", "category",
            ["place of service", "pos", "service location", "setting", "care setting",
             "facility type", "site type", "care site type", "department type", "location type",
             "place of care"],
            "Kind of place care was delivered in (inpatient hospital, office, emergency room...)",
            concept=PLACE_OF_SERVICE_CONCEPTS),
    },
    "provider": {
        "person_id_col": _spec("Patient ID", "id", PATIENT_ID, "Identifier of the patient a row belongs to"),
        "provider_name_col": _spec(
            "Provider name", "name",
            ["provider", "provider name", "doctor", "doctor name", "physician", "physician name",
             "clinician", "clinician name", "practitioner", "attending", "attending physician",
             "treating physician", "referring physician", "gp", "gp name", "provider id"],
            "Name or identifier of the doctor or clinician who treated the patient"),
        "npi_col": _spec("NPI", "npi", ["npi", "national provider identifier", "provider npi", "npi number"],
                         "National Provider Identifier, a 10 digit number"),
        "dea_col": _spec("DEA", "dea", ["dea", "dea number", "provider dea", "dea registration"],
                         "DEA registration number of the provider (2 letters + 7 digits)"),
        "year_of_birth_col": _spec("Provider year of birth", "birth",
                                   _prefixed(PROVIDER_WORDS, BIRTH),
                                   "Year (or date) of birth of the provider, not of the patient"),
        "specialty_source_value_col": _spec(
            "Specialty", "category",
            ["specialty", "speciality", "provider specialty", "physician specialty",
             "doctor specialty", "medical specialty", "discipline", "provider type", "specialism"],
            "Medical specialty of the provider (cardiology, general practice...)",
            concept=ConceptTarget("Provider")),
        "gender_source_value_col": _spec("Provider gender", "gender",
                                         _prefixed(PROVIDER_WORDS, GENDER),
                                         "Gender of the provider, not of the patient",
                                         concept=GENDER_CONCEPTS),
    },
    "person": {
        "person_id": _spec("Patient ID", "id", PATIENT_ID, "Unique identifier of the patient"),
        "gender_concept_id": _spec(
            "Gender", "gender",
            GENDER + ["patient gender", "patient sex", "sesso", "geschlecht", "sexe", "fylo", "genre"],
            "Biological sex or gender of the patient",
            concept=GENDER_CONCEPTS),
        "year_of_birth": _spec("Date of birth", "birth", BIRTH + ["patient dob", "patient birth date"],
                               "Patient's date of birth, or year of birth"),
        "birth_time": _spec("Birth time", "time", ["birth time", "time of birth", "tob", "birth hour"],
                            "Time of day the patient was born"),
        # Datasets often say "ethnicity" for what OMOP calls race (White,
        # Black, Asian), and record a nationality that loads as ethnicity
        # (Greek, Dutch): related synonyms, settled by the values.
        "race_concept_id": _spec("Race", "category",
                                 ["race", "racial group", "race code", "race category", "patient race"],
                                 "Race of the patient",
                                 related=["ethnicity", "ethnic group", "ethnic origin"],
                                 concept=ConceptTarget("Race")),
        "ethnicity_concept_id": _spec(
            "Ethnicity", "category",
            ["ethnicity", "ethnic", "ethnic group", "ethnicity code", "hispanic", "hispanic latino",
             "ethnic origin", "patient ethnicity"],
            "Ethnicity of the patient (Hispanic or not)",
            related=["nationality", "citizenship"],
            concept=ConceptTarget("Ethnicity")),
    },
    "visit_occurrence": {
        "visit_source_col": _spec(
            "Visit identifier", "category",
            ["visit", "visit name", "visit label", "event", "event name", "timepoint", "time point",
             "visit number", "visit no", "visit id", "redcap event name", "wave", "phase",
             "visit code", "follow up", "encounter"],
            "Column whose values name the visit a row belongs to (baseline, follow-up 1...)"),
        "date_col": _spec(
            "Visit start date", "date",
            ["visit date", "visit start date", "start date", "admission date", "admit date",
             "encounter date", "date", "service date", "visit dt", "admission dt", "date of visit",
             "visit start", "examination date", "exam date", "date of admission", "assessment date"],
            "Date the visit started (admission date)"),
        "time_col": _spec(
            "Visit start time", "time",
            ["visit time", "start time", "admission time", "admit time", "encounter time",
             "visit start time", "time"],
            "Time of day the visit started"),
        "end_date_col": _spec(
            "Visit end date", "date",
            ["visit end date", "end date", "discharge date", "disch date", "discharge dt",
             "date of discharge", "visit end", "stop date"],
            "Date the visit ended (discharge date)"),
        "end_time_col": _spec("Visit end time", "time", ["end time", "discharge time", "visit end time"],
                              "Time of day the visit ended"),
        "visit_concept_source_col": _spec(
            "Visit type", "category",
            ["visit type", "encounter type", "visit category", "admission type", "patient class",
             "care type", "visit class", "service type", "inpatient outpatient", "setting"],
            "Kind of visit (inpatient, outpatient, emergency...)",
            concept=VISIT_CONCEPTS),
        "visit_type_source_col": _spec(
            "Visit provenance", "category",
            ["visit provenance", "record type", "data source", "source type", "provenance",
             "record source"],
            "Where the visit record came from (EHR, claim, registry...)"),
        "admitted_from_source_col": _spec(
            "Admitted from", "category",
            ["admitted from", "admission source", "admit source", "source of admission",
             "referral source", "arrived from", "admission origin"],
            "Where the patient came from when admitted (home, other hospital...)",
            concept=PLACE_OF_SERVICE_CONCEPTS),
        "discharged_to_source_col": _spec(
            "Discharged to", "category",
            ["discharged to", "discharge disposition", "discharge destination", "discharge status",
             "disposition", "discharge location"],
            "Where the patient went when discharged (home, nursing facility, deceased...)",
            concept=PLACE_OF_SERVICE_CONCEPTS),
    },
    "observation_period": {
        "start_date_col": _spec(
            "Observation start date", "date",
            ["observation start", "observation period start", "enrollment date", "enrolment date",
             "enrollment start", "start date", "study entry date", "entry date", "registration date",
             "inclusion date", "baseline date", "consent date", "first visit date",
             "recruitment date", "date of enrollment", "index date", "date of inclusion"],
            "Date the patient's observation began (enrollment, study entry)"),
        "end_date_col": _spec(
            "Observation end date", "date",
            ["observation end", "observation period end", "end date", "enrollment end",
             "last follow up", "last followup date", "last follow up date", "last contact date",
             "follow up date", "study exit date", "exit date", "end of follow up", "last visit date",
             "date of last contact", "censor date", "withdrawal date"],
            "Date the patient's observation ended (last follow-up, study exit)"),
    },
    "death": {
        "filter_col": _spec(
            "Death indicator", "category",
            ["death", "deceased", "dead", "died", "vital status", "alive", "death flag",
             "deceased flag", "mortality", "expired", "death status", "outcome", "status"],
            "Column telling whether the patient died (vital status, deceased flag)"),
        "death_date_col": _spec(
            "Death date", "date",
            ["death date", "date of death", "dod", "deceased date", "died on", "date died",
             "death dt", "mortality date", "date of decease", "expired date"],
            "Date the patient died"),
        "death_datetime_col": _spec(
            "Death datetime", "datetime",
            ["death datetime", "death time", "time of death", "datetime of death", "death timestamp"],
            "Date and time the patient died"),
        "cause_source_value_col": _spec(
            "Cause of death", "category",
            ["cause of death", "death cause", "cod", "cause", "primary cause of death",
             "underlying cause", "cause death"],
            "Cause of the patient's death"),
    },
}


# ── Name scoring ─────────────────────────────────────────────────────────────

_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


# Filler words: "date of death" and "date of enrollment" share nothing that matters.
STOP_WORDS = {"of", "the", "a", "an", "and", "or", "for", "to", "in", "on", "at", "by",
              "de", "del", "la", "el", "di", "du", "der"}


def tokenize(name: str) -> list[str]:
    """`PatBirthDt`, `pat_birth_dt` and `pat-birth dt` → [pat, birth, dt]."""
    spaced = _CAMEL.sub(" ", str(name))
    return [t for t in re.split(r"[^a-z0-9]+", spaced.lower()) if t and t not in STOP_WORDS]


# Words that on their own say what a column holds. A column carrying one that
# the synonym doesn't (`visit_date` for `visit`) is probably that other thing.
TYPE_WORDS = {"date", "dt", "time", "city", "zip", "postal", "year", "yr", "age", "id",
              "birth", "dob", "death", "address", "addr", "name"}


def _token_match(a: str, b: str) -> bool:
    # A shortened token matches its long form: addr ↔ address, enrol ↔ enrollment.
    # Not below four letters: `doc` (doctor-reported) is not `doctor`, `dis` is
    # not `discipline`. And only a real shortening: `count` is not `country`,
    # `special` is not `specialty`.
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 4 and len(short) <= 0.65 * len(long_) and long_.startswith(short)


def _contains_tokens(haystack: list[str], needles: list[str]) -> bool:
    return all(any(_token_match(h, n) for h in haystack) for n in needles)


def name_score(column: str, synonym: str) -> float:
    col_norm, syn_norm = _normalize(column), _normalize(synonym)
    if not col_norm or not syn_norm:
        return 0.0
    if col_norm == syn_norm:
        return 1.0
    col_tokens, syn_tokens = tokenize(column), tokenize(synonym)
    if syn_tokens and _contains_tokens(col_tokens, syn_tokens):
        # Every word of the synonym is there; each extra word in the column
        # (`patient_city_code` for `city`) makes it a little less certain, and
        # an extra word that names a field of its own (`hospital_city` for
        # `hospital`) much less.
        extras = [t for t in col_tokens if not any(_token_match(t, s) for s in syn_tokens)]
        strong = sum(1 for t in extras if t in TYPE_WORDS)
        return max(0.5, 0.92 - 0.05 * len(extras) - 0.25 * strong)
    if len(syn_norm) >= 5 and syn_norm in col_norm:
        # `deathdate2`, `patientbirthdate`: the synonym is there, unseparated.
        # Only ever a suggestion — `ethnicity` holds `city` the same way.
        return 0.72
    if len(col_tokens) == 1 and len(syn_norm) == 3 and (col_norm.startswith(syn_norm) or col_norm.endswith(syn_norm)):
        # `patdob`, but not `zipcode` for `cod`.
        return 0.7
    # Near-identical spellings only (`adress`, `postcod`): a looser ratio pairs
    # `zipcode` with `visitcode`.
    ratio = SequenceMatcher(None, col_norm, syn_norm).ratio()
    fuzzy = ratio * 0.8 if ratio >= 0.8 and min(len(col_norm), len(syn_norm)) >= 5 else 0.0
    shared = sum(1 for s in syn_tokens if any(_token_match(c, s) for c in col_tokens))
    overlap = shared / len(syn_tokens) if syn_tokens else 0.0
    return max(fuzzy, overlap * 0.6)


def description_score(description: str, synonym: str) -> float:
    """How well a data-dictionary description names the field."""
    syn_tokens = tokenize(synonym)
    # A lone short word ("id", "lat") turns up in too many descriptions to count.
    if not syn_tokens or (len(syn_tokens) == 1 and len(syn_tokens[0]) < 4):
        return 0.0
    # Prose is written out in full, so whole words only, and as a phrase: "What
    # type of treatment did you visit the hospital for?" holds "visit" and
    # "type", but is not about the visit type.
    words = tokenize(description)
    n = len(syn_tokens)
    return 0.85 if any(words[i:i + n] == syn_tokens for i in range(len(words) - n + 1)) else 0.0


def best_name_score(column: str, spec: FieldSpec, description: str = "", hint: str = "") -> float:
    """The best score over the field's synonyms, from the name or the description.

    `hint` is something on screen that tells this field apart from its siblings,
    e.g. a visit's label: for the "Follow-up" visit, `followup_date` should beat
    `baseline_date`. A synonym combined with the hint scores in full; the bare
    synonyms are discounted so that a column carrying the hint wins.
    """
    synonyms = spec.synonyms

    def scored(names: tuple[str, ...]) -> list[float]:
        out = [name_score(column, s) for s in names]
        if description:
            out += [description_score(description, s) for s in names]
        return out

    scores = scored(synonyms) + [s * RELATED_FACTOR for s in scored(spec.related)]
    best = max(scores, default=0.0)
    if hint and tokenize(hint):
        best *= 0.9
        hinted = [f"{hint} {s}" for s in synonyms] + [f"{s} {hint}" for s in synonyms]
        best = max(best, *(name_score(column, s) for s in hinted))
    return best


# ── Value profiling ──────────────────────────────────────────────────────────

_TIME = r"(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:\s?[AaPp][Mm])?(?:Z|[+-]\d{2}:?\d{2})?)?"
_DATE_PATTERNS = [
    re.compile(r"^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}" + _TIME + "$"),
    re.compile(r"^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}" + _TIME + "$"),
    re.compile(r"^\d{1,2}[ -][A-Za-z]{3,9}[ -,]+\d{2,4}" + _TIME + "$"),
    re.compile(r"^[A-Za-z]{3,9}\.? \d{1,2},? \d{4}" + _TIME + "$"),
]
_COMPACT_DATE = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
_HAS_TIME = re.compile(r"\d{1,2}:\d{2}")
_TIME_ONLY = re.compile(r"^\d{1,2}:\d{2}(:\d{2}(\.\d+)?)?(\s?[AaPp][Mm])?$")
_YEAR = re.compile(r"^\d{4}(\.0+)?$")
_ZIP = re.compile(r"^(?=.*\d)[A-Za-z0-9][A-Za-z0-9 -]{1,8}[A-Za-z0-9]$")
_NPI = re.compile(r"^\d{10}(\.0+)?$")
_DEA = re.compile(r"^[A-Za-z]{2}\d{7}$")
_NUMBER = re.compile(r"^[+-]?\d+(\.\d+)?$")
_LETTER = re.compile(r"[^\W\d_]")
GENDER_VALUES = {
    "m", "f", "male", "female", "man", "woman", "men", "women", "u", "unknown", "o", "other",
    "1", "2", "0", "1.0", "2.0", "0.0", "h", "homme", "femme", "masculino", "femenino",
    "maschio", "femmina", "männlich", "weiblich", "άνδρας", "γυναίκα", "άρρεν", "θήλυ",
    "boy", "girl", "x", "nonbinary", "non-binary", "intersex", "undifferentiated",
}


def is_date(value: str) -> bool:
    return bool(_COMPACT_DATE.match(value)) or any(p.match(value) for p in _DATE_PATTERNS)


@dataclass
class ColumnProfile:
    total: int
    non_null: int
    distinct: int
    samples: list[str]
    date_rate: float
    time_in_date_rate: float
    time_rate: float
    year_rate: float
    numeric_rate: float
    letter_rate: float
    zip_rate: float
    npi_rate: float
    dea_rate: float
    gender_rate: float
    lat_rate: float
    lon_rate: float
    # Up to 500 distinct values, for working out a date column's format.
    values: list[str] = field(default_factory=list)
    # How many rows hold each of `values`.
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def distinct_ratio(self) -> float:
        return self.distinct / self.non_null if self.non_null else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"non_null": self.non_null, "distinct": self.distinct, "samples": self.samples}


def profile_series(series: pd.Series) -> ColumnProfile:
    values = [str(v).strip() for v in series.dropna().tolist()]
    values = [v for v in values if v]
    counts = Counter(values)
    distinct_values = list(counts)
    # The rates come from the distinct values so a column of 5000 "M"/"F" rows
    # costs as little as one of two.
    sample = distinct_values[:500]
    n = len(sample)

    def rate(pred) -> float:
        return sum(1 for v in sample if pred(v)) / n if n else 0.0

    current_year = date.today().year
    numbers = [float(v) for v in sample if _NUMBER.match(v)]
    dates = [v for v in sample if is_date(v)]
    return ColumnProfile(
        total=len(series),
        non_null=len(values),
        distinct=len(distinct_values),
        samples=distinct_values[:SAMPLE_VALUES],
        date_rate=len(dates) / n if n else 0.0,
        time_in_date_rate=(sum(1 for v in dates if _HAS_TIME.search(v)) / len(dates)) if dates else 0.0,
        time_rate=rate(lambda v: bool(_TIME_ONLY.match(v))),
        year_rate=rate(lambda v: bool(_YEAR.match(v)) and 1880 <= int(float(v)) <= current_year),
        numeric_rate=len(numbers) / n if n else 0.0,
        letter_rate=rate(lambda v: bool(_LETTER.search(v))),
        zip_rate=rate(lambda v: bool(_ZIP.match(v))),
        npi_rate=rate(lambda v: bool(_NPI.match(v))),
        dea_rate=rate(lambda v: bool(_DEA.match(v))),
        gender_rate=rate(lambda v: v.lower() in GENDER_VALUES),
        lat_rate=(sum(1 for x in numbers if -90 <= x <= 90 and x != int(x)) / n) if n else 0.0,
        lon_rate=(sum(1 for x in numbers if -180 <= x <= 180 and x != int(x)) / n) if n else 0.0,
        values=sample,
        counts={v: counts[v] for v in sample},
    )


_profile_cache: dict[tuple[str, float, int], dict[str, ColumnProfile]] = {}


def profile_file(path: str, delimiter: str = ",", encoding: str = "utf-8") -> dict[str, ColumnProfile]:
    """Profile every column of a source file, from its first `PROFILE_ROWS` rows."""
    p = Path(path)
    stat = p.stat()
    key = (str(p.resolve()), stat.st_mtime, stat.st_size)
    if key in _profile_cache:
        return _profile_cache[key]
    df = pd.read_csv(p, sep=delimiter, encoding=encoding, dtype=str,
                     on_bad_lines="skip", nrows=PROFILE_ROWS)
    profiles = {str(col): profile_series(df[col]) for col in df.columns}
    # One entry per file: drop the profile of an earlier version of this one.
    for stale in [k for k in _profile_cache if k[0] == key[0]]:
        del _profile_cache[stale]
    if len(_profile_cache) >= 32:
        del _profile_cache[next(iter(_profile_cache))]
    _profile_cache[key] = profiles
    return profiles


def value_score(kind: str, prof: ColumnProfile | None) -> tuple[float, bool]:
    """(score 0–1, veto) — how well a column's values fit the field's kind.

    `veto` means the values plainly are not of that kind, whatever the name says.
    An empty column can be neither confirmed nor ruled out.
    """
    if prof is None or prof.non_null == 0:
        return 0.3, False

    looks_like_dates = prof.date_rate >= 0.8
    if kind == "date":
        return prof.date_rate, prof.date_rate < 0.3
    if kind == "datetime":
        return prof.date_rate * (0.6 + 0.4 * prof.time_in_date_rate), prof.date_rate < 0.3
    if kind == "time":
        score = max(prof.time_rate, prof.date_rate * prof.time_in_date_rate)
        return score, score < 0.3
    if kind == "birth":
        score = max(prof.date_rate, prof.year_rate)
        return score, score < 0.3
    if kind == "year":
        return prof.year_rate, prof.year_rate < 0.3
    if kind == "latitude":
        return prof.lat_rate, prof.lat_rate < 0.5
    if kind == "longitude":
        return prof.lon_rate, prof.lon_rate < 0.5
    if kind == "zip":
        return prof.zip_rate, prof.zip_rate < 0.3 or looks_like_dates
    if kind == "npi":
        return prof.npi_rate, prof.npi_rate < 0.3
    if kind == "dea":
        return prof.dea_rate, prof.dea_rate < 0.3
    if kind == "gender":
        few = prof.distinct <= 10
        score = 0.5 * prof.gender_rate + (0.5 if few else 0.0)
        return score, prof.distinct > 20 or looks_like_dates
    if kind == "id":
        # Not necessarily unique — a long-format file repeats each patient.
        spread = min(1.0, prof.distinct_ratio / 0.3)
        completeness = prof.non_null / prof.total if prof.total else 0.0
        return 0.6 * spread + 0.4 * completeness, looks_like_dates or prof.distinct <= 2
    if kind in ("category", "country"):
        compact = prof.distinct <= 50 or prof.distinct_ratio <= 0.2
        score = 1.0 if compact else 0.4
        if kind == "country":
            score *= 0.5 + 0.5 * prof.letter_rate
        continuous = prof.numeric_rate >= 0.9 and prof.distinct_ratio > 0.9 and prof.distinct > 50
        return score, looks_like_dates or continuous
    if kind in ("text", "name"):
        return prof.letter_rate, looks_like_dates or prof.letter_rate < 0.2
    return 0.5, False


# ── Date formats ─────────────────────────────────────────────────────────────
#
# The generated scripts parse every date with the one strptime format set on
# the step, and skip a row whose date doesn't fit it. Finding a date column is
# therefore only half the job: the step's format has to match the column's.

_DATE_PARTS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%m-%d-%Y", "%d.%m.%Y",
               "%Y/%m/%d", "%Y.%m.%d"]
_TIME_PARTS = [" %H:%M:%S", " %H:%M", " %H:%M:%S.%f", "T%H:%M:%S", "T%H:%M", "T%H:%M:%S.%f"]
# In order of preference when several fit equally: day before month, which is
# how the European datasets this tool is used with write dates.
DATE_FORMATS = (
    _DATE_PARTS
    + [d + t for d in _DATE_PARTS for t in _TIME_PARTS]
    + ["%Y%m%d", "%d/%m/%y", "%m/%d/%y", "%d-%m-%y", "%d.%m.%y",
       "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%d-%b-%y", "%d %B %Y", "%B %d, %Y"]
)
# Share of a column's values the chosen format must parse for the match to be
# applied without asking.
MIN_FORMAT_RATE = 0.95
DATE_KINDS = {"date", "datetime", "birth"}


@dataclass
class DateFormatGuess:
    format: str | None
    rate: float
    # The same format with day and month swapped, when it parses the values
    # just as well (every day so far is 12 or under).
    alternative: str | None


def _parses(value: str, fmt: str) -> bool:
    try:
        datetime.strptime(value, fmt)
        return True
    except ValueError:
        return False


def _day_month_swapped(a: str, b: str) -> bool:
    return a.replace("%d", "\0").replace("%m", "%d").replace("\0", "%m") == b


def infer_date_format(values: list[str], allow_year: bool = False) -> DateFormatGuess:
    """The strptime format that parses the most of `values`.

    `allow_year` admits `%Y` for a date of birth given as a year only.
    """
    sample = [v for v in values if v][:200]
    if not sample:
        return DateFormatGuess(None, 0.0, None)
    best, best_rate, ties = None, 0.0, []
    for fmt in DATE_FORMATS + (["%Y"] if allow_year else []):
        rate = sum(1 for v in sample if _parses(v, fmt)) / len(sample)
        if rate > best_rate:
            best, best_rate, ties = fmt, rate, []
        elif best and rate == best_rate and rate > 0:
            ties.append(fmt)
    alternative = next((t for t in ties if _day_month_swapped(best, t)), None) if best else None
    return DateFormatGuess(best, best_rate, alternative)


def _downgrade(match: dict[str, Any], warning: str) -> None:
    if match["status"] == "auto":
        match["status"] = "suggest"
    match["warning"] = warning


def apply_date_formats(
    fields: list[FieldRequest],
    matches: dict[str, dict[str, Any]],
    sources: dict[str, SourceColumns],
) -> None:
    """Settle the date format of every matched date column, in place.

    Each match gets `detected_format` (what the values are written in),
    `date_format` (what to set the step's format to on applying it, or None to
    leave it) and `warning`. Fields sharing one step format form a
    `format_group`; a format already relied on by a mapped column of the group
    (`format_locked`), or claimed by a better match in this run, is not changed
    — a column written differently is then offered, not applied.
    """
    for m in matches.values():
        m.setdefault("date_format", None)
        m.setdefault("detected_format", None)
        m.setdefault("warning", None)

    dated = [f for f in fields
             if f.format_group and f.spec.kind in DATE_KINDS and matches[f.key]["column"]]
    # Confident matches claim their group's format first.
    dated.sort(key=lambda f: (matches[f.key]["status"] != "auto", -matches[f.key]["score"]))
    claimed: dict[str, str] = {}
    for f in dated:
        m = matches[f.key]
        prof = sources[m["filename"]].profiles.get(m["column"])
        guess = infer_date_format(prof.values if prof else [], allow_year=f.spec.kind == "birth")
        m["detected_format"] = guess.format
        if guess.format is None:
            _downgrade(m, "none of the values could be read as a date")
            continue

        group = f.format_group
        current = claimed.get(group, f.date_format)
        if guess.format != current:
            if group in claimed or f.format_locked:
                _downgrade(m, f"values are written as {guess.format}, but this step's date format "
                              f"{current} is used by another date column — rows that don't fit it are skipped")
                continue
            m["date_format"] = guess.format
        claimed[group] = guess.format

        if guess.rate < MIN_FORMAT_RATE:
            _downgrade(m, f"only {round(guess.rate * 100)}% of the values fit {guess.format}; "
                          "rows that don't are skipped")
        elif guess.alternative:
            m["warning"] = (f"day and month could be either way round ({guess.format} or "
                            f"{guess.alternative}); check the date format")


# ── Concept coverage ─────────────────────────────────────────────────────────
#
# A field whose values are mapped to concepts afterwards (race, ethnicity,
# visit concept...) is checked by doing that mapping: the column whose values
# the concept matcher maps in the field's domain is the one that holds it.

# The most frequent values of a column that are checked.
COVERAGE_VALUES = 30
# Name-matched candidates per field that are checked.
COVERAGE_CANDIDATES = 3
# Below this share of rows mapped — over at least COVERAGE_MIN_CHECKED values —
# a column is not the field's, whatever its name says.
COVERAGE_VETO = 0.1
COVERAGE_MIN_CHECKED = 3
# At or above this, with STRONG_MATCHED values mapped outright, the values
# confirm the name: the score gets COVERAGE_BONUS.
STRONG_COVERAGE = 0.8
STRONG_MATCHED = 3
COVERAGE_BONUS = 1.1
# The value scan offers a column only above these.
SCAN_MIN_COVERAGE = 0.6
SCAN_MIN_MATCHED = 3
SCAN_MAX_DISTINCT = 50

# (target, values) → each value's status from the concept matcher:
# "auto_accept", "review" or "unmapped". Supplied by the caller (the API).
ValueConcepts = Callable[[ConceptTarget, list[str]], Awaitable[dict[str, str]]]


@dataclass
class Coverage:
    """How much of a column the concept matcher maps in a field's domain.

    "Unknown", "Other", empty and the like (`is_no_value`) are left out of both
    sides: they are set to concept 0 whichever column holds them. A column of
    nothing else has no coverage at all — rate 0 and not `telling` — rather
    than 0 of 0 read as everything mapped.
    """
    rate: float      # share of the rows checked; a review counts half
    matched: int     # distinct values mapped outright
    checked: int     # distinct values checked
    rows: int        # rows those values cover

    @property
    def telling(self) -> bool:
        """Enough to go by: two values neither of which maps say little, and
        none say nothing."""
        return self.rows > 0 and (self.checked >= COVERAGE_MIN_CHECKED or self.matched > 0)

    @property
    def veto(self) -> bool:
        return self.checked >= COVERAGE_MIN_CHECKED and self.rate < COVERAGE_VETO

    @property
    def strong(self) -> bool:
        return self.rate >= STRONG_COVERAGE and self.matched >= STRONG_MATCHED

    @property
    def scan_worthy(self) -> bool:
        return self.rate >= SCAN_MIN_COVERAGE and self.matched >= SCAN_MIN_MATCHED


CoverageKey = tuple[ConceptTarget, str, str]   # target, filename, column


def top_values(prof: ColumnProfile, limit: int = COVERAGE_VALUES) -> list[tuple[str, int]]:
    """The column's most frequent values that could map to a concept."""
    vals = [(v, prof.counts.get(v, 1)) for v in prof.values if not is_no_value(v)]
    return sorted(vals, key=lambda vc: -vc[1])[:limit]


def coverage_of(values: list[tuple[str, int]], statuses: dict[str, str]) -> Coverage:
    values = [(v, c) for v, c in values if not is_no_value(v)]
    rows = sum(c for _, c in values)
    if rows == 0:
        return Coverage(rate=0.0, matched=0, checked=0, rows=0)
    weight = {"auto_accept": 1.0, "review": 0.5}
    covered = sum(c * weight.get(statuses.get(v, ""), 0.0) for v, c in values)
    return Coverage(
        rate=covered / rows,
        matched=sum(1 for v, _ in values if statuses.get(v) == "auto_accept"),
        checked=len(values),
        rows=rows,
    )


async def fetch_coverage(
    value_concepts: ValueConcepts,
    wanted: dict[ConceptTarget, set[tuple[str, str]]],
    sources: dict[str, SourceColumns],
) -> dict[CoverageKey, Coverage]:
    """Coverage of each (filename, column) under its target — one call per target."""
    async def one(target: ConceptTarget, cols: set[tuple[str, str]]) -> dict[CoverageKey, Coverage]:
        tops = {(fn, col): top_values(sources[fn].profiles[col]) for fn, col in cols}
        values = sorted({v for vs in tops.values() for v, _ in vs})
        statuses = await value_concepts(target, values) if values else {}
        return {(target, fn, col): coverage_of(vs, statuses) for (fn, col), vs in tops.items()}

    out: dict[CoverageKey, Coverage] = {}
    for part in await asyncio.gather(*(one(t, cols) for t, cols in wanted.items() if cols)):
        out.update(part)
    return out


# ── Matching ─────────────────────────────────────────────────────────────────

@dataclass
class Candidate:
    field: str
    column: str
    filename: str
    score: float
    name: float
    value: float
    veto: bool
    coverage: Coverage | None = None


@dataclass
class FieldRequest:
    key: str
    spec: FieldSpec
    spec_key: str
    filenames: list[str]
    hint: str = ""
    # The step's date format for this field, the fields sharing it, and whether
    # an already-mapped column relies on it (see `apply_date_formats`).
    date_format: str | None = None
    format_group: str | None = None
    format_locked: bool = False


@dataclass
class SourceColumns:
    """The columns of one source file, with their profiles."""
    filename: str
    profiles: dict[str, ColumnProfile]


def _coverage_reason(cov: Coverage, target: ConceptTarget) -> str:
    return f"{cov.matched} of {cov.checked} values map to {target.domain} concepts ({round(cov.rate * 100)}% of rows)"


def _reason(c: Candidate, spec: FieldSpec) -> str:
    bits = [f"name {round(c.name * 100)}%"]
    if c.coverage is not None and spec.concept:
        bits.append(_coverage_reason(c.coverage, spec.concept))
    elif c.veto:
        bits.append(f"values don't look like {spec.kind}")
    else:
        bits.append(f"values {round(c.value * 100)}% {spec.kind}")
    return ", ".join(bits)


def score_candidates(
    fields: list[FieldRequest],
    sources: dict[str, SourceColumns],
    exclude: set[str],
    descriptions: dict[str, str],
    coverage: dict[CoverageKey, Coverage] | None = None,
) -> dict[str, list[Candidate]]:
    """`coverage`, where it has a column under a field's concept target,
    replaces the value check of that field's kind: whether the values map is
    the better question than whether they look like a category."""
    coverage = coverage or {}
    out: dict[str, list[Candidate]] = {}
    for f in fields:
        cands: list[Candidate] = []
        for fn in f.filenames:
            src = sources.get(fn)
            if not src:
                continue
            for col, prof in src.profiles.items():
                if col in exclude:
                    continue
                n = best_name_score(col, f.spec, descriptions.get(col, ""), f.hint)
                if n < MIN_NAME_SCORE:
                    continue
                v, veto = value_score(f.spec.kind, prof)
                cov = coverage.get((f.spec.concept, fn, col)) if f.spec.concept else None
                if cov is not None and not cov.telling:
                    cov = None
                if cov is not None:
                    v, veto = cov.rate, veto or cov.veto
                score = n * (1 - VALUE_WEIGHT + VALUE_WEIGHT * v) * (VETO_FACTOR if veto else 1.0)
                if cov is not None and cov.strong and not veto:
                    score = min(1.0, score * COVERAGE_BONUS)
                cands.append(Candidate(f.key, col, fn, round(score, 3), n, v, veto, cov))
        cands.sort(key=lambda c: c.score, reverse=True)
        out[f.key] = cands
    return out


def coverage_wanted(
    candidates: dict[str, list[Candidate]],
    fields: list[FieldRequest],
) -> dict[ConceptTarget, set[tuple[str, str]]]:
    """The leading candidates of each concept field, to check by their values."""
    wanted: dict[ConceptTarget, set[tuple[str, str]]] = {}
    for f in fields:
        if not f.spec.concept:
            continue
        top = [c for c in candidates[f.key] if c.score >= SUGGEST_THRESHOLD * 0.8][:COVERAGE_CANDIDATES]
        wanted.setdefault(f.spec.concept, set()).update((c.filename, c.column) for c in top)
    return wanted


def assign(candidates: dict[str, list[Candidate]]) -> dict[str, Candidate]:
    """One column per field, one field per column, best scores first."""
    every = sorted((c for cs in candidates.values() for c in cs), key=lambda c: c.score, reverse=True)
    chosen: dict[str, Candidate] = {}
    taken: set[tuple[str, str]] = set()
    for c in every:
        if c.score < SUGGEST_THRESHOLD:
            break
        if c.field in chosen or (c.filename, c.column) in taken:
            continue
        chosen[c.field] = c
        taken.add((c.filename, c.column))
    return chosen


def heuristic_match(
    fields: list[FieldRequest],
    sources: dict[str, SourceColumns],
    exclude: set[str],
    descriptions: dict[str, str],
    coverage: dict[CoverageKey, Coverage] | None = None,
) -> dict[str, dict[str, Any]]:
    by_key = {f.key: f for f in fields}
    candidates = score_candidates(fields, sources, exclude, descriptions, coverage)
    chosen = assign(candidates)
    taken = {(c.filename, c.column) for c in chosen.values()}

    matches: dict[str, dict[str, Any]] = {}
    for f in fields:
        pick = chosen.get(f.key)
        alternatives = [
            {"column": c.column, "filename": c.filename, "score": c.score}
            for c in candidates[f.key]
            if c.score >= SUGGEST_THRESHOLD * 0.8
            and (c.filename, c.column) not in taken
            and not (pick and c.column == pick.column and c.filename == pick.filename)
        ][:3]
        if pick:
            matches[f.key] = {
                "column": pick.column,
                "filename": pick.filename,
                "score": pick.score,
                "status": "auto" if pick.score >= AUTO_THRESHOLD else "suggest",
                "source": "heuristic",
                "reason": _reason(pick, by_key[f.key].spec),
                "alternatives": alternatives,
            }
        else:
            matches[f.key] = {
                "column": None, "filename": None, "score": 0.0, "status": "none",
                "source": "heuristic", "reason": "no column looks like this field",
                "alternatives": alternatives,
            }
    return matches


# ── LLM fallback ─────────────────────────────────────────────────────────────

LLM_STATUS_TTL = 300.0
_llm_status: dict[str, Any] = {}


def llm_configured() -> bool:
    return bool(settings.openai_api_key)


def _set_llm_status(status: str, detail: str | None = None) -> dict[str, Any]:
    _llm_status.clear()
    _llm_status.update({
        "status": status,
        "model": settings.openai_model,
        "detail": detail,
        "checked_at": time.time(),
    })
    return dict(_llm_status)


def _error_detail(exc: Exception) -> str:
    # The OpenAI client's messages carry the whole response body; the first
    # sentence says what went wrong.
    message = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
    status = getattr(exc, "status_code", None)
    if status == 401:
        return "Invalid OpenAI API key"
    if status == 404:
        return f"Model {settings.openai_model!r} is not available to this API key"
    return message.split("\n")[0][:200]


async def llm_health(refresh: bool = False) -> dict[str, Any]:
    """Whether the LLM fallback can be used: `ready`, `not_configured` or `error`.

    Verified with one cheap request (fetching the configured model's metadata),
    cached for `LLM_STATUS_TTL` seconds so opening a step costs nothing.
    """
    if not llm_configured():
        return _set_llm_status("not_configured", "Set OPENAI_API_KEY in backend/.env")
    fresh = _llm_status.get("model") == settings.openai_model and \
        time.time() - _llm_status.get("checked_at", 0) < LLM_STATUS_TTL
    if fresh and not refresh:
        return dict(_llm_status)
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=10.0, max_retries=0)
        await client.models.retrieve(settings.openai_model)
    except Exception as exc:  # noqa: BLE001 — any failure means "not usable"
        return _set_llm_status("error", _error_detail(exc))
    return _set_llm_status("ready")


def _llm_prompt(
    fields: list[FieldRequest],
    columns: list[tuple[str, str]],
    sources: dict[str, SourceColumns],
    descriptions: dict[str, str],
    table: str,
) -> str:
    multi_file = len({fn for fn, _ in columns}) > 1
    column_lines = []
    for fn, col in columns:
        prof = sources[fn].profiles[col]
        entry: dict[str, Any] = {"column": col, "sample_values": prof.samples}
        if multi_file:
            entry["file"] = fn
        if descriptions.get(col):
            entry["description"] = descriptions[col]
        column_lines.append(json.dumps(entry, ensure_ascii=False))
    field_lines = []
    for f in fields:
        line = f"- {f.key}: {f.spec.description}"
        if f.hint:
            line += f" (for the visit labelled {f.hint!r})"
        if multi_file:
            line += f" [look in: {', '.join(f.filenames)}]"
        field_lines.append(line)
    names = sorted({col for _, col in columns})
    return (
        f"You are mapping a clinical source dataset to the OMOP CDM {table.upper()} table.\n\n"
        "SOURCE COLUMNS (name and a few sample values):\n" + "\n".join(column_lines) + "\n\n"
        "TARGET FIELDS (key: what it holds):\n" + "\n".join(field_lines) + "\n\n"
        "For each target field, pick the source column that holds it, judging by the column's name, "
        "description and sample values, or null when no column does. Use each column for at most one field.\n"
        "The \"column\" you answer must be copied exactly from this list of source column names, "
        "never a target field name or a made-up name: " + json.dumps(names, ensure_ascii=False) + "\n\n"
        'Answer in JSON: {"matches": {"<target field key>": {"column": "<source column name or null>", '
        + ('"file": "<file name>", ' if multi_file else "")
        + '"confidence": <0 to 1>}}}'
    )


async def llm_match(
    fields: list[FieldRequest],
    sources: dict[str, SourceColumns],
    exclude: set[str],
    taken: set[tuple[str, str]],
    descriptions: dict[str, str],
    table: str,
) -> dict[str, dict[str, Any]]:
    """Ask the LLM for the fields the heuristics could not settle.

    Raises on any API error; the caller decides what to do with it.
    """
    allowed: list[tuple[str, str]] = []
    for fn in dict.fromkeys(fn for f in fields for fn in f.filenames):
        src = sources.get(fn)
        if not src:
            continue
        allowed += [(fn, col) for col in src.profiles if col not in exclude and (fn, col) not in taken]
    if not allowed:
        return {}

    client = AsyncOpenAI(api_key=settings.openai_api_key, timeout=60.0, max_retries=1)
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[{"role": "user", "content": _llm_prompt(fields, allowed, sources, descriptions, table)}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = response.choices[0].message.content or "{}"
    try:
        answer = json.loads(content).get("matches", {})
    except (json.JSONDecodeError, AttributeError):
        logger.warning("column matcher: LLM answered with something other than JSON: %s", content[:200])
        return {}
    if not isinstance(answer, dict):
        return {}

    by_key = {f.key: f for f in fields}
    allowed_set = set(allowed)
    used: set[tuple[str, str]] = set()
    out: dict[str, dict[str, Any]] = {}
    for key, pick in answer.items():
        f = by_key.get(key)
        if not f or not isinstance(pick, dict) or not pick.get("column"):
            continue
        col = str(pick["column"])
        fn = str(pick.get("file") or "")
        if not fn:
            # Single file, or the model left the file out: the first file of the
            # field's own that has the column.
            fn = next((name for name in f.filenames if (name, col) in allowed_set), "")
        if (fn, col) not in allowed_set or fn not in f.filenames or (fn, col) in used:
            continue  # an invented, excluded or doubly-used column
        used.add((fn, col))
        try:
            confidence = float(pick.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        v, veto = value_score(f.spec.kind, sources[fn].profiles.get(col))
        confident = confidence >= 0.7 and not veto
        out[key] = {
            "column": col,
            "filename": fn,
            "score": round(min(1.0, max(0.0, confidence)), 3),
            "status": "auto" if confident else "suggest",
            "source": "llm",
            "reason": "picked by AI" + ("" if not veto else f", but values don't look like {f.spec.kind}"),
            "alternatives": [],
        }
    return out


# ── Value scan ───────────────────────────────────────────────────────────────

def _scannable(prof: ColumnProfile) -> bool:
    """A short list of words — what a concept field's column looks like."""
    return (prof.non_null > 0 and prof.letter_rate >= 0.5 and prof.date_rate < 0.5
            and prof.distinct <= SCAN_MAX_DISTINCT and len(top_values(prof)) >= SCAN_MIN_MATCHED)


def scan_wanted(
    fields: list[FieldRequest],
    sources: dict[str, SourceColumns],
    exclude: set[str],
    taken: set[tuple[str, str]],
) -> dict[str, set[tuple[str, str]]]:
    """Per field, every column the value scan checks: unclaimed and scannable."""
    return {
        f.key: {
            (fn, col)
            for fn in f.filenames if fn in sources
            for col, prof in sources[fn].profiles.items()
            if col not in exclude and (fn, col) not in taken and _scannable(prof)
        }
        for f in fields
    }


def apply_scan(
    fields: list[FieldRequest],
    columns: dict[str, set[tuple[str, str]]],
    matches: dict[str, dict[str, Any]],
    coverage: dict[CoverageKey, Coverage],
    taken: set[tuple[str, str]],
) -> None:
    """Offer each field the scanned column its values back most, in place.

    Only ever a suggestion: nothing in the name says it is the field. A
    suggestion the name found and the values back is kept; one they don't
    back gives way and becomes an alternative.
    """
    options = []
    for f in fields:
        for fn, col in columns[f.key]:
            cov = coverage.get((f.spec.concept, fn, col))
            if cov is not None and cov.scan_worthy:
                # Most rows mapped first: NATIONALITY (every patient) before
                # NATIONALITY_OTHER (the few with "Other").
                options.append((cov.rate * cov.rows, f, fn, col, cov))
    options.sort(key=lambda o: o[0], reverse=True)
    used = set(taken)
    done: set[str] = set()
    for _, f, fn, col, cov in options:
        if f.key in done or (fn, col) in used:
            continue
        m = matches[f.key]
        if (m["filename"], m["column"]) == (fn, col):
            done.add(f.key)
            continue
        prior = coverage.get((f.spec.concept, m["filename"], m["column"])) if m["column"] else None
        if prior is not None and prior.scan_worthy:
            done.add(f.key)
            continue
        before = [{"column": m["column"], "filename": m["filename"], "score": m["score"]}] if m["column"] else []
        matches[f.key] = {
            "column": col,
            "filename": fn,
            "score": round(cov.rate, 3),
            "status": "suggest",
            "source": "values",
            "reason": f"found by its values: {_coverage_reason(cov, f.spec.concept)}",
            "alternatives": [*before, *m["alternatives"]][:3],
        }
        done.add(f.key)
        used.add((fn, col))


async def match_columns(
    table: str,
    fields: list[FieldRequest],
    sources: dict[str, SourceColumns],
    exclude: set[str],
    descriptions: dict[str, str],
    use_llm: bool,
    value_concepts: ValueConcepts | None = None,
) -> dict[str, Any]:
    """`value_concepts`, when given, checks concept fields by their values
    (see "Concept coverage"); without it, or when it fails, the matching is
    as if there were no such check."""
    coverage: dict[CoverageKey, Coverage] = {}
    value_error: str | None = None
    scanned = 0

    async def check(wanted: dict[ConceptTarget, set[tuple[str, str]]]) -> None:
        nonlocal value_error
        if value_concepts is None or value_error:
            return
        missing = {t: {c for c in cols if (t, *c) not in coverage} for t, cols in wanted.items()}
        try:
            coverage.update(await fetch_coverage(value_concepts, missing, sources))
        except Exception as exc:  # noqa: BLE001 — match without the check
            value_error = getattr(exc, "detail", None) or _error_detail(exc)
            logger.warning("column matcher: value check failed: %s", value_error)

    await check(coverage_wanted(score_candidates(fields, sources, exclude, descriptions), fields))
    matches = heuristic_match(fields, sources, exclude, descriptions, coverage)

    unresolved = [f for f in fields if matches[f.key]["status"] != "auto"]
    llm_used = False
    llm_error: str | None = None
    if use_llm and unresolved and llm_configured():
        taken = {(m["filename"], m["column"]) for m in matches.values() if m["status"] == "auto"}
        try:
            picks = await llm_match(unresolved, sources, exclude, taken, descriptions, table)
            llm_used = True
            if _llm_status.get("status") != "ready":
                _set_llm_status("ready")
        except Exception as exc:  # noqa: BLE001 — fall back to the heuristic results
            llm_error = _error_detail(exc)
            logger.warning("column matcher: LLM fallback failed: %s", llm_error)
            _set_llm_status("error", llm_error)
            picks = {}
        for key, pick in picks.items():
            prior = matches[key]
            if prior["column"] and (prior["column"], prior["filename"]) != (pick["column"], pick["filename"]):
                pick["alternatives"] = [
                    {"column": prior["column"], "filename": prior["filename"], "score": prior["score"]},
                    *prior["alternatives"],
                ][:3]
            else:
                pick["alternatives"] = prior["alternatives"]
            matches[key] = pick

    # The AI's picks for concept fields are held to their values too.
    by_key = {f.key: f for f in fields}
    ai_picks = [by_key[k] for k, m in matches.items() if m["source"] == "llm" and by_key[k].spec.concept]
    wanted: dict[ConceptTarget, set[tuple[str, str]]] = {}
    for f in ai_picks:
        wanted.setdefault(f.spec.concept, set()).add((matches[f.key]["filename"], matches[f.key]["column"]))
    await check(wanted)
    for f in ai_picks:
        m = matches[f.key]
        cov = coverage.get((f.spec.concept, m["filename"], m["column"]))
        if cov is None or not cov.telling:
            continue
        m["reason"] += f", {_coverage_reason(cov, f.spec.concept)}"
        if cov.veto:
            m["status"] = "suggest"

    # Concept fields still open: look for their values anywhere in the file.
    to_scan = [f for f in fields if f.spec.concept and matches[f.key]["status"] != "auto"]
    if to_scan and value_concepts is not None and not value_error:
        taken = {(m["filename"], m["column"]) for m in matches.values() if m["status"] == "auto"}
        columns = scan_wanted(to_scan, sources, exclude, taken)
        scan_targets: dict[ConceptTarget, set[tuple[str, str]]] = {}
        for f in to_scan:
            scan_targets.setdefault(f.spec.concept, set()).update(columns[f.key])
        await check(scan_targets)
        if not value_error:
            apply_scan(to_scan, columns, matches, coverage, taken)
            scanned = len({c for cols in columns.values() for c in cols})

    apply_date_formats(fields, matches, sources)
    return {
        "matches": matches,
        "llm_available": llm_configured(),
        "llm_used": llm_used,
        "llm_error": llm_error,
        "value_check": {
            "used": value_concepts is not None and not value_error and bool(coverage),
            "error": value_error,
            "scanned_columns": scanned,
        },
    }
