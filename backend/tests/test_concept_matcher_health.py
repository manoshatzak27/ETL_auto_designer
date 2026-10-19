import httpx
import pytest

from app.api.concept_mapping import classify_matcher_failure, classify_matcher_problems

REQUEST = httpx.Request("GET", "http://conceptmatcher:8000/health")


def http_503(detail: str) -> httpx.HTTPStatusError:
    resp = httpx.Response(503, json={"ready": False, "detail": detail}, request=REQUEST)
    return httpx.HTTPStatusError("503", request=REQUEST, response=resp)


@pytest.mark.parametrize("exc, status, command", [
    (httpx.ConnectError("[Errno -5] No address associated with hostname"), "unavailable",
     "docker compose up -d conceptmatcher"),
    (httpx.ConnectError("[Errno 111] Connection refused"), "unavailable", "docker compose up -d conceptmatcher"),
    (httpx.ReadTimeout("timed out"), "starting", "docker compose restart conceptmatcher"),
    (http_503("the pool 'pool-1' is already closed"), "unavailable", "docker compose restart conceptmatcher"),
    (http_503("pool initialization incomplete after 30.0 sec"), "unavailable", "docker compose restart conceptmatcher"),
    (http_503('connection failed: connection to server at "172.18.0.2", port 5432 failed: Connection refused'),
     "unavailable", "docker compose up -d postgres"),
    (http_503('relation "pipeline.vocabulary_version" does not exist'), "unavailable",
     "./scripts/setup-conceptmatcher.sh"),
])
def test_failures_map_to_a_status_and_a_fix(exc, status, command):
    result = classify_matcher_failure(exc)
    assert result["status"] == status
    assert command in result["commands"]
    assert result["reason"] and result["hint"] and result["raw"]


def test_missing_vocabulary_points_at_the_finalize_step():
    result = classify_matcher_problems(["missing: vocab.concept"])
    assert "Finalize" in result["hint"]


def test_missing_pipeline_tables_point_at_the_setup_script():
    result = classify_matcher_problems(["missing: pipeline.concept_search (run scripts/setup-conceptmatcher.sh)"])
    assert result["reason"] == "The concept matcher's search tables have not been built."
    assert "./scripts/setup-conceptmatcher.sh" in result["commands"]


def test_unreachable_database_in_problems():
    result = classify_matcher_problems(["cannot connect to postgres:5432 as omop: connection refused"])
    assert "docker compose up -d postgres" in result["commands"]
