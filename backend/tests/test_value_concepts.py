import asyncio

import pytest

from app.api import concept_mapping
from app.api.concept_mapping import suggest_concepts_for_values


@pytest.mark.parametrize("domain", ["Race", "Ethnicity", "Gender", "Visit", "Unit"])
def test_no_values_are_set_to_concept_0_without_asking_the_matcher(monkeypatch, domain):
    async def no_matcher(*_, **__):
        raise AssertionError("the matcher must not be asked")

    monkeypatch.setattr(concept_mapping, "_post_match", no_matcher)
    out = asyncio.run(suggest_concepts_for_values("p", domain, ["Unknown", "Other", " n/a ", "NULL", "-"]))
    assert {v: (e["concept_id"], e["status"], e["source"]) for v, e in out.items()} == {
        v: (0, "auto_accept", "rule") for v in ["Unknown", "Other", " n/a ", "NULL", "-"]
    }
