import { createContext, useContext } from 'react'
import type { WizardSlug } from '../wizard/steps'

/** What auto-run did on one step. */
export interface AutoRunStepReport {
  slug: WizardSlug
  label: string
  lines: string[]
  error?: string
}

export interface AutoRunState {
  running: boolean
  queue: { slug: WizardSlug; label: string }[]
  index: number
  reports: AutoRunStepReport[]
  stopped: boolean
}

export interface AutoRunApi {
  state: AutoRunState
  /** The step auto-run is working on now, or null. */
  current: WizardSlug | null
  start: (steps: { slug: WizardSlug; label: string }[]) => void
  /** Called by a step when it is done; moves on to the next one. */
  finishStep: (report: AutoRunStepReport) => void
  stop: () => void
  dismiss: () => void
}

export const IDLE: AutoRunState = { running: false, queue: [], index: 0, reports: [], stopped: false }

export const AutoRunContext = createContext<AutoRunApi>({
  state: IDLE,
  current: null,
  start: () => {},
  finishStep: () => {},
  stop: () => {},
  dismiss: () => {},
})

export function useAutoRun() {
  return useContext(AutoRunContext)
}
