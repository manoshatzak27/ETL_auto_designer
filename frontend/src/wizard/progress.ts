// Where each project is in the wizard: the step it was last open on (kept in
// this browser) and how many of its steps are done, for the Dashboard.
import type { ProjectSummary } from '../types'
import { getActiveSteps, type StepProject, type WizardSlug, type WizardStep } from './steps'

const LAST_STEP_KEY = (projectId: string) => `etl-designer:last-step:${projectId}`

export function rememberStep(projectId: string, slug: WizardSlug): void {
  try { localStorage.setItem(LAST_STEP_KEY(projectId), slug) } catch { /* storage unavailable */ }
}

export function forgetStep(projectId: string): void {
  try { localStorage.removeItem(LAST_STEP_KEY(projectId)) } catch { /* storage unavailable */ }
}

function rememberedStep(projectId: string): string | null {
  try { return localStorage.getItem(LAST_STEP_KEY(projectId)) } catch { return null }
}

/** The summary in the shape the step list reads; scripts are only marked present. */
function summaryAsStepProject(s: ProjectSummary): StepProject {
  return {
    source_filename: s.source_filename,
    etl_config: Object.fromEntries(s.enabled_tables.map(t => [t, {}])),
    generated_scripts: Object.fromEntries(s.generated_tables.map(t => [t, 'generated'])),
    mapping_files: s.has_mapping_files ? { generated: 'generated' } : {},
    stale_scripts: s.stale_scripts,
    mapping_files_stale: s.mapping_files_stale,
  }
}

export interface StepProgress {
  step: WizardStep
  done: boolean
  stale: boolean
}

export interface ProjectProgress {
  steps: StepProgress[]
  doneCount: number
  staleCount: number
  /** The step to open: the last one visited, if it is still part of the project. */
  resume: WizardStep
}

export function projectProgress(s: ProjectSummary): ProjectProgress {
  const p = summaryAsStepProject(s)
  const steps = getActiveSteps(p).map(step => {
    const complete = step.isComplete(p)
    const stale = complete && !!step.isStale?.(p)
    return { step, done: complete && !stale, stale }
  })
  const last = rememberedStep(s.id)
  return {
    steps,
    doneCount: steps.filter(x => x.done).length,
    staleCount: steps.filter(x => x.stale).length,
    resume: steps.find(x => x.step.slug === last)?.step ?? steps[0].step,
  }
}
