import asyncio
import json
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import column_matcher as cm
from app.services.column_matcher import (
    FIELD_SPECS,
    FieldRequest,
    SourceColumns,
    heuristic_match,
    match_columns,
    name_score,
    profile_series,
    tokenize,
)

N = 60


def source(columns: dict[str, list]) -> dict[str, SourceColumns]:
    df = pd.DataFrame(columns, dtype="object")
    return {"f.csv": SourceColumns("f.csv", {c: profile_series(df[c]) for c in df.columns})}


def fields(table: str, keys: list[str] | None = None, **hints: str) -> list[FieldRequest]:
    specs = FIELD_SPECS[table]
    return [FieldRequest(k, specs[k], k, ["f.csv"], hints.get(k, "")) for k in (keys or specs)]


def picks(matches: dict) -> dict[str, str | None]:
    return {k: m["column"] for k, m in matches.items() if m["status"] == "auto"}


def test_tokenize_splits_camel_case_and_drops_filler():
    assert tokenize("PatBirthDt") == ["pat", "birth", "dt"]
    assert tokenize("date_of_death") == ["date", "death"]
    assert tokenize("addr1") == ["addr", "1"]


@pytest.mark.parametrize("column,synonym", [
    ("DOB", "dob"),
    ("Patient-ID", "patient id"),
    ("zip_code", "zipcode"),
    ("person_adress", "adress"),
])
def test_name_score_exact_and_token_matches(column, synonym):
    assert name_score(column, synonym) >= 0.75


def test_name_score_rejects_lookalikes():
    assert name_score("zipcode", "cod") < 0.45
    assert name_score("zipcode", "visit code") < 0.45


