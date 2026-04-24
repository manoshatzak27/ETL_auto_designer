from src.main.python.model.cdm import StemTable
from datetime import datetime
from src.main.python.util.create_record_source_value import (
    create_basedata_visit_record_source_value,
    create_basedata_stem_table_record_source_value
)
from typing import List
import logging

# Variables mapped to ONSET visit
onset_variables = {
    'active0', 'cannabinar0', 'educlvl', 'livparnt', 'seclvl', 'single', 'studento', 'unempl',
    'diag6m', 'fampsic', 'hospita', 'tp_adolescenciatardia', 'disorgan0', 'dup', 'dui',
    'bprs0', 'tp_adolescenciatemprana', 'tp_general', 'tp_adulto', 'tp_infancia', 'negative0',
    'dap', 'dat', 'saps0', 'positive0', 'sans0', 'contact10y'
}

# Variables mapped to 10-year FOLLOW-UP visit
followup_10y_variables = {
    'active10y', 'das10ydict', 'recovery10m', 'sans10y',
    'negativa10y', 'dasgl10y', 'positiva10y', 'saps10y', 'bprs10y'
}

def basedata_to_stem_table(
    wrapper: object,
    id_col: str = 'code',
) -> List[StemTable]:

    basedata = wrapper.get_basedata()
    records_to_insert: List[StemTable] = []

    for row in basedata:
        fechaini_str = row.get('fechaini', '').strip()
        if not fechaini_str:
            continue

        try:
            onset_date = datetime.strptime(fechaini_str, "%Y-%m-%d")
        except ValueError:
            continue

        fechaevaluacion_str = row.get('fechaevaluacion10a', '').strip()
        followup_date = None
        has_followup_date = False

        if fechaevaluacion_str:
            try:
                followup_date = datetime.strptime(fechaevaluacion_str, "%Y-%m-%d")
                has_followup_date = True
            except ValueError:
                pass

        try:
            person_id = wrapper.lookup_person_id(row[id_col])
        except Exception:
            continue

        for variable, value in row.items():
            if value == '':
                continue

            visit_type = None
            start_date = None
            start_datetime = None

            if variable in onset_variables:
                visit_type = 'onset'
                start_date = onset_date.date()
                start_datetime = onset_date
            elif variable in followup_10y_variables:
                if not has_followup_date:
                    print(f"WARNING: Patient {row[id_col]} has 10-year variable '{variable}' = '{value}' but no FechaEvaluacion10A date")
                    continue
                visit_type = 'followup_10y'
                start_date = followup_date.date()
                start_datetime = followup_date
            else:
                continue

            try:
                visit_record_source_value = create_basedata_visit_record_source_value(row[id_col], visit_type)
                visit_occurrence_id = wrapper.lookup_visit_occurrence_id(visit_record_source_value)
            except Exception:
                continue

            try:
                target = wrapper.variable_mapper.lookup(variable, value)
                concept_id = target.concept_id
                value_as_concept_id = target.value_as_concept_id
                value_as_number = target.value_as_number
                unit_concept_id = target.unit_concept_id
                source_value = target.source_value
                value_source_value = target.value_source_value
            except Exception:
                continue

            operator_concept_id = None
            value_as_string = None
            if variable in ['dui', 'dup', 'dap', 'dat']:
                unit_concept_id = 9580

            if variable == 'das10ydic':
                if value == '1.0':
                    operator_concept_id = 4171754
                    value_as_number = 1
                elif value == '2.0':
                    operator_concept_id = 4171755
                    value_as_number = 2
            elif variable == 'dasgl10y':
                source_value = 'DAS GLOBAL'
            elif variable == 'seclvl':
                value_as_string = 'low' if value == '1.0' else 'medium or higher'
            elif variable == 'tp_adolescenciatardia':
                source_value = 'Late adolescence'
            elif variable == 'tp_adolescenciatemprana':
                source_value = 'Early adolescence'
            elif variable == 'tp_general':
                source_value = 'General'
            elif variable == 'tp_adulto':
                source_value = 'Adult'
            elif variable == 'tp_infancia':
                source_value = 'Childhood'
            elif variable == 'recovery10m':
                value_as_string = 'Recovered' if value == '1.0' else 'Not recovered'

            record_source_value = create_basedata_stem_table_record_source_value(row[id_col], variable)

            record = StemTable(
                person_id=person_id,
                visit_occurrence_id=visit_occurrence_id,
                start_date=start_date,
                start_datetime=start_datetime,
                concept_id=concept_id if concept_id else 0,
                value_as_concept_id=value_as_concept_id,
                value_as_number=value_as_number,
                value_as_string=value_as_string,
                unit_concept_id=unit_concept_id,
                source_value=source_value,
                value_source_value=value_source_value,
                operator_concept_id=operator_concept_id,
                type_concept_id=32879,
                record_source_value=record_source_value
            )
            records_to_insert.append(record)

    return records_to_insert
