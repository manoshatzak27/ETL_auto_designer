// Single source of truth for the wizard's dynamic step list.
//
// Optional OMOP tables (Location, Care Site, Provider, Death) are inserted
// between Source and Person in FK-dependency order based on the project's
// `etl_config[table].enabled` flag, which is toggled by the picker on the
// Source step (Step1_Upload.tsx).
//
// Domain-routed tables (measurement, observation, drug_exposure,
// procedure_occurrence, condition_occurrence) are never their own step —
// they're auto-derived from stem_table during code generation.

import type { Project } from '../types'

export type WizardSlug =
  | 'source'
  | 'location'
  | 'care-site'
  | 'provider'
  | 'person'
  | 'visit'
  | 'obs-period'
  | 'death'
  | 'concepts'
  | 'stem-table'
  | 'finalize'

export interface WizardStep {
  slug: WizardSlug
  label: string
  short: string                   // short form used in the progress chip
  configKey?: string              // key into project.etl_config
  scriptKey?: string              // key into project.generated_scripts for completeness
  isOptional: boolean
  isComplete: (project: Project) => boolean
}

// Tables that can be toggled on/off from the Source step's picker.
// Other tables (person, visit_occurrence, observation_period, stem_table)
// are always required.
export const OPTIONAL_TABLES = ['location', 'care_site', 'provider', 'death'] as const
export type OptionalTable = typeof OPTIONAL_TABLES[number]

// Helper: read enabled flag with backward-compat semantics.
// New projects (no config key) → return false; user must opt in via picker.
// Existing projects (any config saved, no explicit enabled key) → return true.
// Explicit enabled: false → return false.
export function isOptionalTableEnabled(project: Project, table: OptionalTable): boolean {
  const cfg = project.etl_config?.[table] as { enabled?: boolean } | undefined
  if (!cfg) return false
  return cfg.enabled !== false
}

const hasScript = (key: string) => (project: Project) =>
  !!(project.generated_scripts && project.generated_scripts[key])

// Canonical step registry — FK-dependency order. getActiveSteps filters this.
export const ALL_STEPS: WizardStep[] = [
  {
    slug: 'source',
    label: 'Source',
    short: '1',
    isOptional: false,
    isComplete: p => !!p.source_filename,
  },
  {
    slug: 'location',
    label: 'Location',
    short: 'L',
    configKey: 'location',
    scriptKey: 'location',
    isOptional: true,
    isComplete: hasScript('location'),
  },
  {
    slug: 'care-site',
    label: 'Care Site',
    short: 'C',
    configKey: 'care_site',
    scriptKey: 'care_site',
    isOptional: true,
    isComplete: hasScript('care_site'),
  },
  {
    slug: 'provider',
    label: 'Provider',
    short: 'P',
    configKey: 'provider',
    scriptKey: 'provider',
    isOptional: true,
    isComplete: hasScript('provider'),
  },
  {
    slug: 'person',
    label: 'Person',
    short: '2',
    configKey: 'person',
    scriptKey: 'person',
    isOptional: false,
    isComplete: hasScript('person'),
  },
  {
    slug: 'visit',
    label: 'Visit',
    short: '3',
    configKey: 'visit_occurrence',
    scriptKey: 'visit_occurrence',
    isOptional: false,
    isComplete: hasScript('visit_occurrence'),
  },
  {
    slug: 'obs-period',
    label: 'Obs. Period',
    short: '4',
    configKey: 'observation_period',
    scriptKey: 'observation_period',
    isOptional: false,
    isComplete: hasScript('observation_period'),
  },
  {
    slug: 'death',
    label: 'Death',
    short: 'D',
    configKey: 'death',
    scriptKey: 'death',
    isOptional: true,
    isComplete: hasScript('death'),
  },
  {
    slug: 'concepts',
    label: 'Concepts',
    short: '5',
    isOptional: false,
    isComplete: p => Object.keys(p.mapping_files || {}).length > 0,
  },
  {
    slug: 'stem-table',
    label: 'Stem Table',
    short: '6',
    configKey: 'stem_table',
    scriptKey: 'stem_table',
    isOptional: false,
    isComplete: hasScript('stem_table'),
  },
  {
    slug: 'finalize',
    label: 'Generate + Load',
    short: '7',
    isOptional: false,
    // "Done" once at least one script has been generated. Finer-grained
    // completion (execution succeeded, DB loaded) lives inside the page.
    isComplete: p => Object.keys(p.generated_scripts || {}).length > 0,
  },
]

export function getActiveSteps(project: Project): WizardStep[] {
  return ALL_STEPS.filter(step => {
    if (!step.isOptional) return true
    return isOptionalTableEnabled(project, step.configKey as OptionalTable)
  })
}

export function getStepBySlug(slug: string): WizardStep | undefined {
  return ALL_STEPS.find(s => s.slug === slug)
}

export function getAdjacentSlugs(
  project: Project,
  slug: WizardSlug,
): { prev?: WizardSlug; next?: WizardSlug } {
  const active = getActiveSteps(project)
  const i = active.findIndex(s => s.slug === slug)
  if (i === -1) return {}
  return {
    prev: i > 0 ? active[i - 1].slug : undefined,
    next: i < active.length - 1 ? active[i + 1].slug : undefined,
  }
}

// Map legacy numeric step URLs to their new slug. Used by App.tsx to redirect
// `/project/:id/step/11` (etc.) so old bookmarks still work.
export const LEGACY_NUMERIC_SLUGS: Record<string, WizardSlug> = {
  '1': 'source',
  '2': 'location',
  '3': 'care-site',
  '4': 'provider',
  '5': 'person',
  '6': 'visit',
  '7': 'obs-period',
  '8': 'death',
  '9': 'concepts',
  '10': 'stem-table',
  '11': 'finalize',
  '12': 'finalize',
}
