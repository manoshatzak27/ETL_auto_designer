from datetime import datetime, timedelta
import logging
from src.main.python.model.cdm import Death
from typing import List
from src.main.python.util.create_record_source_value import create_basedata_visit_record_source_value

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def basedata_to_death(
    wrapper: object,
    id_col: str = 'code',
) -> List[Death]:
    """
    Transform basedata into death records using fechaini as onset.
    Death recorded when Contact10y == 5.0, and death date = fechaini + 10 years.
    """

    basedata = wrapper.get_basedata()
    records_to_insert: List[Death] = []

    for row in basedata:
        patient_id = row.get(id_col)
        if not patient_id:
            continue

        try:
            person_id: int = wrapper.lookup_person_id(patient_id)
        except Exception as e:
            logger.error(f"Error looking up person_id for patient {patient_id}: {e}")
            continue

        contact10y_value = row.get('contact10y', None)
        if contact10y_value != '5.0' and contact10y_value != 5.0:
            continue  # Skip if not marked as deceased

        fechaini_str = row.get('fechaini', '').strip()
        if not fechaini_str:
            logger.warning(f"Missing fechaini for patient {patient_id}")
            continue

        try:
            onset_date = datetime.strptime(fechaini_str, "%Y-%m-%d")
        except ValueError:
            logger.error(f"Invalid fechaini format for patient {patient_id}: {fechaini_str}")
            continue

        death_datetime = onset_date + timedelta(days=365.25 * 10)
        death_date = death_datetime.date()

        record = Death(
            person_id=person_id,
            death_date=death_date,
            death_datetime=death_datetime,
            death_type_concept_id=32879,  # Registry
            cause_concept_id=None,
            cause_source_value=None,
            cause_source_concept_id=None
        )
        records_to_insert.append(record)

    return records_to_insert
