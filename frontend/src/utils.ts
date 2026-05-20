export function basename(path: string): string {
  return path.replace(/\\/g, '/').split('/').pop() || path
}

export function getStructuralColumns(etlConfig: Record<string, unknown>): Set<string> {
  const mapped = new Set<string>()

  for (const table of ['observation_period', 'location', 'care_site', 'provider', 'death']) {
    const cfg = etlConfig[table] as Record<string, unknown> | undefined
    if (!cfg) continue
    for (const [key, value] of Object.entries(cfg)) {
      if (key.endsWith('_col') && typeof value === 'string' && value.trim()) {
        mapped.add(value.trim())
      }
    }
  }

  const personCfg = etlConfig['person'] as { mappings?: Record<string, Record<string, unknown>> } | undefined
  if (personCfg?.mappings) {
    for (const m of Object.values(personCfg.mappings)) {
      if (m && typeof m.source_col === 'string' && m.source_col.trim()) {
        mapped.add(m.source_col.trim())
      }
    }
  }

  const visitCfg = etlConfig['visit_occurrence'] as { visit_definitions?: Array<Record<string, unknown>> } | undefined
  if (visitCfg?.visit_definitions) {
    const visitColFields = ['date_col', 'end_date_col', 'visit_source_col', 'visit_concept_source_col', 'visit_type_source_col', 'admitted_from_source_col', 'discharged_to_source_col']
    for (const def of visitCfg.visit_definitions) {
      for (const field of visitColFields) {
        if (typeof def[field] === 'string' && (def[field] as string).trim()) mapped.add((def[field] as string).trim())
      }
    }
  }

  return mapped
}
