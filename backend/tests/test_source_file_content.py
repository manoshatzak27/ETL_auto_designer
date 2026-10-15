from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from app.api import sources
from app.api.sources import (
    SourceColumnSpec,
    SourceFileEditsPayload,
    get_source_file_content,
    patch_source_file_content,
)
from app.services.schema_inferrer import infer_schema


class FakeDB:
    """Just enough of a Session for the source-file-content endpoints."""

    def __init__(self, project):
        self.project = project

    def query(self, _model):
        return self

    def filter(self, *_args):
        return self

    def first(self):
        return self.project

    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def make_project(tmp_path, n_rows: int):
    path = tmp_path / "people.csv"
    pd.DataFrame({
        "id": [str(i) for i in range(n_rows)],
        "sex": ["M"] * n_rows,
        "age": [str(20 + i % 50) for i in range(n_rows)],
    }).to_csv(path, index=False)
    schema = infer_schema(str(path))
    entry = {"filename": path.name, "path": str(path), "size_bytes": path.stat().st_size, **schema}
    project = SimpleNamespace(id="p1", source_files=[entry])
    return project, FakeDB(project), path


def cols(*pairs):
    return [SourceColumnSpec(source=s, name=n) for s, n in pairs]


SAME_COLS = cols(("id", "id"), ("sex", "sex"), ("age", "age"))


def test_get_returns_only_the_requested_page(tmp_path):
    project, db, _ = make_project(tmp_path, 2000)
    page = get_source_file_content("p1", "people.csv", offset=1000, limit=500, db=db)
    assert page["offset"] == 1000
    assert page["row_count"] == 2000
    assert page["columns"] == ["id", "sex", "age"]
    assert [r["id"] for r in page["rows"]] == [str(i) for i in range(1000, 1500)]


def test_get_past_the_end_is_short(tmp_path):
    project, db, _ = make_project(tmp_path, 120)
    page = get_source_file_content("p1", "people.csv", offset=100, limit=500, db=db)
    assert [r["id"] for r in page["rows"]] == [str(i) for i in range(100, 120)]


def test_patch_streams_edits_across_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "SOURCE_PATCH_CHUNK_ROWS", 7)
    project, db, path = make_project(tmp_path, 50)
    payload = SourceFileEditsPayload(
        columns=cols(("id", "id"), ("sex", "gender"), (None, "note")),  # rename sex, drop age, add note
        cell_edits={3: {"gender": "F"}, 20: {"note": "hi", "gender": "X"}, 49: {"id": "last"}},
        deleted_rows=[0, 8, 21],
        added_rows=[{"id": "new", "gender": "F"}],
    )
    patch_source_file_content("p1", "people.csv", payload, db=db)

    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    assert list(df.columns) == ["id", "gender", "note"]
    assert len(df) == 50 - 3 + 1
    by_id = df.set_index("id")
    assert not {"0", "8", "21"} & set(by_id.index)
    assert by_id.loc["3", "gender"] == "F"
    assert (by_id.loc["20", "gender"], by_id.loc["20", "note"]) == ("X", "hi")
    assert by_id.loc["last", "gender"] == "M"
    assert (by_id.loc["new", "gender"], by_id.loc["new", "note"]) == ("F", "")
    assert by_id.loc["5", "gender"] == "M" and by_id.loc["5", "note"] == ""
    assert project.source_files[0]["columns"] == ["id", "gender", "note"]
    assert project.source_files[0]["row_count"] == 48


def test_patch_with_no_changes_keeps_every_row(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "SOURCE_PATCH_CHUNK_ROWS", 3)
    project, db, path = make_project(tmp_path, 10)
    before = pd.read_csv(path, dtype=str)
    patch_source_file_content("p1", "people.csv", SourceFileEditsPayload(columns=SAME_COLS), db=db)
    pd.testing.assert_frame_equal(pd.read_csv(path, dtype=str), before)


@pytest.mark.parametrize("payload, status", [
    (SourceFileEditsPayload(columns=SAME_COLS, cell_edits={10: {"sex": "F"}}), 409),
    (SourceFileEditsPayload(columns=SAME_COLS, deleted_rows=[99]), 409),
    (SourceFileEditsPayload(columns=cols(("gone", "gone"))), 409),
    (SourceFileEditsPayload(columns=cols(("id", "x"), ("sex", "x"))), 400),
    (SourceFileEditsPayload(columns=SAME_COLS, cell_edits={1: {"nope": "F"}}), 400),
])
def test_patch_rejects_bad_edits_without_touching_the_file(tmp_path, payload, status):
    project, db, path = make_project(tmp_path, 10)
    before = path.read_bytes()
    with pytest.raises(HTTPException) as exc:
        patch_source_file_content("p1", "people.csv", payload, db=db)
    assert exc.value.status_code == status
    assert path.read_bytes() == before
    assert not path.with_suffix(".csv.tmp").exists()


def test_patch_refuses_files_with_malformed_rows(tmp_path):
    project, db, path = make_project(tmp_path, 5)
    path.write_text(path.read_text() + "1,2,3,4,5\n")
    before = path.read_bytes()
    with pytest.raises(HTTPException) as exc:
        patch_source_file_content("p1", "people.csv", SourceFileEditsPayload(columns=SAME_COLS), db=db)
    assert exc.value.status_code == 422
    assert path.read_bytes() == before
