# Copyright 2019 The Hyve
#
# Licensed under the GNU General Public License, version 3,
# or (at your option) any later version (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# https://www.gnu.org/licenses/
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.

# !/usr/bin/env python3
from src.main.python.model.cdm import StemTable
from datetime import date, datetime
import calendar
from src.main.python.util.create_record_source_value import create_basedata_visit_record_source_value
from src.main.python.util.create_record_source_value import create_basedata_stem_table_record_source_value
import logging

# Month offsets per visit type — must stay in sync with basedata_to_visit.py
VISIT_MONTH_OFFSETS = {
    'baseline':      0,
    'after3months':  3,
    'after6months':  6,
    'after9months':  9,
    'after12months': 12,
    'after18months': 18,
    'after24months': 24,
}

BASELINE_DATE = datetime(2020, 1, 1)

def add_months(dt, months):
    """Add a number of months to a datetime, clamping to the last day of the target month."""
    total_months = dt.month - 1 + months
    year = dt.year + total_months // 12
    month = total_months % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)

def get_visit_type(variable, wrapper):
    """Derive the OMOP visit type name from a source variable's timepoint suffix."""
    var = variable.lower()
    if var.endswith('_bl'):
        return wrapper.VisitType.baseline.name
    elif var.endswith('_12wk'):
        return wrapper.VisitType.after3months.name
    elif var.endswith('_24wk'):
        return wrapper.VisitType.after6months.name
    elif var.endswith('_36wk'):
        return wrapper.VisitType.after9months.name
    elif var.endswith('_48wk'):
        return wrapper.VisitType.after12months.name
    elif var.endswith('_72wk'):
        return wrapper.VisitType.after18months.name
    elif var.endswith('_96wk'):
        return wrapper.VisitType.after24months.name
    return None

# No
LIST1 = [
    "improves_exercise_bl",
    "pain_night_bl",
    "waking_night_bl",
    "buttock_pain_bl",
    "hypertension_bl",
    "angina_bl",
    "infarction_bl",
    "stroke_bl",
    "diabetes_bl",
    "dvtpe_bl",
    "dyslipidaemia_bl",
    "uveitis_ever_bl",
    "uveitis_history_bl",
    "psoriasis_history_bl",
    "crohns_history_bl",
    "crohns_ever_bl",
    "spa_history_bl",
    "ibp_bl",
    "currentpsoriasis_caspar_bl",
    "historypsoriasis_caspar_bl",
    "familyhistory_caspar_bl",
    "naildystrophy_caspar_bl",
    "currentdactylitis_caspar_bl",
    "rheumnegative_caspar_bl",
    "axial_disease_bl",
    "axial_disease_12wk",
    "axial_disease_24wk",
    "axial_disease_36wk",
    "axial_disease_48wk",
    "axial_disease_72wk",
    "axial_disease_96wk",
    'any_sideeffect_12wk',
    'any_sideeffect_24wk',
    'any_sideeffect_36wk',
    'any_sideeffect_48wk',
    'any_sideeffect_72wk',
    'any_sideeffect_96wk',
    'adherence_12wk',
    'adherence_24wk',
    'adherence_36wk',
    'adherence_48wk',
    'adherence_72wk',
    'adherence_96wk'
]

# never
LIST2 =[
    'neck_pain_bl','back_pain_bl','chest_pain_bl','enthesitis_pain_bl','plantar_pain_bl',
    'dactylitis_pain_bl','uveitis_pain_bl','psoriasis_pain_bl','crohns_pain_bl','spa_pain_bl',                           
]

#none
LIST3 = [
    'headache_12wk', 'headache_24wk', 'headache_36wk', 'headache_48wk', 'headache_72wk', 'headache_96wk', 
    'fatigue_sideeffect_12wk', 'fatigue_sideeffect_24wk', 'fatigue_sideeffect_36wk', 'fatigue_sideeffect_48wk', 'fatigue_sideeffect_72wk', 'fatigue_sideeffect_96wk', 
    'hairloss_12wk', 'hairloss_24wk', 'hairloss_36wk', 'hairloss_48wk', 'hairloss_72wk', 'hairloss_96wk', 
    'nausea_12wk', 'nausea_24wk', 'nausea_36wk', 'nausea_48wk', 'nausea_72wk', 'nausea_96wk', 
    'vomiting_12wk', 'vomiting_24wk', 'vomiting_36wk', 'vomiting_48wk', 'vomiting_72wk', 'vomiting_96wk', 
    'diarrhoea_12wk', 'diarrhoea_24wk', 'diarrhoea_36wk', 'diarrhoea_48wk', 'diarrhoea_72wk', 'diarrhoea_96wk', 
    'gi_upset_12wk', 'gi_upset_24wk', 'gi_upset_36wk', 'gi_upset_48wk', 'gi_upset_72wk', 'gi_upset_96wk', 
    'rash_12wk', 'rash_24wk', 'rash_36wk', 'rash_48wk', 'rash_72wk', 'rash_96wk', 
    'reaction_12wk', 'reaction_24wk', 'reaction_36wk', 'reaction_48wk', 'reaction_72wk', 'reaction_96wk', 
    'liver_12wk', 'liver_24wk', 'liver_36wk', 'liver_48wk', 'liver_72wk', 'liver_96wk', 
    'leucopaenia_12wk', 'leucopaenia_24wk', 'leucopaenia_36wk', 'leucopaenia_48wk', 'leucopaenia_72wk', 'leucopaenia_96wk', 
    'thrombocytopaenia_12wk', 'thrombocytopaenia_24wk', 'thrombocytopaenia_36wk', 'thrombocytopaenia_48wk', 'thrombocytopaenia_72wk', 'thrombocytopaenia_96wk', 
    'respinfection_12wk', 'respinfection_24wk', 'respinfection_36wk', 'respinfection_48wk', 'respinfection_72wk', 'respinfection_96wk', 
    'uti_12wk', 'uti_24wk', 'uti_36wk', 'uti_48wk', 'uti_72wk', 'uti_96wk', 
    'infection_12wk', 'infection_24wk', 'infection_36wk', 'infection_48wk', 'infection_72wk', 'infection_96wk'
]

