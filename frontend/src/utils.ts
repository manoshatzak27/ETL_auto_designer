export function basename(path: string): string {
  return path.replace(/\\/g, '/').split('/').pop() || path
}

// Returns col → source filename (null = structural but file unknown → exclude globally).
export function getStructuralColFileMap(etlConfig: Record<string, unknown>): Map<string, string | null> {
  const map = new Map<string, string | null>()

  const add = (col: unknown, filename: string | null | undefined) => {
    if (typeof col === 'string' && col.trim()) {
      map.set(col.trim(), filename ?? null)
    }
  }

  for (const table of ['observation_period', 'location', 'care_site', 'provider', 'death']) {
    const cfg = etlConfig[table] as Record<string, unknown> | undefined
    if (!cfg) continue
    const filename = (cfg.source_filename as string | undefined) ?? null
    for (const [key, value] of Object.entries(cfg)) {
      if (key.endsWith('_col')) add(value, filename)
    }
  }

  const personCfg = etlConfig['person'] as { mappings?: Record<string, Record<string, unknown>>; source_filename?: string } | undefined
  if (personCfg?.mappings) {
    const filename = personCfg.source_filename ?? null
    for (const m of Object.values(personCfg.mappings)) {
      if (m && typeof m.source_col === 'string') add(m.source_col, filename)
    }
  }

  const visitCfg = etlConfig['visit_occurrence'] as {
    visit_definitions?: Array<Record<string, unknown>>
    visit_source_col?: string
    source_filename?: string
  } | undefined
  if (visitCfg) {
    const filename = visitCfg.source_filename ?? null
    add(visitCfg.visit_source_col, filename)
    const visitColFields = ['date_col', 'time_col', 'end_date_col', 'end_time_col', 'visit_concept_source_col', 'visit_type_source_col', 'admitted_from_source_col', 'discharged_to_source_col']
    for (const def of visitCfg.visit_definitions || []) {
      for (const field of visitColFields) add(def[field], filename)
    }
  }

  return map
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

  const visitCfg = etlConfig['visit_occurrence'] as { visit_definitions?: Array<Record<string, unknown>>; visit_source_col?: string } | undefined
  if (visitCfg?.visit_source_col?.trim()) mapped.add(visitCfg.visit_source_col.trim())
  if (visitCfg?.visit_definitions) {
    const visitColFields = ['date_col', 'time_col', 'end_date_col', 'end_time_col', 'visit_concept_source_col', 'visit_type_source_col', 'admitted_from_source_col', 'discharged_to_source_col']
    for (const def of visitCfg.visit_definitions) {
      for (const field of visitColFields) {
        if (typeof def[field] === 'string' && (def[field] as string).trim()) mapped.add((def[field] as string).trim())
      }
    }
  }

  return mapped
}
