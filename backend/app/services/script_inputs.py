"""
Fingerprints of what each generated script was built from, so the UI can tell
a script that still matches the wizard settings from one that is out of date.

At generation time the fingerprint of the script's inputs is stored in
project.generated_scripts_inputs; afterwards the same fingerprint is computed
from the current settings and compared. A script with no stored fingerprint
(generated before this existed, or loaded from outside) is "unknown" and never
reported as out of date.
"""
import hashlib
import json

# The etl_config sections each generator reads (checked against
# code_generator.py). The table's own section comes first.
_CONFIG_INPUTS: dict[str, list[str]] = {
    "location": ["location"],
    "care_site": ["care_site", "location"],
    "provider": ["provider", "care_site"],
    "person": ["person", "location", "care_site", "provider", "dataset_options"],
    "visit_occurrence": ["visit_occurrence", "person", "care_site", "provider"],
    "observation_period": ["observation_period", "person"],
    "death": ["death", "person"],
    "stem_table": ["stem_table", "person", "visit_occurrence"],
}

# Scripts that also read the Concepts step's decisions.
_READS_DECISIONS = {"stem_table"}

# Keys the generators never read: the optional-table switch and the Stem Table
# step's own list of visit labels.
_IGNORED_CONFIG_KEYS = {"enabled", "visit_labels"}
# A table's extra instructions only shape its own script, not the scripts that
# read its settings.
_OWN_ONLY_CONFIG_KEYS = {"extra_instructions"}

# Decision keys only the Concepts page uses ("Load concepts" alternatives).
_IGNORED_DECISION_KEYS = {"suggestions", "value_suggestions"}

# Key under which the mapping CSVs' fingerprint is stored.
MAPPING_FILES_KEY = "__mapping_files__"


def _digest(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_signature(project) -> list:
    # What the scripts know about the source files: names, columns and how to
    # read them. Paths and row counts change on re-upload without affecting them.
    return [
        {k: f.get(k) for k in ("filename", "delimiter", "encoding", "columns")}
        for f in (project.source_files or [])
        if isinstance(f, dict)
    ]


def _decisions(project) -> dict:
    return {
        var: {k: v for k, v in d.items() if k not in _IGNORED_DECISION_KEYS} if isinstance(d, dict) else d
        for var, d in (project.concept_decisions or {}).items()
    }


def fingerprint(project, table: str) -> str | None:
    """The fingerprint of `table`'s current inputs, or None for a script that
    reads no settings (the domain-routing templates)."""
    if table not in _CONFIG_INPUTS:
        return None
    config: dict = project.etl_config or {}
    inputs: dict = {
        "config": {
            section: {
                k: v for k, v in (config.get(section) or {}).items()
                if k not in _IGNORED_CONFIG_KEYS and (section == table or k not in _OWN_ONLY_CONFIG_KEYS)
            }
            for section in _CONFIG_INPUTS[table]
        },
        "sources": _source_signature(project),
    }
    if table in _READS_DECISIONS:
        inputs["decisions"] = _decisions(project)
    return _digest(inputs)


def mapping_files_fingerprint(project) -> str:
    return _digest({
        "decisions": _decisions(project),
        "custom_vocabulary_id": project.custom_vocabulary_id or "CUSTOM",
    })


def record(project, tables: list[str]) -> None:
    """Store the current fingerprint of each of `tables`, just generated."""
    stored = dict(project.generated_scripts_inputs or {})
    for table in tables:
        fp = fingerprint(project, table)
        if fp is None:
            stored.pop(table, None)
        else:
            stored[table] = fp
    project.generated_scripts_inputs = stored


def record_mapping_files(project, fp: str | None) -> None:
    """Store the mapping CSVs' fingerprint; None when they weren't built from
    the decisions (uploaded or copied in), which makes them "unknown"."""
    stored = dict(project.generated_scripts_inputs or {})
    if fp is None:
        stored.pop(MAPPING_FILES_KEY, None)
    else:
        stored[MAPPING_FILES_KEY] = fp
    project.generated_scripts_inputs = stored


def stale_scripts(project) -> list[str]:
    """Generated scripts whose inputs changed since they were generated."""
    stored: dict = project.generated_scripts_inputs or {}
    scripts: dict = project.generated_scripts or {}
    return [
        table for table in scripts
        if table in stored and stored[table] != fingerprint(project, table)
    ]


def mapping_files_stale(project) -> bool:
    stored = (project.generated_scripts_inputs or {}).get(MAPPING_FILES_KEY)
    return bool(project.mapping_files) and stored is not None and stored != mapping_files_fingerprint(project)
