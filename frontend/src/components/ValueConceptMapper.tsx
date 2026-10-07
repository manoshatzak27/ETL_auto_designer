import { useState, useEffect } from 'react'
import { Label } from '@/components/ui/label'
import { lookupConceptDomain, standardConceptError, invalidReasonLabel, type ValueConceptSuggestion } from '../api/client'
import DomainConceptSearch from './DomainConceptSearch'
import { Loader2, AlertTriangle, CheckCircle, X, Search } from 'lucide-react'

interface Props {
  label: string
  sourceValues: string[]
  mapping: Record<string, number>
  onChange: (mapping: Record<string, number>) => void
  hint?: string
  /** If set, concept IDs outside this domain are rejected instead of accepted. */
  expectedDomain?: string
  /** With expectedDomain, enables a concept search beside every value. */
  projectId?: string
  /** Suggestions not confident enough to fill in on their own, keyed by source
   *  value — offered for one-click acceptance on unmapped rows. */
  suggestions?: Record<string, ValueConceptSuggestion>
}

/** The matcher's top candidates for a value auto-fill didn't set, with their
 *  scores (0–100). Usually the best one scored below 90, but a tie or a field's
 *  vocabulary preference can hold back a higher score too. The suggested
 *  concept comes first. */
function SuggestionList({ suggestion, onUse }: {
  suggestion: ValueConceptSuggestion
  onUse: (conceptId: number) => void
}) {
  const rows: { concept_id: number; concept_name: string | null; vocabulary_id: string | null; score: number | null }[] = []
  const seen = new Set<number>()
  const add = (r: (typeof rows)[number]) => {
    if (!seen.has(r.concept_id)) { seen.add(r.concept_id); rows.push(r) }
  }
  const suggested = suggestion.candidates.find(c => c.concept_id === suggestion.concept_id)
  add(suggested ?? {
    concept_id: suggestion.concept_id as number, concept_name: suggestion.concept_name,
    vocabulary_id: null, score: suggestion.confidence,
  })
  suggestion.candidates.forEach(add)

  return (
    <div className="flex flex-col gap-0.5 text-[11px]">
      <span className="text-muted-foreground">Suggested — not set automatically. Top matches, score 0–100:</span>
      {rows.slice(0, 5).map((c, i) => (
        <div key={c.concept_id} className={`flex items-center gap-1.5 ${i === 0 ? 'text-foreground' : 'text-muted-foreground'}`}>
          <span
            className={`font-mono w-7 text-right flex-shrink-0 ${i === 0 ? 'font-semibold' : ''}`}
            title="Matcher score, 0–100"
          >
            {c.score == null ? '—' : Math.round(c.score)}
          </span>
          <span className="truncate" title={c.concept_name ?? undefined}>
            {c.concept_name} <span className="font-mono">({c.concept_id})</span>
          </span>
          {c.vocabulary_id && <span className="text-[10px] text-muted-foreground flex-shrink-0">{c.vocabulary_id}</span>}
          <button
            type="button"
            onClick={() => onUse(c.concept_id)}
            className="text-primary hover:underline flex-shrink-0 ml-auto"
          >
            Use
          </button>
        </div>
      ))}
    </div>
  )
}

