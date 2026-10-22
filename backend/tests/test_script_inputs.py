from types import SimpleNamespace

from app.services import script_inputs as si


def _project(**overrides):
    base = dict(
        etl_config={
            "person": {"enabled": True, "mappings": {"person_id": "PID"}, "extra_instructions": ""},
            "death": {"death_date_col": "DOD"},
            "stem_table": {"variable_groups": {}, "visit_labels": ["V1"]},
        },
        source_files=[{"filename": "a.csv", "delimiter": ";", "encoding": "utf-8", "columns": ["PID", "DOD"],
                       "path": "/u/a.csv", "row_count": 10}],
        concept_decisions={"HB": {"strategy": "map_variable", "variable_concept": {"concept_id": 1}}},
        custom_vocabulary_id="CUSTOM",
        generated_scripts={"person": "...", "death": "...", "stem_table": "...", "measurement": "..."},
        generated_scripts_inputs={},
        mapping_files={"variable_mapping": "/u/m.csv"},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _generated(**overrides):
    p = _project(**overrides)
    si.record(p, ["person", "death", "stem_table", "measurement"])
    si.record_mapping_files(p, si.mapping_files_fingerprint(p))
    return p


def test_nothing_is_stale_right_after_generating():
    p = _generated()
    assert si.stale_scripts(p) == []
    assert si.mapping_files_stale(p) is False


def test_scripts_without_a_fingerprint_are_never_stale():
    p = _project()
    p.etl_config["person"]["mappings"] = {"person_id": "OTHER"}
    assert si.stale_scripts(p) == []
    assert si.mapping_files_stale(p) is False


def test_a_changed_setting_flags_the_script_and_those_that_read_it():
    p = _generated()
    p.etl_config = {**p.etl_config, "person": {**p.etl_config["person"], "mappings": {"person_id": "OTHER"}}}
    assert sorted(si.stale_scripts(p)) == ["death", "person", "stem_table"]


def test_extra_instructions_only_flag_their_own_script():
    p = _generated()
    p.etl_config = {**p.etl_config, "person": {**p.etl_config["person"], "extra_instructions": "be careful"}}
    assert si.stale_scripts(p) == ["person"]


def test_changing_a_setting_back_clears_the_flag():
    p = _generated()
    original = p.etl_config
    p.etl_config = {**original, "death": {"death_date_col": "OTHER"}}
    assert si.stale_scripts(p) == ["death"]
    p.etl_config = original
    assert si.stale_scripts(p) == []


def test_ignored_keys_and_source_paths_do_not_flag():
    p = _generated()
    p.etl_config = {
        **p.etl_config,
        "person": {**p.etl_config["person"], "enabled": False},
        "stem_table": {**p.etl_config["stem_table"], "visit_labels": ["V2"]},
    }
    p.source_files = [{**p.source_files[0], "path": "/elsewhere/a.csv", "row_count": 99}]
    p.concept_decisions = {"HB": {**p.concept_decisions["HB"], "suggestions": [{"concept_id": 2}]}}
    assert si.stale_scripts(p) == []
    assert si.mapping_files_stale(p) is False


def test_new_source_columns_flag_every_script():
    p = _generated()
    p.source_files = [{**p.source_files[0], "columns": ["PID", "DOD", "NEW"]}]
    assert sorted(si.stale_scripts(p)) == ["death", "person", "stem_table"]


def test_concept_decisions_flag_the_stem_table_and_mapping_csvs():
    p = _generated()
    p.concept_decisions = {"HB": {"strategy": "map_variable", "variable_concept": {"concept_id": 3}}}
    assert si.stale_scripts(p) == ["stem_table"]
    assert si.mapping_files_stale(p) is True


def test_uploaded_mapping_csvs_are_unknown():
    p = _generated()
    si.record_mapping_files(p, None)
    p.concept_decisions = {}
    assert si.mapping_files_stale(p) is False
