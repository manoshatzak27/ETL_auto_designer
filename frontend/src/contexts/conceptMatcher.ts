import { createContext, useContext } from 'react'
import type { ConceptMatcherHealth } from '../api/client'

export type ConceptMatcherStatus = ConceptMatcherHealth['status']

export interface ConceptMatcherApi {
  /** 'checking' until the first answer arrives. */
  status: ConceptMatcherStatus | 'checking'
  reason: string
  hint: string
  commands: string[]
  raw: string | null
  /** The full last answer, including the vocabulary fields when ready. */
  health: ConceptMatcherHealth | null
  lastChecked: Date | null
  /** A check is in flight. */
  checking: boolean
  recheck: () => void
}

export const ConceptMatcherContext = createContext<ConceptMatcherApi>({
  status: 'checking', reason: '', hint: '', commands: [], raw: null,
  health: null, lastChecked: null, checking: false, recheck: () => {},
})

/** The concept matcher's status, shared by every wizard step and polled by
 *  ConceptMatcherProvider — the one place it is fetched. */
export function useConceptMatcherStatus() {
  return useContext(ConceptMatcherContext)
}