function ConceptCell({
  conceptId,
  onChange,
  expectedDomain,
  projectId,
  sourceValue,
  suggestion,
}: {
  conceptId: number | undefined
  onChange: (v: number | undefined) => void
  expectedDomain?: string
  projectId?: string
  sourceValue: string
  suggestion?: ValueConceptSuggestion
}) {
  const [searching, setSearching] = useState(false)
  const [pending, setPending] = useState('')
  const [lookingUp, setLookingUp] = useState(false)
  const [domain, setDomain] = useState<string | null>(null)
  const [conceptName, setConceptName] = useState<string | null>(null)
  const [standardConcept, setStandardConcept] = useState<string | null>(null)
  const [invalidReason, setInvalidReason] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [notFound, setNotFound] = useState(false)
  const [commitError, setCommitError] = useState<string | null>(null)

  useEffect(() => {
    if (conceptId === undefined || conceptId < 0) {
      setDomain(null)
      setStandardConcept(null)
      setInvalidReason(null)
      setConceptName(null)
      setFailed(false)
      setNotFound(false)
      return
    }
    if (conceptId === 0) {
      // 0 is the OMOP "no matching concept" sentinel, not a real vocabulary entry.
      setDomain(null)
      setStandardConcept(null)
      setInvalidReason(null)
      setConceptName(null)
      setFailed(false)
      setNotFound(false)
      return
    }
    setLookingUp(true)
    setDomain(null)
    setStandardConcept(null)
    setInvalidReason(null)
    setConceptName(null)
    setFailed(false)
    setNotFound(false)
    lookupConceptDomain(conceptId)
      .then(res => {
        if (res.found && res.domain_id) { setDomain(res.domain_id); setStandardConcept(res.standard_concept); setInvalidReason(res.invalid_reason); setConceptName(res.concept_name) }
        // No vocabulary to check against (empty / unreachable) ≠ "not found".
        else { setDomain(null); if (res.vocab_available) setNotFound(true); else setFailed(true) }
      })
      .catch(() => setFailed(true))
      .finally(() => setLookingUp(false))
  }, [conceptId])

  const mismatch = !!expectedDomain && !!domain && domain.toLowerCase() !== expectedDomain.toLowerCase()
  const nonStandard = !!domain && standardConcept !== 'S'
  const invalidConcept = !!domain && !!invalidReason

  // Always look the id up (except the 0 "not mapped" sentinel): an unknown,
  // invalid or non-standard concept must never be set.
  const commit = async () => {
    const id = parseInt(pending)
    if (isNaN(id) || id < 0) return
    setCommitError(null)
    if (id === 0) {
      onChange(id)
      setPending('')
      return
    }
    setLookingUp(true)
    try {
      const res = await lookupConceptDomain(id)
      const err = standardConceptError(id, res)
      if (err) {
        setCommitError(err)
        return
      }
      if (expectedDomain && res.domain_id && res.domain_id.toLowerCase() !== expectedDomain.toLowerCase()) {
        setCommitError(`Wrong domain: "${res.domain_id}", expected "${expectedDomain}"`)
        return
      }
      onChange(id)
      setPending('')
    } catch {
      setCommitError("Couldn't verify this concept — retry")
    } finally {
      setLookingUp(false)
    }
  }

  const canSearch = !!projectId && !!expectedDomain
  const searchToggle = canSearch && (
    <button
      type="button"
      onClick={() => setSearching(s => !s)}
      className={`flex-shrink-0 ${searching ? 'text-primary' : 'text-muted-foreground hover:text-primary'}`}
      title={`Search ${expectedDomain} concepts`}
    >
      <Search className="w-3.5 h-3.5" />
    </button>
  )
  const searchPanel = projectId && expectedDomain && searching && (
    <DomainConceptSearch
      projectId={projectId}
      domain={expectedDomain}
      initialQuery={sourceValue}
      onSelect={c => { onChange(c.concept_id); setPending(''); setCommitError(null); setSearching(false) }}
      onClose={() => setSearching(false)}
    />
  )

  if (conceptId !== undefined && conceptId >= 0) {
    // Unverified (no vocabulary / lookup failed) isn't wrong, but it isn't a green check either.
    const invalid = mismatch || notFound || invalidConcept || nonStandard || failed
    const isZero = conceptId === 0
    const boxClasses = isZero
      ? 'bg-muted border-border text-muted-foreground'
      : invalid
        ? 'bg-amber-50 border-amber-200 text-amber-800'
        : 'bg-green-50 border-green-200 text-green-800'
    return (
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-1.5">
          <div className={`flex items-center gap-1.5 px-2 py-1 rounded-lg border text-xs flex-1 min-w-0 ${boxClasses}`}>
            {invalid ? <AlertTriangle className="w-3 h-3 flex-shrink-0" /> : <CheckCircle className="w-3 h-3 flex-shrink-0" />}
            <span className="font-semibold font-mono">{conceptId}</span>
            {conceptName && <span className="truncate" title={conceptName}>({conceptName})</span>}
            {lookingUp ? (
              <Loader2 className="w-3 h-3 animate-spin flex-shrink-0 ml-1 text-green-600" />
            ) : isZero ? (
              <span className="ml-1 text-[10px] px-1 rounded bg-muted-foreground/10 text-muted-foreground">Not mapped</span>
            ) : domain ? (
              <span className={`ml-1 text-[10px] px-1 rounded ${mismatch ? 'text-amber-700 bg-amber-100' : 'text-indigo-700 bg-indigo-100'}`}>{domain}</span>
            ) : failed ? (
              <span title="Couldn't check this concept" className="flex-shrink-0 ml-1">
                <AlertTriangle className="w-3 h-3 text-amber-500" />
              </span>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => onChange(undefined)}
            className="text-muted-foreground hover:text-destructive flex-shrink-0"
            title="Clear"
          >
            <X className="w-3.5 h-3.5" />
          </button>
          {searchToggle}
        </div>
        {!lookingUp && mismatch && (
          <p className="text-[11px] text-amber-700">Expected "{expectedDomain}", got "{domain}"</p>
        )}
        {!lookingUp && notFound && (
          <p className="text-[11px] text-amber-700">Not found in vocabulary</p>
        )}
        {!lookingUp && failed && (
          <p className="text-[11px] text-amber-700">Couldn't check — vocabulary not loaded or database unreachable</p>
        )}
        {!lookingUp && !mismatch && !notFound && invalidConcept && (
          <p className="text-[11px] text-amber-700">Invalid concept — {invalidReasonLabel(invalidReason as string)}</p>
        )}
        {!lookingUp && !mismatch && !notFound && !invalidConcept && nonStandard && (
          <p className="text-[11px] text-amber-700">Not a standard concept</p>
        )}
        {searchPanel}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={pending}
          onChange={e => { setPending(e.target.value); setCommitError(null) }}
          onKeyDown={e => e.key === 'Enter' && commit()}
          placeholder="e.g. 8507"
          className="border border-border rounded px-2 py-1 text-xs flex-1 min-w-0 h-8 focus:outline-none focus:ring-1 focus:ring-ring bg-background text-foreground"
        />
        <button
          type="button"
          onClick={commit}
          disabled={!pending || lookingUp}
          className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-30 hover:bg-primary/90 flex-shrink-0 h-8"
        >
          {lookingUp ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Set'}
        </button>
        {searchToggle}
      </div>
      {commitError && <p className="text-[11px] text-destructive">{commitError}</p>}
      {suggestion?.concept_id != null && !searching && (
        <SuggestionList suggestion={suggestion} onUse={onChange} />
      )}
      {searchPanel}
    </div>
  )
}

export default function ValueConceptMapper({ label, sourceValues, mapping, onChange, hint, expectedDomain, projectId, suggestions }: Props) {
  const handleChange = (val: string, conceptId: number | undefined) => {
    const next = { ...mapping }
    if (conceptId !== undefined && conceptId >= 0) {
      next[val] = conceptId
    } else {
      delete next[val]
    }
    onChange(next)
  }

  return (
    <div className="flex flex-col gap-2">
      <div>
        <Label>{label}</Label>
        {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
      </div>
      <div className="border border-border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted">
            <tr>
              <th className="text-left px-4 py-2 font-medium text-muted-foreground">Source Value</th>
              <th className="text-left px-4 py-2 font-medium text-muted-foreground">OMOP Concept ID</th>
            </tr>
          </thead>
          <tbody>
            {sourceValues.map((val, i) => (
              <tr key={val} className={i % 2 === 0 ? 'bg-card' : 'bg-muted'}>
                <td className="px-4 py-2 font-mono text-foreground">{val}</td>
                <td className="px-4 py-2">
                  <ConceptCell
                    conceptId={mapping[val]}
                    onChange={id => handleChange(val, id)}
                    expectedDomain={expectedDomain}
                    projectId={projectId}
                    sourceValue={val}
                    suggestion={suggestions?.[val]}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
