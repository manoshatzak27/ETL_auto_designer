"""
Per-table ETL script generator.

All OMOP tables are generated deterministically. If the user supplies
extra_instructions for a table, a separate AI call patches the generated script.
"""
import json
import re
import threading
from pathlib import Path
from openai import AsyncOpenAI
from app.config import settings

# Tables we support, in dependency order
SUPPORTED_TABLES = [
    "location",
    "care_site",
    "provider",
    "person",
    "visit_occurrence",
    "observation_period",
    "stem_table",
    "death",
    "measurement",
    "observation",
    "drug_exposure",
    "procedure_occurrence",
    "condition_occurrence",
]

# Domain routing tables: read from stem_table.csv and filter by domain_id
_DOMAIN_TABLES: dict[str, int] = {
    "measurement": 1,
    "observation": 2,
    "drug_exposure": 3,
    "procedure_occurrence": 4,
    "condition_occurrence": 5,
}


def _sync_stem_variable_groups(stem_cfg: dict, visit_cfg: dict) -> dict:
    """Return a copy of stem_cfg with variable_groups synced to current visit labels.

    Groups whose names matched old visit labels (tracked in visit_labels) but no longer
    appear in the current visit config are dropped. Manually-added groups are preserved.
    New visit labels get an empty group if not already present.
    """
    current_labels: list[str] = [
        vd["label"]
        for vd in visit_cfg.get("visit_definitions", [])
        if vd.get("label")
    ]
    saved_visit_labels: list[str] = stem_cfg.get("visit_labels", [])
    old_groups: dict = stem_cfg.get("variable_groups", {})

    synced: dict = {}
    for key, val in old_groups.items():
        if key not in saved_visit_labels:
            synced[key] = val
    for label in current_labels:
        synced[label] = old_groups.get(label, [])

    return {**stem_cfg, "variable_groups": synced, "visit_labels": current_labels}


def _xtr(var: str, col: str, indent: int = 8) -> str:
    """Return a line of generated Python that safely extracts a source column into a local var."""
    pad = " " * indent
    if not col:
        return f"{pad}{var} = None"
    c = repr(col)
    return f"{pad}{var} = (str(row.get({c}, '')).strip() or None) if pd.notnull(row.get({c})) else None"


def _xtr_v(var: str, col: str, indent: int = 8) -> str:
    """Like _xtr but also emits an INFO print when the column is configured but the row value is empty."""
    pad = " " * indent
    if not col:
        return f"{pad}{var} = None"
    c = repr(col)
    return (
        f"{pad}_raw = row.get({c})\n"
        f"{pad}{var} = (str(_raw).strip() or None) if pd.notnull(_raw) else None\n"
        f"{pad}if {var} is None:\n"
        f'{pad}    _info(f"INFO: column {col!r} is empty for this row")'
    )


def _xtr_doc(var: str, col: str, indent: int = 8) -> str:
    """Emit _read_str(row, col). When col is empty, emit `var = None` with a comment."""
    pad = " " * indent
    if not col:
        return f"{pad}# {var} is not populated\n{pad}{var} = None"
    c = repr(col)
    return f"{pad}{var} = _read_str(row, {c})"


def _source_file_params(project, table_cfg: dict) -> tuple[str, str, str]:
    """Return (source_path_code, delim_repr, enc_repr) for a table's generated script.

    When table_cfg contains source_filename that resolves to a project.source_files entry,
    the generated script will embed that file's path and use its delimiter/encoding.
    Falls back to ETL_SOURCE_PATH env var and project-level defaults otherwise.
    """
    filename = (table_cfg or {}).get("source_filename")
    if filename and project.source_files:
        entry = next((f for f in project.source_files if f.get("filename") == filename), None)
        if entry:
            path = entry.get("path", "")
            delim = repr(entry.get("delimiter", ","))
            enc = repr(entry.get("encoding", "utf-8"))
            return f"    source_path = r{repr(path)}\n", delim, enc
    return (
        f"    source_path = r{repr(project.source_path or '')}\n",
        repr(project.source_delimiter or ","),
        repr(project.source_encoding or "utf-8"),
    )


def _sf_read_line(project, filename: str) -> str:
    """Return a pd.read_csv(...) line for a named source file."""
    entry = next((f for f in (project.source_files or []) if f.get("filename") == filename), None)
    if entry:
        path = entry.get("path", "")
        delim = repr(entry.get("delimiter", ","))
        enc = repr(entry.get("encoding", "utf-8"))
        return f"    df = pd.read_csv(r{repr(path)}, delimiter={delim}, encoding={enc})\n"
    return f"    # WARNING: source file entry not found for {repr(filename)}\n    df = pd.DataFrame()\n"


def _generate_location_script(project) -> str:
    """Deterministic template-based generator for the OMOP location script."""
    loc = (project.etl_config or {}).get("location", {})

    source_files = loc.get("source_files", [])
    file_configs = loc.get("file_configs", {})
    if source_files and file_configs and len(source_files) > 1:
        person_id_auto_increment = bool(loc.get("person_id_auto_increment", False))
        if not person_id_auto_increment:
            missing = [fn for fn in source_files if not file_configs.get(fn, {}).get("person_id_col", "")]
            if missing:
                raise ValueError(
                    f"Location: person_id_col is not mapped for {missing}. "
                    f"Map the column in the Location step or switch to auto-increment."
                )
        return _generate_location_script_multi(project, source_files, file_configs, person_id_auto_increment)

    if source_files and file_configs:
        first_file = source_files[0]
        fc = file_configs.get(first_file, {})
        loc = {**loc, **fc, "source_filename": first_file}

    source_path_code, delim, enc = _source_file_params(project, loc)

    a1_col = loc.get("address_1_col", "")
    a2_col = loc.get("address_2_col", "")
    city_col = loc.get("city_col", "")
    state_col = loc.get("state_col", "")
    zip_col = loc.get("zip_col", "")
    county_col = loc.get("county_col", "")
    country_col = loc.get("country_col", "")
    country_sv = loc.get("country_source_value", "")
    cid_map = loc.get("country_concept_id_map", {})
    cid_default = loc.get("country_concept_id_default", 0) or 0
    lat_col = loc.get("latitude_col", "")
    lon_col = loc.get("longitude_col", "")

    cs_a1_col = loc.get("cs_address_1_col", "")
    cs_a2_col = loc.get("cs_address_2_col", "")
    cs_city_col = loc.get("cs_city_col", "")
    cs_state_col = loc.get("cs_state_col", "")
    cs_zip_col = loc.get("cs_zip_col", "")
    cs_county_col = loc.get("cs_county_col", "")
    cs_country_col = loc.get("cs_country_col", "")
    cs_country_sv = loc.get("cs_country_source_value", "")
    cs_cid_map = loc.get("cs_country_concept_id_map", {})
    cs_cid_default = loc.get("cs_country_concept_id_default", 0) or 0
    cs_lat_col = loc.get("cs_latitude_col", "")
    cs_lon_col = loc.get("cs_longitude_col", "")

    return (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "from etl_runtime import _info, _read_str\n"
        "\n"
        "\n"
        "def _to_lat(val):\n"
        "    \"\"\"Cast val to float and validate it as a latitude in [-90, 90]. Returns None on failure.\"\"\"\n"
        "    try:\n"
        "        f = float(val)\n"
        "        if -90 <= f <= 90:\n"
        "            return f\n"
        "        print(f'WARNING: latitude value {f} is out of range [-90, 90] — set to NULL')\n"
        "        return None\n"
        "    except (TypeError, ValueError):\n"
        "        return None\n"
        "\n"
        "def _to_lon(val):\n"
        "    \"\"\"Cast val to float and validate it as a longitude in [-180, 180]. Returns None on failure.\"\"\"\n"
        "    try:\n"
        "        f = float(val)\n"
        "        if -180 <= f <= 180:\n"
        "            return f\n"
        "        print(f'WARNING: longitude value {f} is out of range [-180, 180] — set to NULL')\n"
        "        return None\n"
        "    except (TypeError, ValueError):\n"
        "        return None\n"
        "\n"
        "def main():\n"
        "   # Load environmental variables\n"
        + source_path_code +
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        + (
            "    # --- Person Country concept maps ---\n"
            f"    country_concept_id_map     = {json.dumps(cid_map)}\n"
            f"    country_concept_id_default = {cid_default}\n"
            "\n"
            if country_col else ""
        )
        + (
            "    # --- Care_site Country concept maps ---\n"
            f"    cs_country_concept_id_map     = {json.dumps(cs_cid_map)}\n"
            f"    cs_country_concept_id_default = {cs_cid_default}\n"
            "\n"
            if cs_country_col else ""
        )
        + "    # processed rows\n"
        "    rows = []\n"
        "    # a set is used to avoid creating duplicate location records\n"
        "    seen_locations = set()\n"
        "\n"
        "    for _, row in df.iterrows():\n"
        "\n"
        "        # ============================================================\n"
        "        # PERSON ADDRESS PROCESSING\n"
        "        # ============================================================\n"
        "        # Extract and clean person address components\n"
        "\n"
        + _xtr_doc("address_1", a1_col) + "\n\n"
        + _xtr_doc("address_2", a2_col) + "\n\n"
        + _xtr_doc("city", city_col) + "\n\n"
        + _xtr_doc("state", state_col) + "\n\n"
        + _xtr_doc("zip_code", zip_col) + "\n\n"
        + _xtr_doc("county", county_col) + "\n\n"
        + _xtr_doc("latitude", lat_col) + "\n\n"
        + _xtr_doc("longitude", lon_col) + "\n\n"
        + (
            "        # country: Extract and map to OMOP concept ID\n"
            + _xtr("country_source_value", country_col, 8) + "\n"
            + "        if country_source_value:\n"
            + "            # Look up concept ID from mapping dictionary\n"
            + "            _cid = country_concept_id_map.get(country_source_value)\n"
            + "            if _cid is None:\n"
            + "                _info(f'INFO: country value {country_source_value!r} not in concept map — using default')\n"
            + "            country_concept_id = (_cid if _cid is not None else country_concept_id_default) or None\n"
            + "        else:\n"
            + "            # No country specified, use default concept ID\n"
            + "            country_concept_id = country_concept_id_default or None\n"
            if country_col else
            "        # country: No source column mapped — using static value\n"
            + f"        country_source_value = {repr(country_sv) if country_sv else 'None'}\n"
            + f"        country_concept_id = {cid_default if cid_default else 'None'}\n"
        )
        + "\n"
        + "        # Create composite source value for deduplication\n"
        + "        # Join all non-empty fields with pipe separator, limit to 255 characters\n"
        + '        location_source_value = " | ".join(filter(None, [\n'
        + "            address_1, address_2, city, state, zip_code, county, country_source_value\n"
        + "        ]))[:255]\n"
        + "\n"
        + "        # skip if duplicate\n"
        + "        if location_source_value in seen_locations:\n"
        + "            continue\n"
        + "        # Only create location record if essential fields are present\n"
        + "        if (address_1 or city or state or zip_code):\n"
        + "            seen_locations.add(location_source_value)\n"
        + "            rows.append({\n"
        + "                'location_id':           None,\n"
        + "                'address_1':             address_1[:50]  if address_1 else None,\n"
        + "                'address_2':             address_2[:50]  if address_2 else None,\n"
        + "                'city':                  city[:50]       if city      else None,\n"
        + "                'state':                 state[:2]       if state     else None,\n"
        + "                'zip':                   zip_code[:9]    if zip_code  else None,\n"
        + "                'county':                county[:20]     if county    else None,\n"
        + "                'location_source_value': location_source_value,\n"
        + "                'country_concept_id':    country_concept_id,\n"
        + "                'country_source_value':  country_source_value,\n"
        + "                'latitude':              _to_lat(latitude),\n"
        + "                'longitude':             _to_lon(longitude),\n"
        + "            })\n"
        + "\n"
        + "        # ============================================================\n"
        + "        # CARE SITE ADDRESS PROCESSING\n"
        + "        # ============================================================\n"
        + "        # Extract and clean care site address components\n"
        + "\n"
        + _xtr_doc("cs_address_1", cs_a1_col) + "\n\n"
        + _xtr_doc("cs_address_2", cs_a2_col) + "\n\n"
        + _xtr_doc("cs_city", cs_city_col) + "\n\n"
        + _xtr_doc("cs_state", cs_state_col) + "\n\n"
        + _xtr_doc("cs_zip_code", cs_zip_col) + "\n\n"
        + _xtr_doc("cs_county", cs_county_col) + "\n\n"
        + _xtr_doc("cs_latitude", cs_lat_col) + "\n\n"
        + _xtr_doc("cs_longitude", cs_lon_col) + "\n\n"
        + (
            "        # country: Extract and map to OMOP concept ID\n"
            + _xtr("cs_country_source_value", cs_country_col, 8) + "\n"
            + "        if cs_country_source_value:\n"
            + "            # Look up concept ID from mapping dictionary\n"
            + "            _cs_cid = cs_country_concept_id_map.get(cs_country_source_value)\n"
            + "            if _cs_cid is None:\n"
            + "                _info(f'INFO: cs_country value {cs_country_source_value!r} not in concept map — using default')\n"
            + "            cs_country_concept_id = (_cs_cid if _cs_cid is not None else cs_country_concept_id_default) or None\n"
            + "        else:\n"
            + "            # No country specified, use default concept ID\n"
            + "            cs_country_concept_id = cs_country_concept_id_default or None\n"
            if cs_country_col else
            "        # country: No source column mapped — using static value\n"
            + f"        cs_country_source_value = {repr(cs_country_sv) if cs_country_sv else 'None'}\n"
            + f"        cs_country_concept_id = {cs_cid_default if cs_cid_default else 'None'}\n"
        )
        + "\n"
        + "        # Create composite source value for deduplication\n"
        + "        # Join all non-empty fields with pipe separator, limit to 255 characters\n"
        + '        cs_location_source_value = " | ".join(filter(None, [\n'
        + "            cs_address_1, cs_address_2, cs_city, cs_state, cs_zip_code, cs_county, cs_country_source_value\n"
        + "        ]))[:255]\n"
        + "\n"
        + "        # skip if duplicate\n"
        + "        if cs_location_source_value in seen_locations:\n"
        + "            continue\n"
        + "        # Only create location record if essential fields are present\n"
        + "        if (cs_address_1 or cs_city or cs_state or cs_zip_code):\n"
        + "            seen_locations.add(cs_location_source_value)\n"
        + "            rows.append({\n"
        + "                'location_id':           None,\n"
        + "                'address_1':             cs_address_1[:50]  if cs_address_1 else None,\n"
        + "                'address_2':             cs_address_2[:50]  if cs_address_2 else None,\n"
        + "                'city':                  cs_city[:50]       if cs_city      else None,\n"
        + "                'state':                 cs_state[:2]       if cs_state     else None,\n"
        + "                'zip':                   cs_zip_code[:9]    if cs_zip_code  else None,\n"
        + "                'county':                cs_county[:20]     if cs_county    else None,\n"
        + "                'location_source_value': cs_location_source_value,\n"
        + "                'country_concept_id':    cs_country_concept_id,\n"
        + "                'country_source_value':  cs_country_source_value,\n"
        + "                'latitude':              _to_lat(cs_latitude),\n"
        + "                'longitude':             _to_lon(cs_longitude),\n"
        + "            })\n"
        + "\n"
        + "    # --- Build output DataFrame ---\n"
        + "    output_df = pd.DataFrame(rows, columns=[\n"
        + "        'location_id', 'address_1', 'address_2', 'city', 'state', 'zip', 'county',\n"
        + "        'location_source_value', 'country_concept_id', 'country_source_value', 'latitude', 'longitude',\n"
        + "    ])\n"
        + "\n"
        + '    output_df["location_id"] = range(1, len(output_df) + 1)\n'
        + "\n"
        + "    # --- Write output ---\n"
        + "    output_file = os.path.join(output_dir, 'location.csv')\n"
        + "    output_df.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing location.csv ... done ({len(output_df)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _location_file_block(fc: dict, project, person_id_col: str = "",
                         person_id_auto_increment: bool = False) -> tuple:
    """Return (map_init, row_body) for one source file's location config (person + cs addresses).

    When person_id_col is supplied cross-file patient dedup uses conflict/merge semantics:
    - New patient: write location row normally and track it.
    - Returning patient with no conflicting fields: fill any blanks in the existing row (merge).
    - Returning patient with at least one field that differs between files: tombstone the existing
      row (set to None) so it is filtered out before writing the CSV.
    Without person_id_col the original address-key-only dedup is used.
    """
    a1_col = fc.get("address_1_col", "")
    a2_col = fc.get("address_2_col", "")
    city_col = fc.get("city_col", "")
    state_col = fc.get("state_col", "")
    zip_col = fc.get("zip_col", "")
    county_col = fc.get("county_col", "")
    country_col = fc.get("country_col", "")
    country_sv = fc.get("country_source_value", "")
    cid_map = fc.get("country_concept_id_map", {})
    cid_default = fc.get("country_concept_id_default", 0) or 0
    lat_col = fc.get("latitude_col", "")
    lon_col = fc.get("longitude_col", "")

    cs_a1_col = fc.get("cs_address_1_col", "")
    cs_a2_col = fc.get("cs_address_2_col", "")
    cs_city_col = fc.get("cs_city_col", "")
    cs_state_col = fc.get("cs_state_col", "")
    cs_zip_col = fc.get("cs_zip_col", "")
    cs_county_col = fc.get("cs_county_col", "")
    cs_country_col = fc.get("cs_country_col", "")
    cs_country_sv = fc.get("cs_country_source_value", "")
    cs_cid_map = fc.get("cs_country_concept_id_map", {})
    cs_cid_default = fc.get("cs_country_concept_id_default", 0) or 0
    cs_lat_col = fc.get("cs_latitude_col", "")
    cs_lon_col = fc.get("cs_longitude_col", "")

    map_init = ""
    if country_col:
        map_init += (
            f"    country_concept_id_map     = {json.dumps(cid_map)}\n"
            f"    country_concept_id_default = {cid_default}\n"
        )
    if cs_country_col:
        map_init += (
            f"    cs_country_concept_id_map     = {json.dumps(cs_cid_map)}\n"
            f"    cs_country_concept_id_default = {cs_cid_default}\n"
        )

    # ── shared row-append snippets (used in both dedup paths) ────────────
    _person_append = (
        "                rows.append({\n"
        "                    'location_id':           None,\n"
        "                    'address_1':             address_1[:50]  if address_1 else None,\n"
        "                    'address_2':             address_2[:50]  if address_2 else None,\n"
        "                    'city':                  city[:50]       if city      else None,\n"
        "                    'state':                 state[:2]       if state     else None,\n"
        "                    'zip':                   zip_code[:9]    if zip_code  else None,\n"
        "                    'county':                county[:20]     if county    else None,\n"
        "                    'location_source_value': location_source_value,\n"
        "                    'country_concept_id':    country_concept_id,\n"
        "                    'country_source_value':  country_source_value,\n"
        "                    'latitude':              _to_lat(latitude),\n"
        "                    'longitude':             _to_lon(longitude),\n"
        "                })\n"
    )
    _cs_append = (
        "                rows.append({\n"
        "                    'location_id':           None,\n"
        "                    'address_1':             cs_address_1[:50]  if cs_address_1 else None,\n"
        "                    'address_2':             cs_address_2[:50]  if cs_address_2 else None,\n"
        "                    'city':                  cs_city[:50]       if cs_city      else None,\n"
        "                    'state':                 cs_state[:2]       if cs_state     else None,\n"
        "                    'zip':                   cs_zip_code[:9]    if cs_zip_code  else None,\n"
        "                    'county':                cs_county[:20]     if cs_county    else None,\n"
        "                    'location_source_value': cs_location_source_value,\n"
        "                    'country_concept_id':    cs_country_concept_id,\n"
        "                    'country_source_value':  cs_country_source_value,\n"
        "                    'latitude':              _to_lat(cs_latitude),\n"
        "                    'longitude':             _to_lon(cs_longitude),\n"
        "                })\n"
    )

    # ── field-extraction code (same regardless of dedup mode) ────────────
    person_fields = (
        "        # PERSON ADDRESS\n"
        + _xtr_doc("address_1", a1_col) + "\n\n"
        + _xtr_doc("address_2", a2_col) + "\n\n"
        + _xtr_doc("city", city_col) + "\n\n"
        + _xtr_doc("state", state_col) + "\n\n"
        + _xtr_doc("zip_code", zip_col) + "\n\n"
        + _xtr_doc("county", county_col) + "\n\n"
        + _xtr_doc("latitude", lat_col) + "\n\n"
        + _xtr_doc("longitude", lon_col) + "\n\n"
        + (
            _xtr("country_source_value", country_col, 8) + "\n"
            + "        if country_source_value:\n"
            + "            _cid = country_concept_id_map.get(country_source_value)\n"
            + "            if _cid is None:\n"
            + "                _info(f'INFO: country value {country_source_value!r} not in concept map — using default')\n"
            + "            country_concept_id = (_cid if _cid is not None else country_concept_id_default) or None\n"
            + "        else:\n"
            + "            country_concept_id = country_concept_id_default or None\n"
            if country_col else
            f"        country_source_value = {repr(country_sv) if country_sv else 'None'}\n"
            + f"        country_concept_id = {cid_default if cid_default else 'None'}\n"
        )
        + '        location_source_value = " | ".join(filter(None, [\n'
        + "            address_1, address_2, city, state, zip_code, county, country_source_value\n"
        + "        ]))[:255]\n"
    )

    cs_fields = (
        "        # CARE SITE ADDRESS\n"
        + _xtr_doc("cs_address_1", cs_a1_col) + "\n\n"
        + _xtr_doc("cs_address_2", cs_a2_col) + "\n\n"
        + _xtr_doc("cs_city", cs_city_col) + "\n\n"
        + _xtr_doc("cs_state", cs_state_col) + "\n\n"
        + _xtr_doc("cs_zip_code", cs_zip_col) + "\n\n"
        + _xtr_doc("cs_county", cs_county_col) + "\n\n"
        + _xtr_doc("cs_latitude", cs_lat_col) + "\n\n"
        + _xtr_doc("cs_longitude", cs_lon_col) + "\n\n"
        + (
            _xtr("cs_country_source_value", cs_country_col, 8) + "\n"
            + "        if cs_country_source_value:\n"
            + "            _cs_cid = cs_country_concept_id_map.get(cs_country_source_value)\n"
            + "            if _cs_cid is None:\n"
            + "                _info(f'INFO: cs_country value {cs_country_source_value!r} not in concept map — using default')\n"
            + "            cs_country_concept_id = (_cs_cid if _cs_cid is not None else cs_country_concept_id_default) or None\n"
            + "        else:\n"
            + "            cs_country_concept_id = cs_country_concept_id_default or None\n"
            if cs_country_col else
            f"        cs_country_source_value = {repr(cs_country_sv) if cs_country_sv else 'None'}\n"
            + f"        cs_country_concept_id = {cs_cid_default if cs_cid_default else 'None'}\n"
        )
        + '        cs_location_source_value = " | ".join(filter(None, [\n'
        + "            cs_address_1, cs_address_2, cs_city, cs_state, cs_zip_code, cs_county, cs_country_source_value\n"
        + "        ]))[:255]\n"
    )

    # ── dedup logic ──────────────────────────────────────────────────────
    if person_id_col or person_id_auto_increment:
        person_dedup = (
            "        _upsert_location(\n"
            "            _patient_id,\n"
            "            {'address_1': address_1, 'address_2': address_2, 'city': city,\n"
            "             'state': state, 'zip_code': zip_code, 'county': county,\n"
            "             'country_source_value': country_source_value, 'country_concept_id': country_concept_id,\n"
            "             'latitude': latitude, 'longitude': longitude},\n"
            "            location_source_value,\n"
            "            {'by_patient': patient_addr, 'conflicted': patient_conflicted, 'indices': patient_row_indices},\n"
            "            rows, seen_locations, 'person',\n"
            "        )\n"
        )
        cs_dedup = (
            "        _upsert_location(\n"
            "            _patient_id,\n"
            "            {'address_1': cs_address_1, 'address_2': cs_address_2, 'city': cs_city,\n"
            "             'state': cs_state, 'zip_code': cs_zip_code, 'county': cs_county,\n"
            "             'country_source_value': cs_country_source_value, 'country_concept_id': cs_country_concept_id,\n"
            "             'latitude': cs_latitude, 'longitude': cs_longitude},\n"
            "            cs_location_source_value,\n"
            "            {'by_patient': cs_patient_addr, 'conflicted': cs_patient_conflicted, 'indices': cs_patient_row_indices},\n"
            "            rows, seen_locations, 'care site',\n"
            "        )\n"
        )
        if person_id_auto_increment:
            pid_line = "        _patient_id = str(_src_idx)\n"
        else:
            pid_line = f"        _patient_id = str(row.get({repr(person_id_col)}, '') or '')\n"
        combined = pid_line + person_fields + person_dedup + "\n" + cs_fields + cs_dedup
    else:
        person_dedup = (
            "        if location_source_value and location_source_value not in seen_locations:\n"
            "            if (address_1 or city or state or zip_code):\n"
            "                seen_locations.add(location_source_value)\n"
            + _person_append
        )
        cs_dedup = (
            "        if cs_location_source_value and cs_location_source_value not in seen_locations:\n"
            "            if (cs_address_1 or cs_city or cs_state or cs_zip_code):\n"
            "                seen_locations.add(cs_location_source_value)\n"
            + _cs_append
        )
        combined = person_fields + person_dedup + "\n" + cs_fields + cs_dedup

    return map_init, combined


def _generate_location_script_multi(project, source_files: list, file_configs: dict,
                                     person_id_auto_increment: bool = False) -> str:
    """Generate a multi-file location script that merges addresses across sources."""
    header = (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "from etl_runtime import _info, _read_str\n"
        "\n"
        "\n"
        "def _to_lat(val):\n"
        "    try:\n"
        "        f = float(val)\n"
        "        if -90 <= f <= 90:\n"
        "            return f\n"
        "        print(f'WARNING: latitude value {f} is out of range [-90, 90] — set to NULL')\n"
        "        return None\n"
        "    except (TypeError, ValueError):\n"
        "        return None\n"
        "\n"
        "def _to_lon(val):\n"
        "    try:\n"
        "        f = float(val)\n"
        "        if -180 <= f <= 180:\n"
        "            return f\n"
        "        print(f'WARNING: longitude value {f} is out of range [-180, 180] — set to NULL')\n"
        "        return None\n"
        "    except (TypeError, ValueError):\n"
        "        return None\n"
        "\n"
        "\n"
        "def _upsert_location(patient_id, addr, lsv, tracking, rows, seen_locations, warn_label):\n"
        "    a1       = addr.get('address_1')\n"
        "    a2       = addr.get('address_2')\n"
        "    city     = addr.get('city')\n"
        "    state    = addr.get('state')\n"
        "    zip_code = addr.get('zip_code')\n"
        "    county   = addr.get('county')\n"
        "    csv      = addr.get('country_source_value')\n"
        "    ccid     = addr.get('country_concept_id')\n"
        "    lat      = addr.get('latitude')\n"
        "    lon      = addr.get('longitude')\n"
        "    if patient_id and patient_id in tracking['by_patient']:\n"
        "        if patient_id not in tracking['conflicted']:\n"
        "            _old = tracking['by_patient'][patient_id]\n"
        "            _has_conflict = any(\n"
        "                _old.get(_k) and _new and _old[_k] != _new\n"
        "                for _k, _new in [\n"
        "                    ('address_1', a1), ('address_2', a2), ('city', city),\n"
        "                    ('state', state), ('zip_code', zip_code), ('county', county),\n"
        "                    ('country_source_value', csv),\n"
        "                ]\n"
        "            )\n"
        "            if _has_conflict:\n"
        "                tracking['conflicted'].add(patient_id)\n"
        "                if patient_id in tracking['indices']:\n"
        "                    rows[tracking['indices'][patient_id]] = None\n"
        "                print(f'WARNING: patient {patient_id!r} — conflicting {warn_label} addresses across files; location row dropped')\n"
        "            else:\n"
        "                _idx = tracking['indices'].get(patient_id)\n"
        "                if _idx is not None:\n"
        "                    _r = rows[_idx]\n"
        "                    for _k, _v in [\n"
        "                        ('address_1', a1[:50] if a1 else None),\n"
        "                        ('address_2', a2[:50] if a2 else None),\n"
        "                        ('city', city[:50] if city else None),\n"
        "                        ('state', state[:2] if state else None),\n"
        "                        ('zip', zip_code[:9] if zip_code else None),\n"
        "                        ('county', county[:20] if county else None),\n"
        "                        ('country_source_value', csv),\n"
        "                        ('country_concept_id', ccid),\n"
        "                        ('latitude', _to_lat(lat)),\n"
        "                        ('longitude', _to_lon(lon)),\n"
        "                    ]:\n"
        "                        if _r[_k] is None and _v is not None:\n"
        "                            _r[_k] = _v\n"
        "                    _new_lsv = ' | '.join(filter(None, [\n"
        "                        _r.get('address_1'), _r.get('address_2'), _r.get('city'),\n"
        "                        _r.get('state'), _r.get('zip'), _r.get('county'), _r.get('country_source_value'),\n"
        "                    ]))[:255]\n"
        "                    if _new_lsv != _r['location_source_value']:\n"
        "                        seen_locations.add(_new_lsv)\n"
        "                        _r['location_source_value'] = _new_lsv\n"
        "                    for _pk, _rv in [\n"
        "                        ('address_1', _r.get('address_1')), ('address_2', _r.get('address_2')),\n"
        "                        ('city', _r.get('city')), ('state', _r.get('state')),\n"
        "                        ('zip_code', _r.get('zip')), ('county', _r.get('county')),\n"
        "                        ('country_source_value', _r.get('country_source_value')),\n"
        "                    ]:\n"
        "                        if _rv is not None:\n"
        "                            tracking['by_patient'][patient_id][_pk] = _rv\n"
        "    elif lsv and lsv not in seen_locations:\n"
        "        if a1 or city or state or zip_code:\n"
        "            seen_locations.add(lsv)\n"
        "            if patient_id:\n"
        "                tracking['by_patient'][patient_id] = {\n"
        "                    'address_1': a1, 'address_2': a2, 'city': city,\n"
        "                    'state': state, 'zip_code': zip_code, 'county': county,\n"
        "                    'country_source_value': csv,\n"
        "                }\n"
        "                tracking['indices'][patient_id] = len(rows)\n"
        "            rows.append({\n"
        "                'location_id': None,\n"
        "                'address_1': a1[:50] if a1 else None,\n"
        "                'address_2': a2[:50] if a2 else None,\n"
        "                'city': city[:50] if city else None,\n"
        "                'state': state[:2] if state else None,\n"
        "                'zip': zip_code[:9] if zip_code else None,\n"
        "                'county': county[:20] if county else None,\n"
        "                'location_source_value': lsv,\n"
        "                'country_concept_id': ccid,\n"
        "                'country_source_value': csv,\n"
        "                'latitude': _to_lat(lat),\n"
        "                'longitude': _to_lon(lon),\n"
        "            })\n"
        "\n"
        "\n"
        "def main():\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "    rows = []\n"
        "    seen_locations = set()       # address-key dedup (within + across files)\n"
        "    patient_addr = {}            # person_id -> person address fields\n"
        "    patient_conflicted = set()   # person_ids whose person addresses conflict\n"
        "    patient_row_indices = {}     # person_id -> index into rows[]\n"
        "    cs_patient_addr = {}         # person_id -> care-site address fields\n"
        "    cs_patient_conflicted = set()\n"
        "    cs_patient_row_indices = {}\n"
        "\n"
    )

    file_blocks = ""
    for filename in source_files:
        fc = file_configs.get(filename, {})
        person_id_col = "" if person_id_auto_increment else fc.get("person_id_col", "")
        map_init, row_body = _location_file_block(fc, project, person_id_col, person_id_auto_increment)
        read_line = _sf_read_line(project, filename)
        label = f"    # ── File: {filename} {'─' * max(0, 55 - len(filename))}\n"
        file_blocks += (
            label
            + read_line
            + (("    # Concept maps\n" + map_init) if map_init else "")
            + "    for _src_idx, row in df.iterrows():\n"
            + row_body
            + "\n"
        )

    footer = (
        "    output_df = pd.DataFrame([r for r in rows if r is not None], columns=[\n"
        "        'location_id', 'address_1', 'address_2', 'city', 'state', 'zip', 'county',\n"
        "        'location_source_value', 'country_concept_id', 'country_source_value', 'latitude', 'longitude',\n"
        "    ])\n"
        '    output_df["location_id"] = range(1, len(output_df) + 1)\n'
        "    output_file = os.path.join(output_dir, 'location.csv')\n"
        "    output_df.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        "    print(f'Writing location.csv ... done ({len(output_df)} records)')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )

    return header + file_blocks + footer