#negative
LIST4 = ['hlab27_bl']


def basedata_to_stem_table(wrapper) -> list:

    basedata = wrapper.get_basedata()

    records_to_insert = []

    p_id = 1

    # Set operator_concept_id to None until proven otherwise
    operator_concept_id = None

    for row in basedata:

        for variable, value in row.items():

            # Skip any variable with a missing/null value
            if value is None or value == "":
                continue

            if value in ['no', 'No'] and variable in LIST1:
                continue                
            if value in ['never', 'Never'] and variable in LIST2:
                continue
            if value in ['none','None'] and variable in LIST3:
                continue
            if value in ['negative','Negative'] and variable in LIST4:
                continue
            if variable == 'no_improve_rest_bl' and value == 'yes':
                continue


            # onset_40years_bl has no value mapping so the mapper treats it as numeric.
            # Convert yes/no to 1/0 so float() conversion doesn't crash.
            # any_sideeffect and adherence have proper "Yes" entries in variable_value_mapping.csv
            # so they must reach the mapper as strings, not integers.
            if variable == 'onset_40years_bl' and isinstance(value, str):
                value_lower = value.strip().lower()
                if value_lower in ['yes']:
                    value = 1
                elif value_lower in ['no']:
                    value = 0

            #add Duration3months EMS30mins
            if variable == 'onset_40years_bl':
                target = wrapper.variable_mapper.lookup(variable, value)
                concept_id = target.concept_id if target.concept_id else 32
                value_as_concept_id = target.value_as_concept_id
                value_as_number = None
                unit_concept_id = target.unit_concept_id
                source_value = target.source_value
                value_source_value = target.value_source_value
                value_as_string = '<40' if value == 1 else '>40' if value == 0 else None

            elif (variable in ['vas_remain_bl','vas_remain_12wk','vas_remain_24wk','vas_remain_36wk',
                            'vas_remain_48wk','vas_remain_72wk','vas_remain_96wk',
                            ]
                            or variable in ['mda_bl','mda_12wk','mda_24wk','mda_36wk','mda_48wk','mda_72wk','mda_96wk']) and isinstance(value, str):

                # These variables have a variable-level concept in variable_mapping.csv
                # but no entry in value_mapping.csv, so the regular mapper.lookup() path
                # would try float(value) and crash on strings like "MDA achieved".
                # Pull the concept_id directly to bypass numeric coercion; otherwise
                # concept_id=0 would cause the row to be filtered out by every
                # stem_table_to_<domain> post-processing query.
                value_as_string = value  # Keep original case
                value_as_number = None
                value_as_concept_id = None
                unit_concept_id = None
                concept_id = wrapper.variable_mapper.variable_to_concept.get(variable.lower(), 0)
                source_value = variable
                value_source_value = value

                if concept_id == 0:
                    logging.warning(
                        'There is no target_concept_id for variable "{}" and value "{}"'.format(variable, value))


            else:
                # Extract variable and value form mapping tables
                target = wrapper.variable_mapper.lookup(variable, value)

                # Set stem table values
                concept_id = target.concept_id if target.concept_id else 32
                value_as_concept_id = target.value_as_concept_id
                value_as_number = target.value_as_number
                unit_concept_id = target.unit_concept_id
                source_value = target.source_value
                value_source_value = target.value_source_value
                value_as_string = None

                # Give warning when vocabulary mapping is missing
                if target.concept_id is None:
                    logging.warning(
                        'There is no target_concept_id for variable "{}" and value "{}"'.format(variable, value))

            visit_type = get_visit_type(variable, wrapper)
            if visit_type is None:
                continue

            # Compute visit date from the visit type offset
            visit_datetime = add_months(BASELINE_DATE, VISIT_MONTH_OFFSETS[visit_type])

            visit_record_source_value = create_basedata_visit_record_source_value(str(p_id), visit_type)
            visit_occurrence_id = wrapper.lookup_visit_occurrence_id(visit_record_source_value)

            # Add record source value to Stem Table
            stem_table_record_source_value = create_basedata_stem_table_record_source_value(str(p_id), variable)

            record = StemTable(
                person_id=int(p_id),
                visit_occurrence_id=visit_occurrence_id,
                start_date=visit_datetime.date(),
                start_datetime=visit_datetime,
                concept_id=concept_id,
                value_as_concept_id=value_as_concept_id,
                value_as_number=value_as_number,
                value_as_string=value_as_string,
                unit_concept_id=unit_concept_id if unit_concept_id else None,
                source_value=source_value,
                value_source_value=value_source_value,
                operator_concept_id=operator_concept_id,
                type_concept_id=32879,
                record_source_value=stem_table_record_source_value
            )

            # Add debug here
            # logging.info(f"APPENDING NORMAL RECORD - variable: {variable}, concept_id: {record.concept_id}, source_value: {record.source_value}")

            records_to_insert.append(record)
            
        p_id += 1

    return records_to_insert


if __name__ == '__main__':
    from src.main.python.database.database import Database
    from src.main.python.wrapper import Wrapper

    db = Database('postgresql://postgres@localhost:5432/postgres')  # A mock database object
    w = Wrapper(db, '../../../../resources/source_data', '../../../../resources/mapping_tables')
    for x in basedata_to_stem_table(w):
        print(x.__dict__)
