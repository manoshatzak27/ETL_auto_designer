import { useCallback, useEffect, useState } from 'react'
import {
  getConceptMatcherHealth,
  suggestValueConcepts,
  type ConceptPreference,
  type ValueConceptSuggestion,
} from '../api/client'

/** Country-level Geography concepts: SNOMED "Location" (Greece 4330430) and
 *  OSM "2nd level" (Germany 41970930). Neither vocabulary has every country,
 *  and both also hold towns that share a country's name ("Usa", "Canadá"). */
export const COUNTRY_CONCEPTS: ConceptPreference = { concept_classes: ['Location', '2nd level'] }
/** visit_concept_id: the Visit vocabulary (9201 Inpatient Visit), not the CMS
 *  Place of Service concept that scores alongside it (8717 Inpatient Hospital). */
export const VISIT_CONCEPTS: ConceptPreference = { vocabularies: ['Visit'] }
/** Settings rather than visits: care site place of service, admitted from,
 *  discharged to. */
export const PLACE_OF_SERVICE_CONCEPTS: ConceptPreference = { vocabularies: ['CMS Place of Service'] }

/** One field whose source values map to concept ids, e.g. Person's gender. */
export interface AutoFillTarget {
  /** Unique per field on the page — suggestions are kept under it. */
  key: string
  label: string
  domain: string
  /** Narrows the domain for fields that want one vocabulary of it. */
  prefer?: ConceptPreference
  /** The field's source values. */
  values: string[]
  /** What is already mapped. These values are never sent, so never changed. */
  mapped: Record<string, number>
  /** Merge the confidently matched values into the field's value map. */
  apply: (filled: Record<string, number>) => void
}

export type AutoFillSuggestions = Record<string, Record<string, ValueConceptSuggestion>>

/**
 * "Auto-fill concept IDs" for a wizard step: every unmapped value of every
 * target goes through the concept matcher; confident matches are applied,
 * weaker ones are kept as suggestions (pass `suggestions[target.key]` to that
 * field's ValueConceptMapper), the rest stay empty.
 */
export function useConceptAutoFill(projectId: string) {
  const [health, setHealth] = useState<{ available: boolean; detail?: string | null } | null>(null)
  const [running, setRunning] = useState(false)
  const [summary, setSummary] = useState<string[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<AutoFillSuggestions>({})

  useEffect(() => {
    getConceptMatcherHealth()
      .then(setHealth)
      .catch(() => setHealth({ available: false, detail: 'Concept matcher unreachable' }))
  }, [])

  const run = async (targets: AutoFillTarget[]): Promise<string[]> => {
    setRunning(true)
    setError(null)
    setSummary(null)
    try {
      const answers = await Promise.all(targets.map(async t => {
        const todo = t.values.filter(v => t.mapped[v] === undefined)
        return {
          target: t,
          results: todo.length > 0 ? await suggestValueConcepts(projectId, t.domain, todo, t.prefer) : {},
        }
      }))

      const lines: string[] = []
      const offeredByKey: AutoFillSuggestions = {}
      for (const { target, results } of answers) {
        const filled: Record<string, number> = {}
        const offered: Record<string, ValueConceptSuggestion> = {}
        for (const [value, r] of Object.entries(results)) {
          if (r.concept_id == null) continue
          if (r.status === 'auto_accept') filled[value] = r.concept_id
          else offered[value] = r
        }
        offeredByKey[target.key] = offered
        if (Object.keys(filled).length > 0) target.apply(filled)

        const total = Object.keys(results).length
        const nFilled = Object.keys(filled).length
        const nOffered = Object.keys(offered).length
        const missing = total - nFilled - nOffered
        const parts = total === 0
          ? ['all values already mapped']
          : [
              `${nFilled} of ${total} filled`,
              ...(nOffered ? [`${nOffered} to review`] : []),
              ...(missing ? [`${missing} not found`] : []),
            ]
        lines.push(`${target.label}: ${parts.join(', ')}`)
      }
      setSuggestions(prev => ({ ...prev, ...offeredByKey }))
      setSummary(lines)
      return lines
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || 'Auto-fill failed — is the concept matcher running?')
      throw new Error(detail || 'Auto-fill failed — is the concept matcher running?', { cause: e })
    } finally {
      setRunning(false)
    }
  }

  /** Forget suggestions (all, or one field's) — call when a field's column,
   *  mode or file changes, since they belong to the old values. */
  const clear = useCallback((key?: string) => {
    if (key === undefined) {
      setSuggestions({})
      setSummary(null)
    } else {
      setSuggestions(prev => ({ ...prev, [key]: {} }))
    }
  }, [])

  return { health, running, summary, error, suggestions, run, clear }
}

export type ConceptAutoFill = ReturnType<typeof useConceptAutoFill>