def _generate_care_site_script(project) -> str:
    """Deterministic template-based generator for the OMOP care_site script."""
    cs_cfg = (project.etl_config or {}).get("care_site", {})
    loc = (project.etl_config or {}).get("location", {})

    source_files = cs_cfg.get("source_files", [])
    file_configs = cs_cfg.get("file_configs", {})
    if source_files and file_configs and len(source_files) > 1:
        person_id_auto_increment = bool(cs_cfg.get("person_id_auto_increment", False))
        if not person_id_auto_increment:
            missing = [fn for fn in source_files if not file_configs.get(fn, {}).get("person_id_col", "")]
            if missing:
                raise ValueError(
                    f"CareSite: person_id_col is not mapped for {missing}. "
                    f"Map the column in the Care Site step or switch to auto-increment."
                )
        return _generate_care_site_script_multi(project, source_files, file_configs, loc, person_id_auto_increment)

    if source_files and file_configs:
        first_file = source_files[0]
        fc = file_configs.get(first_file, {})
        cs_cfg = {**cs_cfg, **fc, "source_filename": first_file}

        loc_file_configs = loc.get("file_configs", {})
        loc_source_files = loc.get("source_files", [])
        loc_fc = loc_file_configs.get(first_file) or (
            loc_file_configs.get(loc_source_files[0]) if loc_source_files else {}
        ) or {}
        loc = {**loc, **loc_fc}

    source_path_code, delim, enc = _source_file_params(project, cs_cfg)

    name_col = cs_cfg.get("care_site_name_col", "")
    pos_col = cs_cfg.get("place_of_service_col", "")
    pos_value_map = cs_cfg.get("place_of_service_value_map", {})

    cs_a1_col = loc.get("cs_address_1_col", "")
    cs_a2_col = loc.get("cs_address_2_col", "")
    cs_city_col = loc.get("cs_city_col", "")
    cs_state_col = loc.get("cs_state_col", "")
    cs_zip_col = loc.get("cs_zip_col", "")
    cs_county_col = loc.get("cs_county_col", "")
    cs_country_col = loc.get("cs_country_col", "")
    cs_country_sv = loc.get("cs_country_source_value", "")

    has_location = any([cs_a1_col, cs_a2_col, cs_city_col, cs_state_col, cs_zip_col, cs_county_col, cs_country_col, cs_country_sv])

    location_block = (
        "    location_lookup = build_id_lookup(output_dir, 'location.csv', 'location_source_value', 'location_id')\n"
        if has_location else
        "    location_lookup = {}\n"
    )

    cs_extractions = (
        _xtr_v("cs_address_1", cs_a1_col, 8) + "\n"
        + _xtr_v("cs_address_2", cs_a2_col, 8) + "\n"
        + _xtr_v("cs_city", cs_city_col, 8) + "\n"
        + _xtr_v("cs_state", cs_state_col, 8) + "\n"
        + _xtr_v("cs_zip_code", cs_zip_col, 8) + "\n"
        + _xtr_v("cs_county", cs_county_col, 8) + "\n"
        + (
            _xtr_v("cs_country_source_value", cs_country_col, 8) + "\n"
            if cs_country_col else
            f"        cs_country_source_value = {repr(cs_country_sv) if cs_country_sv else 'None'}\n"
        )
        + "        cs_location_source_value = ' | '.join(filter(None, [cs_address_1, cs_address_2, cs_city, cs_state, cs_zip_code, cs_county, cs_country_source_value]))[:255]\n"
        + "        location_id = location_lookup.get(cs_location_source_value)\n"
        + "        if location_id is None and cs_location_source_value:\n"
        + "            _info(f'INFO: care_site row — location not found for address {cs_location_source_value!r}; location_id set to NULL')\n"
        if has_location else
        "        location_id = None\n"
    )

    name_extraction = _xtr_v("care_site_name", name_col, 8) + "\n"

    pos_sv_extraction = (
        _xtr_v("pos_source_value", pos_col, 8) + "\n"
        + "        place_of_service_concept_id = pos_value_map.get(pos_source_value)\n"
        + "        if place_of_service_concept_id is None and pos_source_value is not None:\n"
        + "            _info(f'INFO: place_of_service value {pos_source_value!r} not in map; place_of_service_concept_id set to NULL')\n"
        if pos_col else
        _xtr_doc("pos_source_value", "", 8) + "\n"
        + "        place_of_service_concept_id = None\n"
    )

    seen_set_init = "    _seen_source_values = set()\n"

    dedup_check = (
        "        if care_site_source_value is None:\n"
        "            continue\n"
        "        if care_site_source_value in _seen_source_values:\n"
        "            continue\n"
        "        _seen_source_values.add(care_site_source_value)\n"
    )

    return (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "\n"
        "from etl_runtime import _info, build_id_lookup\n"
        "\n"
        "\n"
        "def main():\n"
        "    # Load environmental variables\n"
        + source_path_code +
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    # --- Place of Service concept maps ---\n"
        f"    pos_value_map      = {json.dumps(pos_value_map)}\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        + location_block
        + "\n"
        + "    # --- Process rows ---\n"
        + "    rows = []\n"
        + seen_set_init
        + "\n"
        + "    for _, row in df.iterrows():\n"
        + "\n"
        + "        # Address / location lookup\n"
        + cs_extractions
        + "\n"
        + "        # Care site name\n"
        + name_extraction
        + "        care_site_source_value = (str(location_id) + ' | ' + care_site_name)[:50] if care_site_name else None\n"
        + "\n"
        + "        # Place of service\n"
        + pos_sv_extraction
        + "\n"
        + dedup_check
        + "        rows.append({\n"
        + "            'care_site_name':                care_site_name[:255] if care_site_name else None,\n"
        + "            'place_of_service_concept_id':   place_of_service_concept_id,\n"
        + "            'location_id':                   location_id,\n"
        + "            'care_site_source_value':        care_site_source_value,\n"
        + "            'place_of_service_source_value': pos_source_value[:50] if pos_source_value else None,\n"
        + "        })\n"
        + "\n"
        + "    # --- Build output DataFrame ---\n"
        + "    CARE_SITE_COLUMNS = ['care_site_id', 'care_site_name', 'place_of_service_concept_id',\n"
        + "                         'location_id', 'care_site_source_value', 'place_of_service_source_value']\n"
        + "    if rows:\n"
        + "        df_out = pd.DataFrame(rows)\n"
        + "        df_out = df_out.reset_index(drop=True)\n"
        + "        df_out['care_site_id'] = df_out.index + 1\n"
        + "        df_out = df_out[CARE_SITE_COLUMNS]\n"
        + "    else:\n"
        + "        df_out = pd.DataFrame(columns=CARE_SITE_COLUMNS)\n"
        + "\n"
        + "    # --- Write output ---\n"
        + "    output_file = os.path.join(output_dir, 'care_site.csv')\n"
        + "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing care_site.csv ... done ({len(df_out)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _loc_lookup_for_care_site(loc: dict) -> str:
    """Return the location_lookup setup lines for a care_site script, based on the location config."""
    has_location = any(loc.get(k) for k in [
        "address_1_col", "address_2_col", "city_col", "state_col", "zip_col",
        "county_col", "country_col", "country_source_value",
        "cs_address_1_col", "cs_address_2_col", "cs_city_col", "cs_state_col",
        "cs_zip_col", "cs_county_col", "cs_country_col", "cs_country_source_value",
    ])
    if not has_location:
        # Check per-file configs
        for fc in loc.get("file_configs", {}).values():
            if any(fc.get(k) for k in ["cs_address_1_col", "cs_address_2_col", "cs_city_col",
                                        "cs_state_col", "cs_zip_col", "cs_county_col",
                                        "cs_country_col", "cs_country_source_value"]):
                has_location = True
                break
    if has_location:
        return (
            "    location_lookup = build_id_lookup(output_dir, 'location.csv', 'location_source_value', 'location_id')\n"
        )
    return "    location_lookup = {}\n"


def _cs_file_row_body(fc: dict, loc_fc: dict, person_id_col: str = "",
                      person_id_auto_increment: bool = False) -> tuple:
    """Row extraction body for one file in the pass-2 care_site loop.

    When a patient identifier is available (column or auto-increment) cs address
    fields are read from _cs_addr[_patient_id], which was populated in pass 1 by
    _cs_addr_pass1_block and already has ALL files' columns merged.  This ensures
    cs_location_source_value always uses the full composite key that location.csv
    carries, regardless of which file is currently being processed.

    The else-path (no patient identifier) is the legacy single-file fallback and
    reads address columns directly from the current row.
    """
    name_col = fc.get("care_site_name_col", "")
    pos_col = fc.get("place_of_service_col", "")
    pos_value_map = fc.get("place_of_service_value_map", {})

    map_init = f"    pos_value_map = {json.dumps(pos_value_map)}\n" if pos_col else ""

    name_line = _xtr_v("care_site_name", name_col, 8) + "\n"
    pos_lines = (
        _xtr_v("pos_source_value", pos_col, 8) + "\n"
        + "        place_of_service_concept_id = pos_value_map.get(pos_source_value)\n"
        + "        if place_of_service_concept_id is None and pos_source_value is not None:\n"
        + "            _info(f'INFO: place_of_service value {pos_source_value!r} not in map; place_of_service_concept_id set to NULL')\n"
        if pos_col else
        _xtr_doc("pos_source_value", "", 8) + "\n"
        + "        place_of_service_concept_id = None\n"
    )

    cs_sv_line = "        care_site_source_value = (str(location_id) + ' | ' + care_site_name)[:50] if care_site_name else None\n"

    if person_id_col or person_id_auto_increment:
        if person_id_auto_increment:
            pid_line = "        _patient_id = str(_src_idx)\n"
        else:
            pid_line = f"        _patient_id = str(row.get({repr(person_id_col)}, '') or '')\n"

        # Read merged cs address from _cs_addr (populated across all files in pass 1)
        addr_lines = (
            "        _merged_addr = _cs_addr.get(_patient_id, {})\n"
            "        cs_address_1            = _merged_addr.get('cs_address_1')\n"
            "        cs_address_2            = _merged_addr.get('cs_address_2')\n"
            "        cs_city                 = _merged_addr.get('cs_city')\n"
            "        cs_state                = _merged_addr.get('cs_state')\n"
            "        cs_zip_code             = _merged_addr.get('cs_zip')\n"
            "        cs_county               = _merged_addr.get('cs_county')\n"
            "        cs_country_source_value = _merged_addr.get('cs_country_source_value')\n"
            "        cs_location_source_value = ' | '.join(filter(None, [\n"
            "            cs_address_1, cs_address_2, cs_city, cs_state, cs_zip_code, cs_county, cs_country_source_value,\n"
            "        ]))[:255]\n"
            "        location_id = location_lookup.get(cs_location_source_value)\n"
            "        if location_id is None and cs_location_source_value:\n"
            "            _info(f'INFO: care_site row — location not found for address {cs_location_source_value!r}; location_id set to NULL')\n"
        )

        _row_append = (
            "            rows.append({\n"
            "                'care_site_name':                care_site_name[:255] if care_site_name else None,\n"
            "                'place_of_service_concept_id':   place_of_service_concept_id,\n"
            "                'location_id':                   location_id,\n"
            "                'care_site_source_value':        care_site_source_value,\n"
            "                'place_of_service_source_value': pos_source_value[:50] if pos_source_value else None,\n"
            "            })\n"
        )

        # Conflict: same patient in a later file maps to a DIFFERENT care site name.
        # Address conflicts can't occur here because all files already share the same
        # merged address via _cs_addr.
        dedup = (
            "        if _patient_id and _patient_id in _patient_cs:\n"
            "            if _patient_id not in _patient_cs_conflicted:\n"
            "                if care_site_source_value and care_site_source_value != _patient_cs[_patient_id]:\n"
            "                    _patient_cs_conflicted.add(_patient_id)\n"
            "                    if _patient_id in _patient_cs_row_idx:\n"
            "                        rows[_patient_cs_row_idx[_patient_id]] = None\n"
            "                    print(f'WARNING: patient {_patient_id!r} — conflicting care sites across files; care site row dropped')\n"
            "        elif care_site_source_value is not None and care_site_source_value not in _seen_source_values:\n"
            "            _seen_source_values.add(care_site_source_value)\n"
            "            if _patient_id:\n"
            "                _patient_cs[_patient_id] = care_site_source_value\n"
            "                _patient_cs_row_idx[_patient_id] = len(rows)\n"
            + _row_append
        )

        # Fallback: if this file doesn't supply name / pos, pull from _cs_addr (pass-1 merged).
        # Use 'is not None' for concept_id because 0 is a valid OMOP value.
        fallback_lines = (
            "        care_site_name = care_site_name or _merged_addr.get('care_site_name')\n"
            "        pos_source_value = pos_source_value or _merged_addr.get('pos_source_value')\n"
            "        place_of_service_concept_id = (place_of_service_concept_id\n"
            "            if place_of_service_concept_id is not None\n"
            "            else _merged_addr.get('place_of_service_concept_id'))\n"
        )

        return map_init, pid_line + addr_lines + "\n" + name_line + pos_lines + fallback_lines + "\n" + cs_sv_line + dedup

    else:
        # Legacy path: read cs address directly from current row (single-file / no patient ID)
        cs_a1_col = loc_fc.get("cs_address_1_col", "")
        cs_a2_col = loc_fc.get("cs_address_2_col", "")
        cs_city_col = loc_fc.get("cs_city_col", "")
        cs_state_col = loc_fc.get("cs_state_col", "")
        cs_zip_col = loc_fc.get("cs_zip_col", "")
        cs_county_col = loc_fc.get("cs_county_col", "")
        cs_country_col = loc_fc.get("cs_country_col", "")
        cs_country_sv = loc_fc.get("cs_country_source_value", "")
        has_location = any([cs_a1_col, cs_a2_col, cs_city_col, cs_state_col, cs_zip_col,
                            cs_county_col, cs_country_col, cs_country_sv])

        addr_lines = (
            _xtr_v("cs_address_1", cs_a1_col, 8) + "\n"
            + _xtr_v("cs_address_2", cs_a2_col, 8) + "\n"
            + _xtr_v("cs_city", cs_city_col, 8) + "\n"
            + _xtr_v("cs_state", cs_state_col, 8) + "\n"
            + _xtr_v("cs_zip_code", cs_zip_col, 8) + "\n"
            + _xtr_v("cs_county", cs_county_col, 8) + "\n"
            + (
                _xtr_v("cs_country_source_value", cs_country_col, 8) + "\n"
                if cs_country_col else
                f"        cs_country_source_value = {repr(cs_country_sv) if cs_country_sv else 'None'}\n"
            )
            + "        cs_location_source_value = ' | '.join(filter(None, [cs_address_1, cs_address_2, cs_city, cs_state, cs_zip_code, cs_county, cs_country_source_value]))[:255]\n"
            + "        location_id = location_lookup.get(cs_location_source_value)\n"
            + "        if location_id is None and cs_location_source_value:\n"
            + "            _info(f'INFO: care_site row — location not found for address {cs_location_source_value!r}; location_id set to NULL')\n"
            if has_location else
            "        location_id = None\n"
        )

        append = (
            cs_sv_line
            + "        if care_site_source_value is None or care_site_source_value in _seen_source_values:\n"
            "            continue\n"
            "        _seen_source_values.add(care_site_source_value)\n"
            "        rows.append({\n"
            "            'care_site_name':                care_site_name[:255] if care_site_name else None,\n"
            "            'place_of_service_concept_id':   place_of_service_concept_id,\n"
            "            'location_id':                   location_id,\n"
            "            'care_site_source_value':        care_site_source_value,\n"
            "            'place_of_service_source_value': pos_source_value[:50] if pos_source_value else None,\n"
            "        })\n"
        )

        return map_init, addr_lines + "\n" + name_line + pos_lines + "\n" + append


def _cs_addr_pass1_block(project, filename: str, loc_fc: dict, fc: dict, person_id_col: str,
                          person_id_auto_increment: bool) -> str:
    """Generate a pass-1 loop that merges cs address + care site fields per patient into _cs_addr.

    Uses first-wins per field: the first non-null value seen across all files wins.
    Collects cs address fields (from loc_fc) AND care_site_name / pos fields (from fc)
    so that pass 2 can fill in missing columns when they are split across files.
    """
    pairs = []
    for key, col in [
        ("cs_address_1", loc_fc.get("cs_address_1_col", "")),
        ("cs_address_2", loc_fc.get("cs_address_2_col", "")),
        ("cs_city",      loc_fc.get("cs_city_col", "")),
        ("cs_state",     loc_fc.get("cs_state_col", "")),
        ("cs_zip",       loc_fc.get("cs_zip_col", "")),
        ("cs_county",    loc_fc.get("cs_county_col", "")),
    ]:
        if col:
            pairs.append(f"({repr(key)}, str(row.get({repr(col)}, '') or '') or None)")
    cc = loc_fc.get("cs_country_col", "")
    cs_sv = loc_fc.get("cs_country_source_value", "")
    if cc:
        pairs.append(f"('cs_country_source_value', str(row.get({repr(cc)}, '') or '') or None)")
    elif cs_sv:
        pairs.append(f"('cs_country_source_value', {repr(cs_sv)})")

    # care site columns from fc — needed when name / pos are split across files
    name_col = fc.get("care_site_name_col", "")
    pos_col  = fc.get("place_of_service_col", "")
    pos_vm   = fc.get("place_of_service_value_map", {})
    if name_col:
        pairs.append(f"('care_site_name', str(row.get({repr(name_col)}, '') or '') or None)")
    if pos_col:
        pairs.append(f"('pos_source_value', str(row.get({repr(pos_col)}, '') or '') or None)")
        pairs.append(
            f"('place_of_service_concept_id', {json.dumps(pos_vm)}.get("
            f"str(row.get({repr(pos_col)}, '') or '') or None))"
        )

    if not pairs:
        return ""

    pid_expr = "str(_src_idx)" if person_id_auto_increment else f"str(row.get({repr(person_id_col)}, '') or '')"
    field_pairs_str = ", ".join(pairs)
    label = f"    # ── File: {filename} (address pass) {'─' * max(0, 46 - len(filename))}\n"
    return (
        label
        + _sf_read_line(project, filename)
        + "    for _src_idx, row in df.iterrows():\n"
        + f"        _pid = {pid_expr}\n"
        + "        if _pid:\n"
        + "            if _pid not in _cs_addr:\n"
        + "                _cs_addr[_pid] = {}\n"
        + f"            for _k, _v in [{field_pairs_str}]:\n"
        + "                if _v and _k not in _cs_addr[_pid]:\n"
        + "                    _cs_addr[_pid][_k] = _v\n"
    )


def _generate_care_site_script_multi(project, source_files: list, file_configs: dict, loc: dict,
                                      person_id_auto_increment: bool = False) -> str:
    """Generate a two-pass multi-file care_site script.

    Pass 1: merge cs address fields per patient across all source files into _cs_addr.
    Pass 2: generate care site rows using the fully-merged address for every patient,
            ensuring cs_location_source_value always matches the composite key in location.csv.
    """
    loc_file_configs = loc.get("file_configs", {})

    def _get_loc_fc(fn: str) -> dict:
        return loc_file_configs.get(fn) or (
            loc_file_configs.get(source_files[0]) if source_files else {}
        ) or {}

    header = (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "from etl_runtime import _info, build_id_lookup\n"
        "\n"
        "\n"
        "def main():\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        + _loc_lookup_for_care_site(loc)
        + "    # ── Pass 1: merge cs address fields per patient across all files ─────────\n"
        "    _cs_addr = {}               # patient_id -> merged cs address fields\n"
        "\n"
    )

    pass1_blocks = ""
    for filename in source_files:
        fc = file_configs.get(filename, {})
        loc_fc = _get_loc_fc(filename)
        person_id_col = "" if person_id_auto_increment else fc.get("person_id_col", "")
        block = _cs_addr_pass1_block(project, filename, loc_fc, fc, person_id_col, person_id_auto_increment)
        if block:
            pass1_blocks += block + "\n"

    pass2_header = (
        "    # ── Pass 2: generate care site rows ──────────────────────────────────────\n"
        "    rows = []\n"
        "    _seen_source_values = set()\n"
        "    _patient_cs = {}            # patient_id -> care_site_source_value string\n"
        "    _patient_cs_conflicted = set()\n"
        "    _patient_cs_row_idx = {}    # patient_id -> index into rows[]\n"
        "\n"
    )

    pass2_blocks = ""
    for filename in source_files:
        fc = file_configs.get(filename, {})
        loc_fc = _get_loc_fc(filename)
        person_id_col = "" if person_id_auto_increment else fc.get("person_id_col", "")
        map_init, row_body = _cs_file_row_body(fc, loc_fc, person_id_col, person_id_auto_increment)
        read_line = _sf_read_line(project, filename)
        label = f"    # ── File: {filename} {'─' * max(0, 55 - len(filename))}\n"
        pass2_blocks += (
            label
            + read_line
            + (map_init if map_init else "")
            + "    for _src_idx, row in df.iterrows():\n"
            + row_body
            + "\n"
        )

    footer = (
        "    CARE_SITE_COLUMNS = ['care_site_id', 'care_site_name', 'place_of_service_concept_id',\n"
        "                         'location_id', 'care_site_source_value', 'place_of_service_source_value']\n"
        "    if rows:\n"
        "        df_out = pd.DataFrame([r for r in rows if r is not None])\n"
        "        df_out = df_out.reset_index(drop=True)\n"
        "        df_out['care_site_id'] = df_out.index + 1\n"
        "        df_out = df_out[CARE_SITE_COLUMNS]\n"
        "    else:\n"
        "        df_out = pd.DataFrame(columns=CARE_SITE_COLUMNS)\n"
        "    output_file = os.path.join(output_dir, 'care_site.csv')\n"
        "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        "    print(f'Writing care_site.csv ... done ({len(df_out)} records)')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )

    return header + pass1_blocks + pass2_header + pass2_blocks + footer


def _generate_provider_script(project) -> str:
    """Deterministic template-based generator for the OMOP provider script."""
    prov = (project.etl_config or {}).get("provider", {})
    cs_cfg = (project.etl_config or {}).get("care_site", {})

    source_files = prov.get("source_files", [])
    file_configs = prov.get("file_configs", {})
    if source_files and file_configs:
        person_id_auto_increment = prov.get("person_id_auto_increment", False)
        return _generate_provider_script_multi(project, source_files, file_configs, cs_cfg,
                                               person_id_auto_increment)

    source_path_code, delim, enc = _source_file_params(project, prov)

    name_col = prov.get("provider_name_col", "")
    npi_col = prov.get("npi_col", "")
    dea_col = prov.get("dea_col", "")
    yob_col = prov.get("year_of_birth_col", "")
    specialty_col = prov.get("specialty_source_value_col", "")
    specialty_map = prov.get("specialty_concept_value_map", {})
    prefix_specialty = prov.get("prefix_specialty", "") or ""
    prefix_specialty_cid = prov.get("prefix_specialty_concept_id")
    gender_col = prov.get("gender_source_value_col", "")
    gender_map = prov.get("gender_concept_value_map", {})
    gender_default = prov.get("gender_concept_id_default", 0) or 0
    _cs_file_cfgs = cs_cfg.get("file_configs", {})
    _cs_src_files = cs_cfg.get("source_files", [])
    cs_name_col = ""
    for _fn in _cs_src_files:
        cs_name_col = _cs_file_cfgs.get(_fn, {}).get("care_site_name_col", "")
        if cs_name_col:
            break
    if not cs_name_col:
        cs_name_col = cs_cfg.get("care_site_name_col", "")

    if cs_name_col:
        care_site_block = (
            "    care_site_lookup = build_id_lookup(output_dir, 'care_site.csv', 'care_site_name', 'care_site_id')\n"
        )
        cs_lookup_line = (
            _xtr("raw_cs_name", cs_name_col, 8) + "\n"
            "        care_site_id = care_site_lookup.get(raw_cs_name) if raw_cs_name else None\n"
            "        if raw_cs_name and care_site_id is None:\n"
            "            _info(f'INFO: provider row — care_site {raw_cs_name!r} not found in care_site.csv; care_site_id set to NULL')\n"
        )
    else:
        care_site_block = "    care_site_lookup = {}\n"
        cs_lookup_line = _xtr_doc("care_site_id", "", 8) + "\n"

    if specialty_col:
        specialty_lines = (
            _xtr("specialty_source_value", specialty_col, 8) + "\n"
            "        if specialty_source_value is None:\n"
            f'            _info(f"INFO: provider row — specialty column {repr(specialty_col)} is empty; specialty_concept_id set to 0")\n'
            "            specialty_concept_id = 0\n"
            "        elif specialty_source_value not in specialty_map:\n"
            "            _info(f'INFO: provider row — specialty value {specialty_source_value!r} not in map; specialty_concept_id set to 0')\n"
            "            specialty_concept_id = 0\n"
            "        else:\n"
            "            specialty_concept_id = specialty_map[specialty_source_value]\n"
        )
    elif prefix_specialty:
        _sv = prefix_specialty[:50]
        _cid = prefix_specialty_cid if prefix_specialty_cid is not None else 0
        specialty_lines = (
            f"        specialty_source_value = {repr(_sv)}\n"
            f"        specialty_concept_id = {_cid}\n"
        )
    else:
        specialty_lines = (
            _xtr_doc("specialty_source_value", "", 8) + "\n"
            "        specialty_concept_id = 0\n"
        )

    if gender_col:
        gender_lines = (
            _xtr("gender_source_value", gender_col, 8) + "\n"
            "        if gender_source_value is None:\n"
            "            _info(f'INFO: provider row — gender column is empty; gender_concept_id set to {gender_default}')\n"
            "            gender_concept_id = gender_default\n"
            "        elif gender_source_value not in gender_map:\n"
            "            _info(f'INFO: provider row — gender value {gender_source_value!r} not in map; gender_concept_id set to {gender_default}')\n"
            "            gender_concept_id = gender_default\n"
            "        else:\n"
            "            gender_concept_id = gender_map[gender_source_value]\n"
        )
    else:
        gender_lines = (
            _xtr_doc("gender_source_value", "", 8) + "\n"
            "        gender_concept_id = gender_default\n"
        )

    name_line = _xtr_doc("provider_name", name_col, 8) + "\n"
    npi_line  = _xtr_doc("npi", npi_col, 8) + "\n"
    dea_line  = _xtr_doc("dea", dea_col, 8) + "\n"
    yob_lines = (
        f"        _yob_raw = row.get({repr(yob_col)})\n"
        "        year_of_birth = _parse_year_of_birth(_yob_raw)\n"
        if yob_col else
        _xtr_doc("year_of_birth", "", 8) + "\n"
    )

    return (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "\n"
        "from etl_runtime import _info, _read_str, build_id_lookup\n"
        "\n"
        "\n"
        "def _parse_year_of_birth(val):\n"
        "    try:\n"
        "        return int(val) if pd.notnull(val) else None\n"
        "    except (TypeError, ValueError):\n"
        "        _info(f'INFO: provider row — year_of_birth value {val!r} could not be parsed as int; year_of_birth set to NULL')\n"
        "        return None\n"
        "\n"
        "\n"
        "def main():\n"
        "    # Load environmental variables\n"
        + source_path_code +
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    # --- specialty_map ---\n"
        f"    specialty_map  = {json.dumps(specialty_map)}\n"
        "    # --- gender_map ---\n"
        f"    gender_map     = {json.dumps(gender_map)}\n"
        f"    gender_default = {gender_default}\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        + care_site_block
        + "\n"
        + "    # --- Process rows ---\n"
        + "    rows = []\n"
        + "    seen = set()  # deduplicate by provider_source_value\n"
        + "\n"
        + "    for _, row in df.iterrows():\n"
        + "\n"
        + "        # Provider identifiers\n"
        + name_line
        + npi_line
        + dea_line
        + yob_lines
        + "\n"
        + "        # Specialty\n"
        + specialty_lines
        + "\n"
        + "        # Care site lookup\n"
        + cs_lookup_line
        + "\n"
        + "        # Gender\n"
        + gender_lines
        + "\n"
        + "        provider_source_value = (str(care_site_id) + ' | ' + (provider_name or ''))[:50]\n"
        + "        if provider_source_value in seen:\n"
        + "            continue\n"
        + "        seen.add(provider_source_value)\n"
        + "\n"
        + "        rows.append({\n"
        + "            'provider_id':               None,\n"
        + "            'provider_name':             provider_name[:255] if provider_name else None,\n"
        + "            'npi':                       npi[:20] if npi else None,\n"
        + "            'dea':                       dea[:20] if dea else None,\n"
        + "            'specialty_concept_id':      specialty_concept_id,\n"
        + "            'care_site_id':              care_site_id,\n"
        + "            'year_of_birth':             year_of_birth,\n"
        + "            'gender_concept_id':         gender_concept_id,\n"
        + "            'provider_source_value':     provider_source_value,\n"
        + "            'specialty_source_value':    specialty_source_value[:50] if specialty_source_value else None,\n"
        + "            'specialty_source_concept_id': 0,\n"
        + "            'gender_source_value':       gender_source_value[:50] if gender_source_value else None,\n"
        + "            'gender_source_concept_id':  0,\n"
        + "        })\n"
        + "\n"
        + "    # --- Build output DataFrame ---\n"
        + "    PROVIDER_COLUMNS = ['provider_id', 'provider_name', 'npi', 'dea', 'specialty_concept_id',\n"
        + "                        'care_site_id', 'year_of_birth', 'gender_concept_id', 'provider_source_value',\n"
        + "                        'specialty_source_value', 'specialty_source_concept_id',\n"
        + "                        'gender_source_value', 'gender_source_concept_id']\n"
        + "    if rows:\n"
        + "        df_out = pd.DataFrame(rows)\n"
        + "        df_out['provider_id'] = range(1, len(df_out) + 1)\n"
        + "        df_out = df_out[PROVIDER_COLUMNS]\n"
        + "    else:\n"
        + "        df_out = pd.DataFrame(columns=PROVIDER_COLUMNS)\n"
        + "\n"
        + "    # --- Write output ---\n"
        + "    output_file = os.path.join(output_dir, 'provider.csv')\n"
        + "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing provider.csv ... done ({len(df_out)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _prov_file_row_body(fc: dict, cs_name_col: str) -> str:
    """Row extraction body for one file in a multi-file provider script."""
    name_col = fc.get("provider_name_col", "")
    npi_col = fc.get("npi_col", "")
    dea_col = fc.get("dea_col", "")
    yob_col = fc.get("year_of_birth_col", "")
    specialty_col = fc.get("specialty_source_value_col", "")
    specialty_map = fc.get("specialty_concept_value_map", {})
    prefix_specialty = fc.get("prefix_specialty", "") or ""
    prefix_specialty_cid = fc.get("prefix_specialty_concept_id")
    gender_col = fc.get("gender_source_value_col", "")
    gender_map = fc.get("gender_concept_value_map", {})
    gender_default = fc.get("gender_concept_id_default", 0) or 0

    map_init = (
        f"    specialty_map  = {json.dumps(specialty_map)}\n"
        f"    gender_map     = {json.dumps(gender_map)}\n"
        f"    gender_default = {gender_default}\n"
    )

    if specialty_col:
        specialty_lines = (
            _xtr("specialty_source_value", specialty_col, 8) + "\n"
            "        if specialty_source_value is None:\n"
            f'            _info(f"INFO: provider row — specialty column {repr(specialty_col)} is empty; specialty_concept_id set to 0")\n'
            "            specialty_concept_id = 0\n"
            "        elif specialty_source_value not in specialty_map:\n"
            "            _info(f'INFO: provider row — specialty value {specialty_source_value!r} not in map; specialty_concept_id set to 0')\n"
            "            specialty_concept_id = 0\n"
            "        else:\n"
            "            specialty_concept_id = specialty_map[specialty_source_value]\n"
        )
    elif prefix_specialty:
        _sv = prefix_specialty[:50]
        _cid = prefix_specialty_cid if prefix_specialty_cid is not None else 0
        specialty_lines = (
            f"        specialty_source_value = {repr(_sv)}\n"
            f"        specialty_concept_id = {_cid}\n"
        )
    else:
        specialty_lines = (
            _xtr_doc("specialty_source_value", "", 8) + "\n"
            "        specialty_concept_id = 0\n"
        )

    if gender_col:
        gender_lines = (
            _xtr("gender_source_value", gender_col, 8) + "\n"
            "        if gender_source_value is None:\n"
            "            _info(f'INFO: provider row — gender column is empty; gender_concept_id set to {gender_default}')\n"
            "            gender_concept_id = gender_default\n"
            "        elif gender_source_value not in gender_map:\n"
            "            _info(f'INFO: provider row — gender value {gender_source_value!r} not in map; gender_concept_id set to {gender_default}')\n"
            "            gender_concept_id = gender_default\n"
            "        else:\n"
            "            gender_concept_id = gender_map[gender_source_value]\n"
        )
    else:
        gender_lines = (
            _xtr_doc("gender_source_value", "", 8) + "\n"
            "        gender_concept_id = gender_default\n"
        )

    if cs_name_col:
        cs_lookup = (
            _xtr("raw_cs_name", cs_name_col, 8) + "\n"
            "        care_site_id = care_site_lookup.get(raw_cs_name) if raw_cs_name else None\n"
            "        if raw_cs_name and care_site_id is None:\n"
            "            _info(f'INFO: provider row — care_site {raw_cs_name!r} not found in care_site.csv; care_site_id set to NULL')\n"
        )
    else:
        cs_lookup = _xtr_doc("care_site_id", "", 8) + "\n"

    name_line = _xtr_doc("provider_name", name_col, 8) + "\n"
    npi_line  = _xtr_doc("npi", npi_col, 8) + "\n"
    dea_line  = _xtr_doc("dea", dea_col, 8) + "\n"
    yob_lines = (
        f"        _yob_raw = row.get({repr(yob_col)})\n"
        "        year_of_birth = _parse_year_of_birth(_yob_raw)\n"
        if yob_col else
        _xtr_doc("year_of_birth", "", 8) + "\n"
    )

    append = (
        "        provider_source_value = (str(care_site_id) + ' | ' + (provider_name or ''))[:50]\n"
        "        if provider_source_value in seen:\n"
        "            continue\n"
        "        seen.add(provider_source_value)\n"
        "        rows.append({\n"
        "            'provider_id':               None,\n"
        "            'provider_name':             provider_name[:255] if provider_name else None,\n"
        "            'npi':                       npi[:20] if npi else None,\n"
        "            'dea':                       dea[:20] if dea else None,\n"
        "            'specialty_concept_id':      specialty_concept_id,\n"
        "            'care_site_id':              care_site_id,\n"
        "            'year_of_birth':             year_of_birth,\n"
        "            'gender_concept_id':         gender_concept_id,\n"
        "            'provider_source_value':     provider_source_value,\n"
        "            'specialty_source_value':    specialty_source_value[:50] if specialty_source_value else None,\n"
        "            'specialty_source_concept_id': 0,\n"
        "            'gender_source_value':       gender_source_value[:50] if gender_source_value else None,\n"
        "            'gender_source_concept_id':  0,\n"
        "        })\n"
    )

    row_body = (
        name_line + npi_line + dea_line + yob_lines
        + "\n        # Specialty\n" + specialty_lines
        + "\n        # Care site lookup\n" + cs_lookup
        + "\n        # Gender\n" + gender_lines
        + "\n" + append
    )
    return map_init, row_body


def _prov_pass1_block(project, filename: str, fc: dict, cs_name_col: str,
                       person_id_col: str, person_id_auto_increment: bool) -> str:
    """Generate a pass-1 loop that merges provider field values per patient into _prov_data.

    Collects: provider_name, npi, dea, year_of_birth, specialty_source_value,
              specialty_concept_id, gender_source_value, gender_concept_id, care_site_id.
    care_site_id is stored first-wins so that pass 2 uses a consistent value across all
    files for the same patient, ensuring provider_source_value is identical everywhere.
    Returns "" if there is nothing to collect for this file.
    """
    name_col      = fc.get("provider_name_col", "")
    npi_col       = fc.get("npi_col", "")
    dea_col       = fc.get("dea_col", "")
    yob_col       = fc.get("year_of_birth_col", "")
    specialty_col = fc.get("specialty_source_value_col", "")
    specialty_map = fc.get("specialty_concept_value_map", {})
    prefix_sp     = (fc.get("prefix_specialty", "") or "").strip()
    prefix_sp_cid = fc.get("prefix_specialty_concept_id")
    gender_col    = fc.get("gender_source_value_col", "")
    gender_map    = fc.get("gender_concept_value_map", {})
    gender_default = fc.get("gender_concept_id_default", 0) or 0

    pairs = []
    if name_col:
        pairs.append(f"('provider_name', str(row.get({repr(name_col)}, '') or '') or None)")
    if npi_col:
        pairs.append(f"('npi', str(row.get({repr(npi_col)}, '') or '') or None)")
    if dea_col:
        pairs.append(f"('dea', str(row.get({repr(dea_col)}, '') or '') or None)")
    if yob_col:
        pairs.append(f"('year_of_birth', _parse_year_of_birth(row.get({repr(yob_col)})))")
    if specialty_col:
        pairs.append(f"('specialty_source_value', str(row.get({repr(specialty_col)}, '') or '') or None)")
        pairs.append(
            f"('specialty_concept_id', {json.dumps(specialty_map)}.get("
            f"str(row.get({repr(specialty_col)}, '') or '') or None))"
        )
    elif prefix_sp:
        pairs.append(f"('specialty_source_value', {repr(prefix_sp[:50])})")
        pairs.append(f"('specialty_concept_id', {prefix_sp_cid or 0})")
    if gender_col:
        pairs.append(f"('gender_source_value', str(row.get({repr(gender_col)}, '') or '') or None)")
        pairs.append(
            f"('gender_concept_id', {json.dumps(gender_map)}.get("
            f"str(row.get({repr(gender_col)}, '') or '') or None, {gender_default}))"
        )
    if cs_name_col:
        pairs.append(
            f"('care_site_id', care_site_lookup.get("
            f"(str(row.get({repr(cs_name_col)}, '') or '').strip() or None)))"
        )

    if not pairs:
        return ""

    pid_expr = "str(_src_idx)" if person_id_auto_increment else f"str(row.get({repr(person_id_col)}, '') or '')"
    field_pairs_str = ", ".join(pairs)
    label = f"    # ── File: {filename} (data pass) {'─' * max(0, 47 - len(filename))}\n"
    return (
        label
        + _sf_read_line(project, filename)
        + "    for _src_idx, row in df.iterrows():\n"
        + f"        _pid = {pid_expr}\n"
        + "        if _pid:\n"
        + "            if _pid not in _prov_data:\n"
        + "                _prov_data[_pid] = {}\n"
        + f"            for _k, _v in [{field_pairs_str}]:\n"
        + "                if _v and _k not in _prov_data[_pid]:\n"
        + "                    _prov_data[_pid][_k] = _v\n"
    )


def _prov_file_row_body_pass2(fc: dict, cs_name_col: str,
                               person_id_col: str, person_id_auto_increment: bool) -> str:
    """Pass-2 row body for a provider file.

    Reads merged provider attributes (name, specialty, gender, npi, dea, yob) from
    _prov_data[_pid]. care_site_id is looked up fresh from the current row via
    care_site_lookup, identical to the original single-pass code.
    """
    prefix_sp     = (fc.get("prefix_specialty", "") or "").strip()
    prefix_sp_cid = fc.get("prefix_specialty_concept_id")
    gender_default = fc.get("gender_concept_id_default", 0) or 0

    pid_expr = "str(_src_idx)" if person_id_auto_increment else f"str(row.get({repr(person_id_col)}, '') or '')"

    map_init = (
        f"    _prov_prefix_specialty     = {repr(prefix_sp[:50] if prefix_sp else '')}\n"
        f"    _prov_prefix_specialty_cid = {prefix_sp_cid or 0}\n"
        f"    _prov_gender_default       = {gender_default}\n"
    )

    if cs_name_col:
        cs_lookup_line = (
            # Prefer care_site_id merged in pass 1 so all files for the same patient agree.
            # Fall back to a fresh row lookup only if pass 1 didn't capture it (e.g. the
            # cs column was missing/null in every file that contributed to _prov_data).
            "        care_site_id = _merged.get('care_site_id')\n"
            "        if care_site_id is None:\n"
            + _xtr("_raw_cs_name", cs_name_col, 12) + "\n"
            "            care_site_id = care_site_lookup.get(_raw_cs_name) if _raw_cs_name else None\n"
            "            if _raw_cs_name and care_site_id is None:\n"
            f"                _info(f'INFO: provider row — care_site {{_raw_cs_name!r}} not found in care_site.csv; care_site_id set to NULL')\n"
        )
    else:
        cs_lookup_line = "        care_site_id = _merged.get('care_site_id')\n"

    row_body = (
        f"        _pid = {pid_expr}\n"
        "        _merged = _prov_data.get(_pid, {})\n"
        "        if not _merged:\n"
        "            continue\n"
        "        provider_name = _merged.get('provider_name')\n"
        "        npi           = _merged.get('npi')\n"
        "        dea           = _merged.get('dea')\n"
        "        year_of_birth = _merged.get('year_of_birth')\n"
        "        specialty_source_value  = _merged.get('specialty_source_value') or (_prov_prefix_specialty or None)\n"
        "        specialty_concept_id    = _merged.get('specialty_concept_id') or _prov_prefix_specialty_cid\n"
        "        gender_source_value     = _merged.get('gender_source_value')\n"
        "        _gcid = _merged.get('gender_concept_id')\n"
        "        gender_concept_id       = _gcid if _gcid is not None else _prov_gender_default\n"
        + cs_lookup_line
        + "        provider_source_value = (str(care_site_id) + ' | ' + (provider_name or ''))[:50]\n"
        "        if provider_source_value in seen:\n"
        "            continue\n"
        "        seen.add(provider_source_value)\n"
        "        rows.append({\n"
        "            'provider_id':               None,\n"
        "            'provider_name':             provider_name[:255] if provider_name else None,\n"
        "            'npi':                       npi[:20] if npi else None,\n"
        "            'dea':                       dea[:20] if dea else None,\n"
        "            'specialty_concept_id':      specialty_concept_id,\n"
        "            'care_site_id':              care_site_id,\n"
        "            'year_of_birth':             year_of_birth,\n"
        "            'gender_concept_id':         gender_concept_id,\n"
        "            'provider_source_value':     provider_source_value,\n"
        "            'specialty_source_value':    specialty_source_value[:50] if specialty_source_value else None,\n"
        "            'specialty_source_concept_id': 0,\n"
        "            'gender_source_value':       gender_source_value[:50] if gender_source_value else None,\n"
        "            'gender_source_concept_id':  0,\n"
        "        })\n"
    )
    return map_init, row_body


def _generate_provider_script_multi(project, source_files: list, file_configs: dict, cs_cfg: dict,
                                     person_id_auto_increment: bool = False) -> str:
    """Generate a multi-file provider script.

    When person_id is configured (auto_increment or per-file column), a two-pass approach is
    used: pass 1 merges all provider fields per patient; pass 2 generates deduplicated rows.
    Without person_id config, falls back to sequential single-pass processing.
    """
    # Determine the care_site_name_col from any configured care-site file (first-wins), or legacy
    cs_file_configs = cs_cfg.get("file_configs", {})
    cs_source_files = cs_cfg.get("source_files", [])
    cs_name_col = ""
    for _cs_fn in cs_source_files:
        cs_name_col = cs_file_configs.get(_cs_fn, {}).get("care_site_name_col", "")
        if cs_name_col:
            break
    if not cs_name_col:
        cs_name_col = cs_cfg.get("care_site_name_col", "")

    if cs_name_col:
        care_site_block = (
            "    care_site_lookup = build_id_lookup(output_dir, 'care_site.csv', 'care_site_name', 'care_site_id')\n"
        )
    else:
        care_site_block = "    care_site_lookup = {}\n"

    common_header = (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        "from etl_runtime import _info, _read_str, build_id_lookup\n"
        "\n"
        "\n"
        "def _parse_year_of_birth(val):\n"
        "    try:\n"
        "        return int(val) if pd.notnull(val) else None\n"
        "    except (TypeError, ValueError):\n"
        "        _info(f'INFO: provider row — year_of_birth value {val!r} could not be parsed as int; year_of_birth set to NULL')\n"
        "        return None\n"
        "\n"
        "\n"
        "def main():\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        + care_site_block
    )

    footer = (
        "    PROVIDER_COLUMNS = ['provider_id', 'provider_name', 'npi', 'dea', 'specialty_concept_id',\n"
        "                        'care_site_id', 'year_of_birth', 'gender_concept_id', 'provider_source_value',\n"
        "                        'specialty_source_value', 'specialty_source_concept_id',\n"
        "                        'gender_source_value', 'gender_source_concept_id']\n"
        "    if rows:\n"
        "        df_out = pd.DataFrame(rows)\n"
        "        df_out['provider_id'] = range(1, len(df_out) + 1)\n"
        "        df_out = df_out[PROVIDER_COLUMNS]\n"
        "    else:\n"
        "        df_out = pd.DataFrame(columns=PROVIDER_COLUMNS)\n"
        "    output_file = os.path.join(output_dir, 'provider.csv')\n"
        "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        "    print(f'Writing provider.csv ... done ({len(df_out)} records)')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )

    # ── Two-pass path (person_id configured) ─────────────────────────────
    has_person_id = person_id_auto_increment or any(
        file_configs.get(fn, {}).get("person_id_col") for fn in source_files
    )
    if has_person_id:
        pass1_blocks = ""
        for filename in source_files:
            fc = file_configs.get(filename, {})
            person_id_col = "" if person_id_auto_increment else fc.get("person_id_col", "")
            block = _prov_pass1_block(project, filename, fc, cs_name_col, person_id_col, person_id_auto_increment)
            if block:
                pass1_blocks += block + "\n"

        pass2_blocks = ""
        for filename in source_files:
            fc = file_configs.get(filename, {})
            person_id_col = "" if person_id_auto_increment else fc.get("person_id_col", "")
            map_init, row_body = _prov_file_row_body_pass2(fc, cs_name_col, person_id_col, person_id_auto_increment)
            read_line = _sf_read_line(project, filename)
            label = f"    # ── File: {filename} {'─' * max(0, 55 - len(filename))}\n"
            pass2_blocks += (
                label
                + read_line
                + map_init
                + "    for _src_idx, row in df.iterrows():\n"
                + row_body
                + "\n"
            )

        header = (
            common_header
            + "    # ── Pass 1: merge provider fields per patient across all files ──────────\n"
            "    _prov_data = {}              # patient_id -> merged provider fields\n"
            "\n"
        )
        pass2_header = (
            "    # ── Pass 2: generate provider rows ───────────────────────────────────────\n"
            "    rows = []\n"
            "    seen = set()\n"
            "\n"
        )
        return header + pass1_blocks + pass2_header + pass2_blocks + footer

    # ── Legacy single-pass path (no person_id) ────────────────────────────
    header = common_header + "    rows = []\n    seen = set()\n\n"
    file_blocks = ""
    for filename in source_files:
        fc = file_configs.get(filename, {})
        map_init, row_body = _prov_file_row_body(fc, cs_name_col)
        read_line = _sf_read_line(project, filename)
        label = f"    # ── File: {filename} {'─' * max(0, 55 - len(filename))}\n"
        file_blocks += (
            label
            + read_line
            + (map_init if map_init else "")
            + "    for _src_idx, row in df.iterrows():\n"
            + row_body
            + "\n"
        )
    return header + file_blocks + footer


def _person_pid_cfg(person_cfg: dict) -> dict:
    """Return the person_id FieldMapping from either new (file_configs) or legacy (mappings) format."""
    file_configs = person_cfg.get("file_configs", {})
    source_files = person_cfg.get("source_files", [])
    if file_configs and source_files:
        primary_fc = file_configs.get(source_files[0], {})
        return (primary_fc.get("mappings") or {}).get("person_id") or {}
    return (person_cfg.get("mappings") or {}).get("person_id") or {}


def _person_parse_file_cfg(fc: dict) -> dict:
    """Parse a PersonFileConfig dict into a flat vars dict for code generation."""
    mappings = fc.get("mappings", {})
    pid_cfg = mappings.get("person_id") or {}
    gender_cfg = mappings.get("gender_concept_id") or {}
    dob_cfg = mappings.get("year_of_birth") or {}
    race_cfg = mappings.get("race_concept_id") or {}
    eth_cfg = mappings.get("ethnicity_concept_id") or {}

    auto_increment = pid_cfg.get("auto_increment", False)
    pid_col = pid_cfg.get("source_col", "")
    pid_transform = pid_cfg.get("transform", "int_float")

    gender_col = gender_cfg.get("source_col", "")
    gender_map = gender_cfg.get("value_map") or {}
    gender_default = int(gender_cfg.get("default") or 0)

    dob_col = dob_cfg.get("source_col", "")
    date_format = dob_cfg.get("date_format", "%Y-%m-%d")
    birth_time_col = fc.get("birth_time_col", "")
    birth_time_format = fc.get("birth_time_format", "%H:%M:%S") or "%H:%M:%S"

    if "constant" in race_cfg:
        race_mode, race_constant = "constant", int(race_cfg["constant"])
        race_col, race_map, race_default = "", {}, 0
    elif race_cfg.get("source_col"):
        race_mode = "column"
        race_col = race_cfg["source_col"]
        race_map = race_cfg.get("value_map") or {}
        race_default = int(race_cfg.get("default") or 0)
        race_constant = 0
    else:
        race_mode = "default"
        race_default = int(race_cfg.get("default") or 0)
        race_col, race_map, race_constant = "", {}, 0

    if "constant" in eth_cfg:
        eth_mode, eth_constant = "constant", int(eth_cfg["constant"])
        eth_col, eth_map, eth_default = "", {}, 0
    elif eth_cfg.get("source_col"):
        eth_mode = "column"
        eth_col = eth_cfg["source_col"]
        eth_map = eth_cfg.get("value_map") or {}
        eth_default = int(eth_cfg.get("default") or 0)
        eth_constant = 0
    else:
        eth_mode = "default"
        eth_default = int(eth_cfg.get("default") or 0)
        eth_col, eth_map, eth_constant = "", {}, 0

    return dict(
        auto_increment=auto_increment, pid_col=pid_col, pid_transform=pid_transform,
        gender_col=gender_col, gender_map=gender_map, gender_default=gender_default,
        dob_col=dob_col, date_format=date_format,
        birth_time_col=birth_time_col, birth_time_format=birth_time_format,
        race_mode=race_mode, race_col=race_col, race_map=race_map,
        race_default=race_default, race_constant=race_constant if "constant" in race_cfg or not race_cfg.get("source_col") else 0,
        eth_mode=eth_mode, eth_col=eth_col, eth_map=eth_map,
        eth_default=eth_default, eth_constant=eth_constant if "constant" in eth_cfg or not eth_cfg.get("source_col") else 0,
    )


def _person_concept_map_code(v: dict, indent: str = "    ") -> str:
    """Generate concept-map variable initialisation lines for a single file block."""
    out = f"{indent}gender_map = {json.dumps(v['gender_map'])}\n"
    out += f"{indent}gender_default = {v['gender_default']}\n"
    if v["race_mode"] == "column":
        out += f"{indent}race_map = {json.dumps(v['race_map'])}\n"
        out += f"{indent}race_default = {v['race_default']}\n"
    if v["eth_mode"] == "column":
        out += f"{indent}ethnicity_map = {json.dumps(v['eth_map'])}\n"
        out += f"{indent}ethnicity_default = {v['eth_default']}\n"
    return out


def _person_row_body(v: dict, loc: dict, cs_cfg: dict, prov_cfg: dict, indent: str = "            ") -> str:
    """Generate the per-row extraction body (pid, dob, demographics, lookups)."""
    pid_col = v["pid_col"]
    auto_increment = v["auto_increment"]
    dob_col = v["dob_col"]
    date_format = v["date_format"]
    birth_time_col = v["birth_time_col"]
    birth_time_format = v["birth_time_format"]
    gender_col = v["gender_col"]
    gender_default = v["gender_default"]
    race_mode = v["race_mode"]
    race_col = v["race_col"]
    race_default = v["race_default"]
    race_constant = v["race_constant"]
    eth_mode = v["eth_mode"]
    eth_col = v["eth_col"]
    eth_default = v["eth_default"]
    eth_constant = v["eth_constant"]

    # --- pid ---
    if pid_col:
        pid_lines = (
            f"{indent}_pid_raw = row.get({repr(pid_col)})\n"
            f"{indent}if pd.isnull(_pid_raw):\n"
            f'{indent}    print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            f"{indent}    continue\n"
            f"{indent}person_source_value = str(_pid_raw)\n"
        )
    else:
        pid_lines = f"{indent}person_source_value = str(_src_idx)\n"

    # --- dob ---
    if birth_time_col:
        birth_time_lines = (
            f"{indent}_btv = row.get({repr(birth_time_col)})\n"
            f"{indent}_bt_str = str(_btv).strip() if pd.notnull(_btv) else ''\n"
            f"{indent}if _bt_str and _bt_str != 'nan':\n"
            f"{indent}    try:\n"
            f"{indent}        birth_datetime = datetime.combine(_dob, datetime.strptime(_bt_str, {repr(birth_time_format)}).time())\n"
            f"{indent}    except Exception:\n"
            f"{indent}        _info(f'INFO: person {{person_source_value}} — could not parse birth time {{_bt_str!r}}; defaulting to midnight')\n"
            f"{indent}        birth_datetime = datetime.combine(_dob, datetime.min.time())\n"
            f"{indent}else:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — birth time column is empty; birth_datetime defaulting to midnight')\n"
            f"{indent}    birth_datetime = datetime.combine(_dob, datetime.min.time())\n"
        )
    else:
        birth_time_lines = f"{indent}birth_datetime = datetime.combine(_dob, datetime.min.time())\n"

    if dob_col:
        dob_lines = (
            f"{indent}_dob_raw = str(row.get({repr(dob_col)}, '')).strip()\n"
            f"{indent}if not _dob_raw or _dob_raw == 'nan':\n"
            f'{indent}    print(f"WARNING: skipping row {{_src_idx}} — birth-date column {repr(dob_col)} is empty or null")\n'
            f"{indent}    continue\n"
            f"{indent}_dob = datetime.strptime(_dob_raw, {repr(date_format)})\n"
            f"{indent}year_of_birth = _dob.year\n"
            f"{indent}month_of_birth = _dob.month\n"
            f"{indent}day_of_birth = _dob.day\n"
            + birth_time_lines
        )
    else:
        dob_lines = (
            f'{indent}print(f"WARNING: skipping row {{_src_idx}} — no birth-date column configured; year_of_birth is required by OMOP CDM")\n'
            f"{indent}continue\n"
        )

    # --- gender ---
    if gender_col:
        gender_lines = (
            _xtr("gender_source_value", gender_col, len(indent)) + "\n"
            f"{indent}if gender_source_value is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — gender column is empty; gender_concept_id set to {{gender_default}}')\n"
            f"{indent}    gender_concept_id = gender_default\n"
            f"{indent}elif gender_source_value not in gender_map:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — gender value {{gender_source_value!r}} not in map; gender_concept_id set to {{gender_default}}')\n"
            f"{indent}    gender_concept_id = gender_default\n"
            f"{indent}else:\n"
            f"{indent}    gender_concept_id = gender_map[gender_source_value]\n"
        )
    else:
        gender_lines = (
            _xtr_doc("gender_source_value", "", len(indent)) + "\n"
            f"{indent}gender_concept_id = {gender_default}\n"
        )

    # --- race ---
    if race_mode == "column":
        race_lines = (
            _xtr("race_source_value", race_col, len(indent)) + "\n"
            f"{indent}if race_source_value is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — race column is empty; race_concept_id set to {{race_default}}')\n"
            f"{indent}    race_concept_id = race_default\n"
            f"{indent}elif race_source_value not in race_map:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — race value {{race_source_value!r}} not in map; race_concept_id set to {{race_default}}')\n"
            f"{indent}    race_concept_id = race_default\n"
            f"{indent}else:\n"
            f"{indent}    race_concept_id = race_map[race_source_value]\n"
        )
    elif race_mode == "constant":
        race_lines = f"{indent}race_concept_id = {race_constant}\n{indent}race_source_value = None\n"
    else:
        race_lines = f"{indent}race_concept_id = {race_default}\n" + _xtr_doc("race_source_value", "", len(indent)) + "\n"

    # --- ethnicity ---
    if eth_mode == "column":
        eth_lines = (
            _xtr("ethnicity_source_value", eth_col, len(indent)) + "\n"
            f"{indent}if ethnicity_source_value is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — ethnicity column is empty; ethnicity_concept_id set to {{eth_default}}')\n"
            f"{indent}    ethnicity_concept_id = eth_default\n"
            f"{indent}elif ethnicity_source_value not in ethnicity_map:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — ethnicity value {{ethnicity_source_value!r}} not in map; ethnicity_concept_id set to {{eth_default}}')\n"
            f"{indent}    ethnicity_concept_id = eth_default\n"
            f"{indent}else:\n"
            f"{indent}    ethnicity_concept_id = ethnicity_map[ethnicity_source_value]\n"
        )
    elif eth_mode == "constant":
        eth_lines = f"{indent}ethnicity_concept_id = {eth_constant}\n{indent}ethnicity_source_value = None\n"
    else:
        eth_lines = f"{indent}ethnicity_concept_id = {eth_default}\n" + _xtr_doc("ethnicity_source_value", "", len(indent)) + "\n"

    # --- location / care_site / provider ---
    a1_col = loc.get("address_1_col", "")
    a2_col = loc.get("address_2_col", "")
    city_col = loc.get("city_col", "")
    state_col = loc.get("state_col", "")
    zip_col = loc.get("zip_col", "")
    county_col = loc.get("county_col", "")
    country_col = loc.get("country_col", "")
    country_sv = loc.get("country_source_value", "")
    has_location = any([a1_col, a2_col, city_col, state_col, zip_col, county_col, country_col, country_sv])

    cs_name_col = cs_cfg.get("care_site_name_col", "")
    prov_name_col = prov_cfg.get("provider_name_col", "")

    if has_location:
        loc_lines = (
            _xtr("_a1", a1_col, len(indent)) + "\n"
            + _xtr("_a2", a2_col, len(indent)) + "\n"
            + _xtr("_city", city_col, len(indent)) + "\n"
            + _xtr("_state", state_col, len(indent)) + "\n"
            + _xtr("_zip", zip_col, len(indent)) + "\n"
            + _xtr("_county", county_col, len(indent)) + "\n"
            + (
                _xtr("_country_sv", country_col, len(indent)) + "\n"
                if country_col else
                f"{indent}_country_sv = {repr(country_sv) if country_sv else 'None'}\n"
            )
            + f"{indent}person_location_source_value = ' | '.join(filter(None, [_a1, _a2, _city, _state, _zip, _county, _country_sv]))[:255]\n"
            + f"{indent}location_id = location_lookup.get(person_location_source_value)\n"
            + f"{indent}if location_id is None and person_location_source_value:\n"
            + f"{indent}    _info(f'INFO: person {{person_source_value}} — location {{person_location_source_value!r}} not found in location.csv; location_id set to NULL')\n"
        )
    else:
        loc_lines = _xtr_doc("location_id", "", len(indent)) + "\n"

    if cs_name_col:
        cs_lines = (
            _xtr("_cs_name_raw", cs_name_col, len(indent)) + "\n"
            f"{indent}care_site_id = care_site_lookup.get(_cs_name_raw) if _cs_name_raw else None\n"
            f"{indent}if _cs_name_raw and care_site_id is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — care_site {{_cs_name_raw!r}} not found in care_site.csv; care_site_id set to NULL')\n"
        )
    else:
        cs_lines = _xtr_doc("care_site_id", "", len(indent)) + "\n"

    if prov_name_col:
        prov_lines = (
            _xtr("_prov_name_raw", prov_name_col, len(indent)) + "\n"
            f"{indent}_prov_source_value = (str(care_site_id) + ' | ' + (_prov_name_raw or ''))[:50]\n"
            f"{indent}provider_id = provider_lookup.get(_prov_source_value)\n"
            f"{indent}if _prov_name_raw and provider_id is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — provider {{_prov_name_raw!r}} not found in provider.csv; provider_id set to NULL')\n"
        )
    else:
        prov_lines = _xtr_doc("provider_id", "", len(indent)) + "\n"

    return pid_lines + dob_lines + "\n" + gender_lines + race_lines + eth_lines + "\n" + loc_lines + cs_lines + prov_lines


def _resolve_flat_cfg_merged(cfg: dict) -> dict:
    """Return a merged flat view with the union of non-empty values across all file configs.

    Used when we need to know whether *any* file has a given column configured,
    rather than what a specific file's config says.
    """
    fc = cfg.get("file_configs")
    if not fc:
        return cfg
    merged: dict = {}
    for file_cfg in fc.values():
        for k, v in file_cfg.items():
            if v and k not in merged:
                merged[k] = v
    return merged


def _resolve_flat_cfg(cfg: dict, filename: str = '') -> dict:
    """Return a flat (field→value) view of a config dict.

    Handles both legacy format (flat keys at top level) and new multi-file format
    (keys nested under file_configs[filename]).  When the new format is detected,
    falls back to the primary file's config if *filename* is not found.
    """
    fc = cfg.get("file_configs")
    if not fc:
        return cfg  # legacy flat format
    if filename and filename in fc:
        return fc[filename]
    # Fall back to primary file
    src_files = cfg.get("source_files", [])
    if src_files and src_files[0] in fc:
        return fc[src_files[0]]
    # Last resort: first value
    return next(iter(fc.values()), {})


def _person_lookup_setup(loc: dict, cs_cfg: dict, prov_cfg: dict) -> str:
    """Generate lookup-table loading code (location, care_site, provider) for the person script."""
    loc_flat = _resolve_flat_cfg_merged(loc)
    cs_flat = _resolve_flat_cfg_merged(cs_cfg)
    prov_flat = _resolve_flat_cfg_merged(prov_cfg)

    a1_col = loc_flat.get("address_1_col", "")
    a2_col = loc_flat.get("address_2_col", "")
    city_col = loc_flat.get("city_col", "")
    state_col = loc_flat.get("state_col", "")
    zip_col = loc_flat.get("zip_col", "")
    county_col = loc_flat.get("county_col", "")
    country_col = loc_flat.get("country_col", "")
    country_sv = loc_flat.get("country_source_value", "")
    has_location = any([a1_col, a2_col, city_col, state_col, zip_col, county_col, country_col, country_sv])

    cs_name_col = cs_flat.get("care_site_name_col", "")
    prov_name_col = prov_flat.get("provider_name_col", "")

    if has_location:
        loc_setup = (
            "    location_lookup = build_id_lookup(output_dir, 'location.csv', 'location_source_value', 'location_id')\n"
        )
    else:
        loc_setup = "    location_lookup = {}\n"

    if cs_name_col:
        cs_setup = (
            "    care_site_lookup = build_id_lookup(output_dir, 'care_site.csv', 'care_site_name', 'care_site_id')\n"
        )
    else:
        cs_setup = "    care_site_lookup = {}\n"

    if prov_name_col:
        prov_setup = (
            "    provider_lookup = build_id_lookup(output_dir, 'provider.csv', 'provider_source_value', 'provider_id')\n"
        )
    else:
        prov_setup = "    provider_lookup = {}\n"

    return loc_setup + cs_setup + prov_setup


_PERSON_RECORD_APPEND = (
    "                'person_id':                  person_id,\n"
    "                'gender_concept_id':          gender_concept_id,\n"
    "                'year_of_birth':              year_of_birth,\n"
    "                'month_of_birth':             month_of_birth,\n"
    "                'day_of_birth':               day_of_birth,\n"
    "                'birth_datetime':             birth_datetime,\n"
    "                'race_concept_id':            race_concept_id,\n"
    "                'ethnicity_concept_id':       ethnicity_concept_id,\n"
    "                'location_id':                location_id,\n"
    "                'provider_id':                provider_id,\n"
    "                'care_site_id':               care_site_id,\n"
    "                'person_source_value':        person_source_value,\n"
    "                'gender_source_value':        gender_source_value,\n"
    "                'race_source_value':          race_source_value,\n"
    "                'race_source_concept_id':     0,\n"
    "                'ethnicity_source_value':     ethnicity_source_value,\n"
    "                'ethnicity_source_concept_id': 0,\n"
    "                'gender_source_concept_id':   0,\n"
)

_PERSON_MERGE_FIELDS = [
    "gender_concept_id", "year_of_birth", "month_of_birth", "day_of_birth",
    "birth_datetime", "race_concept_id", "ethnicity_concept_id",
    "location_id", "provider_id", "care_site_id",
    "gender_source_value", "race_source_value", "ethnicity_source_value",
]


def _generate_person_script_multi(project, source_files: list, file_configs: dict,
                                   loc: dict, cs_cfg: dict, prov_cfg: dict) -> str:
    """Generate a multi-file person script that merges patients across sources."""

    lookup_setup = _person_lookup_setup(loc, cs_cfg, prov_cfg)

    # Determine if any file has location columns — used to decide whether to use the
    # deferred accumulation approach (needed when columns are split across files).
    _loc_merged = _resolve_flat_cfg_merged(loc)
    has_location_any = any([
        _loc_merged.get("address_1_col"), _loc_merged.get("address_2_col"),
        _loc_merged.get("city_col"), _loc_merged.get("state_col"),
        _loc_merged.get("zip_col"), _loc_merged.get("county_col"),
        _loc_merged.get("country_col"), _loc_merged.get("country_source_value"),
    ])

    # Ensure DOB-bearing files are processed first so the primary file always has DOB.
    # Files lacking DOB become merge files, where existing patients pass through with
    # None birth values (preserved from the DOB-bearing file via _is_meaningful).
    source_files = sorted(
        source_files,
        key=lambda fn: 0 if (file_configs.get(fn, {}).get("mappings", {}).get("year_of_birth") or {}).get("source_col") else 1,
    )

    # Build per-file blocks
    file_blocks = ""
    for file_idx, filename in enumerate(source_files):
        fc = file_configs.get(filename, {})
        v = _person_parse_file_cfg(fc)
        is_primary = (file_idx == 0)

        # Find source file entry for path/delim/enc
        sf_entry = next(
            (f for f in (project.source_files or []) if f.get("filename") == filename),
            None,
        )
        if sf_entry:
            path = sf_entry.get("path", "")
            delim = repr(sf_entry.get("delimiter", ","))
            enc = repr(sf_entry.get("encoding", "utf-8"))
            path_line = f"    df = pd.read_csv(r{repr(path)}, delimiter={delim}, encoding={enc})\n"
        else:
            path_line = f"    # WARNING: source file entry not found for {repr(filename)}\n    df = pd.DataFrame()\n"

        label_comment = f"    # ── {'Primary' if is_primary else 'Merge'} file: {filename} {'─' * max(0, 50 - len(filename))}\n"
        map_code = _person_concept_map_code(v)
        pid_col = v["pid_col"]
        pid_repr = repr(pid_col) if pid_col else None

        if is_primary:
            # Primary: simple insert; skip duplicates within the file
            store_block = (
                "            person_id = person_id_counter\n"
                "            if person_source_value in person_dict:\n"
                "                continue\n"
                "            person_dict[person_source_value] = {\n"
                + _PERSON_RECORD_APPEND
                + "            }\n"
                "            person_id_counter += 1\n"
            )
            post_loop = f"    print(f'Primary file ({filename}): {{len(person_dict)}} patients loaded')\n"
        else:
            # Merge: new patient → append; existing → merge or drop on conflict
            store_block = (
                "            if person_source_value in conflicted_ids:\n"
                "                continue\n"
                "            if person_source_value in _seen_in_file:\n"
                "                continue\n"
                "            _seen_in_file.add(person_source_value)\n"
                "\n"
                "            _new_rec = {\n"
                "                'gender_concept_id':          gender_concept_id,\n"
                "                'year_of_birth':              year_of_birth,\n"
                "                'month_of_birth':             month_of_birth,\n"
                "                'day_of_birth':               day_of_birth,\n"
                "                'birth_datetime':             birth_datetime,\n"
                "                'race_concept_id':            race_concept_id,\n"
                "                'ethnicity_concept_id':       ethnicity_concept_id,\n"
                "                'location_id':                location_id,\n"
                "                'provider_id':                provider_id,\n"
                "                'care_site_id':               care_site_id,\n"
                "                'gender_source_value':        gender_source_value,\n"
                "                'race_source_value':          race_source_value,\n"
                "                'ethnicity_source_value':     ethnicity_source_value,\n"
                "            }\n"
                "\n"
                "            if person_source_value not in person_dict:\n"
                "                _new_rec['person_id'] = person_id_counter\n"
                "                _new_rec['person_source_value'] = person_source_value\n"
                "                _new_rec['race_source_concept_id'] = 0\n"
                "                _new_rec['ethnicity_source_concept_id'] = 0\n"
                "                _new_rec['gender_source_concept_id'] = 0\n"
                "                person_dict[person_source_value] = _new_rec\n"
                "                person_id_counter += 1\n"
                "                _new_count += 1\n"
                "            else:\n"
                "                _existing = person_dict[person_source_value]\n"
                "                _conflict = False\n"
                f"                for _field, _new_val in _new_rec.items():\n"
                "                    if not _is_meaningful(_new_val):\n"
                "                        continue\n"
                "                    _existing_val = _existing.get(_field)\n"
                "                    if not _is_meaningful(_existing_val):\n"
                "                        _existing[_field] = _new_val\n"
                "                    elif _existing_val != _new_val:\n"
                f'                        print(f"WARNING: patient {{person_source_value!r}} — conflicting {{_field!r}} from {repr(filename)} vs earlier data; patient dropped")\n'
                "                        _conflict = True\n"
                "                        break\n"
                "                if _conflict:\n"
                "                    del person_dict[person_source_value]\n"
                "                    conflicted_ids.add(person_source_value)\n"
                "                    _conflict_count += 1\n"
            )
            post_loop = (
                f"    print(f'Merge file ({filename}): +{{_new_count}} new, {{_conflict_count}} conflicts dropped, "
                f"total {{len(person_dict)}} patients')\n"
            )

        pid_extract = (
            f"            _pid_raw = row.get({pid_repr})\n"
            f"            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {pid_repr} is null or missing")\n'
            f"                continue\n"
            f"            person_source_value = str(_pid_raw)\n"
        ) if pid_col else (
            "            person_source_value = str(_src_idx)\n"
        )

        pre_loop = ""
        if not is_primary:
            pre_loop = (
                "    _new_count = 0\n"
                "    _conflict_count = 0\n"
                "    _seen_in_file = set()\n"
            )

        file_blocks += (
            "\n"
            + label_comment
            + map_code
            + path_line
            + pre_loop
            + "    for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
            "        try:\n"
            "\n"
            + pid_extract
            + "            # Date of birth\n"
            + _person_dob_lines(v, is_merge=not is_primary)
            + "\n"
            "            # Demographics\n"
            + _person_gender_lines(v)
            + _person_race_lines(v)
            + _person_eth_lines(v)
            + "\n"
            + _person_loc_lines(loc, cs_cfg, prov_cfg, filename=filename, deferred_location=has_location_any)
            + "\n"
            + store_block
            + "        except Exception as e:\n"
            "            print(f'WARNING: skipping row — {e}')\n"
            + post_loop
        )

    return (
        "import os\n"
        "import pandas as pd\n"
        "from datetime import datetime\n"
        "\n"
        "\n"
        "from etl_runtime import _info, _read_str, build_id_lookup\n"
        "\n"
        "\n"
        "def _is_meaningful(val):\n"
        '    """Return True if val carries real information (not null/unknown)."""\n'
        "    if val is None:\n"
        "        return False\n"
        "    if isinstance(val, (int, float)) and val == 0:\n"
        "        return False\n"
        "    return True\n"
        "\n"
        "\n"
        "def main():\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        + lookup_setup
        + "\n"
        "    person_dict = {}        # keyed by person_source_value\n"
        "    conflicted_ids = set()  # dropped due to cross-file conflicts\n"
        "    person_id_counter = 1\n"
        + ("    _loc_parts = {}         # cross-file location components per patient\n" if has_location_any else "")
        + file_blocks
        + ((_person_loc_resolve_block()) if has_location_any else "")
        + "\n"
        "    # --- Build output DataFrame ---\n"
        "    PERSON_COLUMNS = [\n"
        "        'person_id', 'gender_concept_id', 'year_of_birth', 'month_of_birth',\n"
        "        'day_of_birth', 'birth_datetime', 'race_concept_id', 'ethnicity_concept_id',\n"
        "        'location_id', 'provider_id', 'care_site_id', 'person_source_value',\n"
        "        'gender_source_value', 'race_source_value', 'race_source_concept_id',\n"
        "        'ethnicity_source_value', 'ethnicity_source_concept_id', 'gender_source_concept_id',\n"
        "    ]\n"
        "    rows = sorted(person_dict.values(), key=lambda r: r['person_id'])\n"
        "    df_out = pd.DataFrame(rows, columns=PERSON_COLUMNS)\n"
        "\n"
        "    # --- Write output ---\n"
        "    output_file = os.path.join(output_dir, 'person.csv')\n"
        "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        "    if conflicted_ids:\n"
        "        print(f'NOTE: {len(conflicted_ids)} patient(s) dropped due to cross-file conflicts.')\n"
        "    print(f'Writing person.csv ... done ({len(df_out)} records)')\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _person_dob_lines(v: dict, indent: str = "            ", is_merge: bool = False) -> str:
    dob_col = v["dob_col"]
    date_format = v["date_format"]
    birth_time_col = v["birth_time_col"]
    birth_time_format = v["birth_time_format"]
    if birth_time_col:
        bt = (
            f"{indent}_btv = row.get({repr(birth_time_col)})\n"
            f"{indent}_bt_str = str(_btv).strip() if pd.notnull(_btv) else ''\n"
            f"{indent}if _bt_str and _bt_str != 'nan':\n"
            f"{indent}    try:\n"
            f"{indent}        birth_datetime = datetime.combine(_dob, datetime.strptime(_bt_str, {repr(birth_time_format)}).time())\n"
            f"{indent}    except Exception:\n"
            f"{indent}        _info(f'INFO: person {{person_source_value}} — could not parse birth time {{_bt_str!r}}; defaulting to midnight')\n"
            f"{indent}        birth_datetime = datetime.combine(_dob, datetime.min.time())\n"
            f"{indent}else:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — birth time column is empty; birth_datetime defaulting to midnight')\n"
            f"{indent}    birth_datetime = datetime.combine(_dob, datetime.min.time())\n"
        )
    else:
        bt = f"{indent}birth_datetime = datetime.combine(_dob, datetime.min.time())\n"
    if dob_col:
        return (
            f"{indent}_dob_raw = str(row.get({repr(dob_col)}, '')).strip()\n"
            f"{indent}if not _dob_raw or _dob_raw == 'nan':\n"
            f'{indent}    print(f"WARNING: skipping row {{_src_idx}} — birth-date column {repr(dob_col)} is empty or null")\n'
            f"{indent}    continue\n"
            f"{indent}_dob = datetime.strptime(_dob_raw, {repr(date_format)})\n"
            f"{indent}year_of_birth = _dob.year\n"
            f"{indent}month_of_birth = _dob.month\n"
            f"{indent}day_of_birth = _dob.day\n"
            + bt
        )
    # No DOB column configured.
    # For merge files: allow existing patients through (DOB already loaded from a prior file).
    # New patients in this file still require DOB and are skipped.
    if is_merge:
        return (
            f"{indent}if person_source_value not in person_dict:\n"
            f'{indent}    print(f"WARNING: skipping row {{_src_idx}} — no birth-date column configured; year_of_birth is required by OMOP CDM")\n'
            f"{indent}    continue\n"
            f"{indent}year_of_birth = None\n"
            f"{indent}month_of_birth = None\n"
            f"{indent}day_of_birth = None\n"
            f"{indent}birth_datetime = None\n"
        )
    return (
        f'{indent}print(f"WARNING: skipping row {{_src_idx}} — no birth-date column configured; year_of_birth is required by OMOP CDM")\n'
        f"{indent}continue\n"
    )


def _person_gender_lines(v: dict, indent: str = "            ") -> str:
    gender_col = v["gender_col"]
    gender_default = v["gender_default"]
    if gender_col:
        return (
            _xtr("gender_source_value", gender_col, len(indent)) + "\n"
            f"{indent}if gender_source_value is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — gender column is empty; gender_concept_id set to {{gender_default}}')\n"
            f"{indent}    gender_concept_id = gender_default\n"
            f"{indent}elif gender_source_value not in gender_map:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — gender value {{gender_source_value!r}} not in map; gender_concept_id set to {{gender_default}}')\n"
            f"{indent}    gender_concept_id = gender_default\n"
            f"{indent}else:\n"
            f"{indent}    gender_concept_id = gender_map[gender_source_value]\n"
        )
    return _xtr_doc("gender_source_value", "", len(indent)) + "\n" + f"{indent}gender_concept_id = {gender_default}\n"


def _person_race_lines(v: dict, indent: str = "            ") -> str:
    race_mode = v["race_mode"]
    race_col = v["race_col"]
    race_default = v["race_default"]
    race_constant = v["race_constant"]
    if race_mode == "column":
        return (
            _xtr("race_source_value", race_col, len(indent)) + "\n"
            f"{indent}if race_source_value is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — race column is empty; race_concept_id set to {{race_default}}')\n"
            f"{indent}    race_concept_id = race_default\n"
            f"{indent}elif race_source_value not in race_map:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — race value {{race_source_value!r}} not in map; race_concept_id set to {{race_default}}')\n"
            f"{indent}    race_concept_id = race_default\n"
            f"{indent}else:\n"
            f"{indent}    race_concept_id = race_map[race_source_value]\n"
        )
    if race_mode == "constant":
        return f"{indent}race_concept_id = {race_constant}\n{indent}race_source_value = None\n"
    return f"{indent}race_concept_id = {race_default}\n" + _xtr_doc("race_source_value", "", len(indent)) + "\n"


def _person_eth_lines(v: dict, indent: str = "            ") -> str:
    eth_mode = v["eth_mode"]
    eth_col = v["eth_col"]
    eth_default = v["eth_default"]
    eth_constant = v["eth_constant"]
    if eth_mode == "column":
        return (
            _xtr("ethnicity_source_value", eth_col, len(indent)) + "\n"
            f"{indent}if ethnicity_source_value is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — ethnicity column is empty; ethnicity_concept_id set to {{eth_default}}')\n"
            f"{indent}    ethnicity_concept_id = eth_default\n"
            f"{indent}elif ethnicity_source_value not in ethnicity_map:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — ethnicity value {{ethnicity_source_value!r}} not in map; ethnicity_concept_id set to {{eth_default}}')\n"
            f"{indent}    ethnicity_concept_id = eth_default\n"
            f"{indent}else:\n"
            f"{indent}    ethnicity_concept_id = ethnicity_map[ethnicity_source_value]\n"
        )
    if eth_mode == "constant":
        return f"{indent}ethnicity_concept_id = {eth_constant}\n{indent}ethnicity_source_value = None\n"
    return f"{indent}ethnicity_concept_id = {eth_default}\n" + _xtr_doc("ethnicity_source_value", "", len(indent)) + "\n"


def _person_loc_lines(loc: dict, cs_cfg: dict, prov_cfg: dict, indent: str = "            ",
                      *, filename: str = '', deferred_location: bool = False) -> str:
    """Generate per-row location/care-site/provider lookup lines for the person script.

    *loc*, *cs_cfg*, *prov_cfg* may be either the legacy flat dict or the new
    multi-file format (with ``file_configs``).  Pass *filename* to resolve the
    per-file config for that specific source file; falls back to the primary file.

    When *deferred_location* is True, the location part accumulates field values into
    ``_loc_parts[person_source_value]`` instead of doing an inline lookup.  The actual
    lookup is emitted separately via :func:`_person_loc_resolve_block`.
    """
    loc_flat = _resolve_flat_cfg(loc, filename)
    cs_flat = _resolve_flat_cfg(cs_cfg, filename)
    prov_flat = _resolve_flat_cfg(prov_cfg, filename)

    a1_col = loc_flat.get("address_1_col", "")
    a2_col = loc_flat.get("address_2_col", "")
    city_col = loc_flat.get("city_col", "")
    state_col = loc_flat.get("state_col", "")
    zip_col = loc_flat.get("zip_col", "")
    county_col = loc_flat.get("county_col", "")
    country_col = loc_flat.get("country_col", "")
    country_sv = loc_flat.get("country_source_value", "")
    has_location = any([a1_col, a2_col, city_col, state_col, zip_col, county_col, country_col, country_sv])
    cs_name_col = cs_flat.get("care_site_name_col", "")
    prov_name_col = prov_flat.get("provider_name_col", "")

    if has_location and deferred_location:
        # Accumulate this file's location columns into _loc_parts; lookup happens later.
        field_pairs = [
            ("a1",         a1_col),
            ("a2",         a2_col),
            ("city",       city_col),
            ("state",      state_col),
            ("zip",        zip_col),
            ("county",     county_col),
        ]
        acc = f"{indent}if person_source_value not in _loc_parts:\n{indent}    _loc_parts[person_source_value] = {{}}\n"
        for key, col in field_pairs:
            if col:
                acc += (
                    f"{indent}__{key} = _read_str(row, {repr(col)})\n"
                    f"{indent}if __{key} is not None: _loc_parts[person_source_value][{repr(key)}] = __{key}\n"
                )
        if country_col:
            acc += (
                f"{indent}__country_sv = _read_str(row, {repr(country_col)})\n"
                f"{indent}if __country_sv is not None: _loc_parts[person_source_value]['country_sv'] = __country_sv\n"
            )
        elif country_sv:
            acc += f"{indent}_loc_parts[person_source_value]['country_sv'] = {repr(country_sv)}\n"
        acc += f"{indent}location_id = None\n"
        loc_lines = acc
    elif has_location:
        loc_lines = (
            _xtr("_a1", a1_col, len(indent)) + "\n"
            + _xtr("_a2", a2_col, len(indent)) + "\n"
            + _xtr("_city", city_col, len(indent)) + "\n"
            + _xtr("_state", state_col, len(indent)) + "\n"
            + _xtr("_zip", zip_col, len(indent)) + "\n"
            + _xtr("_county", county_col, len(indent)) + "\n"
            + (
                _xtr("_country_sv", country_col, len(indent)) + "\n"
                if country_col else
                f"{indent}_country_sv = {repr(country_sv) if country_sv else 'None'}\n"
            )
            + f"{indent}person_location_source_value = ' | '.join(filter(None, [_a1, _a2, _city, _state, _zip, _county, _country_sv]))[:255]\n"
            + f"{indent}location_id = location_lookup.get(person_location_source_value)\n"
            + f"{indent}if location_id is None and person_location_source_value:\n"
            + f"{indent}    _info(f'INFO: person {{person_source_value}} — location {{person_location_source_value!r}} not found in location.csv; location_id set to NULL')\n"
        )
    else:
        loc_lines = _xtr_doc("location_id", "", len(indent)) + "\n"

    if cs_name_col:
        cs_lines = (
            _xtr("_cs_name_raw", cs_name_col, len(indent)) + "\n"
            f"{indent}care_site_id = care_site_lookup.get(_cs_name_raw) if _cs_name_raw else None\n"
            f"{indent}if _cs_name_raw and care_site_id is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — care_site {{_cs_name_raw!r}} not found in care_site.csv; care_site_id set to NULL')\n"
        )
    else:
        cs_lines = _xtr_doc("care_site_id", "", len(indent)) + "\n"

    if prov_name_col:
        prov_lines = (
            _xtr("_prov_name_raw", prov_name_col, len(indent)) + "\n"
            f"{indent}_prov_source_value = (str(care_site_id) + ' | ' + (_prov_name_raw or ''))[:50]\n"
            f"{indent}provider_id = provider_lookup.get(_prov_source_value)\n"
            f"{indent}if _prov_name_raw and provider_id is None:\n"
            f"{indent}    _info(f'INFO: person {{person_source_value}} — provider {{_prov_name_raw!r}} not found in provider.csv; provider_id set to NULL')\n"
        )
    else:
        prov_lines = _xtr_doc("provider_id", "", len(indent)) + "\n"

    return loc_lines + cs_lines + prov_lines


def _person_loc_resolve_block() -> str:
    """Generate the post-loop block that resolves location_id from accumulated components."""
    return (
        "\n"
        "    # --- Resolve location_id from cross-file accumulated components ---\n"
        "    for _psv, _parts in _loc_parts.items():\n"
        "        if _psv not in person_dict:\n"
        "            continue\n"
        "        _loc_sv = ' | '.join(filter(None, [\n"
        "            _parts.get('a1'), _parts.get('a2'), _parts.get('city'),\n"
        "            _parts.get('state'), _parts.get('zip'), _parts.get('county'),\n"
        "            _parts.get('country_sv'),\n"
        "        ]))[:255]\n"
        "        if _loc_sv:\n"
        "            _lid = location_lookup.get(_loc_sv)\n"
        "            if _lid is None:\n"
        "                _info(f'INFO: person {_psv!r} — location {_loc_sv!r} not found in location.csv; location_id set to NULL')\n"
        "            person_dict[_psv]['location_id'] = _lid\n"
    )


def _generate_person_script(project) -> str:
    """Deterministic template-based generator for the OMOP person script."""
    person = (project.etl_config or {}).get("person", {})
    loc = (project.etl_config or {}).get("location", {})
    cs_cfg = (project.etl_config or {}).get("care_site", {})
    prov_cfg = (project.etl_config or {}).get("provider", {})
    dataset_options = (project.etl_config or {}).get("dataset_options", {})
    multiple_rows_per_patient = dataset_options.get("multiple_rows_per_patient", False)

    # ── Multi-file path ───────────────────────────────────────────────────
    source_files = person.get("source_files", [])
    file_configs = person.get("file_configs", {})
    if file_configs and len(source_files) > 1:
        return _generate_person_script_multi(project, source_files, file_configs, loc, cs_cfg, prov_cfg)

    # ── Single-file path (new format or legacy) ───────────────────────────
    # Normalise new single-file format → legacy-compatible dict so the rest of
    # this function can stay unchanged.
    if file_configs and source_files:
        first_file = source_files[0]
        fc = file_configs.get(first_file, {})
        person = {
            **fc,
            "source_filename": first_file,
            "enabled": person.get("enabled", True),
        }

    source_path_code, delim, enc = _source_file_params(project, person)

    mappings = person.get("mappings", {})

    pid_cfg = mappings.get("person_id") or {}
    auto_increment = pid_cfg.get("auto_increment", False)
    pid_col = pid_cfg.get("source_col", "")
    pid_transform = pid_cfg.get("transform", "int_float")

    gender_cfg = mappings.get("gender_concept_id") or {}
    gender_col = gender_cfg.get("source_col", "")
    gender_map = gender_cfg.get("value_map") or {}
    gender_default = int(gender_cfg.get("default") or 0)

    dob_cfg = mappings.get("year_of_birth") or {}
    dob_col = dob_cfg.get("source_col", "")
    date_format = dob_cfg.get("date_format", "%Y-%m-%d")
    birth_time_col = person.get("birth_time_col", "")
    birth_time_format = person.get("birth_time_format", "%H:%M:%S") or "%H:%M:%S"

    race_cfg = mappings.get("race_concept_id") or {}
    if "constant" in race_cfg:
        race_mode, race_constant = "constant", int(race_cfg["constant"])
        race_col, race_map, race_default = "", {}, 0
    elif race_cfg.get("source_col"):
        race_mode = "column"
        race_col = race_cfg["source_col"]
        race_map = race_cfg.get("value_map") or {}
        race_default = int(race_cfg.get("default") or 0)
        race_constant = 0
    else:
        race_mode = "default"
        race_default = int(race_cfg.get("default") or 0)
        race_col, race_map, race_constant = "", {}, 0

    eth_cfg = mappings.get("ethnicity_concept_id") or {}
    if "constant" in eth_cfg:
        eth_mode, eth_constant = "constant", int(eth_cfg["constant"])
        eth_col, eth_map, eth_default = "", {}, 0
    elif eth_cfg.get("source_col"):
        eth_mode = "column"
        eth_col = eth_cfg["source_col"]
        eth_map = eth_cfg.get("value_map") or {}
        eth_default = int(eth_cfg.get("default") or 0)
        eth_constant = 0
    else:
        eth_mode = "default"
        eth_default = int(eth_cfg.get("default") or 0)
        eth_col, eth_map, eth_constant = "", {}, 0

    # Resolve new multi-file format configs to flat dicts for the single-file path
    _person_filename = person.get("source_filename", "")
    loc_flat = _resolve_flat_cfg(loc, _person_filename)
    cs_flat = _resolve_flat_cfg(cs_cfg, _person_filename)
    prov_flat = _resolve_flat_cfg(prov_cfg, _person_filename)

    a1_col = loc_flat.get("address_1_col", "")
    a2_col = loc_flat.get("address_2_col", "")
    city_col = loc_flat.get("city_col", "")
    state_col = loc_flat.get("state_col", "")
    zip_col = loc_flat.get("zip_col", "")
    county_col = loc_flat.get("county_col", "")
    country_col = loc_flat.get("country_col", "")
    country_sv = loc_flat.get("country_source_value", "")
    has_location = any([a1_col, a2_col, city_col, state_col, zip_col, county_col, country_col, country_sv])

    cs_name_col = cs_flat.get("care_site_name_col", "")
    prov_name_col = prov_flat.get("provider_name_col", "")

    # ── per-row code blocks ───────────────────────────────────────────────

    # person_id is always a sequential auto-incrementing integer per OMOP CDM.
    # person_source_value holds the original patient identifier from the source.
    counter_init = "    person_id_counter = 1\n"
    counter_inc = "            person_id_counter += 1\n"
    if pid_col:
        pid_lines = (
            f"            _pid_raw = row.get({repr(pid_col)})\n"
            "            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            "                continue\n"
            "            person_id = person_id_counter\n"
            "            person_source_value = str(_pid_raw)\n"
        )
    else:
        pid_lines = (
            "            person_id = person_id_counter\n"
            "            person_source_value = str(_src_idx)\n"
        )

    if dob_col:
        if birth_time_col:
            _birth_time_lines = (
                f"            _btv = row.get({repr(birth_time_col)})\n"
                "            _bt_str = str(_btv).strip() if pd.notnull(_btv) else ''\n"
                "            if _bt_str and _bt_str != 'nan':\n"
                "                try:\n"
                f"                    birth_datetime = datetime.combine(_dob, datetime.strptime(_bt_str, {repr(birth_time_format)}).time())\n"
                "                except Exception:\n"
                "                    _info(f'INFO: person {person_source_value} — could not parse birth time {_bt_str!r}; defaulting to midnight')\n"
                "                    birth_datetime = datetime.combine(_dob, datetime.min.time())\n"
                "            else:\n"
                "                _info(f'INFO: person {person_source_value} — birth time column is empty; birth_datetime defaulting to midnight')\n"
                "                birth_datetime = datetime.combine(_dob, datetime.min.time())\n"
            )
        else:
            _birth_time_lines = "            birth_datetime = datetime.combine(_dob, datetime.min.time())\n"
        dob_lines = (
            f"            _dob_raw = str(row.get({repr(dob_col)}, '')).strip()\n"
            "            if not _dob_raw or _dob_raw == 'nan':\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — birth-date column {repr(dob_col)} is empty or null")\n'
            "                continue\n"
            f"            _dob = datetime.strptime(_dob_raw, {repr(date_format)})\n"
            "            year_of_birth = _dob.year\n"
            "            month_of_birth = _dob.month\n"
            "            day_of_birth = _dob.day\n"
            + _birth_time_lines
        )
    else:
        dob_lines = (
            f'            print(f"WARNING: skipping row {{_src_idx}} — no birth-date column configured; year_of_birth is required by OMOP CDM")\n'
            "            continue\n"
        )

    if gender_col:
        gender_lines = (
            _xtr("gender_source_value", gender_col, 12) + "\n"
            "            if gender_source_value is None:\n"
            "                _info(f'INFO: person {person_source_value} — gender column is empty; gender_concept_id set to {gender_default}')\n"
            "                gender_concept_id = gender_default\n"
            "            elif gender_source_value not in gender_map:\n"
            "                _info(f'INFO: person {person_source_value} — gender value {gender_source_value!r} not in map; gender_concept_id set to {gender_default}')\n"
            "                gender_concept_id = gender_default\n"
            "            else:\n"
            "                gender_concept_id = gender_map[gender_source_value]\n"
        )
    else:
        gender_lines = (
            _xtr_doc("gender_source_value", "", 12) + "\n"
            f"            gender_concept_id = {gender_default}\n"
        )

    if race_mode == "column":
        race_lines = (
            _xtr("race_source_value", race_col, 12) + "\n"
            "            if race_source_value is None:\n"
            "                _info(f'INFO: person {person_source_value} — race column is empty; race_concept_id set to {race_default}')\n"
            "                race_concept_id = race_default\n"
            "            elif race_source_value not in race_map:\n"
            "                _info(f'INFO: person {person_source_value} — race value {race_source_value!r} not in map; race_concept_id set to {race_default}')\n"
            "                race_concept_id = race_default\n"
            "            else:\n"
            "                race_concept_id = race_map[race_source_value]\n"
        )
    elif race_mode == "constant":
        race_lines = (
            f"            race_concept_id = {race_constant}\n"
            "            race_source_value = None\n"
        )
    else:
        race_lines = (
            f"            race_concept_id = {race_default}\n"
            + _xtr_doc("race_source_value", "", 12) + "\n"
        )

    if eth_mode == "column":
        eth_lines = (
            _xtr("ethnicity_source_value", eth_col, 12) + "\n"
            "            if ethnicity_source_value is None:\n"
            "                _info(f'INFO: person {person_source_value} — ethnicity column is empty; ethnicity_concept_id set to {ethnicity_default}')\n"
            "                ethnicity_concept_id = ethnicity_default\n"
            "            elif ethnicity_source_value not in ethnicity_map:\n"
            "                _info(f'INFO: person {person_source_value} — ethnicity value {ethnicity_source_value!r} not in map; ethnicity_concept_id set to {ethnicity_default}')\n"
            "                ethnicity_concept_id = ethnicity_default\n"
            "            else:\n"
            "                ethnicity_concept_id = ethnicity_map[ethnicity_source_value]\n"
        )
    elif eth_mode == "constant":
        eth_lines = (
            f"            ethnicity_concept_id = {eth_constant}\n"
            "            ethnicity_source_value = None\n"
        )
    else:
        eth_lines = (
            f"            ethnicity_concept_id = {eth_default}\n"
            + _xtr_doc("ethnicity_source_value", "", 12) + "\n"
        )

    if has_location:
        location_setup = (
            "    location_lookup = build_id_lookup(output_dir, 'location.csv', 'location_source_value', 'location_id')\n"
        )
        loc_lookup_lines = (
            _xtr("_a1", a1_col, 12) + "\n"
            + _xtr("_a2", a2_col, 12) + "\n"
            + _xtr("_city", city_col, 12) + "\n"
            + _xtr("_state", state_col, 12) + "\n"
            + _xtr("_zip", zip_col, 12) + "\n"
            + _xtr("_county", county_col, 12) + "\n"
            + (
                _xtr("_country_sv", country_col, 12) + "\n"
                if country_col else
                f"            _country_sv = {repr(country_sv) if country_sv else 'None'}\n"
            )
            + "            person_location_source_value = ' | '.join(filter(None, [_a1, _a2, _city, _state, _zip, _county, _country_sv]))[:255]\n"
            + "            location_id = location_lookup.get(person_location_source_value)\n"
            + "            if location_id is None and person_location_source_value:\n"
            + "                _info(f'INFO: person {person_source_value} — location {person_location_source_value!r} not found in location.csv; location_id set to NULL')\n"
        )
    else:
        location_setup = "    location_lookup = {}\n"
        loc_lookup_lines = _xtr_doc("location_id", "", 12) + "\n"

    if cs_name_col:
        cs_setup = (
            "    care_site_lookup = build_id_lookup(output_dir, 'care_site.csv', 'care_site_name', 'care_site_id')\n"
        )
        cs_lookup_line = (
            _xtr("_cs_name_raw", cs_name_col, 12) + "\n"
            "            care_site_id = care_site_lookup.get(_cs_name_raw) if _cs_name_raw else None\n"
            "            if _cs_name_raw and care_site_id is None:\n"
            "                _info(f'INFO: person {person_source_value} — care_site {_cs_name_raw!r} not found in care_site.csv; care_site_id set to NULL')\n"
        )
    else:
        cs_setup = "    care_site_lookup = {}\n"
        cs_lookup_line = _xtr_doc("care_site_id", "", 12) + "\n"

    if prov_name_col:
        prov_setup = (
            "    provider_lookup = build_id_lookup(output_dir, 'provider.csv', 'provider_source_value', 'provider_id')\n"
        )
        prov_lookup_line = (
            _xtr("_prov_name_raw", prov_name_col, 12) + "\n"
            "            _prov_source_value = (str(care_site_id) + ' | ' + (_prov_name_raw or ''))[:50]\n"
            "            provider_id = provider_lookup.get(_prov_source_value)\n"
            "            if _prov_name_raw and provider_id is None:\n"
            "                _info(f'INFO: person {person_source_value} — provider {_prov_name_raw!r} not found in provider.csv; provider_id set to NULL')\n"
        )
    else:
        prov_setup = "    provider_lookup = {}\n"
        prov_lookup_line = _xtr_doc("provider_id", "", 12) + "\n"

    gender_maps = (
        "    # --- gender concept maps ---\n"
        f"    gender_map = {json.dumps(gender_map)}\n"
        f"    gender_default = {gender_default}\n"
    )
    race_maps = ""
    if race_mode == "column":
        race_maps = (
            "    # --- race concept maps ---\n"
            f"    race_map = {json.dumps(race_map)}\n"
            f"    race_default = {race_default}\n"
        )
    eth_maps = ""
    if eth_mode == "column":
        eth_maps = (
            "    # --- Ethnicity concept maps ---\n"
            f"    ethnicity_map = {json.dumps(eth_map)}\n"
            f"    ethnicity_default = {eth_default}\n"
        )
    seen_init = "    seen_person_ids = set()\n" if multiple_rows_per_patient else ""
    dedup_check = (
        "            if person_source_value in seen_person_ids:\n"
        "                continue\n"
        "            seen_person_ids.add(person_source_value)\n"
    ) if multiple_rows_per_patient else ""

    return (
        "import os\n"
        "import pandas as pd\n"
        "from datetime import datetime\n"
        "\n"
        "\n"
        "from etl_runtime import _info, _read_str, build_id_lookup\n"
        "\n"
        "\n"
        "def main():\n"
        "    # Load environmental variables\n"
        + source_path_code +
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        + gender_maps
        + race_maps
        + eth_maps
        + "\n"
        + "    # --- Load lookup tables ---\n"
        + location_setup
        + cs_setup
        + prov_setup
        + counter_init
        + seen_init
        + "\n"
        + "    # --- Process rows ---\n"
        + "    rows = []\n"
        + "\n"
        + "    for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
        + "        try:\n"
        + "\n"
        + "            # Person identifier & DOB\n"
        + pid_lines
        + dedup_check
        + dob_lines
        + "\n"
        + "            # Demographics\n"
        + gender_lines
        + race_lines
        + eth_lines
        + "\n"
        + loc_lookup_lines
        + cs_lookup_line
        + prov_lookup_line
        + counter_inc
        + "\n"
        + "            rows.append({\n"
        + "                'person_id':                  person_id,\n"
        + "                'gender_concept_id':          gender_concept_id,\n"
        + "                'year_of_birth':              year_of_birth,\n"
        + "                'month_of_birth':             month_of_birth,\n"
        + "                'day_of_birth':               day_of_birth,\n"
        + "                'birth_datetime':             birth_datetime,\n"
        + "                'race_concept_id':            race_concept_id,\n"
        + "                'ethnicity_concept_id':       ethnicity_concept_id,\n"
        + "                'location_id':                location_id,\n"
        + "                'provider_id':                provider_id,\n"
        + "                'care_site_id':               care_site_id,\n"
        + "                'person_source_value':        person_source_value,\n"
        + "                'gender_source_value':        gender_source_value,\n"
        + "                'race_source_value':          race_source_value,\n"
        + "                'race_source_concept_id':     0,\n"
        + "                'ethnicity_source_value':     ethnicity_source_value,\n"
        + "                'ethnicity_source_concept_id': 0,\n"
        + "                'gender_source_concept_id':   0,\n"
        + "            })\n"
        + "        except Exception as e:\n"
        + "            print(f'WARNING: skipping row — {e}')\n"
        + "\n"
        + "    # --- Build output DataFrame ---\n"
        + "    PERSON_COLUMNS = [\n"
        + "        'person_id', 'gender_concept_id', 'year_of_birth', 'month_of_birth',\n"
        + "        'day_of_birth', 'birth_datetime', 'race_concept_id', 'ethnicity_concept_id',\n"
        + "        'location_id', 'provider_id', 'care_site_id', 'person_source_value',\n"
        + "        'gender_source_value', 'race_source_value', 'race_source_concept_id',\n"
        + "        'ethnicity_source_value', 'ethnicity_source_concept_id', 'gender_source_concept_id',\n"
        + "    ]\n"
        + "    df_out = pd.DataFrame(rows, columns=PERSON_COLUMNS)\n"
        + "\n"
        + "    # --- Write output ---\n"
        + "    output_file = os.path.join(output_dir, 'person.csv')\n"
        + "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing person.csv ... done ({len(df_out)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _generate_visit_occurrence_script(project) -> str:
    """Deterministic template-based generator for the OMOP visit_occurrence script."""
    visit_cfg = (project.etl_config or {}).get("visit_occurrence", {})
    person_cfg = (project.etl_config or {}).get("person", {})
    cs_cfg = (project.etl_config or {}).get("care_site", {})
    prov_cfg = (project.etl_config or {}).get("provider", {})

    file_configs = visit_cfg.get("file_configs") or []
    is_multi_file = len(file_configs) > 1

    pid_cfg = _person_pid_cfg(person_cfg)
    auto_increment = pid_cfg.get("auto_increment", False)
    pid_col = pid_cfg.get("source_col", "")
    pid_transform = pid_cfg.get("transform", "int_float")

    # person_source_value in visit_occurrence must match what person.csv stores —
    # which is always str(_pid_raw) with no type casting.
    psv_setup = ""
    if pid_col:
        psv_lines = (
            f"            _pid_raw = row.get({repr(pid_col)})\n"
            "            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            "                continue\n"
            "            person_source_value = str(_pid_raw)\n"
        )
    else:
        psv_lines = "            person_source_value = str(_src_idx)\n"

    # Multi-file: pid_col is resolved per-file at runtime from _CUR_PID_COL
    psv_lines_multi = (
        "            if _CUR_PID_COL:\n"
        "                _pid_raw = row.get(_CUR_PID_COL)\n"
        "                if pd.isnull(_pid_raw):\n"
        "                    print(f'WARNING: skipping row {_src_idx} in {_CUR_FILE} — pid column {_CUR_PID_COL!r} is null or missing')\n"
        "                    continue\n"
        "                person_source_value = str(_pid_raw)\n"
        "            else:\n"
        "                person_source_value = str(_src_idx)\n"
    )

    cs_name_col = cs_cfg.get("care_site_name_col", "")
    prov_name_col = prov_cfg.get("provider_name_col", "")

    if cs_name_col:
        cs_setup = (
            "    care_site_lookup = build_id_lookup(output_dir, 'care_site.csv', 'care_site_name', 'care_site_id')\n"
        )
        cs_lookup_line = (
            _xtr("_cs_name_raw", cs_name_col, 12) + "\n"
            "            care_site_id = care_site_lookup.get(_cs_name_raw) if _cs_name_raw else None\n"
            "            if _cs_name_raw and care_site_id is None:\n"
            "                _info(f'INFO: visit for person {person_source_value} — care_site {_cs_name_raw!r} not found in care_site.csv; care_site_id set to NULL')\n"
        )
    else:
        cs_setup = "    care_site_lookup = {}\n"
        cs_lookup_line = _xtr_doc("care_site_id", "", 12) + "\n"

    if prov_name_col:
        prov_setup = (
            "    provider_lookup = build_id_lookup(output_dir, 'provider.csv', 'provider_source_value', 'provider_id')\n"
        )
        prov_lookup_line = (
            _xtr("_prov_name_raw", prov_name_col, 12) + "\n"
            "            _prov_source_value = (str(care_site_id) + ' | ' + (_prov_name_raw or ''))[:50]\n"
            "            provider_id = provider_lookup.get(_prov_source_value)\n"
            "            if _prov_name_raw and provider_id is None:\n"
            "                _info(f'INFO: visit for person {person_source_value} — provider {_prov_name_raw!r} not found in provider.csv; provider_id set to NULL')\n"
        )
    else:
        prov_setup = "    provider_lookup = {}\n"
        prov_lookup_line = _xtr_doc("provider_id", "", 12) + "\n"

    script_header = (
        "import os\n"
        "import pandas as pd\n"
        "from datetime import datetime, date\n"
        "\n"
        "\n"
        "from etl_runtime import _info, _read_str, build_id_lookup\n"
        "\n"
        "\n"
        "# --- Module-level constants ---\n"
        "INPATIENT_CONCEPT_IDS = {9201, 262, 42898160}  # concept IDs treated as inpatient\n"
        "\n"
    )

    lookup_setup = (
        "    # --- Load lookup tables ---\n"
        "    person_lookup = build_id_lookup(output_dir, 'person.csv', 'person_source_value', 'person_id')\n"
        "\n"
        + cs_setup
        + "\n"
        + prov_setup
        + "\n"
        + psv_setup
    )

    rows_init = (
        "    # --- Process rows ---\n"
        "    visit_id_counter = 1\n"
        "    rows = []\n"
        "    _visit_counters = {}  # person_source_value -> int, used for auto-numbering\n"
        "    _seen_visits = set()  # record_source_value dedup guard\n"
        "\n"
    )

    def _make_row_loop_body(pid_lines: str) -> str:
        return (
            "    for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
            "        try:\n"
            "\n"
            "            # Person identifier\n"
            + pid_lines
            + "            person_id = person_lookup.get(person_source_value)\n"
        )

    row_loop_body = _make_row_loop_body(psv_lines) + (
        "            if person_id is None:\n"
        "                print(f'WARNING: skipping row {_src_idx} — person \"{person_source_value}\" not found in person.csv')\n"
        "                continue\n"
        "\n"
        "            # Foreign-key lookups\n"
        + cs_lookup_line
        + prov_lookup_line
        + "\n"
        "            # Iterate over visit definitions for this row\n"
        "            _defs_to_use = VISIT_DEFS[:1] if VISIT_SOURCE_COL else VISIT_DEFS\n"
        "            for vd in _defs_to_use:\n"
        "                try:\n"
        "                    date_val = row.get(vd['date_col'])\n"
        "                    _date_str = str(date_val).strip() if pd.notnull(date_val) else ''\n"
        "                    if vd.get('optional') and (not _date_str or _date_str == 'nan'):\n"
        "                        continue\n"
        "                    if not _date_str or _date_str == 'nan':\n"
        "                        continue\n"
        "                    _date_fmt = vd.get('date_format') or '%Y-%m-%d'\n"
        "                    _time_fmt = vd.get('time_format') or '%H:%M:%S'\n"
        "                    _date_fmt_has_time = any(_tok in _date_fmt for _tok in ('%H', '%I', '%M', '%S', '%f', '%p', '%X'))\n"
        "                    try:\n"
        "                        if _date_fmt_has_time:\n"
        "                            visit_start_datetime = datetime.strptime(_date_str, _date_fmt)\n"
        "                            visit_start_date = visit_start_datetime.date()\n"
        "                        else:\n"
        "                            visit_start_date = datetime.strptime(_date_str, _date_fmt).date()\n"
        "                    except Exception as _e:\n"
        "                        print(f'ERROR: cannot parse start date {_date_str!r} with format {_date_fmt!r} for person {person_source_value} visit \"{vd[\"label\"]}\" — skipping row. ({_e})')\n"
        "                        continue\n"
        "                    if not _date_fmt_has_time:\n"
        "                        _start_time_col = vd.get('time_col') or ''\n"
        "                        if _start_time_col:\n"
        "                            _tv = row.get(_start_time_col)\n"
        "                            _time_str = str(_tv).strip() if pd.notnull(_tv) else ''\n"
        "                            if _time_str and _time_str != 'nan':\n"
        "                                try:\n"
        "                                    visit_start_datetime = datetime.combine(visit_start_date, datetime.strptime(_time_str, _time_fmt).time())\n"
        "                                except Exception:\n"
        "                                    _info(f'INFO: person {person_source_value} visit \"{vd[\"label\"]}\" — could not parse start time {_time_str!r}; defaulting to midnight')\n"
        "                                    visit_start_datetime = datetime.combine(visit_start_date, datetime.min.time())\n"
        "                            else:\n"
        "                                visit_start_datetime = datetime.combine(visit_start_date, datetime.min.time())\n"
        "                        else:\n"
        "                            visit_start_datetime = datetime.combine(visit_start_date, datetime.min.time())\n"
        "\n"
        "                    end_col = vd.get('end_date_col') or ''\n"
        "                    if end_col:\n"
        "                        _ev = row.get(end_col)\n"
        "                        _ev_str = str(_ev).strip() if pd.notnull(_ev) else ''\n"
        "                        if _ev_str and _ev_str != 'nan':\n"
        "                            try:\n"
        "                                if _date_fmt_has_time:\n"
        "                                    visit_end_datetime = datetime.strptime(_ev_str, _date_fmt)\n"
        "                                    visit_end_date = visit_end_datetime.date()\n"
        "                                else:\n"
        "                                    visit_end_date = datetime.strptime(_ev_str, _date_fmt).date()\n"
        "                            except Exception as _e:\n"
        "                                print(f'WARNING: cannot parse end date {_ev_str!r} with format {_date_fmt!r} for person {person_source_value} visit \"{vd[\"label\"]}\" — defaulting to start date. ({_e})')\n"
        "                                visit_end_date = visit_start_date\n"
        "                                if _date_fmt_has_time:\n"
        "                                    visit_end_datetime = visit_start_datetime\n"
        "                        else:\n"
        "                            visit_end_date = visit_start_date\n"
        "                            if _date_fmt_has_time:\n"
        "                                visit_end_datetime = visit_start_datetime\n"
        "                    elif vd.get('visit_concept_id') in INPATIENT_CONCEPT_IDS:\n"
        "                        visit_end_date = date.today()\n"
        "                        if _date_fmt_has_time:\n"
        "                            visit_end_datetime = datetime.combine(visit_end_date, datetime.min.time())\n"
        "                    else:\n"
        "                        visit_end_date = visit_start_date\n"
        "                        if _date_fmt_has_time:\n"
        "                            visit_end_datetime = visit_start_datetime\n"
        "                    if not _date_fmt_has_time:\n"
        "                        _end_time_col = vd.get('end_time_col') or ''\n"
        "                        if _end_time_col:\n"
        "                            _etv = row.get(_end_time_col)\n"
        "                            _etime_str = str(_etv).strip() if pd.notnull(_etv) else ''\n"
        "                            if _etime_str and _etime_str != 'nan':\n"
        "                                try:\n"
        "                                    visit_end_datetime = datetime.combine(visit_end_date, datetime.strptime(_etime_str, _time_fmt).time())\n"
        "                                except Exception:\n"
        "                                    _info(f'INFO: person {person_source_value} visit \"{vd[\"label\"]}\" — could not parse end time {_etime_str!r}; defaulting to midnight')\n"
        "                                    visit_end_datetime = datetime.combine(visit_end_date, datetime.min.time())\n"
        "                            else:\n"
        "                                visit_end_datetime = datetime.combine(visit_end_date, datetime.min.time())\n"
        "                        else:\n"
        "                            visit_end_datetime = datetime.combine(visit_end_date, datetime.min.time())\n"
        "\n"
        "                    vcsc = vd.get('visit_concept_source_col') or ''\n"
        "                    if vcsc:\n"
        "                        _vcv_raw = row.get(vcsc)\n"
        "                        _vcv = (str(_vcv_raw).strip() or None) if pd.notnull(_vcv_raw) else None\n"
        "                        visit_concept_id = (vd.get('visit_concept_value_map') or {}).get(_vcv, vd['visit_concept_id']) if _vcv else vd['visit_concept_id']\n"
        "                        # visit_source_value: verbatim source value for the kind of visit (OMOP spec)\n"
        "                        visit_source_value = _vcv\n"
        "                    else:\n"
        "                        visit_concept_id = vd['visit_concept_id']\n"
        "                        visit_source_value = None\n"
        "\n"
        "                    vtsc = vd.get('visit_type_source_col') or ''\n"
        "                    if vtsc:\n"
        "                        _vtv_raw = row.get(vtsc)\n"
        "                        _vtv = (str(_vtv_raw).strip() or None) if pd.notnull(_vtv_raw) else None\n"
        "                        visit_type_concept_id = (vd.get('visit_type_value_map') or {}).get(_vtv, vd['type_concept_id']) if _vtv else vd['type_concept_id']\n"
        "                    else:\n"
        "                        visit_type_concept_id = vd['type_concept_id']\n"
        "\n"
        "                    if VISIT_SOURCE_COL:\n"
        "                        _vsv_raw = str(row.get(VISIT_SOURCE_COL, '')).strip()\n"
        "                        if not _vsv_raw or _vsv_raw == 'nan':\n"
        "                            continue\n"
        "                        label_norm = _vsv_raw.lower().replace(' ', '_')\n"
        "                        # Multi-row dataset: the visit identifier column IS the verbatim kind-of-visit value\n"
        "                        visit_source_value = _vsv_raw\n"
        "                    elif AUTO_NUMBER_VISITS:\n"
        "                        _visit_counters[person_source_value] = _visit_counters.get(person_source_value, 0) + 1\n"
        "                        label_norm = f'visit{_visit_counters[person_source_value]}'\n"
        "                    else:\n"
        "                        label_norm = vd['label'].lower().replace(' ', '_')\n"
        "                    # record_source_value: internal dedup/join key, independent of visit_source_value\n"
        "                    record_source_value = f'{person_source_value}-{label_norm}'\n"
        "\n"
        "                    afc_col = vd.get('admitted_from_source_col') or ''\n"
        "                    if afc_col:\n"
        "                        _afv_raw = row.get(afc_col)\n"
        "                        _afv = (str(_afv_raw).strip() or None) if pd.notnull(_afv_raw) else None\n"
        "                        admitted_from_source_value = _afv\n"
        "                        admitted_from_concept_id = (vd.get('admitted_from_value_map') or {}).get(_afv, vd.get('admitted_from_concept_id') or 0) if _afv else (vd.get('admitted_from_concept_id') or 0)\n"
        "                    else:\n"
        "                        admitted_from_source_value = vd.get('admitted_from_source_value')\n"
        "                        admitted_from_concept_id = vd.get('admitted_from_concept_id') or 0\n"
        "\n"
        "                    dtc_col = vd.get('discharged_to_source_col') or ''\n"
        "                    if dtc_col:\n"
        "                        _dtv_raw = row.get(dtc_col)\n"
        "                        _dtv = (str(_dtv_raw).strip() or None) if pd.notnull(_dtv_raw) else None\n"
        "                        discharged_to_source_value = _dtv\n"
        "                        discharged_to_concept_id = (vd.get('discharged_to_value_map') or {}).get(_dtv, vd.get('discharged_to_concept_id') or 0) if _dtv else (vd.get('discharged_to_concept_id') or 0)\n"
        "                    else:\n"
        "                        discharged_to_source_value = vd.get('discharged_to_source_value')\n"
        "                        discharged_to_concept_id = vd.get('discharged_to_concept_id') or 0\n"
        "\n"
        "                    if record_source_value in _seen_visits:\n"
        "                        print(f'WARNING: duplicate visit skipped — record_source_value {record_source_value!r} already exists')\n"
        "                        continue\n"
        "                    _seen_visits.add(record_source_value)\n"
        "                    rows.append({\n"
        "                        'visit_occurrence_id': visit_id_counter,\n"
        "                        'person_id': person_id,\n"
        "                        'visit_concept_id': visit_concept_id,\n"
        "                        'visit_start_date': visit_start_date,\n"
        "                        'visit_start_datetime': visit_start_datetime,\n"
        "                        'visit_end_date': visit_end_date,\n"
        "                        'visit_end_datetime': visit_end_datetime,\n"
        "                        'visit_type_concept_id': visit_type_concept_id,\n"
        "                        'provider_id': provider_id,\n"
        "                        'care_site_id': care_site_id,\n"
        "                        'visit_source_value': visit_source_value,\n"
        "                        'visit_source_concept_id': 0,\n"
        "                        'admitted_from_concept_id': admitted_from_concept_id,\n"
        "                        'admitted_from_source_value': admitted_from_source_value,\n"
        "                        'discharged_to_concept_id': discharged_to_concept_id,\n"
        "                        'discharged_to_source_value': discharged_to_source_value,\n"
        "                        'preceding_visit_occurrence_id': None,\n"
        "                        'record_source_value': record_source_value,\n"
        "                    })\n"
        "                    visit_id_counter += 1\n"
        "                except Exception as e:\n"
        "                    print(f'WARNING: skipping visit for {person_source_value} — {e}')\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: skipping row — {e}')\n"
    )

    output_section = (
        "\n"
        "    # --- Build output DataFrame ---\n"
        "    VISIT_OCCURRENCE_COLUMNS = [\n"
        "        'visit_occurrence_id', 'person_id', 'visit_concept_id',\n"
        "        'visit_start_date', 'visit_start_datetime', 'visit_end_date', 'visit_end_datetime',\n"
        "        'visit_type_concept_id', 'provider_id', 'care_site_id',\n"
        "        'visit_source_value', 'visit_source_concept_id',\n"
        "        'admitted_from_concept_id', 'admitted_from_source_value',\n"
        "        'discharged_to_concept_id', 'discharged_to_source_value',\n"
        "        'preceding_visit_occurrence_id', 'record_source_value',\n"
        "    ]\n"
        "    df_out = pd.DataFrame(rows, columns=VISIT_OCCURRENCE_COLUMNS)\n"
        "\n"
        "    if not df_out.empty:\n"
        "        for _pid, grp in df_out.groupby('person_id', sort=False):\n"
        "            idx_list = grp.index.tolist()\n"
        "            for k in range(1, len(idx_list)):\n"
        "                df_out.at[idx_list[k], 'preceding_visit_occurrence_id'] = df_out.at[idx_list[k - 1], 'visit_occurrence_id']\n"
        "\n"
        "    # Serialise datetime columns to ISO strings for CSV output\n"
        "    for _dt_col in ['visit_start_datetime', 'visit_end_datetime']:\n"
        "        if _dt_col in df_out.columns:\n"
        "            df_out[_dt_col] = df_out[_dt_col].apply(\n"
        "                lambda x: x.strftime('%Y-%m-%d %H:%M:%S') if pd.notnull(x) and hasattr(x, 'strftime') else x\n"
        "            )\n"
        "\n"
        "    # --- Write output ---\n"
        "    output_file = os.path.join(output_dir, 'visit_occurrence.csv')\n"
        "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        "    print(f'Writing visit_occurrence.csv ... done ({len(df_out)} records)')\n"
    )

    if is_multi_file:
        # Resolve pid_col per visit file from PersonConfig's file_configs
        person_file_cfgs = person_cfg.get("file_configs", {})

        def _visit_pid_col_for(fname: str) -> str:
            pfc = person_file_cfgs.get(fname, {})
            if pfc:
                return pfc.get("mappings", {}).get("person_id", {}).get("source_col", "") or pid_col
            return pid_col

        sf_lookup = {sf.get("filename", ""): sf for sf in (project.source_files or [])}
        fc_list = []
        for fc in file_configs:
            fname = fc.get("source_filename", "")
            sf = sf_lookup.get(fname, {})
            fc_list.append({
                "filename": fname,
                "path": sf.get("path", ""),
                "delimiter": sf.get("delimiter", ","),
                "encoding": sf.get("encoding", "utf-8"),
                "visit_defs": fc.get("visit_definitions", []),
                "visit_source_col": fc.get("visit_source_col", "") or "",
                "auto_number_visits": bool(fc.get("auto_number_visits", False)),
                "pid_col": _visit_pid_col_for(fname),
            })
        fc_repr = repr(fc_list)

        # Multi-file row loop: pid_col comes from fc['pid_col'] via _CUR_PID_COL
        row_loop_body_multi = _make_row_loop_body(psv_lines_multi) + (
            "            if person_id is None:\n"
            "                print(f'WARNING: skipping row {_src_idx} in {_CUR_FILE} — person \"{person_source_value}\" not found in person.csv')\n"
            "                continue\n"
            "\n"
            "            # Foreign-key lookups\n"
        ) + cs_lookup_line + prov_lookup_line + row_loop_body[row_loop_body.index("            # Iterate over visit definitions"):]

        # Add 4 extra spaces to every non-empty line so the row loop sits inside `for fc in FILE_CONFIGS:`
        indented_row_loop = "".join(
            ("    " + line) if line.rstrip() else line
            for line in row_loop_body_multi.splitlines(keepends=True)
        )
        return (
            script_header
            + f"FILE_CONFIGS = {fc_repr}\n"
            + "\n\n"
            + "def main():\n"
            + "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
            + "\n"
            + lookup_setup
            + "\n"
            + rows_init
            + "    for fc in FILE_CONFIGS:\n"
            + "        VISIT_DEFS         = fc['visit_defs']\n"
            + "        VISIT_SOURCE_COL   = fc['visit_source_col']\n"
            + "        AUTO_NUMBER_VISITS = fc['auto_number_visits']\n"
            + "        _CUR_PID_COL       = fc.get('pid_col', '')\n"
            + "        _CUR_FILE          = fc.get('filename', '')\n"
            + "        df = pd.read_csv(fc['path'], delimiter=fc['delimiter'], encoding=fc['encoding'])\n"
            + "\n"
            + indented_row_loop
            + output_section
            + "\n\n"
            + "if __name__ == '__main__':\n"
            + "    main()\n"
        )

    # --- Single-file path (original behaviour, unchanged) ---
    source_path_code, delim, enc = _source_file_params(project, visit_cfg)
    visit_defs = visit_cfg.get("visit_definitions", [])
    vd_repr = repr(visit_defs)
    visit_source_col = visit_cfg.get("visit_source_col", "")
    auto_number_visits = bool(visit_cfg.get("auto_number_visits", False))

    return (
        script_header
        + f"VISIT_DEFS      = {vd_repr}  # visit definitions from the Visit step\n"
        + f"\nVISIT_SOURCE_COL = {repr(visit_source_col)}  # column that carries visit label (multi-row mode)\n"
        + f"AUTO_NUMBER_VISITS = {repr(auto_number_visits)}  # number visits visit1/visit2/... when no identifier col\n"
        + "\n\n"
        + "def main():\n"
        + "    # Load environmental variables\n"
        + source_path_code
        + "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        + "\n"
        + "    # --- Load source data ---\n"
        + f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        + "\n"
        + lookup_setup
        + "\n"
        + rows_init
        + row_loop_body
        + output_section
        + "\n\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _obs_period_end_date_lines(end_col: str, fallbacks: list, date_fmt: str) -> str:
    """Generate end_date assignment code for single-file obs period (inside try block, 16-space indent)."""
    i16 = "                "
    i20 = "                    "

    def terminal_line(indent: str) -> str:
        for fb in fallbacks:
            if fb.get("type") == "today":
                return f"{indent}obs_end_date = date.today()\n"
            if fb.get("type") == "start_date":
                return f"{indent}obs_end_date = obs_start_date\n"
        return f"{indent}obs_end_date = obs_start_date\n"

    col_fbs = [(i, fb) for i, fb in enumerate(fallbacks) if fb.get("type") == "column" and fb.get("col")]

    def col_chain(indent: str, else_indent: str) -> str:
        if not col_fbs:
            return terminal_line(indent)
        lines = f"{indent}_obs_end_resolved = False\n"
        for _i, fb in col_fbs:
            col = fb["col"]
            lines += (
                f"{indent}if not _obs_end_resolved:\n"
                f"{indent}    _fb_raw = str(row.get({repr(col)}, '')).strip()\n"
                f"{indent}    if _fb_raw and _fb_raw != 'nan':\n"
                f"{indent}        try:\n"
                f"{indent}            obs_end_date = datetime.strptime(_fb_raw, {repr(date_fmt)}).date()\n"
                f"{indent}            _obs_end_resolved = True\n"
                f"{indent}        except Exception:\n"
                f"{indent}            pass\n"
            )
        term = terminal_line(indent)
        lines += f"{indent}if not _obs_end_resolved:\n"
        lines += "".join(f"{indent}    {l}" for l in term.lstrip().splitlines(keepends=True))
        return lines

    if end_col:
        return (
            f"{i16}_end_raw = str(row.get({repr(end_col)}, '')).strip()\n"
            f"{i16}if _end_raw and _end_raw != 'nan':\n"
            f"{i16}    obs_end_date = datetime.strptime(_end_raw, {repr(date_fmt)}).date()\n"
            f"{i16}else:\n"
            + col_chain(i20, i20)
        )
    else:
        return col_chain(i16, i16)


def _obs_period_common_header() -> str:
    return (
        "import os\n"
        "import pandas as pd\n"
        "from datetime import datetime, date, timedelta\n"
        "\n"
        "\n"
        "from etl_runtime import _info, build_id_lookup\n"
        "\n"
        "\n"
        "def _merge_periods(periods):\n"
        "    \"\"\"Merge overlapping or adjacent (start, end) date pairs.\"\"\"\n"
        "    if not periods:\n"
        "        return []\n"
        "    periods = sorted(periods, key=lambda x: x[0])\n"
        "    merged = [list(periods[0])]\n"
        "    for start, end in periods[1:]:\n"
        "        if start <= merged[-1][1] + timedelta(days=1):\n"
        "            merged[-1][1] = max(merged[-1][1], end)\n"
        "        else:\n"
        "            merged.append([start, end])\n"
        "    return merged\n"
        "\n"
        "\n"
    )


def _obs_period_common_tail(type_concept_id: int) -> str:
    return (
        "\n"
        "    # --- Merge overlapping periods and build records ---\n"
        "    rows = []\n"
        "    obs_id = 1\n"
        "    for person_id, periods in person_periods.items():\n"
        "        for start, end in _merge_periods(periods):\n"
        "            rows.append({\n"
        "                'observation_period_id':         obs_id,\n"
        "                'person_id':                     person_id,\n"
        "                'observation_period_start_date': start,\n"
        "                'observation_period_end_date':   end,\n"
        f"                'period_type_concept_id':        {type_concept_id},\n"
        "            })\n"
        "            obs_id += 1\n"
        "\n"
        "    # --- Write output ---\n"
        "    OBSERVATION_PERIOD_COLUMNS = [\n"
        "        'observation_period_id', 'person_id',\n"
        "        'observation_period_start_date', 'observation_period_end_date',\n"
        "        'period_type_concept_id',\n"
        "    ]\n"
        "    df_out = pd.DataFrame(rows, columns=OBSERVATION_PERIOD_COLUMNS)\n"
        "    output_file = os.path.join(output_dir, 'observation_period.csv')\n"
        "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        "    print(f'Writing observation_period.csv ... done ({len(df_out)} records)')\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _generate_observation_period_script_multi(project, obs: dict, person_cfg: dict, fallbacks: list) -> str:
    """Multi-source observation period generator: start_date/end_date/fallback columns span different files."""
    start_col = obs.get("start_date_col", "")
    start_file = obs.get("start_date_file", "")
    end_col = obs.get("end_date_col", "")
    end_file = obs.get("end_date_file", "")
    date_fmt = obs.get("date_format") or "%Y-%m-%d"
    type_concept_id = int(obs.get("period_type_concept_id") or 32879)

    pid_cfg = _person_pid_cfg(person_cfg)
    pid_col = pid_cfg.get("source_col", "")

    # Ordered list of distinct files involved
    involved_files: list = []
    for fn in [start_file, end_file]:
        if fn and fn not in involved_files:
            involved_files.append(fn)
    for fb in fallbacks:
        fn = fb.get("source_filename", "")
        if fn and fn not in involved_files:
            involved_files.append(fn)

    if pid_col:
        psv_lines = (
            f"            _pid_raw = row.get({repr(pid_col)})\n"
            "            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            "                continue\n"
            "            psv = str(_pid_raw)\n"
        )
    else:
        psv_lines = "            psv = str(_src_idx)\n"

    # Build per-file loop blocks
    file_blocks = ""
    for filename in involved_files:
        contributes_start = bool(start_col and filename == start_file)
        contributes_end = bool(end_col and filename == end_file)
        fb_for_file = [
            (i, fb) for i, fb in enumerate(fallbacks)
            if fb.get("type") == "column" and fb.get("source_filename") == filename and fb.get("col")
        ]

        body = ""
        if contributes_start:
            body += (
                f"            _start_raw = str(row.get({repr(start_col)}, '')).strip()\n"
                "            if _start_raw and _start_raw != 'nan':\n"
                "                try:\n"
                f"                    person_data[psv]['start'] = datetime.strptime(_start_raw, {repr(date_fmt)}).date()\n"
                "                except Exception as _e:\n"
                f"                    _info(f'INFO: row {{_src_idx}} person {{psv!r}} — could not parse start_date ({{_e}})')\n"
            )
        if contributes_end:
            body += (
                f"            _end_raw = str(row.get({repr(end_col)}, '')).strip()\n"
                "            if _end_raw and _end_raw != 'nan':\n"
                "                try:\n"
                f"                    person_data[psv]['end'] = datetime.strptime(_end_raw, {repr(date_fmt)}).date()\n"
                "                except Exception as _e:\n"
                f"                    _info(f'INFO: row {{_src_idx}} person {{psv!r}} — could not parse end_date ({{_e}})')\n"
            )
        for i, fb in fb_for_file:
            col = fb["col"]
            body += (
                f"            _fb{i}_raw = str(row.get({repr(col)}, '')).strip()\n"
                f"            if _fb{i}_raw and _fb{i}_raw != 'nan':\n"
                f"                try:\n"
                f"                    person_data[psv]['fb_{i}'] = datetime.strptime(_fb{i}_raw, {repr(date_fmt)}).date()\n"
                f"                except Exception:\n"
                f"                    pass\n"
            )

        read_line = _sf_read_line(project, filename)
        file_blocks += (
            f"\n    # --- {filename} ---\n"
            + read_line
            + "    for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
            "        try:\n"
            + psv_lines
            + "            if psv not in person_data:\n"
            "                person_data[psv] = {}\n"
            + body
            + "        except Exception as e:\n"
            "            print(f'WARNING: skipping row — {e}')\n"
        )

    # Fallback resolution block (runs after all file loops)
    resolve = (
        "\n"
        "    # --- Resolve observation periods from accumulated data ---\n"
        "    person_periods: dict = {}\n"
        "    for psv, data in person_data.items():\n"
        "        start = data.get('start')\n"
        "        if not start:\n"
        "            print(f'WARNING: skipping person {psv!r} — no start_date found across all source files')\n"
        "            continue\n"
        "        person_id = person_lookup.get(psv)\n"
        "        if person_id is None:\n"
        "            print(f'WARNING: skipping person {psv!r} — not found in person.csv')\n"
        "            continue\n"
        "        end = data.get('end')\n"
    )
    for i, fb in enumerate(fallbacks):
        fb_type = fb.get("type")
        if fb_type == "column" and fb.get("col"):
            resolve += f"        if end is None:\n            end = data.get('fb_{i}')\n"
        elif fb_type == "start_date":
            resolve += "        if end is None:\n            end = start\n"
            break
        elif fb_type == "today":
            resolve += "        if end is None:\n            end = date.today()\n"
            break
    else:
        # No terminal fallback found — default to start_date
        resolve += "        if end is None:\n            end = start\n"
    resolve += "        person_periods.setdefault(person_id, []).append((start, end))\n"

    return (
        _obs_period_common_header()
        + "def main():\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        "    person_lookup = build_id_lookup(output_dir, 'person.csv', 'person_source_value', 'person_id')\n"
        "\n"
        "    # Accumulate per-patient date components across all source files\n"
        "    person_data: dict = {}  # psv -> {start, end, fb_0, ...}\n"
        + file_blocks
        + resolve
        + _obs_period_common_tail(type_concept_id)
    )


def _generate_observation_period_script(project) -> str:
    """Deterministic template-based generator for the OMOP observation_period script."""
    obs = (project.etl_config or {}).get("observation_period", {})
    person_cfg = (project.etl_config or {}).get("person", {})

    # Normalise fallback chain (migrate legacy end_date_fallback string)
    fallbacks: list = obs.get("end_date_fallbacks") or []
    if not fallbacks and obs.get("end_date_fallback"):
        fallbacks = [{"type": obs["end_date_fallback"]}]

    # Detect multi-file: distinct files referenced for start, end, or fallback columns
    start_file = obs.get("start_date_file", "")
    end_file = obs.get("end_date_file", "")
    fb_files = [fb.get("source_filename", "") for fb in fallbacks if fb.get("type") == "column" and fb.get("source_filename")]
    distinct_files = set(filter(None, [start_file, end_file] + fb_files))
    if len(distinct_files) > 1:
        return _generate_observation_period_script_multi(project, obs, person_cfg, fallbacks)

    # Single-file path — resolve source from whichever file was selected (if any)
    obs_for_src = obs
    if distinct_files:
        obs_for_src = {**obs, "source_filename": next(iter(distinct_files))}
    source_path_code, delim, enc = _source_file_params(project, obs_for_src)

    start_col = obs.get("start_date_col", "")
    end_col = obs.get("end_date_col", "")
    type_concept_id = int(obs.get("period_type_concept_id") or 32879)
    date_fmt = obs.get("date_format") or "%Y-%m-%d"

    pid_cfg = _person_pid_cfg(person_cfg)
    pid_col = pid_cfg.get("source_col", "")

    if pid_col:
        psv_lines = (
            f"            _pid_raw = row.get({repr(pid_col)})\n"
            "            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            "                continue\n"
            "            person_source_value = str(_pid_raw)\n"
        )
    else:
        psv_lines = "            person_source_value = str(_src_idx)\n"

    end_date_lines = _obs_period_end_date_lines(end_col, fallbacks, date_fmt)

    return (
        _obs_period_common_header()
        + "def main():\n"
        + source_path_code
        + "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        "    person_lookup = build_id_lookup(output_dir, 'person.csv', 'person_source_value', 'person_id')\n"
        "\n"
        "    # --- Collect observation periods per person ---\n"
        "    person_periods: dict = {}  # person_id -> [(start_date, end_date), ...]\n"
        "\n"
        "    for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
        "        try:\n"
        "\n"
        "            # Person identifier\n"
        + psv_lines
        + "            person_id = person_lookup.get(person_source_value)\n"
        "            if person_id is None:\n"
        "                print(f'WARNING: skipping row {_src_idx} — person \"{person_source_value}\" not found in person.csv')\n"
        "                continue\n"
        "\n"
        "            # Start date (required)\n"
        f"            _start_raw = str(row.get({repr(start_col)}, '')).strip()\n"
        "            if not _start_raw or _start_raw == 'nan':\n"
        "                print(f'WARNING: skipping row {_src_idx} — observation_period start_date is empty for person \"{person_source_value}\"')\n"
        "                continue\n"
        f"            obs_start_date = datetime.strptime(_start_raw, {repr(date_fmt)}).date()\n"
        "\n"
        "            # End date (optional — resolved via fallback chain)\n"
        "            try:\n"
        + end_date_lines
        + "            except Exception as _end_exc:\n"
        "                _info(f'INFO: person \"{person_source_value}\" — could not parse end_date; defaulting to start_date ({_end_exc})')\n"
        "                obs_end_date = obs_start_date\n"
        "\n"
        "            person_periods.setdefault(person_id, []).append((obs_start_date, obs_end_date))\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: skipping row — {e}')\n"
        + _obs_period_common_tail(type_concept_id)
    )


def _generate_death_script(project) -> str:
    """Deterministic template-based generator for the OMOP death script.

    Reads the source CSV, keeps rows where filter_col == filter_value, looks
    up the OMOP person_id from person.csv, and writes one death row per
    person. Always emits the full DEATH header — when no rows match the
    filter, an empty-but-parseable CSV is still produced so downstream
    previews/tooling don't choke.
    """
    death_cfg = (project.etl_config or {}).get("death", {})
    person_cfg = (project.etl_config or {}).get("person", {})

    source_path_code, delim, enc = _source_file_params(project, death_cfg)

    pid_cfg = _person_pid_cfg(person_cfg)
    pid_col = pid_cfg.get("source_col", "")

    filter_col = death_cfg.get("filter_col", "")
    filter_value = str(death_cfg.get("filter_value", "") or "")
    death_date_col = death_cfg.get("death_date_col", "")
    death_dt_col = death_cfg.get("death_datetime_col", "")
    death_type_cid = int(death_cfg.get("death_type_concept_id") or 32879)
    date_fmt = death_cfg.get("date_format") or "%Y-%m-%d"

    _cc = death_cfg.get("cause_concept_id")
    cause_cid_lit = "None" if _cc in (None, "") else str(int(_cc))
    _csc = death_cfg.get("cause_source_concept_id")
    cause_sc_cid_lit = "None" if _csc in (None, "") else str(int(_csc))
    cause_sv_col = death_cfg.get("cause_source_value_col", "")

    if filter_col:
        filter_check = (
            f"            _fv = row.get({repr(filter_col)})\n"
            "            if pd.isnull(_fv):\n"
            "                continue\n"
            f"            _fv_target = {repr(filter_value)}\n"
            "            _matched = str(_fv).strip() == _fv_target\n"
            "            if not _matched:\n"
            "                try:\n"
            "                    _matched = float(_fv) == float(_fv_target)\n"
            "                except (ValueError, TypeError):\n"
            "                    _matched = False\n"
            "            if not _matched:\n"
            "                continue\n"
        )
    else:
        filter_check = "            # no filter column configured — every row becomes a death\n"

    if pid_col:
        psv_lines = (
            f"            _pid_raw = row.get({repr(pid_col)})\n"
            "            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            "                continue\n"
            "            person_source_value = str(_pid_raw)\n"
        )
    else:
        psv_lines = "            person_source_value = str(_src_idx)\n"

    if death_date_col:
        date_lines = (
            f"            _dd_raw = row.get({repr(death_date_col)})\n"
            "            if pd.isnull(_dd_raw):\n"
            "                continue\n"
            "            _dd_str = str(_dd_raw).strip()\n"
            "            if not _dd_str or _dd_str == 'nan':\n"
            "                continue\n"
            "            try:\n"
            f"                _dd_dt = datetime.strptime(_dd_str, {repr(date_fmt)})\n"
            "            except ValueError as _e:\n"
            "                print(f'WARNING: cannot parse death_date {_dd_str!r} with format ' + repr("
            + repr(date_fmt) + ") + f' for person {person_source_value} — skipping row. ({_e})')\n"
            "                continue\n"
            "            death_date = _dd_dt.date().isoformat()\n"
        )
    else:
        date_lines = (
            "            _dd_dt = None\n"
            + _xtr_doc("death_date", "", 12) + "\n"
        )

    if death_dt_col:
        dt_lines = (
            f"            _ddt_raw = row.get({repr(death_dt_col)})\n"
            "            if pd.notnull(_ddt_raw):\n"
            "                _ddt_str = str(_ddt_raw).strip()\n"
            "                if _ddt_str and _ddt_str != 'nan':\n"
            "                    try:\n"
            f"                        _ddt_dt = datetime.strptime(_ddt_str, {repr(date_fmt)})\n"
            "                        death_datetime = _ddt_dt.strftime('%Y-%m-%d %H:%M:%S')\n"
            "                    except ValueError as _ddt_exc:\n"
            "                        _info(f'INFO: person \"{person_source_value}\" — could not parse death_datetime {_ddt_str!r}; death_datetime set to NULL ({_ddt_exc})')\n"
            "                        death_datetime = None\n"
            "                else:\n"
            "                    death_datetime = None\n"
            "            else:\n"
            "                death_datetime = None\n"
        )
    else:
        dt_lines = (
            "            death_datetime = (\n"
            "                _dd_dt.strftime('%Y-%m-%d %H:%M:%S') if _dd_dt is not None else None\n"
            "            )\n"
        )

    cause_sv_lines = _xtr_doc("cause_source_value", cause_sv_col, 12) + "\n"

    return (
        "import os\n"
        "import pandas as pd\n"
        "from datetime import datetime\n"
        "\n"
        "\n"
        "from etl_runtime import _info, _read_str, build_id_lookup\n"
        "\n"
        "\n"
        "# Output column order\n"
        "COLUMNS = [\n"
        "    'person_id', 'death_date', 'death_datetime', 'death_type_concept_id',\n"
        "    'cause_concept_id', 'cause_source_value', 'cause_source_concept_id',\n"
        "]\n"
        "\n"
        "\n"
        "def main():\n"
        + source_path_code +
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        "    person_lookup = build_id_lookup(output_dir, 'person.csv', 'person_source_value', 'person_id')\n"
        "\n"
        "    # --- Process rows ---\n"
        "    rows = []\n"
        "    seen_person_ids = set()  # keep only first death per person\n"
        "\n"
        "    for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
        "        try:\n"
        "\n"
        "            # Filter check (only rows matching configured filter value)\n"
        + filter_check
        + "\n"
        + "            # Person identifier\n"
        + psv_lines
        + "            person_id = person_lookup.get(person_source_value)\n"
        "            if person_id is None:\n"
        "                print(f'WARNING: skipping row {_src_idx} — person \"{person_source_value}\" not found in person.csv')\n"
        "                continue\n"
        "            if person_id in seen_person_ids:\n"
        "                continue\n"
        "            seen_person_ids.add(person_id)\n"
        "\n"
        "            # Death date & datetime\n"
        + date_lines
        + dt_lines
        + "\n"
        + "            # Cause of death\n"
        + cause_sv_lines
        + "\n"
        + "            rows.append({\n"
        + "                'person_id':               person_id,\n"
        + "                'death_date':              death_date,\n"
        + "                'death_datetime':          death_datetime,\n"
        f"                'death_type_concept_id':   {death_type_cid},\n"
        f"                'cause_concept_id':         {cause_cid_lit},\n"
        + "                'cause_source_value':      cause_source_value[:50] if cause_source_value else None,\n"
        f"                'cause_source_concept_id':  {cause_sc_cid_lit},\n"
        + "            })\n"
        + "        except Exception as e:\n"
        + "            print(f'WARNING: skipping row — {e}')\n"
        + "\n"
        + "    # --- Write output ---\n"
        + "    df_out = pd.DataFrame(rows, columns=COLUMNS)\n"
        + "    output_file = os.path.join(output_dir, 'death.csv')\n"
        + "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing death.csv ... done ({len(df_out)} records)')\n"
        + "\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _infer_stem_overrides(project) -> list[dict]:
    """Deterministically derive SPECIAL_OVERRIDES entries from Concepts step decisions.

    The runtime stem_table.py already consumes unit_mapping.csv for per-row
    unit lookups. This function covers the edge case where the Concepts step captured
    a unit_mapping with a SINGLE concept_id and NO unit_col (a fixed unit
    for every row of that variable) — in which case unit_mapping.csv has
    nothing to look up on, and the unit must be hardcoded as an override.

    Rules (v1):
      - Skip variables with strategy == 'skip' or no decision.
      - Any variable whose unit_mapping carries exactly one unit concept_id and
        no unit_col emits { variable, field: 'unit_concept_id', value: <concept_id>,
        unit_source_value: <unit name> } — not domain-restricted: map_values
        variables (e.g. Drug Exposure's dose unit) never populate domain_id at
        the decision level in the first place, and applying the override is a
        harmless no-op for domains that don't use it. The unit name (the
        unit_concepts key the user picked the fixed concept for) is carried
        through so unit_source_value is populated the same way it would be for
        a per-row unit column, instead of staying null.
      - No legacy hardcoded overrides; every entry traces back to data.

    The extensible hook for future inferred-override rules.
    """
    decisions: dict = project.concept_decisions or {}
    inferred: list[dict] = []
    for variable, d in decisions.items():
        if not isinstance(d, dict):
            continue
        if d.get("strategy") == "skip":
            continue
        um = d.get("unit_mapping") or {}
        if um.get("unit_col"):
            continue  # handled at runtime via unit_mapping.csv
        unit_concepts = um.get("unit_concepts") or {}
        valid = [(k, v) for k, v in unit_concepts.items() if isinstance(v, int) and v > 0]
        if len(valid) != 1:
            continue
        unit_name, unit_cid = valid[0]
        inferred.append({
            "variable": variable,
            "field": "unit_concept_id",
            "value": unit_cid,
            "unit_source_value": unit_name,
        })
    return inferred


def _generate_stem_table_script(project) -> str:
    """Deterministic template-based generator for the OMOP stem_table script.

    Visit assignment per variable is no longer driven by a user-edited
    `variable_groups` mapping. Instead the generated script does a runtime
    substring match between each variable name and the configured visit
    labels, builds the standard visit_record_source_value composite key, and
    delegates the dict lookup to a `lookup_visit_occurrence_id` helper that
    mirrors the OHDSI wrapper.lookup_visit_occurrence_id reference (single
    arg, lazy dict init, logger.info on miss, return None).
    """
    stem_cfg = (project.etl_config or {}).get("stem_table", {})
    visit_cfg = (project.etl_config or {}).get("visit_occurrence", {})
    person_cfg = (project.etl_config or {}).get("person", {})

    pid_cfg = _person_pid_cfg(person_cfg)
    pid_col = pid_cfg.get("source_col", "")

    # Background-inferred overrides (Concepts step fixed-unit cases) come first;
    # user-entered overrides (Stem Table step UI) win on conflict because the
    # generated OVERRIDE_MAP build uses dict overwrite (later entry wins).
    inferred_overrides = _infer_stem_overrides(project)
    user_overrides = stem_cfg.get("special_overrides", []) or []
    special_overrides = inferred_overrides + user_overrides

    visit_defs = visit_cfg.get("visit_definitions", [])
    visit_source_col = visit_cfg.get("visit_source_col", "")
    auto_number_visits = bool(visit_cfg.get("auto_number_visits", False))
    visit_date_info = {
        vd["label"]: {
            "date_col": vd.get("date_col", ""),
            "date_format": vd.get("date_format") or "%Y-%m-%d",
        }
        for vd in visit_defs
        if vd.get("label")
    }

    # In multi-row mode all variables on a row share one visit; use the first
    # visit definition's date column as the default for all stem rows.
    first_vd = visit_defs[0] if visit_defs else {}
    default_date_col = first_vd.get("date_col", "") if visit_source_col else ""
    default_date_format = (first_vd.get("date_format") or "%Y-%m-%d") if visit_source_col else "%Y-%m-%d"

    # variable → visit label, as configured by the user in the Stem Table step.
    # Keys are normalised to lowercase to match the runtime _lc lookup.
    variable_visit_map = {
        k.lower(): v
        for k, v in (stem_cfg.get("variable_visit_map") or {}).items()
    }

    if pid_col:
        psv_lines = (
            f"            _pid_raw = row.get({repr(pid_col)})\n"
            "            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            "                continue\n"
            "            person_source_value = str(_pid_raw)\n"
        )
    else:
        psv_lines = "            person_source_value = str(_src_idx)\n"

    # Universe of variables the user explicitly mapped in the Concepts step (strategy != skip).
    # Structural columns already used by Person/Visit/Death/etc. are absent from
    # concept_decisions entirely, so this set automatically excludes them.
    mapped_variables = {
        k.lower()
        for k, v in (project.concept_decisions or {}).items()
        if isinstance(v, dict) and v.get("strategy") != "skip"
    }

    # Build per-source-file variable sets so the generated script can iterate
    # each file independently and process only its own mapped columns.
    col_to_file: dict[str, str] = {}
    for sf in (project.source_files or []):
        for col in (sf.get("columns") or []):
            col_to_file[col.lower()] = sf.get("filename", "")

    source_files_data: list[dict] = []
    for sf in (project.source_files or []):
        fn = sf.get("filename", "")
        file_vars = sorted(
            v for v in mapped_variables if col_to_file.get(v) == fn
        )
        if not file_vars:
            continue
        source_files_data.append({
            "path": sf.get("path", ""),
            "delimiter": sf.get("delimiter", ","),
            "encoding": sf.get("encoding", "utf-8"),
            "variables": file_vars,
        })

    # Fallback: single-file projects or projects where source_files metadata is absent.
    if not source_files_data:
        source_files_data = [{
            "path": project.source_path or "",
            "delimiter": project.source_delimiter or ",",
            "encoding": project.source_encoding or "utf-8",
            "variables": sorted(mapped_variables),
        }]

    source_files_repr = repr(source_files_data)

    vdi_repr = repr(visit_date_info)
    vvm_repr = repr(variable_visit_map)
    so_repr = repr(special_overrides)

    # Drug Exposure (domain_id 3): variable → name of a sibling column in the
    # same source row whose value is pulled into a drug_exposure field (e.g. a
    # dosage column next to a drug-name column). Configured per variable in
    # the Concepts step. Route and Dose unit are NOT here — they resolve
    # per-row through route_mapping.csv / unit_mapping.csv instead (see below),
    # same as any other per-value concept mapping.
    def _drug_col_map(field: str) -> dict[str, str]:
        return {
            k.lower(): d[field]
            for k, d in (project.concept_decisions or {}).items()
            if isinstance(d, dict) and d.get(field)
        }

    quantity_col_map = _drug_col_map("quantity_col")
    days_supply_col_map = _drug_col_map("days_supply_col")
    refills_col_map = _drug_col_map("refills_col")
    sig_col_map = _drug_col_map("sig_col")
    lot_number_col_map = _drug_col_map("lot_number_col")
    stop_reason_col_map = _drug_col_map("stop_reason_col")

    # Measurement (domain_id 1): sibling columns holding the reference range low/high
    # bounds for each row, same convention as the Drug Exposure fields above.
    range_low_col_map = _drug_col_map("range_low_col")
    range_high_col_map = _drug_col_map("range_high_col")

    # Route "Fixed value" mode: route_col is null and route_concepts carries exactly one
    # entry (see UnitMappingSection's identical fixed-mode convention) — route_mapping.csv
    # has nothing to look up in that case, so apply the single concept to every row directly.
    route_fixed_map: dict[str, int] = {}
    for k, d in (project.concept_decisions or {}).items():
        if not isinstance(d, dict):
            continue
        rm = d.get("route_mapping") or {}
        if rm.get("route_col"):
            continue
        ids = [v for v in (rm.get("route_concepts") or {}).values() if isinstance(v, int) and v > 0]
        if len(ids) == 1:
            route_fixed_map[k.lower()] = ids[0]

    # Modifier (Procedure Occurrence) "Fixed value" mode: same convention as Route —
    # modifier_col is null and modifier_concepts carries exactly one entry, applied to
    # every row directly since modifier_mapping.csv has nothing to look up in that case.
    modifier_fixed_map: dict[str, int] = {}
    for k, d in (project.concept_decisions or {}).items():
        if not isinstance(d, dict):
            continue
        mm_dec = d.get("modifier_mapping") or {}
        if mm_dec.get("modifier_col"):
            continue
        ids = [v for v in (mm_dec.get("modifier_concepts") or {}).values() if isinstance(v, int) and v > 0]
        if len(ids) == 1:
            modifier_fixed_map[k.lower()] = ids[0]

    # Condition Status (Condition Occurrence) "Fixed value" mode: same convention as
    # Modifier — condition_status_col is null and condition_status_concepts carries
    # exactly one entry, applied to every row directly.
    condition_status_fixed_map: dict[str, int] = {}
    for k, d in (project.concept_decisions or {}).items():
        if not isinstance(d, dict):
            continue
        csm_dec = d.get("condition_status_mapping") or {}
        if csm_dec.get("condition_status_col"):
            continue
        ids = [v for v in (csm_dec.get("condition_status_concepts") or {}).values() if isinstance(v, int) and v > 0]
        if len(ids) == 1:
            condition_status_fixed_map[k.lower()] = ids[0]

    # Qualifier (Observation) "Fixed value" mode: same convention as Modifier/Condition
    # Status — qualifier_col is null and qualifier_concepts carries exactly one entry,
    # applied to every row directly.
    qualifier_fixed_map: dict[str, int] = {}
    for k, d in (project.concept_decisions or {}).items():
        if not isinstance(d, dict):
            continue
        qm_dec = d.get("qualifier_mapping") or {}
        if qm_dec.get("qualifier_col"):
            continue
        ids = [v for v in (qm_dec.get("qualifier_concepts") or {}).values() if isinstance(v, int) and v > 0]
        if len(ids) == 1:
            qualifier_fixed_map[k.lower()] = ids[0]

    # Operator (Measurement) "Fixed value" mode: same convention as Qualifier —
    # operator_col is null and operator_concepts carries exactly one entry, applied to
    # every row directly since operator_mapping.csv has nothing to look up in that case.
    operator_fixed_map: dict[str, int] = {}
    for k, d in (project.concept_decisions or {}).items():
        if not isinstance(d, dict):
            continue
        om_dec = d.get("operator_mapping") or {}
        if om_dec.get("operator_col"):
            continue
        ids = [v for v in (om_dec.get("operator_concepts") or {}).values() if isinstance(v, int) and v > 0]
        if len(ids) == 1:
            operator_fixed_map[k.lower()] = ids[0]

    # Type: fixed drug_type_concept_id per variable, falling back to the pipeline default.
    type_fixed_map: dict[str, int] = {
        k.lower(): int(d["type_concept_id"])
        for k, d in (project.concept_decisions or {}).items()
        if isinstance(d, dict) and d.get("type_concept_id")
    }

    # Start/End datetime: sibling columns overriding the visit-derived start date and
    # filling the otherwise-always-empty end date, parsed with a per-variable strptime
    # format shared by both columns.
    start_dt_col_map = _drug_col_map("start_datetime_col")
    end_dt_col_map = _drug_col_map("end_datetime_col")
    datetime_format_map: dict[str, str] = {
        k.lower(): d["datetime_format"]
        for k, d in (project.concept_decisions or {}).items()
        if isinstance(d, dict) and d.get("datetime_format")
    }

    qcm_repr = repr(quantity_col_map)
    dsc_repr = repr(days_supply_col_map)
    rfc_repr = repr(refills_col_map)
    sgc_repr = repr(sig_col_map)
    lnc_repr = repr(lot_number_col_map)
    src_repr = repr(stop_reason_col_map)
    rlc_repr = repr(range_low_col_map)
    rhc_repr = repr(range_high_col_map)
    rfx_repr = repr(route_fixed_map)
    mfx_repr = repr(modifier_fixed_map)
    csfx_repr = repr(condition_status_fixed_map)
    qfx_repr = repr(qualifier_fixed_map)
    ofx_repr = repr(operator_fixed_map)
    tfx_repr = repr(type_fixed_map)
    sdc_repr = repr(start_dt_col_map)
    edc_repr = repr(end_dt_col_map)
    dtf_repr = repr(datetime_format_map)

    # Inner per-row processing body — indented 8 spaces (inside the per-file for-loop).
    inner_rows = (
        "        for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
        "            try:\n"
        "\n"
        "                # Person identifier\n"
        + "\n".join("    " + ln for ln in psv_lines.rstrip("\n").split("\n")) + "\n"
        + "                person_id = person_lookup.get(person_source_value)\n"
        "                if person_id is None:\n"
        "                    print(f'WARNING: skipping row {_src_idx} — person \"{person_source_value}\" not found in person.csv')\n"
        "                    continue\n"
        "\n"
        "                # Determine the visit label for this entire row (once, outside the variable loop)\n"
        "                if VISIT_SOURCE_COL:\n"
        "                    _row_vsv_raw = str(row.get(VISIT_SOURCE_COL, '')).strip()\n"
        "                    _row_visit_label = _row_vsv_raw if (_row_vsv_raw and _row_vsv_raw != 'nan') else None\n"
        "                    _row_date_col = DEFAULT_DATE_COL\n"
        "                    _row_date_fmt = DEFAULT_DATE_FORMAT\n"
        "                elif AUTO_NUMBER_VISITS:\n"
        "                    _stem_visit_counters[person_source_value] = _stem_visit_counters.get(person_source_value, 0) + 1\n"
        "                    _row_visit_label = f'visit{_stem_visit_counters[person_source_value]}'\n"
        "                    _row_date_col = DEFAULT_DATE_COL\n"
        "                    _row_date_fmt = DEFAULT_DATE_FORMAT\n"
        "                else:\n"
        "                    _row_visit_label = None  # resolved per-variable below\n"
        "                    _row_date_col = None\n"
        "                    _row_date_fmt = None\n"
        "\n"
        "                # Iterate over each clinical variable in this row\n"
        "                for variable in df.columns:\n"
        "                    try:\n"
        "                        _lc = variable.lower()\n"
        "                        # Only process columns the user explicitly mapped in\n"
        "                        # Concepts step. Structural fields (visit_*_date, patient_id,\n"
        "                        # death_date, gender, …) are absent from STEM_VARIABLES.\n"
        "                        if _lc not in STEM_VARIABLES:\n"
        "                            continue\n"
        "                        if VISIT_SOURCE_COL or AUTO_NUMBER_VISITS:\n"
        "                            if not _row_visit_label:\n"
        "                                continue\n"
        "                            visit_label = _row_visit_label\n"
        "                            date_col = _row_date_col\n"
        "                            date_fmt = _row_date_fmt\n"
        "                        else:\n"
        "                            visit_label = VARIABLE_VISIT_MAP.get(_lc)\n"
        "                            if visit_label is None:\n"
        "                                continue\n"
        "                            date_info = VISIT_DATE_INFO.get(visit_label, {})\n"
        "                            date_col = date_info.get('date_col', '')\n"
        "                            date_fmt = date_info.get('date_format', '%Y-%m-%d')\n"
        "\n"
        "                        raw_value = row.get(variable)\n"
        "                        if pd.isnull(raw_value) or str(raw_value).strip() in ('', 'nan'):\n"
        "                            continue\n"
        "\n"
        "                        start_date = None\n"
        "                        start_datetime = None\n"
        "                        # Drug Exposure (domain_id 3) never inherits its start date from the\n"
        "                        # visit's own date column — only from Start/End datetime below (or it\n"
        "                        # stays unset). Every other domain keeps the existing visit-derived date.\n"
        "                        if date_col and domain_map.get(variable.lower()) != 3:\n"
        "                            _raw_date = row.get(date_col)\n"
        "                            _date_str = str(_raw_date).strip() if pd.notnull(_raw_date) else ''\n"
        "                            if not _date_str or _date_str == 'nan':\n"
        "                                continue\n"
        "                            _dt = datetime.strptime(_date_str, date_fmt)\n"
        "                            start_date = _dt.date()\n"
        "                            start_datetime = _dt\n"
        "\n"
        "                        # Start/End datetime (Drug Exposure): sibling columns override the\n"
        "                        # visit-derived start date and fill the otherwise-empty end date.\n"
        "                        _dt_fmt = DATETIME_FORMAT_MAP.get(variable.lower()) or '%Y-%m-%d'\n"
        "                        _start_dt_col = START_DATETIME_COL_MAP.get(variable.lower())\n"
        "                        if _start_dt_col:\n"
        "                            _raw_start_dt = row.get(_start_dt_col)\n"
        "                            if pd.notnull(_raw_start_dt) and str(_raw_start_dt).strip() not in ('', 'nan'):\n"
        "                                try:\n"
        "                                    _parsed_start = datetime.strptime(str(_raw_start_dt).strip(), _dt_fmt)\n"
        "                                    start_date = _parsed_start.date()\n"
        "                                    start_datetime = _parsed_start\n"
        "                                except ValueError:\n"
        "                                    print(f'WARNING: variable {variable!r} — start datetime {_raw_start_dt!r} does not match format {_dt_fmt!r}; keeping visit-derived date')\n"
        "                            else:\n"
        "                                print(f'WARNING: variable {variable!r} — start datetime column {_start_dt_col!r} is empty for this row; keeping visit-derived date')\n"
        "                        end_date = None\n"
        "                        end_datetime = None\n"
        "                        _end_dt_col = END_DATETIME_COL_MAP.get(variable.lower())\n"
        "                        if _end_dt_col:\n"
        "                            _raw_end_dt = row.get(_end_dt_col)\n"
        "                            if pd.notnull(_raw_end_dt) and str(_raw_end_dt).strip() not in ('', 'nan'):\n"
        "                                try:\n"
        "                                    _parsed_end = datetime.strptime(str(_raw_end_dt).strip(), _dt_fmt)\n"
        "                                    end_date = _parsed_end.date()\n"
        "                                    end_datetime = _parsed_end\n"
        "                                except ValueError:\n"
        "                                    print(f'WARNING: variable {variable!r} — end datetime {_raw_end_dt!r} does not match format {_dt_fmt!r}')\n"
        "                            else:\n"
        "                                print(f'WARNING: variable {variable!r} — end datetime column {_end_dt_col!r} is empty for this row; end date left unset')\n"
        "\n"
        "                        label_norm = visit_label.lower().replace(' ', '_')\n"
        "                        visit_record_source_value = f'{person_source_value}-{label_norm}'\n"
        "                        visit_occurrence_id = lookup_visit_occurrence_id(visit_record_source_value)\n"
        "                        if visit_occurrence_id is None:\n"
        "                            continue   # helper already logged the miss\n"
        "\n"
        "                        mapped = lookup_concept(variable, raw_value, var_map, val_map, var_val_map)\n"
        "                        concept_id = mapped['concept_id']\n"
        "                        # Skip when neither the variable nor the (variable, value) pair\n"
        "                        # resolved to a concept — e.g. an unmapped value under a\n"
        "                        # `map_values` strategy. No concept = no clinical event.\n"
        "                        if not concept_id:\n"
        "                            continue\n"
        "                        value_as_concept_id = mapped['value_as_concept_id']\n"
        "                        value_as_number = mapped['value_as_number']\n"
        "                        unit_concept_id = mapped['unit_concept_id']\n"
        "                        unit_source_value = None\n"
        "                        _unit_col = unit_col_map.get(variable.lower())\n"
        "                        if _unit_col:\n"
        "                            _raw_unit = row.get(_unit_col)\n"
        "                            if pd.notnull(_raw_unit) and str(_raw_unit).strip() not in ('', 'nan'):\n"
        "                                unit_source_value = str(_raw_unit).strip()\n"
        "                                _looked_up = unit_concept_map.get((variable.lower(), unit_source_value))\n"
        "                                if _looked_up is not None:\n"
        "                                    unit_concept_id = _looked_up\n"
        "                                else:\n"
        "                                    _info(f'INFO: variable {variable!r} — unit value {unit_source_value!r} not in unit_concept_map; unit_concept_id unchanged')\n"
        "                        operator_concept_id = None\n"
        "                        operator_source_value = None\n"
        "                        value_as_string = mapped.get('value_as_string')\n"
        "\n"
        "                        # Drug Exposure (domain_id 3) fields pulled from sibling columns —\n"
        "                        # harmless no-ops for other domains since their *_COL_MAP is empty\n"
        "                        # unless the user configured it for this variable.\n"
        "                        _raw_qty = _col_str(row, QUANTITY_COL_MAP, variable)\n"
        "                        quantity = None\n"
        "                        if _raw_qty is not None:\n"
        "                            try:\n"
        "                                quantity = float(_raw_qty)\n"
        "                            except (ValueError, TypeError):\n"
        "                                _info(f'INFO: variable {variable!r} — quantity value {_raw_qty!r} is not numeric; quantity left unset')\n"
        "                        days_supply = _col_int(row, DAYS_SUPPLY_COL_MAP, variable)\n"
        "                        refills = _col_int(row, REFILLS_COL_MAP, variable)\n"
        "                        sig = _col_str(row, SIG_COL_MAP, variable)\n"
        "                        lot_number = _col_str(row, LOT_NUMBER_COL_MAP, variable)\n"
        "                        stop_reason = _col_str(row, STOP_REASON_COL_MAP, variable)\n"
        "\n"
        "                        # Reference range (Measurement) fields pulled from sibling columns —\n"
        "                        # same no-op-elsewhere convention as the Drug Exposure fields above.\n"
        "                        range_low = _col_float(row, RANGE_LOW_COL_MAP, variable)\n"
        "                        range_high = _col_float(row, RANGE_HIGH_COL_MAP, variable)\n"
        "\n"
        "                        # Route (Drug Exposure): per-row lookup via route_mapping.csv, same\n"
        "                        # pattern as the unit_concept_id resolution above.\n"
        "                        route_concept_id = None\n"
        "                        route_source_value = None\n"
        "                        _route_col = route_col_map.get(variable.lower())\n"
        "                        if _route_col:\n"
        "                            _raw_route = row.get(_route_col)\n"
        "                            if pd.notnull(_raw_route) and str(_raw_route).strip() not in ('', 'nan'):\n"
        "                                route_source_value = str(_raw_route).strip()\n"
        "                                _looked_up_route = route_concept_map.get((variable.lower(), route_source_value))\n"
        "                                if _looked_up_route is not None:\n"
        "                                    route_concept_id = _looked_up_route\n"
        "                                else:\n"
        "                                    _info(f'INFO: variable {variable!r} — route value {route_source_value!r} not in route_concept_map; route_concept_id unset')\n"
        "                        if route_concept_id is None:\n"
        "                            route_concept_id = ROUTE_FIXED_MAP.get(variable.lower())\n"
        "\n"
        "                        # Modifier (Procedure Occurrence): per-row lookup via modifier_mapping.csv,\n"
        "                        # same pattern as the unit_concept_id resolution above.\n"
        "                        modifier_concept_id = None\n"
        "                        modifier_source_value = None\n"
        "                        _modifier_col = modifier_col_map.get(variable.lower())\n"
        "                        if _modifier_col:\n"
        "                            _raw_modifier = row.get(_modifier_col)\n"
        "                            if pd.notnull(_raw_modifier) and str(_raw_modifier).strip() not in ('', 'nan'):\n"
        "                                modifier_source_value = str(_raw_modifier).strip()\n"
        "                                _looked_up_modifier = modifier_concept_map.get((variable.lower(), modifier_source_value))\n"
        "                                if _looked_up_modifier is not None:\n"
        "                                    modifier_concept_id = _looked_up_modifier\n"
        "                                else:\n"
        "                                    _info(f'INFO: variable {variable!r} — modifier value {modifier_source_value!r} not in modifier_concept_map; modifier_concept_id unset')\n"
        "                        if modifier_concept_id is None:\n"
        "                            modifier_concept_id = MODIFIER_FIXED_MAP.get(variable.lower())\n"
        "\n"
        "                        # Condition Status (Condition Occurrence): per-row lookup via\n"
        "                        # condition_status_mapping.csv, same pattern as unit_concept_id above.\n"
        "                        condition_status_concept_id = None\n"
        "                        condition_status_source_value = None\n"
        "                        _condition_status_col = condition_status_col_map.get(variable.lower())\n"
        "                        if _condition_status_col:\n"
        "                            _raw_condition_status = row.get(_condition_status_col)\n"
        "                            if pd.notnull(_raw_condition_status) and str(_raw_condition_status).strip() not in ('', 'nan'):\n"
        "                                condition_status_source_value = str(_raw_condition_status).strip()\n"
        "                                _looked_up_condition_status = condition_status_concept_map.get((variable.lower(), condition_status_source_value))\n"
        "                                if _looked_up_condition_status is not None:\n"
        "                                    condition_status_concept_id = _looked_up_condition_status\n"
        "                                else:\n"
        "                                    _info(f'INFO: variable {variable!r} — condition status value {condition_status_source_value!r} not in condition_status_concept_map; condition_status_concept_id unset')\n"
        "                        if condition_status_concept_id is None:\n"
        "                            condition_status_concept_id = CONDITION_STATUS_FIXED_MAP.get(variable.lower())\n"
        "\n"
        "                        # Qualifier (Observation): per-row lookup via qualifier_mapping.csv,\n"
        "                        # same pattern as the unit_concept_id resolution above.\n"
        "                        qualifier_concept_id = None\n"
        "                        qualifier_source_value = None\n"
        "                        _qualifier_col = qualifier_col_map.get(variable.lower())\n"
        "                        if _qualifier_col:\n"
        "                            _raw_qualifier = row.get(_qualifier_col)\n"
        "                            if pd.notnull(_raw_qualifier) and str(_raw_qualifier).strip() not in ('', 'nan'):\n"
        "                                qualifier_source_value = str(_raw_qualifier).strip()\n"
        "                                _looked_up_qualifier = qualifier_concept_map.get((variable.lower(), qualifier_source_value))\n"
        "                                if _looked_up_qualifier is not None:\n"
        "                                    qualifier_concept_id = _looked_up_qualifier\n"
        "                                else:\n"
        "                                    _info(f'INFO: variable {variable!r} — qualifier value {qualifier_source_value!r} not in qualifier_concept_map; qualifier_concept_id unset')\n"
        "                        if qualifier_concept_id is None:\n"
        "                            qualifier_concept_id = QUALIFIER_FIXED_MAP.get(variable.lower())\n"
        "\n"
        "                        # Operator (Measurement): per-row lookup via operator_mapping.csv,\n"
        "                        # same pattern as the unit_concept_id resolution above.\n"
        "                        _operator_col = operator_col_map.get(variable.lower())\n"
        "                        if _operator_col:\n"
        "                            _raw_operator = row.get(_operator_col)\n"
        "                            if pd.notnull(_raw_operator) and str(_raw_operator).strip() not in ('', 'nan'):\n"
        "                                operator_source_value = str(_raw_operator).strip()\n"
        "                                _looked_up_operator = operator_concept_map.get((variable.lower(), operator_source_value))\n"
        "                                if _looked_up_operator is not None:\n"
        "                                    operator_concept_id = _looked_up_operator\n"
        "                                else:\n"
        "                                    _info(f'INFO: variable {variable!r} — operator value {operator_source_value!r} not in operator_concept_map; operator_concept_id unset')\n"
        "                        if operator_concept_id is None:\n"
        "                            operator_concept_id = OPERATOR_FIXED_MAP.get(variable.lower())\n"
        "\n"
        "                        # Dose unit (Drug Exposure): reuses the generic unit_concept_id/\n"
        "                        # unit_source_value resolved above from unit_mapping.csv — no\n"
        "                        # separate mechanism needed.\n"
        "                        dose_unit_source_value = unit_source_value\n"
        "\n"
        "                        # Type (Drug Exposure): fixed drug_type_concept_id per variable,\n"
        "                        # falling back to the pipeline default used by every other domain.\n"
        "                        type_concept_id = TYPE_FIXED_MAP.get(variable.lower(), 32879)\n"
        "\n"
        "                        override = OVERRIDE_MAP.get(variable.lower())\n"
        "                        if override:\n"
        "                            if override.get('field') == 'unit_concept_id' and override.get('value') is not None:\n"
        "                                unit_concept_id = int(override['value'])\n"
        "                                if override.get('unit_source_value') is not None:\n"
        "                                    unit_source_value = str(override['unit_source_value'])\n"
        "                                    dose_unit_source_value = unit_source_value\n"
        "                            if override.get('value_as_string') is not None:\n"
        "                                value_as_string = str(override['value_as_string'])\n"
        "                            if override.get('value_map'):\n"
        "                                vmap_entry = override['value_map'].get(str(raw_value))\n"
        "                                if vmap_entry:\n"
        "                                    for _k, _v in vmap_entry.items():\n"
        "                                        if _k == 'operator_concept_id':\n"
        "                                            operator_concept_id = int(_v)\n"
        "                                        elif _k == 'value_as_number':\n"
        "                                            value_as_number = float(_v)\n"
        "                                        elif _k == 'value_as_string':\n"
        "                                            value_as_string = str(_v)\n"
        "                                        elif _k == 'unit_concept_id':\n"
        "                                            unit_concept_id = int(_v)\n"
        "\n"
        "                        record_source_value = f'{person_source_value}-{variable}'\n"
        "\n"
        "                        # Appends one stem row from the fields computed above, letting a\n"
        "                        # keyword override any of them for this call only. AI-patched\n"
        "                        # per-variable logic (Extra Instructions) MUST route through this\n"
        "                        # helper instead of writing its own rows.append({...}) — that keeps\n"
        "                        # the ~35-field dict defined in exactly one place, and a variable\n"
        "                        # needing several records (e.g. one per comma-separated value) can\n"
        "                        # just call _append_row(...) once per record inside a loop.\n"
        "                        def _append_row(**overrides):\n"
        "                            nonlocal stem_id\n"
        "                            _row_fields = dict(\n"
        "                                id=stem_id, domain_id=domain_map.get(variable.lower(), ''), person_id=person_id,\n"
        "                                visit_occurrence_id=visit_occurrence_id, visit_detail_id=None,\n"
        "                                concept_id=(concept_id if concept_id else 0),\n"
        "                                start_date=start_date, start_datetime=start_datetime, end_date=end_date, end_datetime=end_datetime,\n"
        "                                type_concept_id=type_concept_id, operator_concept_id=operator_concept_id,\n"
        "                                operator_source_value=operator_source_value, value_as_number=value_as_number,\n"
        "                                value_as_string=value_as_string, value_as_concept_id=value_as_concept_id,\n"
        "                                unit_concept_id=unit_concept_id, unit_source_value=unit_source_value,\n"
        "                                range_low=range_low, range_high=range_high, provider_id=None,\n"
        "                                modifier_concept_id=modifier_concept_id, modifier_source_value=modifier_source_value,\n"
        "                                condition_status_concept_id=condition_status_concept_id,\n"
        "                                condition_status_source_value=condition_status_source_value,\n"
        "                                qualifier_concept_id=qualifier_concept_id, qualifier_source_value=qualifier_source_value,\n"
        "                                quantity=quantity, value_source_value=str(raw_value), source_value=variable,\n"
        "                                source_concept_id=None, record_source_value=record_source_value,\n"
        "                                days_supply=days_supply, refills=refills, sig=sig, lot_number=lot_number, stop_reason=stop_reason,\n"
        "                                route_source_value=route_source_value, route_concept_id=route_concept_id,\n"
        "                                dose_unit_source_value=dose_unit_source_value,\n"
        "                            )\n"
        "                            _row_fields.update(overrides)\n"
        "                            rows.append(_row_fields)\n"
        "                            stem_id += 1\n"
        "\n"
        "                        # <<< AI-PATCH INSERTION POINT >>>\n"
        "                        # Per-variable custom logic (Extra Instructions) is inserted here,\n"
        "                        # after every field above has its final value, and calls\n"
        "                        # _append_row(field=value, ...) — never inline rows.append({...}).\n"
        "                        _append_row()\n"
        "                    except Exception as _var_exc:\n"
        "                        print(f'WARNING: skipping variable {variable!r} for person {person_source_value} — {_var_exc}')\n"
        "            except Exception as e:\n"
        "                print(f'WARNING: skipping row — {e}')\n"
    )

    return (
        "import os\n"
        "import logging\n"
        "import pandas as pd\n"
        "from datetime import datetime\n"
        "\n"
        "\n"
        "from etl_runtime import _info, build_id_lookup\n"
        "\n"
        "\n"
        "def _col_str(row, col_map, variable):\n"
        "    \"\"\"Read a sibling column configured in a *_COL_MAP dict as a stripped string, or None.\"\"\"\n"
        "    col = col_map.get(variable.lower())\n"
        "    if not col:\n"
        "        return None\n"
        "    raw = row.get(col)\n"
        "    if pd.isnull(raw) or str(raw).strip() in ('', 'nan'):\n"
        "        return None\n"
        "    return str(raw).strip()\n"
        "\n"
        "\n"
        "def _col_int(row, col_map, variable):\n"
        "    \"\"\"Like _col_str but parsed as int; logs and returns None on a non-numeric value.\"\"\"\n"
        "    raw = _col_str(row, col_map, variable)\n"
        "    if raw is None:\n"
        "        return None\n"
        "    try:\n"
        "        return int(float(raw))\n"
        "    except (ValueError, TypeError):\n"
        "        _info(f'INFO: variable {variable!r} — column value {raw!r} is not numeric; field left unset')\n"
        "        return None\n"
        "\n"
        "\n"
        "def _col_float(row, col_map, variable):\n"
        "    \"\"\"Like _col_str but parsed as float; logs and returns None on a non-numeric value.\"\"\"\n"
        "    raw = _col_str(row, col_map, variable)\n"
        "    if raw is None:\n"
        "        return None\n"
        "    try:\n"
        "        return float(raw)\n"
        "    except (ValueError, TypeError):\n"
        "        _info(f'INFO: variable {variable!r} — column value {raw!r} is not numeric; field left unset')\n"
        "        return None\n"
        "\n"
        "\n"
        "# --- Logging setup ---\n"
        "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "logger = logging.getLogger(__name__)\n"
        "\n"
        "\n"
        "# --- Module-level constants ---\n"
        f"VISIT_SOURCE_COL     = {repr(visit_source_col)}   # column that carries visit label (multi-row mode)\n"
        f"AUTO_NUMBER_VISITS   = {repr(auto_number_visits)}  # number visits visit1/visit2/... when no identifier col\n"
        f"DEFAULT_DATE_COL     = {repr(default_date_col)}\n"
        f"DEFAULT_DATE_FORMAT  = {repr(default_date_format)}\n"
        "\n"
        f"VISIT_DATE_INFO = {vdi_repr}\n"
        "\n"
        "# Maps each variable (lowercase) to its visit label as configured in the Stem Table step.\n"
        f"VARIABLE_VISIT_MAP = {vvm_repr}\n"
        "\n"
        f"SPECIAL_OVERRIDES = {so_repr}\n"
        "\n"
        "OVERRIDE_MAP = {o['variable'].lower(): o for o in SPECIAL_OVERRIDES}\n"
        "\n"
        "# Drug Exposure (domain_id 3): variable (lowercase) → sibling column supplying each field.\n"
        f"QUANTITY_COL_MAP = {qcm_repr}\n"
        f"DAYS_SUPPLY_COL_MAP = {dsc_repr}\n"
        f"REFILLS_COL_MAP = {rfc_repr}\n"
        f"SIG_COL_MAP = {sgc_repr}\n"
        f"LOT_NUMBER_COL_MAP = {lnc_repr}\n"
        f"STOP_REASON_COL_MAP = {src_repr}\n"
        "# Measurement (domain_id 1): variable (lowercase) → sibling column supplying each field.\n"
        f"RANGE_LOW_COL_MAP = {rlc_repr}\n"
        f"RANGE_HIGH_COL_MAP = {rhc_repr}\n"
        "# Fixed (per-variable, not per-row) fallbacks — used when the column-based\n"
        "# mapping/lookup for that field has nothing configured.\n"
        f"ROUTE_FIXED_MAP = {rfx_repr}\n"
        f"MODIFIER_FIXED_MAP = {mfx_repr}\n"
        f"CONDITION_STATUS_FIXED_MAP = {csfx_repr}\n"
        f"QUALIFIER_FIXED_MAP = {qfx_repr}\n"
        f"OPERATOR_FIXED_MAP = {ofx_repr}\n"
        f"TYPE_FIXED_MAP = {tfx_repr}\n"
        f"START_DATETIME_COL_MAP = {sdc_repr}\n"
        f"END_DATETIME_COL_MAP = {edc_repr}\n"
        f"DATETIME_FORMAT_MAP = {dtf_repr}\n"
        "\n"
        "visit_occurrence_id_lookup = None  # built lazily on first lookup call\n"
        "\n"
        "# Each entry: path, delimiter, encoding,\n"
        "# variables (list → converted to set below for O(1) lookup).\n"
        f"SOURCE_FILES = {source_files_repr}\n"
        "for _sf in SOURCE_FILES:\n"
        "    _sf['variables'] = set(_sf['variables'])\n"
        "\n"
        "\n"
        "def _load_csv(path):\n"
        "    if not path:\n"
        "        return pd.DataFrame()\n"
        "    try:\n"
        "        return pd.read_csv(path, sep=',', encoding='utf-8')\n"
        "    except FileNotFoundError:\n"
        "        return pd.DataFrame()\n"
        "\n"
        "\n"
        "def lookup_concept(variable, value, var_map, val_map, var_val_map):\n"
        "    key_vv = (variable.lower(), str(value))\n"
        "    if key_vv in var_val_map:\n"
        "        return {'concept_id': var_val_map[key_vv], 'value_as_concept_id': None, 'value_as_number': None, 'value_as_string': None, 'unit_concept_id': None}\n"
        "    concept_id = var_map.get(variable.lower(), 0)\n"
        "    value_as_concept_id = val_map.get(key_vv)\n"
        "    if value_as_concept_id is not None:\n"
        "        return {'concept_id': concept_id, 'value_as_concept_id': int(value_as_concept_id), 'value_as_number': None, 'value_as_string': None, 'unit_concept_id': None}\n"
        "    try:\n"
        "        return {'concept_id': concept_id, 'value_as_concept_id': None, 'value_as_number': float(value), 'value_as_string': None, 'unit_concept_id': None}\n"
        "    except (ValueError, TypeError):\n"
        "        # Non-numeric value under map_variable/map_both — preserve it as text\n"
        "        # instead of silently dropping it.\n"
        "        return {'concept_id': concept_id, 'value_as_concept_id': None, 'value_as_number': None, 'value_as_string': str(value), 'unit_concept_id': None}\n"
        "\n"
        "\n"
        "def create_visit_lookup():\n"
        "    \"\"\"Build {record_source_value -> visit_occurrence_id} from visit_occurrence.csv.\"\"\"\n"
        "    global visit_occurrence_id_lookup\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "    visit_occurrence_id_lookup = build_id_lookup(output_dir, 'visit_occurrence.csv', 'record_source_value', 'visit_occurrence_id') if output_dir else {}\n"
        "\n"
        "\n"
        "def lookup_visit_occurrence_id(visit_record_source_value):\n"
        "    \"\"\"Mirror of wrapper.lookup_visit_occurrence_id from the OHDSI reference.\n"
        "    Returns the visit_occurrence_id for the given key, or None if the key\n"
        "    isn't present (the miss is logged so the user can audit which records\n"
        "    have no visit attached).\n"
        "    \"\"\"\n"
        "    global visit_occurrence_id_lookup\n"
        "    if visit_occurrence_id_lookup is None:\n"
        "        create_visit_lookup()\n"
        "    if visit_record_source_value not in visit_occurrence_id_lookup:\n"
        "        logger.info('Visit record_source_value \"{}\" not found in lookup.'.format(visit_record_source_value))\n"
        "        return None\n"
        "    return visit_occurrence_id_lookup.get(visit_record_source_value)\n"
        "\n"
        "\n"
        "def main():\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        "    person_lookup = build_id_lookup(output_dir, 'person.csv', 'person_source_value', 'person_id')\n"
        "\n"
        "    # visit_lookup is built lazily on first lookup_visit_occurrence_id call\n"
        "\n"
        "    # --- Load mapping CSVs ---\n"
        "    vm = _load_csv(os.environ.get('ETL_MAPPING_variable_mapping', ''))\n"
        "    vl = _load_csv(os.environ.get('ETL_MAPPING_value_mapping', ''))\n"
        "    vv = _load_csv(os.environ.get('ETL_MAPPING_variable_value_mapping', ''))\n"
        "    um = _load_csv(os.environ.get('ETL_MAPPING_unit_mapping', ''))\n"
        "    rm = _load_csv(os.environ.get('ETL_MAPPING_route_mapping', ''))\n"
        "    mm = _load_csv(os.environ.get('ETL_MAPPING_modifier_mapping', ''))\n"
        "    csm = _load_csv(os.environ.get('ETL_MAPPING_condition_status_mapping', ''))\n"
        "    qm = _load_csv(os.environ.get('ETL_MAPPING_qualifier_mapping', ''))\n"
        "    om = _load_csv(os.environ.get('ETL_MAPPING_operator_mapping', ''))\n"
        "\n"
        "    # Build in-memory concept lookup dicts from mapping CSVs\n"
        "    var_map     = {r['variable_source_code'].lower(): int(r['target_concept_id']) for _, r in vm.iterrows()} if not vm.empty else {}\n"
        "    val_map     = {(r['variable_source_code'].lower(), str(r['value_source_code'])): int(r['target_concept_id']) for _, r in vl.iterrows()} if not vl.empty else {}\n"
        "    var_val_map = {(r['variable_source_code'].lower(), str(r['value_source_code'])): int(r['target_concept_id']) for _, r in vv.iterrows()} if not vv.empty else {}\n"
        "\n"
        "    # domain_map: variable → OMOP domain_id integer\n"
        "    domain_map = {}\n"
        "    if not vm.empty and 'domain_id' in vm.columns:\n"
        "        for _, r in vm.iterrows():\n"
        "            if pd.notna(r['domain_id']) and r['domain_id'] != '':\n"
        "                domain_map[r['variable_source_code'].lower()] = int(r['domain_id'])\n"
        "    if not vv.empty and 'domain_id' in vv.columns:\n"
        "        for _, r in vv.iterrows():\n"
        "            k = r['variable_source_code'].lower()\n"
        "            if k not in domain_map and pd.notna(r['domain_id']) and r['domain_id'] != '':\n"
        "                domain_map[k] = int(r['domain_id'])\n"
        "\n"
        "    # unit_col_map:     variable → source column holding the unit string\n"
        "    # unit_concept_map: (variable, unit_source_value) → unit_concept_id\n"
        "    unit_col_map     = {}\n"
        "    unit_concept_map = {}\n"
        "    if not um.empty:\n"
        "        for _, r in um.iterrows():\n"
        "            v = str(r['variable_source_code']).lower()\n"
        "            unit_col_map[v]                               = str(r['unit_col'])\n"
        "            unit_concept_map[(v, str(r['unit_source_value']))] = int(r['unit_concept_id'])\n"
        "\n"
        "    # route_col_map:     variable → source column holding the route string\n"
        "    # route_concept_map: (variable, route_source_value) → route_concept_id\n"
        "    route_col_map     = {}\n"
        "    route_concept_map = {}\n"
        "    if not rm.empty:\n"
        "        for _, r in rm.iterrows():\n"
        "            v = str(r['variable_source_code']).lower()\n"
        "            route_col_map[v]                                 = str(r['route_col'])\n"
        "            route_concept_map[(v, str(r['route_source_value']))] = int(r['route_concept_id'])\n"
        "\n"
        "    # modifier_col_map:     variable → source column holding the modifier string\n"
        "    # modifier_concept_map: (variable, modifier_source_value) → modifier_concept_id\n"
        "    modifier_col_map     = {}\n"
        "    modifier_concept_map = {}\n"
        "    if not mm.empty:\n"
        "        for _, r in mm.iterrows():\n"
        "            v = str(r['variable_source_code']).lower()\n"
        "            modifier_col_map[v]                                       = str(r['modifier_col'])\n"
        "            modifier_concept_map[(v, str(r['modifier_source_value']))] = int(r['modifier_concept_id'])\n"
        "\n"
        "    # condition_status_col_map:     variable → source column holding the condition status string\n"
        "    # condition_status_concept_map: (variable, condition_status_source_value) → condition_status_concept_id\n"
        "    condition_status_col_map     = {}\n"
        "    condition_status_concept_map = {}\n"
        "    if not csm.empty:\n"
        "        for _, r in csm.iterrows():\n"
        "            v = str(r['variable_source_code']).lower()\n"
        "            condition_status_col_map[v]                                             = str(r['condition_status_col'])\n"
        "            condition_status_concept_map[(v, str(r['condition_status_source_value']))] = int(r['condition_status_concept_id'])\n"
        "\n"
        "    # qualifier_col_map:     variable → source column holding the qualifier string\n"
        "    # qualifier_concept_map: (variable, qualifier_source_value) → qualifier_concept_id\n"
        "    qualifier_col_map     = {}\n"
        "    qualifier_concept_map = {}\n"
        "    if not qm.empty:\n"
        "        for _, r in qm.iterrows():\n"
        "            v = str(r['variable_source_code']).lower()\n"
        "            qualifier_col_map[v]                                       = str(r['qualifier_col'])\n"
        "            qualifier_concept_map[(v, str(r['qualifier_source_value']))] = int(r['qualifier_concept_id'])\n"
        "\n"
        "    # operator_col_map:     variable → source column holding the operator string\n"
        "    # operator_concept_map: (variable, operator_source_value) → operator_concept_id\n"
        "    operator_col_map     = {}\n"
        "    operator_concept_map = {}\n"
        "    if not om.empty:\n"
        "        for _, r in om.iterrows():\n"
        "            v = str(r['variable_source_code']).lower()\n"
        "            operator_col_map[v]                                     = str(r['operator_col'])\n"
        "            operator_concept_map[(v, str(r['operator_source_value']))] = int(r['operator_concept_id'])\n"
        "\n"
        "    # --- Process rows (one source file at a time) ---\n"
        "    stem_id = 1\n"
        "    rows    = []\n"
        "    _stem_visit_counters = {}  # shared across files for consistent visit auto-numbering\n"
        "\n"
        "    for _sf in SOURCE_FILES:\n"
        "        _sp = _sf['path']\n"
        "        STEM_VARIABLES = _sf['variables']\n"
        "        df = pd.read_csv(_sp, delimiter=_sf['delimiter'], encoding=_sf['encoding'])\n"
        "\n"
        + inner_rows
        + "\n"
        "    # --- Write output ---\n"
        "    STEM_COLUMNS = [\n"
        "        'id', 'domain_id', 'person_id', 'visit_occurrence_id', 'visit_detail_id',\n"
        "        'concept_id', 'start_date', 'start_datetime', 'end_date', 'end_datetime',\n"
        "        'type_concept_id', 'operator_concept_id', 'operator_source_value', 'value_as_number', 'value_as_string',\n"
        "        'value_as_concept_id', 'unit_concept_id', 'unit_source_value',\n"
        "        'range_low', 'range_high', 'provider_id', 'modifier_concept_id', 'modifier_source_value', 'quantity',\n"
        "        'value_source_value', 'source_value', 'source_concept_id', 'record_source_value',\n"
        "        'days_supply', 'refills', 'sig', 'lot_number', 'stop_reason',\n"
        "        'route_source_value', 'route_concept_id', 'dose_unit_source_value',\n"
        "        'condition_status_concept_id', 'condition_status_source_value',\n"
        "        'qualifier_concept_id', 'qualifier_source_value',\n"
        "    ]\n"
        "    df_out = pd.DataFrame(rows, columns=STEM_COLUMNS)\n"
        "    output_file = os.path.join(output_dir, 'stem_table.csv')\n"
        "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        "    print(f'Writing stem_table.csv ... done ({len(df_out)} records)')\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _generate_domain_script(table: str) -> str:
    """Deterministic template-based generator for OMOP domain routing tables.

    Reads stem_table.csv, filters by domain_id, and writes the domain table CSV.
    All five domain tables (measurement, observation, drug_exposure,
    procedure_occurrence, condition_occurrence) share the same scaffold; only
    the row-building block differs.
    """
    domain_id = _DOMAIN_TABLES[table]

    columns_per_table: dict[str, list[str]] = {
        "measurement": [
            "measurement_id", "person_id", "measurement_concept_id",
            "measurement_date", "measurement_datetime", "measurement_time",
            "measurement_type_concept_id", "operator_concept_id",
            "value_as_number", "value_as_concept_id", "unit_concept_id",
            "range_low", "range_high", "provider_id", "visit_occurrence_id",
            "visit_detail_id", "measurement_source_value",
            "measurement_source_concept_id", "unit_source_value",
            "unit_source_concept_id", "value_source_value",
            "measurement_event_id", "meas_event_field_concept_id",
        ],
        "observation": [
            "observation_id", "person_id", "observation_concept_id",
            "observation_date", "observation_datetime",
            "observation_type_concept_id", "value_as_number",
            "value_as_string", "value_as_concept_id", "qualifier_concept_id",
            "unit_concept_id", "provider_id", "visit_occurrence_id",
            "visit_detail_id", "observation_source_value",
            "observation_source_concept_id", "unit_source_value",
            "qualifier_source_value", "value_source_value",
            "obs_event_field_concept_id", "observation_event_id",
        ],
        "drug_exposure": [
            "drug_exposure_id", "person_id", "drug_concept_id",
            "drug_exposure_start_date", "drug_exposure_start_datetime",
            "drug_exposure_end_date", "drug_exposure_end_datetime",
            "verbatim_end_date", "drug_type_concept_id", "stop_reason",
            "refills", "quantity", "days_supply", "sig", "route_concept_id",
            "lot_number", "provider_id", "visit_occurrence_id",
            "visit_detail_id", "drug_source_value", "drug_source_concept_id",
            "route_source_value", "dose_unit_source_value",
        ],
        "procedure_occurrence": [
            "procedure_occurrence_id", "person_id", "procedure_concept_id",
            "procedure_date", "procedure_datetime", "procedure_end_date",
            "procedure_end_datetime", "procedure_type_concept_id",
            "modifier_concept_id", "quantity", "provider_id",
            "visit_occurrence_id", "visit_detail_id",
            "procedure_source_value", "procedure_source_concept_id",
            "modifier_source_value",
        ],
        "condition_occurrence": [
            "condition_occurrence_id", "person_id", "condition_concept_id",
            "condition_start_date", "condition_start_datetime",
            "condition_end_date", "condition_end_datetime",
            "condition_type_concept_id", "condition_status_concept_id",
            "stop_reason", "provider_id", "visit_occurrence_id",
            "visit_detail_id", "condition_source_value",
            "condition_source_concept_id", "condition_status_source_value",
        ],
    }
    columns_lit = repr(columns_per_table[table])

    if table == "measurement":
        row_lines = (
            "            rows.append({\n"
            "                'measurement_id': rec_id,\n"
            "                'person_id': person_id,\n"
            "                'measurement_concept_id': _si(row.get('concept_id'), 0),\n"
            "                'measurement_date': _ss(row.get('start_date')),\n"
            "                'measurement_datetime': _ss(row.get('start_datetime')),\n"
            "                'measurement_time': _time_str(row.get('start_datetime')),\n"
            "                'measurement_type_concept_id': _si(row.get('type_concept_id'), 32879),\n"
            "                'operator_concept_id': _si(row.get('operator_concept_id')),\n"
            "                'value_as_number': _sf(row.get('value_as_number')),\n"
            "                'value_as_concept_id': _si(row.get('value_as_concept_id')),\n"
            "                'unit_concept_id': _si(row.get('unit_concept_id')),\n"
            "                'range_low': _sf(row.get('range_low')),\n"
            "                'range_high': _sf(row.get('range_high')),\n"
            "                'provider_id': _si(row.get('provider_id')),\n"
            "                'visit_occurrence_id': _si(row.get('visit_occurrence_id')),\n"
            "                'visit_detail_id': _si(row.get('visit_detail_id')),\n"
            "                'measurement_source_value': _ss(row.get('source_value')),\n"
            "                'measurement_source_concept_id': _si(row.get('source_concept_id'), 0),\n"
            "                'unit_source_value': _ss(row.get('unit_source_value')),\n"
            "                'unit_source_concept_id': None,\n"
            "                'value_source_value': _ss(row.get('value_source_value')),\n"
            "                'measurement_event_id': None,\n"
            "                'meas_event_field_concept_id': None,\n"
            "            })\n"
        )
    elif table == "observation":
        row_lines = (
            "            rows.append({\n"
            "                'observation_id': rec_id,\n"
            "                'person_id': person_id,\n"
            "                'observation_concept_id': _si(row.get('concept_id'), 0),\n"
            "                'observation_date': _ss(row.get('start_date')),\n"
            "                'observation_datetime': _ss(row.get('start_datetime')),\n"
            "                'observation_type_concept_id': _si(row.get('type_concept_id'), 32879),\n"
            "                'value_as_number': _sf(row.get('value_as_number')),\n"
            "                'value_as_string': _ss(row.get('value_as_string')),\n"
            "                'value_as_concept_id': _si(row.get('value_as_concept_id')),\n"
            "                'qualifier_concept_id': _si(row.get('qualifier_concept_id')),\n"
            "                'unit_concept_id': _si(row.get('unit_concept_id')),\n"
            "                'provider_id': _si(row.get('provider_id')),\n"
            "                'visit_occurrence_id': _si(row.get('visit_occurrence_id')),\n"
            "                'visit_detail_id': _si(row.get('visit_detail_id')),\n"
            "                'observation_source_value': _ss(row.get('source_value')),\n"
            "                'observation_source_concept_id': _si(row.get('source_concept_id'), 0),\n"
            "                'unit_source_value': _ss(row.get('unit_source_value')),\n"
            "                'qualifier_source_value': _ss(row.get('qualifier_source_value')),\n"
            "                'value_source_value': _ss(row.get('value_source_value')),\n"
            "                'obs_event_field_concept_id': None,\n"
            "                'observation_event_id': None,\n"
            "            })\n"
        )
    elif table == "drug_exposure":
        row_lines = (
            "            _start_date = _ss(row.get('start_date'))\n"
            "            _end_date = _ss(row.get('end_date'))\n"
            "            if _start_date is None or _end_date is None:\n"
            "                _missing = ', '.join(\n"
            "                    n for n, v in (('start_date', _start_date), ('end_date', _end_date)) if v is None\n"
            "                )\n"
            "                print(f'WARNING: dropping drug_exposure row (person_id={person_id}) — missing {_missing}')\n"
            "                continue\n"
            "            rows.append({\n"
            "                'drug_exposure_id': rec_id,\n"
            "                'person_id': person_id,\n"
            "                'drug_concept_id': _si(row.get('concept_id'), 0),\n"
            "                'drug_exposure_start_date': _start_date,\n"
            "                'drug_exposure_start_datetime': _ss(row.get('start_datetime')),\n"
            "                'drug_exposure_end_date': _end_date,\n"
            "                'drug_exposure_end_datetime': _ss(row.get('end_datetime')),\n"
            "                'verbatim_end_date': _end_date,\n"
            "                'drug_type_concept_id': _si(row.get('type_concept_id'), 32879),\n"
            "                'stop_reason': _ss(row.get('stop_reason')),\n"
            "                'refills': _si(row.get('refills')),\n"
            "                'quantity': _sf(row.get('quantity')),\n"
            "                'days_supply': _si(row.get('days_supply')),\n"
            "                'sig': _ss(row.get('sig')),\n"
            "                'route_concept_id': _si(row.get('route_concept_id')),\n"
            "                'lot_number': _ss(row.get('lot_number')),\n"
            "                'provider_id': _si(row.get('provider_id')),\n"
            "                'visit_occurrence_id': _si(row.get('visit_occurrence_id')),\n"
            "                'visit_detail_id': _si(row.get('visit_detail_id')),\n"
            "                'drug_source_value': _ss(row.get('source_value')),\n"
            "                'drug_source_concept_id': _si(row.get('source_concept_id'), 0),\n"
            "                'route_source_value': _ss(row.get('route_source_value')),\n"
            "                'dose_unit_source_value': _ss(row.get('dose_unit_source_value')),\n"
            "            })\n"
        )
    elif table == "procedure_occurrence":
        row_lines = (
            "            _qty_raw = _si(row.get('quantity'))\n"
            "            _qty = 1 if _qty_raw is not None and _qty_raw == 0 else _qty_raw\n"
            "            rows.append({\n"
            "                'procedure_occurrence_id': rec_id,\n"
            "                'person_id': person_id,\n"
            "                'procedure_concept_id': _si(row.get('concept_id'), 0),\n"
            "                'procedure_date': _ss(row.get('start_date')),\n"
            "                'procedure_datetime': _ss(row.get('start_datetime')),\n"
            "                'procedure_end_date': _ss(row.get('end_date')),\n"
            "                'procedure_end_datetime': _ss(row.get('end_datetime')),\n"
            "                'procedure_type_concept_id': _si(row.get('type_concept_id'), 32879),\n"
            "                'modifier_concept_id': _si(row.get('modifier_concept_id')),\n"
            "                'quantity': _qty,\n"
            "                'provider_id': _si(row.get('provider_id')),\n"
            "                'visit_occurrence_id': _si(row.get('visit_occurrence_id')),\n"
            "                'visit_detail_id': _si(row.get('visit_detail_id')),\n"
            "                'procedure_source_value': _ss(row.get('source_value')),\n"
            "                'procedure_source_concept_id': _si(row.get('source_concept_id'), 0),\n"
            "                'modifier_source_value': _ss(row.get('modifier_source_value')),\n"
            "            })\n"
        )
    else:  # condition_occurrence
        row_lines = (
            "            _cond_status_cid = _si(row.get('condition_status_concept_id'))\n"
            "            _cond_status_sv = _ss(row.get('condition_status_source_value'))\n"
            "            if _cond_status_cid is None:\n"
            "                # Fall back to the legacy map_both-driven value_as_concept_id path — only\n"
            "                # when it actually resolved to something, and only together with its\n"
            "                # paired value_source_value. value_source_value alone is populated for\n"
            "                # every row regardless of domain, so it must never leak in on its own for\n"
            "                # a variable with no status configured at all.\n"
            "                _legacy_status_cid = _si(row.get('value_as_concept_id'))\n"
            "                if _legacy_status_cid is not None:\n"
            "                    _cond_status_cid = _legacy_status_cid\n"
            "                    _cond_status_sv = _ss(row.get('value_source_value'))\n"
            "            rows.append({\n"
            "                'condition_occurrence_id': rec_id,\n"
            "                'person_id': person_id,\n"
            "                'condition_concept_id': _si(row.get('concept_id'), 0),\n"
            "                'condition_start_date': _ss(row.get('start_date')),\n"
            "                'condition_start_datetime': _ss(row.get('start_datetime')),\n"
            "                'condition_end_date': _ss(row.get('end_date')),\n"
            "                'condition_end_datetime': _ss(row.get('end_datetime')),\n"
            "                'condition_type_concept_id': _si(row.get('type_concept_id'), 32879),\n"
            "                'condition_status_concept_id': _cond_status_cid,\n"
            "                'stop_reason': _ss(row.get('stop_reason')),\n"
            "                'provider_id': _si(row.get('provider_id')),\n"
            "                'visit_occurrence_id': _si(row.get('visit_occurrence_id')),\n"
            "                'visit_detail_id': _si(row.get('visit_detail_id')),\n"
            "                'condition_source_value': _ss(row.get('source_value')),\n"
            "                'condition_source_concept_id': _si(row.get('source_concept_id'), 0),\n"
            "                'condition_status_source_value': _cond_status_sv,\n"
            "            })\n"
        )

    return (
        "import os\n"
        "import pandas as pd\n"
        "\n"
        f"DOMAIN_ID = {domain_id}\n"
        "\n"
        f"COLUMNS = {columns_lit}\n"
        "\n"
        "\n"
        "def _si(v, default=None):\n"
        "    try:\n"
        "        if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ('', 'nan', 'None'):\n"
        "            return default\n"
        "        return int(float(str(v)))\n"
        "    except (ValueError, TypeError):\n"
        "        return default\n"
        "\n"
        "\n"
        "def _sf(v, default=None):\n"
        "    try:\n"
        "        if v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip() in ('', 'nan', 'None'):\n"
        "            return default\n"
        "        return float(str(v))\n"
        "    except (ValueError, TypeError):\n"
        "        return default\n"
        "\n"
        "\n"
        "def _ss(v):\n"
        "    if v is None or (isinstance(v, float) and pd.isna(v)):\n"
        "        return None\n"
        "    s = str(v).strip()\n"
        "    return None if s in ('', 'nan', 'None') else s\n"
        "\n"
        "\n"
        "def _time_str(v):\n"
        "    \"\"\"Extract HH:MM:SS from a 'YYYY-MM-DD HH:MM:SS' / ISO datetime string.\"\"\"\n"
        "    s = _ss(v)\n"
        "    if not s:\n"
        "        return None\n"
        "    parts = s.replace('T', ' ').split(' ')\n"
        "    return parts[1] if len(parts) > 1 else None\n"
        "\n"
        "\n"
        "def main():\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "    stem_file = os.path.join(output_dir, 'stem_table.csv')\n"
        "\n"
        "    df = pd.read_csv(stem_file, delimiter=';', encoding='utf-8')\n"
        "\n"
        "    rows = []\n"
        "    rec_id = 1\n"
        "\n"
        "    for _, row in df.iterrows():\n"
        "        try:\n"
        "            if _si(row.get('domain_id')) != DOMAIN_ID:\n"
        "                continue\n"
        "\n"
        "            person_id = _si(row.get('person_id'))\n"
        "            if person_id is None:\n"
        "                continue\n"
        "\n"
        + row_lines
        + "            rec_id += 1\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: skipping row — {e}')\n"
        "\n"
        "    df_out = pd.DataFrame(rows, columns=COLUMNS)\n"
        f"    output_file = os.path.join(output_dir, '{table}.csv')\n"
        "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        f"    print(f'Writing {table}.csv ... done ({{len(df_out)}} records)')\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


# Output-token ceiling for the AI patch call. 16384 is gpt-4o's hard per-request
# completion-token ceiling (the API rejects max_tokens above that regardless of
# what we ask for).
_DEFAULT_PATCH_MAX_TOKENS = 16000

# stem_table's deterministic script routinely runs 500-900+ lines (it folds in
# per-variable "Extra instructions" from the Concepts step on top of the user's
# own instructions), which can exceed 16384 completion tokens on its own if the
# model has to retype the whole file — and even when it fits, retyping hundreds
# of unchanged lines is what makes generation look stalled. So stem_table uses a
# SEARCH/REPLACE diff protocol (see _apply_search_replace_blocks) instead of a
# full-file rewrite: the model only emits the changed lines, which stays well
# under budget even at the default ceiling.
_STEM_TABLE_PATCH_MAX_TOKENS = _DEFAULT_PATCH_MAX_TOKENS


_SEARCH_REPLACE_RE = re.compile(
    r"<{7}\s*SEARCH\s*\n(.*?)\n={7}\s*\n(.*?)\n>{7}\s*REPLACE",
    re.DOTALL,
)


def _apply_search_replace_blocks(code: str, diff_text: str) -> str:
    """Apply one or more '<<<<<<< SEARCH / ======= / >>>>>>> REPLACE' blocks to
    `code`, in order. Each SEARCH text must match the progressively-patched
    code exactly once at the time it's applied (so a later block can target
    text introduced by an earlier one). Raises ValueError naming the offending
    block if a match is missing or ambiguous.
    """
    blocks = _SEARCH_REPLACE_RE.findall(_strip_fences(diff_text))
    if not blocks:
        raise ValueError(
            "The AI patch response didn't contain any SEARCH/REPLACE blocks — "
            "try rephrasing the extra instructions."
        )
    patched = code
    for i, (search, replace) in enumerate(blocks, 1):
        count = patched.count(search)
        if count == 0:
            raise ValueError(f"SEARCH/REPLACE block {i} didn't match the script exactly — try rephrasing the extra instructions.")
        if count > 1:
            raise ValueError(f"SEARCH/REPLACE block {i} matched the script {count} times (must be unique) — try rephrasing the extra instructions.")
        patched = patched.replace(search, replace, 1)
    return patched


# In-memory, per (project_id, table) token progress for an AI patch call that's
# currently in flight — lets a status endpoint be polled while the generating
# request is still running, so the frontend can show a live-updating counter
# instead of only the final number. Mirrors the module-level status pattern
# used by vocab_loader.py.
_generation_progress: dict[str, dict] = {}
_generation_progress_lock = threading.Lock()


def _progress_key(project_id: str, table: str) -> str:
    return f"{project_id}:{table}"


def get_generation_progress(project_id: str, table: str) -> dict | None:
    with _generation_progress_lock:
        entry = _generation_progress.get(_progress_key(project_id, table))
        return dict(entry) if entry else None


def _set_generation_progress(project_id: str, table: str, used: int, limit: int, content: str) -> None:
    with _generation_progress_lock:
        _generation_progress[_progress_key(project_id, table)] = {
            "used": used,
            "limit": limit,
            "content": content,
        }


def _clear_generation_progress(project_id: str, table: str) -> None:
    with _generation_progress_lock:
        _generation_progress.pop(_progress_key(project_id, table), None)


async def _apply_extra_instructions(code: str, instructions: str, table: str, project_id: str) -> tuple[str, dict]:
    """Patch a deterministically generated script with user-supplied instructions via AI.

    Streams the completion so get_generation_progress(project_id, table) reflects
    tokens generated so far while this call is in flight.

    For stem_table the model returns SEARCH/REPLACE diff blocks instead of the
    full script (see _apply_search_replace_blocks) — its deterministic script is
    too large to retype affordably. Other tables' scripts are short enough that
    a full rewrite is simpler and still cheap, so they keep that behavior.

    Returns (patched_code, usage) where usage = {"used": completion tokens, "limit": max_tokens}.
    """
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    use_diff = table == "stem_table"
    max_tokens = _STEM_TABLE_PATCH_MAX_TOKENS if use_diff else _DEFAULT_PATCH_MAX_TOKENS
    _set_generation_progress(project_id, table, 0, max_tokens, "")

    if use_diff:
        system_prompt = (
            "You are an expert Python ETL engineer. Apply the user's modifications to the given "
            "script by returning ONLY the changed portions as SEARCH/REPLACE blocks — never retype "
            "unchanged code. Format every change exactly as:\n"
            "<<<<<<< SEARCH\n"
            "<exact original lines, verbatim, character-for-character>\n"
            "=======\n"
            "<replacement lines>\n"
            ">>>>>>> REPLACE\n"
            "Use as many blocks as needed, one per distinct edit. Each SEARCH block must match the "
            "CURRENT SCRIPT exactly, including whitespace, and must be unique to the one spot you "
            "intend to change — include a few lines of surrounding context if needed to make it "
            "unique. Output nothing but the SEARCH/REPLACE blocks: no markdown fences, no explanations.\n"
            "\n"
            "Every stem row is appended via the `_append_row(**overrides)` helper defined right "
            "before the line `_append_row()` (marked `# <<< AI-PATCH INSERTION POINT >>>`), NOT by "
            "an inline `rows.append({...})`. Follow these rules exactly:\n"
            "- To override a field for the current variable's row (e.g. a custom value_as_string), "
            "insert your logic AFTER the `# <<< AI-PATCH INSERTION POINT >>>` comment and BEFORE the "
            "`_append_row()` call, then either reassign the local variable (e.g. `value_as_string = "
            "...`) before that call, or pass it directly: `_append_row(value_as_string=...)`. Never "
            "insert field-setting logic earlier in the per-variable block — later lines in the "
            "unmodified script (e.g. `value_as_string = mapped.get('value_as_string')`) already run "
            "before that point and will silently overwrite an override placed too early.\n"
            "- To emit several records for one variable (e.g. one row per comma-separated value), "
            "replace the single `_append_row()` call with a small loop that calls `_append_row(...)` "
            "once per record, then nothing else — do NOT copy or retype the `_row_fields = dict(...)` "
            "literal inside `_append_row`; call the helper instead, exactly like the rest of the file "
            "does. Duplicating that dict is the single most common mistake — never do it.\n"
        )
    else:
        system_prompt = (
            "You are an expert Python ETL engineer. "
            "Apply the user's modifications to the given script exactly as requested. "
            "Return ONLY the complete modified Python script — no markdown fences, no explanations."
        )

    try:
        stream = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        f"Apply these modifications to the OMOP {table} script:\n\n"
                        f"{instructions}\n\n"
                        f"CURRENT SCRIPT:\n{code}"
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )

        content_parts: list[str] = []
        streamed_tokens = 0
        finish_reason: str | None = None
        final_usage = None
        async for chunk in stream:
            if chunk.choices:
                choice = chunk.choices[0]
                delta = choice.delta.content if choice.delta else None
                if delta:
                    content_parts.append(delta)
                    streamed_tokens += 1  # one streamed chunk ≈ one token
                    _set_generation_progress(
                        project_id, table, streamed_tokens, max_tokens, "".join(content_parts)
                    )
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
            if chunk.usage:
                final_usage = chunk.usage
        content = "".join(content_parts)
    finally:
        _clear_generation_progress(project_id, table)

    if finish_reason == "length":
        raise ValueError(
            f"The AI patch for the {table} script was cut off before it finished "
            "(the script + instructions were too long for one response). "
            "Try shortening the extra instructions or splitting them into smaller steps."
        )
    patched = _apply_search_replace_blocks(code, content) if use_diff else _strip_fences(content)
    usage = {
        "used": final_usage.completion_tokens if final_usage else streamed_tokens,
        "limit": max_tokens,
    }
    return patched, usage


async def generate_table_script(project, table: str) -> tuple[str, dict | None]:
    """Generate the Python ETL script for a single OMOP table.

    Returns (code, usage). usage is None unless the script went through the AI
    patch step (i.e. extra instructions were supplied for this table).
    """
    code: str | None = None

    if table == "location":
        code = _generate_location_script(project)
    elif table == "care_site":
        code = _generate_care_site_script(project)
    elif table == "provider":
        code = _generate_provider_script(project)
    elif table == "person":
        code = _generate_person_script(project)
    elif table == "visit_occurrence":
        code = _generate_visit_occurrence_script(project)
    elif table == "observation_period":
        code = _generate_observation_period_script(project)
    elif table == "stem_table":
        code = _generate_stem_table_script(project)
    elif table == "death":
        code = _generate_death_script(project)
    elif table in _DOMAIN_TABLES:
        code = _generate_domain_script(table)

    extra = (project.etl_config or {}).get(table, {}).get("extra_instructions", "").strip()
    if table == "stem_table":
        extra = "\n\n".join(filter(None, [extra, _stem_variable_instructions(project)]))
    if extra:
        code, usage = await _apply_extra_instructions(code, extra, table, project.id)
        return code, usage
    return code, None


def _stem_variable_instructions(project) -> str:
    """Fold per-variable "Extra instructions (AI)" text from the Concepts step
    into the stem_table AI-patch prompt, so users can request custom
    transformation/loading logic scoped to a single variable."""
    lines = []
    for variable, d in (project.concept_decisions or {}).items():
        if not isinstance(d, dict) or d.get("strategy") == "skip":
            continue
        instr = (d.get("extra_instructions") or "").strip()
        if instr:
            lines.append(f'- For variable "{variable}": {instr}')
    if not lines:
        return ""
    return "Per-variable instructions:\n" + "\n".join(lines)


async def generate_all_table_scripts(project) -> tuple[dict[str, str], dict[str, dict]]:
    """Generate scripts for all tables configured in etl_config.

    Returns ({table: code}, {table: usage}) — the usage dict only contains
    entries for tables that went through the AI patch step.
    """
    import asyncio

    config: dict = project.etl_config or {}
    # Respect the per-table enabled flag set by the Source-step picker.
    # Default to True when the key is absent so older projects keep working.
    tables = [
        t for t in SUPPORTED_TABLES
        if t in config and config[t].get("enabled", True) is not False
    ]

    # Always include domain tables when stem_table is enabled (they depend on it)
    if "stem_table" in config and config["stem_table"].get("enabled", True) is not False:
        for dt in _DOMAIN_TABLES:
            if dt not in tables:
                tables.append(dt)

    if not tables:
        tables = list(SUPPORTED_TABLES)

    tasks = {t: generate_table_script(project, t) for t in tables}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    out: dict[str, str] = {}
    usage_out: dict[str, dict] = {}
    for table, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            out[table] = f"# ERROR generating {table}: {result}"
        else:
            code, usage = result  # type: ignore[misc]
            out[table] = code
            if usage:
                usage_out[table] = usage

    return out, usage_out


def _strip_fences(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    inner: list[str] = []
    in_block = False
    for line in lines:
        if line.startswith("```") and not in_block:
            in_block = True
            continue
        if line.startswith("```") and in_block:
            in_block = False
            continue
        if in_block:
            inner.append(line)
    return "\n".join(inner)
