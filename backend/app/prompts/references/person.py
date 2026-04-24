from datetime import datetime
from typing import List
import logging
from src.main.python.model.cdm import Person

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def basedata_to_person(
    wrapper: object,
    id_col: str = 'code',
) -> List[Person]:

    logging.info("Calling wrapper.get_basedata()...")
    base_data = wrapper.get_basedata()
    logger.info(f"Retrieved base_data with {len(list(base_data))} rows")

    records_to_insert = []

    for row in base_data:
        try:
            person_id = int(float(row[id_col]))
            gender_raw = float(row['gender']) if row['gender'] != '' else None
            fechanac_raw = row.get('fechanac', '').strip()

            if gender_raw is None or not fechanac_raw:
                continue

            if gender_raw == 1.0:
                gender_concept_id = 8507  # Male
            elif gender_raw == 2.0:
                gender_concept_id = 8532  # Female
            else:
                gender_concept_id = 0

            try:
                dob = datetime.strptime(fechanac_raw, "%Y-%m-%d")
            except ValueError:
                logger.warning(f"Invalid date format for fechanac: {fechanac_raw}")
                continue

            year_of_birth = dob.year
            month_of_birth = dob.month
            day_of_birth = dob.day

            record = Person(
                person_id=person_id,
                person_source_value=str(row[id_col]),
                year_of_birth=year_of_birth,
                month_of_birth=month_of_birth,
                day_of_birth=day_of_birth,
                gender_concept_id=gender_concept_id,
                race_concept_id=0,
                ethnicity_concept_id=0,
                gender_source_concept_id=0,
                race_source_concept_id=0,
                ethnicity_source_concept_id=0
            )
            records_to_insert.append(record)

        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Skipping row due to error: {e}")
            continue

    return records_to_insert
