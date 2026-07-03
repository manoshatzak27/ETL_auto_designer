export function basename(path: string): string {
  return path.replace(/\\/g, '/').split('/').pop() || path
}

const STRUCTURAL_TABLES = ['observation_period', 'location', 'care_site', 'provider', 'death', 'person', 'visit_occurrence']

// Recursively walks a table config, following `file_configs` (a per-filename map,
// as used by person/location/care_site/provider, or a per-filename array, as used
// by visit_occurrence) so that every `*_col` value — however deeply nested (e.g.
// person's `mappings.<field>.source_col`) — is attributed to the source file it
// actually belongs to, not just whichever file happens to be active.
function collectColFiles(obj: unknown, filename: string | null, out: Map<string, string | null>): void {
  if (!obj || typeof obj !== 'object') return
  if (Array.isArray(obj)) {
    for (const item of obj) {
      const ownFilename = (item && typeof item === 'object' && typeof (item as Record<string, unknown>).source_filename === 'string')
        ? (item as Record<string, unknown>).source_filename as string
        : filename
      collectColFiles(item, ownFilename, out)
    }
    return
  }
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    if (key === 'file_configs' && value && typeof value === 'object') {
      if (Array.isArray(value)) {
        collectColFiles(value, filename, out)
      } else {
        for (const [fname, fileCfg] of Object.entries(value as Record<string, unknown>)) {
          collectColFiles(fileCfg, fname, out)
        }
      }
      continue
    }
    if (key.endsWith('_col') && typeof value === 'string' && value.trim()) {
      out.set(value.trim(), filename)
    } else if (value && typeof value === 'object') {
      collectColFiles(value, filename, out)
    }
  }
}

// Returns col → source filename (null = structural but file unknown → exclude globally).
export function getStructuralColFileMap(etlConfig: Record<string, unknown>): Map<string, string | null> {
  const map = new Map<string, string | null>()
  for (const table of STRUCTURAL_TABLES) {
    const cfg = etlConfig[table] as Record<string, unknown> | undefined
    if (!cfg) continue
    const rootFilename = (cfg.source_filename as string | undefined) ?? null
    collectColFiles(cfg, rootFilename, map)
  }
  return map
}

export function getStructuralColumns(etlConfig: Record<string, unknown>): Set<string> {
  return new Set(getStructuralColFileMap(etlConfig).keys())
}
