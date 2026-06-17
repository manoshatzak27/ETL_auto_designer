"""
Per-table ETL script generator.

All OMOP tables are generated deterministically. If the user supplies
extra_instructions for a table, a separate AI call patches the generated script.
"""
import json
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
    """Like _xtr_v but uses an intermediate `val` variable.
    When no column is mapped, emits a '# <var> is not populated' comment instead."""
    pad = " " * indent
    if not col:
        return f"{pad}# {var} is not populated\n{pad}{var} = None"
    c = repr(col)
    return (
        f"{pad}val = row.get({c})\n"
        f"{pad}{var} = (str(val).strip() or None) if pd.notnull(val) else None\n"
        f"{pad}if {var} is None:\n"
        f'{pad}    _info(f"INFO: column {col!r} is empty for this row")'
    )


def _generate_location_script(project) -> str:
    """Deterministic template-based generator for the OMOP location script."""
    loc = (project.etl_config or {}).get("location", {})
    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

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
        "VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'\n"
        "\n"
        "def _info(msg):\n"
        "    \"\"\"Print diagnostic message only in detailed output mode.\"\"\"\n"
        "    if VERBOSE:\n"
        "        print(msg)\n"
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
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
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
        + "            rows.append([\n"
        + "                None,                                    # location_id (will be assigned later)\n"
        + "                address_1[:50]  if address_1 else None,  # Truncate to OMOP limit\n"
        + "                address_2[:50]  if address_2 else None,  # Truncate to OMOP limit\n"
        + "                city[:50]       if city      else None,  # Truncate to OMOP limit\n"
        + "                state[:2]       if state     else None,  # OMOP expects 2-character state code\n"
        + "                zip_code        if zip_code  else None,  # Keep as-is (no truncation specified)\n"
        + "                county[:20]     if county    else None,  # Truncate to OMOP limit\n"
        + "                location_source_value,\n"
        + "                country_concept_id,\n"
        + "                country_source_value,\n"
        + "                _to_lat(latitude),\n"
        + "                _to_lon(longitude),\n"
        + "            ])\n"
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
        + "            rows.append([\n"
        + "                None,                                          # location_id (will be assigned later)\n"
        + "                cs_address_1[:50]  if cs_address_1 else None,  # Truncate to OMOP limit\n"
        + "                cs_address_2[:50]  if cs_address_2 else None,  # Truncate to OMOP limit\n"
        + "                cs_city[:50]       if cs_city      else None,  # Truncate to OMOP limit\n"
        + "                cs_state[:2]       if cs_state     else None,  # OMOP expects 2-character state code\n"
        + "                cs_zip_code        if cs_zip_code  else None,  # Keep as-is (no truncation specified)\n"
        + "                cs_county[:20]     if cs_county    else None,  # Truncate to OMOP limit\n"
        + "                cs_location_source_value,\n"
        + "                cs_country_concept_id,\n"
        + "                cs_country_source_value,\n"
        + "                _to_lat(cs_latitude),\n"
        + "                _to_lon(cs_longitude),\n"
        + "            ])\n"
        + "\n"
        + "    # --- Build output DataFrame ---\n"
        + "    output_df = pd.DataFrame(rows, columns=[\n"
        + '        "location_id", "address_1", "address_2", "city", "state", "zip", "county",\n'
        + '        "location_source_value", "country_concept_id", "country_source_value", "latitude", "longitude"\n'
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


