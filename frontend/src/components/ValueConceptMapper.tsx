import { useState, useEffect } from 'react'
import { Label } from '@/components/ui/label'
import { lookupConceptDomain } from '../api/client'
import { Loader2, AlertTriangle, CheckCircle, X } from 'lucide-react'

interface Props {
  label: string
  sourceValues: string[]
  mapping: Record<string, number>
  onChange: (mapping: Record<string, number>) => void
  hint?: string
  /** If set, concept IDs outside this domain are rejected instead of accepted. */
  expectedDomain?: string
}

function ConceptCell({
  conceptId,
  onChange,
  expectedDomain,
}: {
  conceptId: number | undefined
  onChange: (v: number | undefined) => void
  expectedDomain?: string
}) {
  const [pending, setPending] = useState('')
  const [lookingUp, setLookingUp] = useState(false)
  const [domain, setDomain] = useState<string | null>(null)
  const [standardConcept, setStandardConcept] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [notFound, setNotFound] = useState(false)
  const [commitError, setCommitError] = useState<string | null>(null)

  useEffect(() => {
    if (conceptId === undefined || conceptId < 0) {
      setDomain(null)
      setStandardConcept(null)
      setFailed(false)
      setNotFound(false)
      return
    }
    if (conceptId === 0) {
      // 0 is the OMOP "no matching concept" sentinel, not a real vocabulary entry.
      setDomain(null)
      setStandardConcept(null)
      setFailed(false)
      setNotFound(false)
      return
    }
    setLookingUp(true)
    setDomain(null)
    setStandardConcept(null)
    setFailed(false)
    setNotFound(false)
    lookupConceptDomain(conceptId)
      .then(res => {
        if (res.found && res.domain_id) { setDomain(res.domain_id); setStandardConcept(res.standard_concept) }
        else { setDomain(null); setNotFound(true) }
      })
      .catch(() => setFailed(true))
      .finally(() => setLookingUp(false))
  }, [conceptId])

  const mismatch = !!expectedDomain && !!domain && domain.toLowerCase() !== expectedDomain.toLowerCase()
  const nonStandard = !!domain && standardConcept !== 'S'

  const commit = async () => {
    const id = parseInt(pending)
    if (isNaN(id) || id < 0) return
    setCommitError(null)
    if (!expectedDomain || id === 0) {
      onChange(id)
      setPending('')
      return
    }
    setLookingUp(true)
    try {
      const res = await lookupConceptDomain(id)
      if (!res.found) {
        setCommitError('Not found in vocabulary')
        return
      }
      if (res.domain_id && res.domain_id.toLowerCase() !== expectedDomain.toLowerCase()) {
        setCommitError(`Wrong domain: "${res.domain_id}", expected "${expectedDomain}"`)
        return
      }
      onChange(id)
      setPending('')
    } catch {
      setCommitError('Lookup failed — retry')
    } finally {
      setLookingUp(false)
    }
  }

  if (conceptId !== undefined && conceptId >= 0) {
    const invalid = mismatch || notFound || nonStandard
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
            {lookingUp ? (
              <Loader2 className="w-3 h-3 animate-spin flex-shrink-0 ml-1 text-green-600" />
            ) : isZero ? (
              <span className="ml-1 text-[10px] px-1 rounded bg-muted-foreground/10 text-muted-foreground">Not mapped</span>
            ) : domain ? (
              <span className={`ml-1 text-[10px] px-1 rounded ${mismatch ? 'text-amber-700 bg-amber-100' : 'text-indigo-700 bg-indigo-100'}`}>{domain}</span>
            ) : failed ? (
              <AlertTriangle className="w-3 h-3 flex-shrink-0 ml-1 text-amber-500" title="Domain lookup failed" />
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
        </div>
        {!lookingUp && mismatch && (
          <p className="text-[11px] text-amber-700">Expected "{expectedDomain}", got "{domain}"</p>
        )}
        {!lookingUp && notFound && (
          <p className="text-[11px] text-amber-700">Not found in vocabulary</p>
        )}
        {!lookingUp && !mismatch && !notFound && nonStandard && (
          <p className="text-[11px] text-amber-700">Not a standard concept</p>
        )}
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
      </div>
      {commitError && <p className="text-[11px] text-destructive">{commitError}</p>}
    </div>
  )
}

export default function ValueConceptMapper({ label, sourceValues, mapping, onChange, hint, expectedDomain }: Props) {
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