def test_person_fields_matched_by_name_and_values():
    src = source({
        "patient_id": [str(i) for i in range(N)],
        "Sex": ["M", "F"] * (N // 2),
        "dob": [f"1980-01-{i % 28 + 1:02d}" for i in range(N)],
        "race": ["White", "Black", "Asian"] * (N // 3),
    })
    assert picks(heuristic_match(fields("person"), src, set(), {})) == {
        "person_id": "patient_id",
        "gender_concept_id": "Sex",
        "year_of_birth": "dob",
        "race_concept_id": "race",
    }


def test_values_veto_a_name_that_fits():
    # `death_date` holding free text is not the death date.
    src = source({"death_date": ["unknown", "see notes", "n/a"] * (N // 3)})
    m = heuristic_match(fields("death", ["death_date_col"]), src, set(), {})["death_date_col"]
    assert m["status"] != "auto"


def test_one_column_per_field():
    # `city` is the person's city; the care site's city must not reuse it.
    src = source({"city": ["Athens", "Patras"] * (N // 2)})
    m = heuristic_match(fields("location", ["city_col", "cs_city_col"]), src, set(), {})
    assert m["city_col"]["column"] == "city"
    assert m["cs_city_col"]["column"] is None


def test_excluded_columns_are_skipped():
    src = source({"gender": ["M", "F"] * (N // 2)})
    m = heuristic_match(fields("person", ["gender_concept_id"]), src, {"gender"}, {})
    assert m["gender_concept_id"]["column"] is None


def test_hint_tells_visit_dates_apart():
    src = source({
        "baseline_date": ["2020-01-01"] * N,
        "followup_date": ["2021-01-01"] * N,
    })
    specs = FIELD_SPECS["visit_occurrence"]
    reqs = [
        FieldRequest("0:date_col", specs["date_col"], "date_col", ["f.csv"], "Baseline"),
        FieldRequest("1:date_col", specs["date_col"], "date_col", ["f.csv"], "Followup"),
    ]
    assert picks(heuristic_match(reqs, src, set(), {})) == {
        "0:date_col": "baseline_date",
        "1:date_col": "followup_date",
    }


def test_description_can_name_a_cryptic_column():
    src = source({"v07": [f"19{50 + i % 40}-03-01" for i in range(N)]})
    m = heuristic_match(fields("person", ["year_of_birth"]), src, set(), {"v07": "Date of birth of the patient"})
    assert m["year_of_birth"]["column"] == "v07"


def test_description_must_name_the_field_as_a_phrase():
    # "visit" and "type" are both in it, but it is not about the visit type.
    src = source({"fp011SQ002_SQ001": ["Rheumatologist", "Wondcontrole", "CT-Scan"] * (N // 3)})
    desc = {"fp011SQ002_SQ001": "11. What type of treatment did you visit the hospital for during the day?"}
    m = heuristic_match(fields("visit_occurrence", ["visit_concept_source_col"]), src, set(), desc)
    assert m["visit_concept_source_col"]["column"] is None


# ── Concept coverage ─────────────────────────────────────────────────────────

# What the concept matcher fills in outright, per domain.
MAPPED = {
    "Race": {"White", "Black", "Asian"},
    "Ethnicity": {"PRT", "GR", "NL", "GB", "BRA", "Greek", "The Netherlands", "German", "Irish"},
    "Visit": {"Inpatient", "Outpatient", "Emergency Room"},
}


def fake_concepts(mapped=MAPPED, calls=None):
    async def statuses(target, values):
        if calls is not None:
            calls.append((target.domain, list(values)))
        return {v: "auto_accept" if v in mapped.get(target.domain, set()) else "unmapped" for v in values}
    return statuses


def match_values(table, src, keys, value_concepts, descriptions=None, exclude=frozenset()):
    return asyncio.run(match_columns(
        table, fields(table, keys), src, set(exclude), descriptions or {}, False, value_concepts,
    ))


def harmonized_prospective():
    """The demographics of the dataset that prompted this check."""
    return source({
        "DEMOGR_ETHNICITY": ["White European", "Greek", "The Netherlands", "Unknown", "Other", "Black",
                             "White non-European", "Asian or Asian British: Indian, Pakistani"] * 10,
        "DEMOGR_ETHNICITY_HARMONIZED": ["White", "White", "Other", "Asian", "Black", "White", "White", "Black"] * 10,
        "DEMOGR_NATIONALITY": ["PRT", "GR", "NL", "Other", "GB", "BRA", "GR", "NL"] * 10,
        "DEMOGR_NATIONALITY_OTHER": ["", "", "", "German", "", "", "", ""] * 10,
    })


def test_no_values_count_on_neither_side_of_coverage():
    statuses = {"White": "auto_accept", "Mixed": "unmapped", "Unknown": "auto_accept", "Other": "auto_accept"}
    cov = cm.coverage_of([("White", 30), ("Mixed", 10), ("Unknown", 50), ("Other", 10), ("N/A", 5)], statuses)
    assert (cov.rate, cov.matched, cov.checked, cov.rows) == (0.75, 1, 2, 40)


def test_a_column_of_only_no_values_has_no_coverage():
    cov = cm.coverage_of([("Unknown", 50), ("Other", 10), ("null", 3)], {"Unknown": "auto_accept"})
    assert cov.rate == 0.0 and cov.rows == 0
    assert not cov.telling and not cov.veto and not cov.strong and not cov.scan_worthy


def test_column_of_only_no_values_neither_helps_nor_hurts():
    src = source({"race": ["Unknown", "Other", "N/A"] * (N // 3)})
    m = match_values("person", src, ["race_concept_id"], fake_concepts())
    without = asyncio.run(match_columns("person", fields("person", ["race_concept_id"]), src, set(), {}, False))
    assert m["matches"]["race_concept_id"]["score"] == without["matches"]["race_concept_id"]["score"]
    assert "values map" not in m["matches"]["race_concept_id"]["reason"]


def test_values_settle_race_and_ethnicity():
    result = match_values("person", harmonized_prospective(),
                          ["race_concept_id", "ethnicity_concept_id"], fake_concepts())
    assert picks(result["matches"]) == {
        "race_concept_id": "DEMOGR_ETHNICITY_HARMONIZED",
        "ethnicity_concept_id": "DEMOGR_NATIONALITY",
    }
    assert "map to Ethnicity concepts" in result["matches"]["ethnicity_concept_id"]["reason"]
    assert result["value_check"]["used"] is True


def test_without_value_check_names_decide():
    m = asyncio.run(match_columns("person", fields("person", ["ethnicity_concept_id"]),
                                  harmonized_prospective(), set(), {}, False))["matches"]
    assert m["ethnicity_concept_id"]["column"] == "DEMOGR_ETHNICITY"


def test_values_that_map_to_nothing_are_only_suggested():
    src = source({"visit_type": ["Rheumatologist", "Wondcontrole", "CT-Scan", "Hysterectomy"] * (N // 4)})
    m = match_values("visit_occurrence", src, ["visit_concept_source_col"], fake_concepts())
    assert m["matches"]["visit_concept_source_col"]["status"] != "auto"


def test_scan_finds_a_cryptic_column_and_only_suggests_it():
    src = source({
        "Q7": ["Inpatient", "Outpatient", "Emergency Room", "Unknown"] * (N // 4),
        "Q8": ["mild", "moderate", "severe", "none"] * (N // 4),
    })
    m = match_values("visit_occurrence", src, ["visit_concept_source_col"], fake_concepts())
    vc = m["matches"]["visit_concept_source_col"]
    assert (vc["column"], vc["status"], vc["source"]) == ("Q7", "suggest", "values")
    assert m["value_check"]["scanned_columns"] == 2


def test_scan_finds_nothing_leaves_the_field_empty():
    src = source({"Q8": ["mild", "moderate", "severe"] * (N // 3)})
    m = match_values("visit_occurrence", src, ["visit_concept_source_col"], fake_concepts())
    assert m["matches"]["visit_concept_source_col"]["column"] is None


def test_scan_ignores_columns_with_few_mapped_values():
    # One value that happens to map ("mg") says nothing about the column.
    src = source({"PAT_MED_UNIT_1": ["mg", "tablet", "mg", "puff"] * (N // 4)})
    m = match_values("person", src, ["ethnicity_concept_id"], fake_concepts({"Ethnicity": {"mg"}}))
    assert m["matches"]["ethnicity_concept_id"]["column"] is None


def test_scan_prefers_the_column_covering_more_rows():
    src = source({
        "c1": ["PRT", "GR", "NL", "GB"] * (N // 4),
        "c2": ["German", "Irish", "Greek", ""] * (N // 4),
    })
    src["f.csv"].profiles["c2"].counts = {"German": 1, "Irish": 1, "Greek": 1}
    m = match_values("person", src, ["ethnicity_concept_id"], fake_concepts())
    e = m["matches"]["ethnicity_concept_id"]
    assert e["column"] == "c1" and e["alternatives"] == []


def test_columns_matched_with_confidence_are_not_scanned():
    calls = []
    src = source({"gender": ["M", "F"] * (N // 2), "Q1": ["Inpatient", "Outpatient", "Emergency Room"] * (N // 3)})
    match_values("person", src, ["gender_concept_id"], fake_concepts(calls=calls))
    # The gender column is checked; nothing is left open to scan for.
    assert [d for d, _ in calls] == ["Gender"]


def test_value_check_failure_keeps_name_matching():
    async def down(target, values):
        raise RuntimeError("concept matcher down")
    m = match_values("person", harmonized_prospective(), ["ethnicity_concept_id"], down)
    assert m["matches"]["ethnicity_concept_id"]["column"] == "DEMOGR_ETHNICITY"
    assert m["value_check"] == {"used": False, "error": "concept matcher down", "scanned_columns": 0}


def test_non_concept_fields_are_not_value_checked():
    calls = []
    src = source({"Visit Occurrence": ["T0", "T3", "T6"] * (N // 3)})
    m = match_values("visit_occurrence", src, ["visit_source_col"], fake_concepts(calls=calls))
    assert calls == [] and m["matches"]["visit_source_col"]["column"] == "Visit Occurrence"


class FakeCompletions:
    def __init__(self, answer):
        self.answer = answer

    async def create(self, **_):
        content = self.answer if isinstance(self.answer, str) else json.dumps(self.answer)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def fake_openai(monkeypatch, answer):
    class FakeClient:
        def __init__(self, **_):
            self.chat = SimpleNamespace(completions=FakeCompletions(answer))

    monkeypatch.setattr(cm, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(cm.settings, "openai_api_key", "sk-test")


def test_llm_fills_what_heuristics_cannot(monkeypatch):
    fake_openai(monkeypatch, {"matches": {
        "person_id": {"column": "CODE", "confidence": 0.9},
        "year_of_birth": {"column": "fnac", "confidence": 0.9},
    }})
    src = source({
        "CODE": [str(i) for i in range(N)],
        "fnac": [f"1980-01-{i % 28 + 1:02d}" for i in range(N)],
    })
    result = asyncio.run(match_columns("person", fields("person", ["person_id", "year_of_birth"]), src, set(), {}, True))
    assert result["llm_used"] is True
    assert {k: (m["column"], m["status"], m["source"]) for k, m in result["matches"].items()} == {
        "person_id": ("CODE", "auto", "llm"),
        "year_of_birth": ("fnac", "auto", "llm"),
    }


def test_llm_picks_are_checked(monkeypatch):
    fake_openai(monkeypatch, {"matches": {
        # Invented column: dropped.
        "person_id": {"column": "patient_number", "confidence": 0.95},
        # Values aren't dates: offered, not applied.
        "year_of_birth": {"column": "notes", "confidence": 0.95},
    }})
    src = source({"notes": ["fine", "ok", "follow up"] * (N // 3)})
    result = asyncio.run(match_columns("person", fields("person", ["person_id", "year_of_birth"]), src, set(), {}, True))
    assert result["matches"]["person_id"]["column"] is None
    assert result["matches"]["year_of_birth"]["status"] == "suggest"


def test_llm_error_keeps_heuristic_results(monkeypatch):
    class Boom:
        def __init__(self, **_):
            raise RuntimeError("network down")

    monkeypatch.setattr(cm, "AsyncOpenAI", Boom)
    monkeypatch.setattr(cm.settings, "openai_api_key", "sk-test")
    src = source({"gender": ["M", "F"] * (N // 2), "x1": ["a"] * N})
    result = asyncio.run(match_columns("person", fields("person", ["gender_concept_id", "race_concept_id"]), src, set(), {}, True))
    assert result["llm_used"] is False
    assert result["llm_error"] == "network down"
    assert result["matches"]["gender_concept_id"]["column"] == "gender"


def test_llm_not_called_without_key(monkeypatch):
    monkeypatch.setattr(cm.settings, "openai_api_key", "")
    src = source({"x1": ["a"] * N})
    result = asyncio.run(match_columns("person", fields("person", ["race_concept_id"]), src, set(), {}, True))
    assert result["llm_used"] is False and result["llm_available"] is False


def test_health_not_configured(monkeypatch):
    monkeypatch.setattr(cm.settings, "openai_api_key", "")
    assert asyncio.run(cm.llm_health())["status"] == "not_configured"


# ── Date formats ─────────────────────────────────────────────────────────────

def date_fields(table: str, specs: dict[str, dict]) -> list[FieldRequest]:
    all_specs = FIELD_SPECS[table]
    return [FieldRequest(k, all_specs[k], k, ["f.csv"], **opts) for k, opts in specs.items()]


def run_match(table, src, reqs):
    return asyncio.run(match_columns(table, reqs, src, set(), {}, False))["matches"]


def test_infer_date_format():
    assert cm.infer_date_format(["24/11/1978", "05/03/1985"]).format == "%d/%m/%Y"
    assert cm.infer_date_format(["03/25/2020", "04/01/2020"]).format == "%m/%d/%Y"
    assert cm.infer_date_format(["2020-01-01 10:00:00"]).format == "%Y-%m-%d %H:%M:%S"
    assert cm.infer_date_format(["1978", "1985"], allow_year=True).format == "%Y"
    assert cm.infer_date_format(["1978", "1985"]).format is None
    ambiguous = cm.infer_date_format(["01/02/2020", "03/04/2021"])
    assert (ambiguous.format, ambiguous.alternative) == ("%d/%m/%Y", "%m/%d/%Y")


def test_detected_format_is_set_on_the_step():
    src = source({"death_date": [f"{i % 28 + 1:02d}/03/2020" for i in range(N)]})
    m = run_match("death", src, date_fields("death", {
        "death_date_col": {"date_format": "%Y-%m-%d", "format_group": "death"},
    }))["death_date_col"]
    assert (m["column"], m["status"], m["date_format"]) == ("death_date", "auto", "%d/%m/%Y")


def test_matching_format_is_left_alone():
    src = source({"death_date": ["2020-03-01"] * N})
    m = run_match("death", src, date_fields("death", {
        "death_date_col": {"date_format": "%Y-%m-%d", "format_group": "death"},
    }))["death_date_col"]
    assert (m["status"], m["date_format"], m["warning"]) == ("auto", None, None)


def test_format_used_by_a_mapped_column_is_not_changed():
    src = source({"death_date": [f"{i % 28 + 1:02d}/03/2020" for i in range(N)]})
    m = run_match("death", src, date_fields("death", {
        "death_date_col": {"date_format": "%Y-%m-%d", "format_group": "death", "format_locked": True},
    }))["death_date_col"]
    assert m["status"] == "suggest" and m["date_format"] is None
    assert "%d/%m/%Y" in m["warning"]


def test_fields_sharing_a_format_must_agree():
    src = source({
        "admission_date": [f"2020-03-{i % 28 + 1:02d}" for i in range(N)],
        "discharge_date": [f"{i % 28 + 1:02d}/04/2020" for i in range(N)],
    })
    group = {"date_format": "%Y-%m-%d", "format_group": "visit:0"}
    matches = run_match("visit_occurrence", src, date_fields("visit_occurrence", {
        "date_col": group, "end_date_col": group,
    }))
    assert matches["date_col"]["status"] == "auto"
    assert matches["end_date_col"]["status"] == "suggest"
    assert matches["end_date_col"]["date_format"] is None


def test_mixed_formats_are_only_suggested():
    src = source({"death_date": ["2020-03-01", "01/03/2020"] * (N // 2)})
    m = run_match("death", src, date_fields("death", {
        "death_date_col": {"date_format": "%Y-%m-%d", "format_group": "death"},
    }))["death_date_col"]
    assert m["status"] == "suggest" and "50%" in m["warning"]


def test_year_only_birth_sets_year_format():
    src = source({"birth_year": [str(1950 + i % 40) for i in range(N)]})
    m = run_match("person", src, date_fields("person", {
        "year_of_birth": {"date_format": "%Y-%m-%d", "format_group": "dob"},
    }))["year_of_birth"]
    assert (m["column"], m["date_format"]) == ("birth_year", "%Y")


def test_fields_without_a_format_group_are_untouched():
    src = source({"dob": ["24/11/1978"] * N})
    m = run_match("provider", src, date_fields("provider", {"year_of_birth_col": {}}))["year_of_birth_col"]
    assert m["date_format"] is None and m["detected_format"] is None
