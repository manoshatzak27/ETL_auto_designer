/**
 * Recursively walks a config object and collects any string values
 * stored under keys ending in `_col` — the naming convention used
 * for every source-column mapping in this project.
 */
export function extractMappedCols(config: unknown): Set<string> {
  const result = new Set<string>()
  collect(config, result)
  return result
}

function collect(obj: unknown, result: Set<string>): void {
  if (!obj || typeof obj !== 'object') return
  if (Array.isArray(obj)) {
    for (const item of obj) collect(item, result)
    return
  }
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    if (key.endsWith('_col') && typeof value === 'string' && value) {
      result.add(value)
    } else {
      collect(value, result)
    }
  }
}

/**
 * Returns the set of source columns already mapped in any table config
 * OTHER than `excludeTable`. Pass additional table keys in `extraExclude`
 * to suppress them too (e.g. obs-period passes ['visit_occurrence'] so
 * visit columns remain available there).
 */
export function getCrossStepUsedCols(
  etlConfig: Record<string, unknown>,
  excludeTable: string,
  extraExclude: string[] = [],
): Set<string> {
  const result = new Set<string>()
  for (const [table, config] of Object.entries(etlConfig)) {
    if (table === excludeTable || extraExclude.includes(table)) continue
    for (const col of extractMappedCols(config)) result.add(col)
  }
  return result
}