def _generate_care_site_script(project) -> str:
    """Deterministic template-based generator for the OMOP care_site script."""
    cs_cfg = (project.etl_config or {}).get("care_site", {})
    loc = (project.etl_config or {}).get("location", {})
    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

    name_col = cs_cfg.get("care_site_name_col", "")
    pos_col = cs_cfg.get("place_of_service_col", "")
    pos_value_map = cs_cfg.get("place_of_service_value_map", {})

    cs_a1_col = loc.get("cs_address_1_col", "")
    cs_a2_col = loc.get("cs_address_2_col", "")
    cs_city_col = loc.get("cs_city_col", "")
    cs_state_col = loc.get("cs_state_col", "")
    cs_zip_col = loc.get("cs_zip_col", "")
    cs_county_col = loc.get("cs_county_col", "")
    cs_country_sv = loc.get("cs_country_source_value", "")

    has_location = any([cs_a1_col, cs_a2_col, cs_city_col, cs_state_col, cs_zip_col, cs_county_col, cs_country_sv])

    location_block = (
        "    location_lookup = {}\n"
        "    location_file = os.path.join(output_dir, 'location.csv')\n"
        "    if os.path.exists(location_file):\n"
        "        try:\n"
        "            loc_df = pd.read_csv(location_file, delimiter=';', encoding='utf-8')\n"
        "            location_lookup = dict(zip(loc_df['location_source_value'], loc_df['location_id']))\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: could not load location.csv: {e}')\n"
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
        + f"        cs_country_source_value = {repr(cs_country_sv)}\n"
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
        "VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'\n"
        "\n"
        "def _info(msg):\n"
        "    \"\"\"Print diagnostic message only in detailed output mode.\"\"\"\n"
        "    if VERBOSE:\n"
        "        print(msg)\n"
        "\n"
        "\n"
        "def main():\n"
        "    # Load environmental variables\n"
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
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
        + "    df_out = pd.DataFrame(rows)\n"
        + "    df_out = df_out.reset_index(drop=True)\n"
        + "    df_out['care_site_id'] = df_out.index + 1\n"
        + "\n"
        + "    df_out = df_out[['care_site_id', 'care_site_name', 'place_of_service_concept_id',\n"
        + "                     'location_id', 'care_site_source_value', 'place_of_service_source_value']]\n"
        + "\n"
        + "    # --- Write output ---\n"
        + "    output_file = os.path.join(output_dir, 'care_site.csv')\n"
        + "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing care_site.csv ... done ({len(df_out)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _generate_provider_script(project) -> str:
    """Deterministic template-based generator for the OMOP provider script."""
    prov = (project.etl_config or {}).get("provider", {})
    cs_cfg = (project.etl_config or {}).get("care_site", {})
    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

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
    cs_name_col = cs_cfg.get("care_site_name_col", "")

    if cs_name_col:
        care_site_block = (
            "    care_site_lookup = {}\n"
            "    care_site_file = os.path.join(output_dir, 'care_site.csv')\n"
            "    if os.path.exists(care_site_file):\n"
            "        try:\n"
            "            cs_df = pd.read_csv(care_site_file, delimiter=';', encoding='utf-8')\n"
            "            care_site_lookup = {str(r['care_site_name']): int(r['care_site_id']) for _, r in cs_df.iterrows()}\n"
            "        except Exception as e:\n"
            "            print(f'WARNING: could not load care_site.csv: {e}')\n"
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
        "VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'\n"
        "\n"
        "def _info(msg):\n"
        "    \"\"\"Print diagnostic message only in detailed output mode.\"\"\"\n"
        "    if VERBOSE:\n"
        "        print(msg)\n"
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
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
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
        + "    df_out = pd.DataFrame(rows)\n"
        + "    df_out['provider_id'] = range(1, len(df_out) + 1)\n"
        + "\n"
        + "    df_out = df_out[['provider_id', 'provider_name', 'npi', 'dea', 'specialty_concept_id',\n"
        + "                     'care_site_id', 'year_of_birth', 'gender_concept_id', 'provider_source_value',\n"
        + "                     'specialty_source_value', 'specialty_source_concept_id',\n"
        + "                     'gender_source_value', 'gender_source_concept_id']]\n"
        + "\n"
        + "    # --- Write output ---\n"
        + "    output_file = os.path.join(output_dir, 'provider.csv')\n"
        + "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        + "    print(f'Writing provider.csv ... done ({len(df_out)} records)')\n"
        + "\n"
        + "if __name__ == '__main__':\n"
        + "    main()\n"
    )


def _generate_person_script(project) -> str:
    """Deterministic template-based generator for the OMOP person script."""
    person = (project.etl_config or {}).get("person", {})
    loc = (project.etl_config or {}).get("location", {})
    cs_cfg = (project.etl_config or {}).get("care_site", {})
    prov_cfg = (project.etl_config or {}).get("provider", {})
    dataset_options = (project.etl_config or {}).get("dataset_options", {})
    multiple_rows_per_patient = dataset_options.get("multiple_rows_per_patient", False)
    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

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

    a1_col = loc.get("address_1_col", "")
    a2_col = loc.get("address_2_col", "")
    city_col = loc.get("city_col", "")
    state_col = loc.get("state_col", "")
    zip_col = loc.get("zip_col", "")
    county_col = loc.get("county_col", "")
    country_sv = loc.get("country_source_value", "")
    has_location = any([a1_col, a2_col, city_col, state_col, zip_col])

    cs_name_col = cs_cfg.get("care_site_name_col", "")
    prov_name_col = prov_cfg.get("provider_name_col", "")

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
            "    location_lookup = {}\n"
            "    location_file = os.path.join(output_dir, 'location.csv')\n"
            "    if os.path.exists(location_file):\n"
            "        try:\n"
            "            loc_df = pd.read_csv(location_file, delimiter=';', encoding='utf-8')\n"
            "            location_lookup = dict(zip(loc_df['location_source_value'], loc_df['location_id']))\n"
            "        except Exception as e:\n"
            "            print(f'WARNING: could not load location.csv: {e}')\n"
        )
        loc_lookup_lines = (
            _xtr("_a1", a1_col, 12) + "\n"
            + _xtr("_a2", a2_col, 12) + "\n"
            + _xtr("_city", city_col, 12) + "\n"
            + _xtr("_state", state_col, 12) + "\n"
            + _xtr("_zip", zip_col, 12) + "\n"
            + _xtr("_county", county_col, 12) + "\n"
            + f"            _country_sv = {repr(country_sv)}\n"
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
            "    care_site_lookup = {}\n"
            "    care_site_file = os.path.join(output_dir, 'care_site.csv')\n"
            "    if os.path.exists(care_site_file):\n"
            "        try:\n"
            "            cs_df = pd.read_csv(care_site_file, delimiter=';', encoding='utf-8')\n"
            "            care_site_lookup = {str(r['care_site_name']): int(r['care_site_id']) for _, r in cs_df.iterrows()}\n"
            "        except Exception as e:\n"
            "            print(f'WARNING: could not load care_site.csv: {e}')\n"
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
            "    provider_lookup = {}\n"
            "    provider_file = os.path.join(output_dir, 'provider.csv')\n"
            "    if os.path.exists(provider_file):\n"
            "        try:\n"
            "            prov_df = pd.read_csv(provider_file, delimiter=';', encoding='utf-8')\n"
            "            provider_lookup = {str(r['provider_source_value']): int(r['provider_id']) for _, r in prov_df.iterrows()}\n"
            "        except Exception as e:\n"
            "            print(f'WARNING: could not load provider.csv: {e}')\n"
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
        "VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'\n"
        "\n"
        "def _info(msg):\n"
        "    \"\"\"Print diagnostic message only in detailed output mode.\"\"\"\n"
        "    if VERBOSE:\n"
        "        print(msg)\n"
        "\n"
        "\n"
        "def main():\n"
        "    # Load environmental variables\n"
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
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
        + "    df_out = pd.DataFrame(rows)\n"
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

    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")
    source_stem = Path(project.source_filename).stem if project.source_filename else "basedata"

    pid_cfg = (person_cfg.get("mappings") or {}).get("person_id") or {}
    auto_increment = pid_cfg.get("auto_increment", False)
    pid_col = pid_cfg.get("source_col", "")
    pid_transform = pid_cfg.get("transform", "int_float")

    visit_defs = visit_cfg.get("visit_definitions", [])
    vd_repr = repr(visit_defs)
    visit_source_col = visit_cfg.get("visit_source_col", "")
    auto_number_visits = bool(visit_cfg.get("auto_number_visits", False))

    # person_source_value in visit_occurrence must match what person.csv stores —
    # which is always str(_pid_raw) with no type casting.
    if pid_col:
        psv_setup = ""
        psv_lines = (
            f"            _pid_raw = row.get({repr(pid_col)})\n"
            "            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            "                continue\n"
            "            person_source_value = str(_pid_raw)\n"
        )
    else:
        psv_setup = ""
        psv_lines = (
            "            person_source_value = str(_src_idx)\n"
        )

    cs_name_col = cs_cfg.get("care_site_name_col", "")
    prov_name_col = prov_cfg.get("provider_name_col", "")

    if cs_name_col:
        cs_setup = (
            "    care_site_lookup = {}\n"
            "    care_site_file = os.path.join(output_dir, 'care_site.csv')\n"
            "    if os.path.exists(care_site_file):\n"
            "        try:\n"
            "            cs_df = pd.read_csv(care_site_file, delimiter=';', encoding='utf-8')\n"
            "            care_site_lookup = {str(r['care_site_name']): int(r['care_site_id']) for _, r in cs_df.iterrows()}\n"
            "        except Exception as e:\n"
            "            print(f'WARNING: could not load care_site.csv: {e}')\n"
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
            "    provider_lookup = {}\n"
            "    provider_file = os.path.join(output_dir, 'provider.csv')\n"
            "    if os.path.exists(provider_file):\n"
            "        try:\n"
            "            prov_df = pd.read_csv(provider_file, delimiter=';', encoding='utf-8')\n"
            "            provider_lookup = {str(r['provider_source_value']): int(r['provider_id']) for _, r in prov_df.iterrows()}\n"
            "        except Exception as e:\n"
            "            print(f'WARNING: could not load provider.csv: {e}')\n"
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

    return (
        "import os\n"
        "import pandas as pd\n"
        "from datetime import datetime, date\n"
        "\n"
        "\n"
        "VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'\n"
        "\n"
        "def _info(msg):\n"
        "    \"\"\"Print diagnostic message only in detailed output mode.\"\"\"\n"
        "    if VERBOSE:\n"
        "        print(msg)\n"
        "\n"
        "\n"
        "# --- Module-level constants ---\n"
        "INPATIENT_CONCEPT_IDS = {9201, 262, 42898160}  # concept IDs treated as inpatient\n"
        "\n"
        f"VISIT_DEFS      = {vd_repr}  # visit definitions from Step 5\n"
        "\n"
        f"SOURCE_STEM      = {repr(source_stem)}\n"
        f"VISIT_SOURCE_COL = {repr(visit_source_col)}  # column that carries visit label (multi-row mode)\n"
        f"AUTO_NUMBER_VISITS = {repr(auto_number_visits)}  # number visits visit1/visit2/... when no identifier col\n"
        "\n"
        "\n"
        "def main():\n"
        "    # Load environmental variables\n"
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        "    person_lookup = {}\n"
        "    person_file = os.path.join(output_dir, 'person.csv')\n"
        "    if os.path.exists(person_file):\n"
        "        try:\n"
        "            pers_df = pd.read_csv(person_file, delimiter=';', encoding='utf-8')\n"
        "            person_lookup = dict(zip(pers_df['person_source_value'].astype(str), pers_df['person_id']))\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: could not load person.csv: {e}')\n"
        "\n"
        + cs_setup
        + "\n"
        + prov_setup
        + "\n"
        + psv_setup
        + "\n"
        + "    # --- Process rows ---\n"
        + "    visit_id_counter = 1\n"
        + "    rows = []\n"
        + "    _visit_counters = {}  # person_source_value -> int, used for auto-numbering\n"
        + "\n"
        + "    for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
        + "        try:\n"
        + "\n"
        + "            # Person identifier\n"
        + psv_lines
        + "            person_id = person_lookup.get(person_source_value)\n"
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
        "                    try:\n"
        "                        visit_start_date = datetime.strptime(_date_str, _date_fmt).date()\n"
        "                    except Exception as _e:\n"
        "                        print(f'ERROR: cannot parse start date {_date_str!r} with format {_date_fmt!r} for person {person_source_value} visit \"{vd[\"label\"]}\" — skipping row. ({_e})')\n"
        "                        continue\n"
        "                    _start_time_col = vd.get('time_col') or ''\n"
        "                    if _start_time_col:\n"
        "                        _tv = row.get(_start_time_col)\n"
        "                        _time_str = str(_tv).strip() if pd.notnull(_tv) else ''\n"
        "                        if _time_str and _time_str != 'nan':\n"
        "                            try:\n"
        "                                visit_start_datetime = datetime.combine(visit_start_date, datetime.strptime(_time_str, _time_fmt).time())\n"
        "                            except Exception:\n"
        "                                _info(f'INFO: person {person_source_value} visit \"{vd[\"label\"]}\" — could not parse start time {_time_str!r}; defaulting to midnight')\n"
        "                                visit_start_datetime = datetime.combine(visit_start_date, datetime.min.time())\n"
        "                        else:\n"
        "                            visit_start_datetime = datetime.combine(visit_start_date, datetime.min.time())\n"
        "                    else:\n"
        "                        visit_start_datetime = datetime.combine(visit_start_date, datetime.min.time())\n"
        "\n"
        "                    end_col = vd.get('end_date_col') or ''\n"
        "                    if end_col:\n"
        "                        _ev = row.get(end_col)\n"
        "                        _ev_str = str(_ev).strip() if pd.notnull(_ev) else ''\n"
        "                        if _ev_str and _ev_str != 'nan':\n"
        "                            try:\n"
        "                                visit_end_date = datetime.strptime(_ev_str, _date_fmt).date()\n"
        "                            except Exception as _e:\n"
        "                                print(f'WARNING: cannot parse end date {_ev_str!r} with format {_date_fmt!r} for person {person_source_value} visit \"{vd[\"label\"]}\" — defaulting to start date. ({_e})')\n"
        "                                visit_end_date = visit_start_date\n"
        "                        else:\n"
        "                            visit_end_date = visit_start_date\n"
        "                    elif vd.get('visit_concept_id') in INPATIENT_CONCEPT_IDS:\n"
        "                        visit_end_date = date.today()\n"
        "                    else:\n"
        "                        visit_end_date = visit_start_date\n"
        "                    _end_time_col = vd.get('end_time_col') or ''\n"
        "                    if _end_time_col:\n"
        "                        _etv = row.get(_end_time_col)\n"
        "                        _etime_str = str(_etv).strip() if pd.notnull(_etv) else ''\n"
        "                        if _etime_str and _etime_str != 'nan':\n"
        "                            try:\n"
        "                                visit_end_datetime = datetime.combine(visit_end_date, datetime.strptime(_etime_str, _time_fmt).time())\n"
        "                            except Exception:\n"
        "                                _info(f'INFO: person {person_source_value} visit \"{vd[\"label\"]}\" — could not parse end time {_etime_str!r}; defaulting to midnight')\n"
        "                                visit_end_datetime = datetime.combine(visit_end_date, datetime.min.time())\n"
        "                        else:\n"
        "                            visit_end_datetime = datetime.combine(visit_end_date, datetime.min.time())\n"
        "                    else:\n"
        "                        visit_end_datetime = datetime.combine(visit_end_date, datetime.min.time())\n"
        "\n"
        "                    vcsc = vd.get('visit_concept_source_col') or ''\n"
        "                    if vcsc:\n"
        "                        _vcv_raw = row.get(vcsc)\n"
        "                        _vcv = (str(_vcv_raw).strip() or None) if pd.notnull(_vcv_raw) else None\n"
        "                        visit_concept_id = (vd.get('visit_concept_value_map') or {}).get(_vcv, vd['visit_concept_id']) if _vcv else vd['visit_concept_id']\n"
        "                    else:\n"
        "                        visit_concept_id = vd['visit_concept_id']\n"
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
        "                    elif AUTO_NUMBER_VISITS:\n"
        "                        _visit_counters[person_source_value] = _visit_counters.get(person_source_value, 0) + 1\n"
        "                        label_norm = f'visit{_visit_counters[person_source_value]}'\n"
        "                    else:\n"
        "                        label_norm = vd['label'].lower().replace(' ', '_')\n"
        "                    visit_source_value = f'{person_source_value}-{SOURCE_STEM}-{label_norm}'\n"
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
        "                        'record_source_value': visit_source_value,\n"
        "                    })\n"
        "                    visit_id_counter += 1\n"
        "                except Exception as e:\n"
        "                    print(f'WARNING: skipping visit for {person_source_value} — {e}')\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: skipping row — {e}')\n"
        "\n"
        "    # --- Build output DataFrame ---\n"
        "    df_out = pd.DataFrame(rows)\n"
        "\n"
        "    if not df_out.empty:\n"
        "        # Sort by person and date, then chain preceding_visit_occurrence_id\n"
        "        df_out = df_out.sort_values(['person_id', 'visit_start_date']).reset_index(drop=True)\n"
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
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
    )


def _generate_observation_period_script(project) -> str:
    """Deterministic template-based generator for the OMOP observation_period script."""
    obs = (project.etl_config or {}).get("observation_period", {})
    person_cfg = (project.etl_config or {}).get("person", {})

    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

    start_col = obs.get("start_date_col", "")
    end_col = obs.get("end_date_col", "")
    fallback = obs.get("end_date_fallback", "start_date")
    type_concept_id = int(obs.get("period_type_concept_id") or 32879)
    date_fmt = obs.get("date_format") or "%Y-%m-%d"

    pid_cfg = (person_cfg.get("mappings") or {}).get("person_id") or {}
    pid_col = pid_cfg.get("source_col", "")

    if pid_col:
        psv_setup = ""
        psv_lines = (
            f"            _pid_raw = row.get({repr(pid_col)})\n"
            "            if pd.isnull(_pid_raw):\n"
            f'                print(f"WARNING: skipping row {{_src_idx}} — person_id column {repr(pid_col)} is null or missing")\n'
            "                continue\n"
            "            person_source_value = str(_pid_raw)\n"
        )
    else:
        psv_setup = ""
        psv_lines = (
            "            person_source_value = str(_src_idx)\n"
        )

    if end_col:
        end_date_lines = (
            f"                _end_raw = str(row.get({repr(end_col)}, '')).strip()\n"
            "                if _end_raw and _end_raw != 'nan':\n"
            f"                    obs_end_date = datetime.strptime(_end_raw, {repr(date_fmt)}).date()\n"
            "                else:\n"
            + ("                    obs_end_date = obs_start_date\n" if fallback == "start_date"
               else "                    obs_end_date = date.today()\n")
        )
    else:
        end_date_lines = (
            "                obs_end_date = obs_start_date\n" if fallback == "start_date"
            else "                obs_end_date = date.today()\n"
        )

    return (
        "import os\n"
        "import pandas as pd\n"
        "from datetime import datetime, date, timedelta\n"
        "\n"
        "\n"
        "VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'\n"
        "\n"
        "def _info(msg):\n"
        "    \"\"\"Print diagnostic message only in detailed output mode.\"\"\"\n"
        "    if VERBOSE:\n"
        "        print(msg)\n"
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
        "def main():\n"
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        "    person_lookup = {}\n"
        "    person_file = os.path.join(output_dir, 'person.csv')\n"
        "    if os.path.exists(person_file):\n"
        "        try:\n"
        "            pers_df = pd.read_csv(person_file, delimiter=';', encoding='utf-8')\n"
        "            person_lookup = dict(zip(pers_df['person_source_value'].astype(str), pers_df['person_id']))\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: could not load person.csv: {e}')\n"
        "\n"
        + psv_setup
        + "\n"
        + "    # --- Collect observation periods per person ---\n"
        + "    person_periods: dict = {}  # person_id -> [(start_date, end_date), ...]\n"
        + "\n"
        + "    for _src_idx, (_, row) in enumerate(df.iterrows(), start=1):\n"
        + "        try:\n"
        + "\n"
        + "            # Person identifier\n"
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
        "            # End date (optional — falls back to start_date or today)\n"
        "            try:\n"
        + end_date_lines
        + "            except Exception as _end_exc:\n"
        "                _info(f'INFO: person \"{person_source_value}\" — could not parse end_date; defaulting to start_date ({_end_exc})')\n"
        "                obs_end_date = obs_start_date\n"
        "\n"
        "            person_periods.setdefault(person_id, []).append((obs_start_date, obs_end_date))\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: skipping row — {e}')\n"
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
        "    df_out = pd.DataFrame(rows)\n"
        "    output_file = os.path.join(output_dir, 'observation_period.csv')\n"
        "    df_out.to_csv(output_file, sep=';', index=False, encoding='utf-8')\n"
        "    print(f'Writing observation_period.csv ... done ({len(df_out)} records)')\n"
        "\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n"
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

    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")

    pid_cfg = (person_cfg.get("mappings") or {}).get("person_id") or {}
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
        "VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'\n"
        "\n"
        "def _info(msg):\n"
        "    \"\"\"Print diagnostic message only in detailed output mode.\"\"\"\n"
        "    if VERBOSE:\n"
        "        print(msg)\n"
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
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        "    person_lookup = {}\n"
        "    person_file = os.path.join(output_dir, 'person.csv')\n"
        "    if os.path.exists(person_file):\n"
        "        try:\n"
        "            pers_df = pd.read_csv(person_file, delimiter=';', encoding='utf-8')\n"
        "            person_lookup = dict(zip(pers_df['person_source_value'].astype(str), pers_df['person_id']))\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: could not load person.csv: {e}')\n"
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
    """Deterministically derive SPECIAL_OVERRIDES entries from Step 9 decisions.

    The runtime stem_table.py already consumes unit_mapping.csv for per-row
    unit lookups. This function covers the edge case where Step 9 captured
    a unit_mapping with a SINGLE concept_id and NO unit_col (a fixed unit
    for every row of that variable) — in which case unit_mapping.csv has
    nothing to look up on, and the unit must be hardcoded as an override.

    Rules (v1):
      - Skip variables with strategy == 'skip' or no decision.
      - For Measurement/Observation variables (domain_id in {1, 2}) whose
        unit_mapping carries exactly one unit concept_id and no unit_col,
        emit { variable, field: 'unit_concept_id', value: <concept_id> }.
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
        if d.get("domain_id") not in (1, 2):
            continue
        um = d.get("unit_mapping") or {}
        if um.get("unit_col"):
            continue  # handled at runtime via unit_mapping.csv
        unit_concepts = um.get("unit_concepts") or {}
        ids = [v for v in unit_concepts.values() if isinstance(v, int) and v > 0]
        if len(ids) != 1:
            continue
        inferred.append({
            "variable": variable,
            "field": "unit_concept_id",
            "value": ids[0],
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

    delim = repr(project.source_delimiter or ",")
    enc = repr(project.source_encoding or "utf-8")
    source_stem = Path(project.source_filename).stem if project.source_filename else "basedata"

    pid_cfg = (person_cfg.get("mappings") or {}).get("person_id") or {}
    pid_col = pid_cfg.get("source_col", "")

    # Background-inferred overrides (Step 9 fixed-unit cases) come first;
    # user-entered overrides (Step 10 UI) win on conflict because the
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

    # variable → visit label, as configured by the user in Step 10.
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

    # Universe of variables the user explicitly mapped in Step 9 (strategy != skip).
    # Structural columns already used by Person/Visit/Death/etc. are absent from
    # concept_decisions entirely, so this set automatically excludes them.
    mapped_variables = {
        k.lower()
        for k, v in (project.concept_decisions or {}).items()
        if isinstance(v, dict) and v.get("strategy") != "skip"
    }

    vdi_repr = repr(visit_date_info)
    vvm_repr = repr(variable_visit_map)
    so_repr = repr(special_overrides)
    sv_repr = repr(sorted(mapped_variables))

    return (
        "import os\n"
        "import logging\n"
        "import pandas as pd\n"
        "from datetime import datetime\n"
        "\n"
        "\n"
        "VERBOSE = os.getenv('ETL_OUTPUT_MODE', 'basic') == 'detailed'\n"
        "\n"
        "def _info(msg):\n"
        "    \"\"\"Print diagnostic message only in detailed output mode.\"\"\"\n"
        "    if VERBOSE:\n"
        "        print(msg)\n"
        "\n"
        "\n"
        "# --- Logging setup ---\n"
        "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
        "logger = logging.getLogger(__name__)\n"
        "\n"
        "\n"
        "# --- Module-level constants ---\n"
        f"SOURCE_STEM          = {repr(source_stem)}\n"
        f"VISIT_SOURCE_COL     = {repr(visit_source_col)}   # column that carries visit label (multi-row mode)\n"
        f"AUTO_NUMBER_VISITS   = {repr(auto_number_visits)}  # number visits visit1/visit2/... when no identifier col\n"
        f"DEFAULT_DATE_COL     = {repr(default_date_col)}\n"
        f"DEFAULT_DATE_FORMAT  = {repr(default_date_format)}\n"
        "\n"
        f"VISIT_DATE_INFO = {vdi_repr}\n"
        "\n"
        "# Maps each variable (lowercase) to its visit label as configured in Step 10.\n"
        f"VARIABLE_VISIT_MAP = {vvm_repr}\n"
        "\n"
        f"SPECIAL_OVERRIDES = {so_repr}\n"
        "\n"
        "# Variables the user explicitly mapped in Step 9 (strategy != 'skip').\n"
        "# Source columns NOT in this set (structural fields already consumed by\n"
        "# Person/Visit/Death/etc.) are skipped — even if their name happens to\n"
        "# contain a visit-label substring like 'baseline'.\n"
        f"STEM_VARIABLES = set({sv_repr})\n"
        "\n"
        "OVERRIDE_MAP = {o['variable'].lower(): o for o in SPECIAL_OVERRIDES}\n"
        "\n"
        "visit_occurrence_id_lookup = None  # built lazily on first lookup call\n"
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
        "        return {'concept_id': var_val_map[key_vv], 'value_as_concept_id': None, 'value_as_number': None, 'unit_concept_id': None}\n"
        "    concept_id = var_map.get(variable.lower(), 0)\n"
        "    value_as_concept_id = val_map.get(key_vv)\n"
        "    if value_as_concept_id is not None:\n"
        "        return {'concept_id': concept_id, 'value_as_concept_id': int(value_as_concept_id), 'value_as_number': None, 'unit_concept_id': None}\n"
        "    try:\n"
        "        return {'concept_id': concept_id, 'value_as_concept_id': None, 'value_as_number': float(value), 'unit_concept_id': None}\n"
        "    except (ValueError, TypeError):\n"
        "        return {'concept_id': concept_id, 'value_as_concept_id': None, 'value_as_number': None, 'unit_concept_id': None}\n"
        "\n"
        "\n"
        "def create_visit_lookup():\n"
        "    \"\"\"Build {visit_source_value -> visit_occurrence_id} from visit_occurrence.csv.\"\"\"\n"
        "    global visit_occurrence_id_lookup\n"
        "    visit_occurrence_id_lookup = {}\n"
        "    output_dir = os.getenv('ETL_OUTPUT_DIR')\n"
        "    if not output_dir:\n"
        "        return\n"
        "    visit_file = os.path.join(output_dir, 'visit_occurrence.csv')\n"
        "    if os.path.exists(visit_file):\n"
        "        try:\n"
        "            vis_df = pd.read_csv(visit_file, delimiter=';', encoding='utf-8')\n"
        "            visit_occurrence_id_lookup = dict(\n"
        "                zip(vis_df['visit_source_value'].astype(str), vis_df['visit_occurrence_id'])\n"
        "            )\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: could not load visit_occurrence.csv: {e}')\n"
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
        "    source_path = os.getenv('ETL_SOURCE_PATH')\n"
        "    output_dir  = os.getenv('ETL_OUTPUT_DIR')\n"
        "\n"
        "    # --- Load source data ---\n"
        f"    df = pd.read_csv(source_path, delimiter={delim}, encoding={enc})\n"
        "\n"
        "    # --- Load lookup tables ---\n"
        "    person_lookup = {}\n"
        "    person_file = os.path.join(output_dir, 'person.csv')\n"
        "    if os.path.exists(person_file):\n"
        "        try:\n"
        "            pers_df = pd.read_csv(person_file, delimiter=';', encoding='utf-8')\n"
        "            person_lookup = dict(zip(pers_df['person_source_value'].astype(str), pers_df['person_id']))\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: could not load person.csv: {e}')\n"
        "\n"
        "    # visit_lookup is built lazily on first lookup_visit_occurrence_id call\n"
        "\n"
        "    # --- Load mapping CSVs ---\n"
        "    vm = _load_csv(os.environ.get('ETL_MAPPING_variable_mapping', ''))\n"
        "    vl = _load_csv(os.environ.get('ETL_MAPPING_value_mapping', ''))\n"
        "    vv = _load_csv(os.environ.get('ETL_MAPPING_variable_value_mapping', ''))\n"
        "    um = _load_csv(os.environ.get('ETL_MAPPING_unit_mapping', ''))\n"
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
        "    # --- Process rows ---\n"
        "    stem_id = 1\n"
        "    rows    = []\n"
        "    _stem_visit_counters = {}  # person_source_value -> int, for auto-numbering\n"
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
        "            # Determine the visit label for this entire row (once, outside the variable loop)\n"
        "            if VISIT_SOURCE_COL:\n"
        "                _row_vsv_raw = str(row.get(VISIT_SOURCE_COL, '')).strip()\n"
        "                _row_visit_label = _row_vsv_raw if (_row_vsv_raw and _row_vsv_raw != 'nan') else None\n"
        "                _row_date_col = DEFAULT_DATE_COL\n"
        "                _row_date_fmt = DEFAULT_DATE_FORMAT\n"
        "            elif AUTO_NUMBER_VISITS:\n"
        "                _stem_visit_counters[person_source_value] = _stem_visit_counters.get(person_source_value, 0) + 1\n"
        "                _row_visit_label = f'visit{_stem_visit_counters[person_source_value]}'\n"
        "                _row_date_col = DEFAULT_DATE_COL\n"
        "                _row_date_fmt = DEFAULT_DATE_FORMAT\n"
        "            else:\n"
        "                _row_visit_label = None  # resolved per-variable below\n"
        "                _row_date_col = None\n"
        "                _row_date_fmt = None\n"
        "\n"
        "            # Iterate over each clinical variable in this row\n"
        "            for variable in df.columns:\n"
        "                try:\n"
        "                    _lc = variable.lower()\n"
        "                    # Only process columns the user explicitly mapped in\n"
        "                    # Step 9. Structural fields (visit_*_date, patient_id,\n"
        "                    # death_date, gender, …) are absent from STEM_VARIABLES.\n"
        "                    if _lc not in STEM_VARIABLES:\n"
        "                        continue\n"
        "                    if VISIT_SOURCE_COL or AUTO_NUMBER_VISITS:\n"
        "                        if not _row_visit_label:\n"
        "                            continue\n"
        "                        visit_label = _row_visit_label\n"
        "                        date_col = _row_date_col\n"
        "                        date_fmt = _row_date_fmt\n"
        "                    else:\n"
        "                        visit_label = VARIABLE_VISIT_MAP.get(_lc)\n"
        "                        if visit_label is None:\n"
        "                            continue\n"
        "                        date_info = VISIT_DATE_INFO.get(visit_label, {})\n"
        "                        date_col = date_info.get('date_col', '')\n"
        "                        date_fmt = date_info.get('date_format', '%Y-%m-%d')\n"
        "\n"
        "                    raw_value = row.get(variable)\n"
        "                    if pd.isnull(raw_value) or str(raw_value).strip() in ('', 'nan'):\n"
        "                        continue\n"
        "\n"
        "                    start_date = None\n"
        "                    start_datetime = None\n"
        "                    if date_col:\n"
        "                        _raw_date = row.get(date_col)\n"
        "                        _date_str = str(_raw_date).strip() if pd.notnull(_raw_date) else ''\n"
        "                        if not _date_str or _date_str == 'nan':\n"
        "                            continue\n"
        "                        _dt = datetime.strptime(_date_str, date_fmt)\n"
        "                        start_date = _dt.date()\n"
        "                        start_datetime = _dt\n"
        "\n"
        "                    label_norm = visit_label.lower().replace(' ', '_')\n"
        "                    visit_record_source_value = f'{person_source_value}-{SOURCE_STEM}-{label_norm}'\n"
        "                    visit_occurrence_id = lookup_visit_occurrence_id(visit_record_source_value)\n"
        "                    if visit_occurrence_id is None:\n"
        "                        continue   # helper already logged the miss\n"
        "\n"
        "                    mapped = lookup_concept(variable, raw_value, var_map, val_map, var_val_map)\n"
        "                    concept_id = mapped['concept_id']\n"
        "                    # Skip when neither the variable nor the (variable, value) pair\n"
        "                    # resolved to a concept — e.g. an unmapped value under a\n"
        "                    # `map_values` strategy. No concept = no clinical event.\n"
        "                    if not concept_id:\n"
        "                        continue\n"
        "                    value_as_concept_id = mapped['value_as_concept_id']\n"
        "                    value_as_number = mapped['value_as_number']\n"
        "                    unit_concept_id = mapped['unit_concept_id']\n"
        "                    unit_source_value = None\n"
        "                    _unit_col = unit_col_map.get(variable.lower())\n"
        "                    if _unit_col:\n"
        "                        _raw_unit = row.get(_unit_col)\n"
        "                        if pd.notnull(_raw_unit) and str(_raw_unit).strip() not in ('', 'nan'):\n"
        "                            unit_source_value = str(_raw_unit).strip()\n"
        "                            _looked_up = unit_concept_map.get((variable.lower(), unit_source_value))\n"
        "                            if _looked_up is not None:\n"
        "                                unit_concept_id = _looked_up\n"
        "                            else:\n"
        "                                _info(f'INFO: variable {variable!r} — unit value {unit_source_value!r} not in unit_concept_map; unit_concept_id unchanged')\n"
        "                    operator_concept_id = None\n"
        "                    value_as_string = None\n"
        "\n"
        "                    override = OVERRIDE_MAP.get(variable.lower())\n"
        "                    if override:\n"
        "                        if override.get('field') == 'unit_concept_id' and override.get('value') is not None:\n"
        "                            unit_concept_id = int(override['value'])\n"
        "                        if override.get('value_as_string') is not None:\n"
        "                            value_as_string = str(override['value_as_string'])\n"
        "                        if override.get('value_map'):\n"
        "                            vmap_entry = override['value_map'].get(str(raw_value))\n"
        "                            if vmap_entry:\n"
        "                                for _k, _v in vmap_entry.items():\n"
        "                                    if _k == 'operator_concept_id':\n"
        "                                        operator_concept_id = int(_v)\n"
        "                                    elif _k == 'value_as_number':\n"
        "                                        value_as_number = float(_v)\n"
        "                                    elif _k == 'value_as_string':\n"
        "                                        value_as_string = str(_v)\n"
        "                                    elif _k == 'unit_concept_id':\n"
        "                                        unit_concept_id = int(_v)\n"
        "\n"
        "                    record_source_value = f'{person_source_value}-{variable}'\n"
        "                    rows.append({\n"
        "                        'id': stem_id,\n"
        "                        'domain_id': domain_map.get(variable.lower(), ''),\n"
        "                        'person_id': person_id,\n"
        "                        'visit_occurrence_id': visit_occurrence_id,\n"
        "                        'visit_detail_id': None,\n"
        "                        'concept_id': concept_id if concept_id else 0,\n"
        "                        'start_date': start_date,\n"
        "                        'start_datetime': start_datetime,\n"
        "                        'end_date': None,\n"
        "                        'end_datetime': None,\n"
        "                        'type_concept_id': 32879,\n"
        "                        'operator_concept_id': operator_concept_id,\n"
        "                        'value_as_number': value_as_number,\n"
        "                        'value_as_string': value_as_string,\n"
        "                        'value_as_concept_id': value_as_concept_id,\n"
        "                        'unit_concept_id': unit_concept_id,\n"
        "                        'unit_source_value': unit_source_value,\n"
        "                        'range_low': None,\n"
        "                        'range_high': None,\n"
        "                        'provider_id': None,\n"
        "                        'modifier_concept_id': None,\n"
        "                        'quantity': None,\n"
        "                        'value_source_value': str(raw_value),\n"
        "                        'source_value': variable,\n"
        "                        'source_concept_id': None,\n"
        "                        'record_source_value': record_source_value,\n"
        "                    })\n"
        "                    stem_id += 1\n"
        "                except Exception as _var_exc:\n"
        "                    print(f'WARNING: skipping variable {variable!r} for person {person_source_value} — {_var_exc}')\n"
        "        except Exception as e:\n"
        "            print(f'WARNING: skipping row — {e}')\n"
        "\n"
        "    # --- Write output ---\n"
        "    df_out = pd.DataFrame(rows)\n"
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
            "                'measurement_time': None,\n"
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
            "                'qualifier_concept_id': None,\n"
            "                'unit_concept_id': _si(row.get('unit_concept_id')),\n"
            "                'provider_id': _si(row.get('provider_id')),\n"
            "                'visit_occurrence_id': _si(row.get('visit_occurrence_id')),\n"
            "                'visit_detail_id': _si(row.get('visit_detail_id')),\n"
            "                'observation_source_value': _ss(row.get('source_value')),\n"
            "                'observation_source_concept_id': _si(row.get('source_concept_id'), 0),\n"
            "                'unit_source_value': _ss(row.get('unit_source_value')),\n"
            "                'qualifier_source_value': None,\n"
            "                'value_source_value': _ss(row.get('value_source_value')),\n"
            "                'obs_event_field_concept_id': None,\n"
            "                'observation_event_id': None,\n"
            "            })\n"
        )
    elif table == "drug_exposure":
        row_lines = (
            "            _end_date = _ss(row.get('end_date'))\n"
            "            rows.append({\n"
            "                'drug_exposure_id': rec_id,\n"
            "                'person_id': person_id,\n"
            "                'drug_concept_id': _si(row.get('concept_id'), 0),\n"
            "                'drug_exposure_start_date': _ss(row.get('start_date')),\n"
            "                'drug_exposure_start_datetime': _ss(row.get('start_datetime')),\n"
            "                'drug_exposure_end_date': _end_date or _ss(row.get('start_date')),\n"
            "                'drug_exposure_end_datetime': _ss(row.get('end_datetime')),\n"
            "                'verbatim_end_date': _end_date,\n"
            "                'drug_type_concept_id': _si(row.get('type_concept_id'), 32879),\n"
            "                'stop_reason': None,\n"
            "                'refills': None,\n"
            "                'quantity': _sf(row.get('quantity')),\n"
            "                'days_supply': None,\n"
            "                'sig': None,\n"
            "                'route_concept_id': None,\n"
            "                'lot_number': None,\n"
            "                'provider_id': _si(row.get('provider_id')),\n"
            "                'visit_occurrence_id': _si(row.get('visit_occurrence_id')),\n"
            "                'visit_detail_id': _si(row.get('visit_detail_id')),\n"
            "                'drug_source_value': _ss(row.get('source_value')),\n"
            "                'drug_source_concept_id': _si(row.get('source_concept_id'), 0),\n"
            "                'route_source_value': None,\n"
            "                'dose_unit_source_value': _ss(row.get('value_source_value')),\n"
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
            "                'modifier_source_value': None,\n"
            "            })\n"
        )
    else:  # condition_occurrence
        row_lines = (
            "            rows.append({\n"
            "                'condition_occurrence_id': rec_id,\n"
            "                'person_id': person_id,\n"
            "                'condition_concept_id': _si(row.get('concept_id'), 0),\n"
            "                'condition_start_date': _ss(row.get('start_date')),\n"
            "                'condition_start_datetime': _ss(row.get('start_datetime')),\n"
            "                'condition_end_date': _ss(row.get('end_date')),\n"
            "                'condition_end_datetime': _ss(row.get('end_datetime')),\n"
            "                'condition_type_concept_id': _si(row.get('type_concept_id'), 32879),\n"
            "                'condition_status_concept_id': _si(row.get('value_as_concept_id')),\n"
            "                'stop_reason': None,\n"
            "                'provider_id': _si(row.get('provider_id')),\n"
            "                'visit_occurrence_id': _si(row.get('visit_occurrence_id')),\n"
            "                'visit_detail_id': _si(row.get('visit_detail_id')),\n"
            "                'condition_source_value': _ss(row.get('source_value')),\n"
            "                'condition_source_concept_id': _si(row.get('source_concept_id'), 0),\n"
            "                'condition_status_source_value': _ss(row.get('value_source_value')),\n"
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


async def _apply_extra_instructions(code: str, instructions: str, table: str) -> str:
    """Patch a deterministically generated script with user-supplied instructions via AI."""
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    response = await client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an expert Python ETL engineer. "
                    "Apply the user's modifications to the given script exactly as requested. "
                    "Return ONLY the complete modified Python script — no markdown fences, no explanations."
                ),
            },
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
        max_tokens=8192,
    )
    content = response.choices[0].message.content or ""
    return _strip_fences(content)


async def generate_table_script(project, table: str) -> str:
    """Generate the Python ETL script for a single OMOP table."""
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
    if extra:
        code = await _apply_extra_instructions(code, extra, table)
    return code


async def generate_all_table_scripts(project) -> dict[str, str]:
    """Generate scripts for all tables configured in etl_config. Returns {table: code}."""
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
    for table, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            out[table] = f"# ERROR generating {table}: {result}"
        else:
            out[table] = result  # type: ignore[assignment]

    return out


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
