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
