#!/usr/bin/env python3
from src.main.python.model.cdm import ObservationPeriod
from datetime import datetime
from typing import List

def basedata_to_observation_period(
    wrapper: object,
    id_col: str = 'code',
) -> List[ObservationPeriod]:
    """
    Transform basedata into observation period records using fechaini (onset date)
    and FechaEvaluacion10A (actual 10-year follow-up date).
    """

    basedata = wrapper.get_basedata()
    records_to_insert: List[ObservationPeriod] = []

    for row in basedata:
        fechaini_str = row.get('fechaini', '').strip()
        if not fechaini_str:
            continue

        try:
            person_id: int = wrapper.lookup_person_id(row[id_col])
        except Exception:
            continue

        try:
            onset_date: datetime = datetime.strptime(fechaini_str, "%Y-%m-%d")
        except ValueError:
            continue

        fechaevaluacion_str = row.get('fechaevaluacion10a', '').strip()
        if fechaevaluacion_str:
            try:
                followup_date: datetime = datetime.strptime(fechaevaluacion_str, "%Y-%m-%d")
                end_date = followup_date.date()
            except ValueError:
                continue
        else:
            end_date = onset_date.date()

        observation_period_record = ObservationPeriod(
            person_id=person_id,
            observation_period_start_date=onset_date.date(),
            observation_period_end_date=end_date,
            period_type_concept_id=32879,  # Registry
        )
        records_to_insert.append(observation_period_record)

    return records_to_insert
