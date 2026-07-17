import { useState, useEffect } from 'react'
import { lookupConceptDomain } from '../api/client'
import { Loader2, AlertTriangle, CheckCircle, X } from 'lucide-react'

interface Props {
  value: number | null | undefined
  onChange: (v: number | null) => void
  onConceptName?: (name: string | null) => void
  placeholder?: string
  className?: string
  /** If set, concept IDs outside this domain are rejected instead of accepted. */
  expectedDomain?: string
}

export default function SingleConceptInput({ value, onChange, onConceptName, placeholder = 'Concept ID', className, expectedDomain }: Props) {
  const [pending, setPending] = useState('')
  const [lookingUp, setLookingUp] = useState(false)
  const [domain, setDomain] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [notFound, setNotFound] = useState(false)
  const [commitError, setCommitError] = useState<string | null>(null)

  const hasValue = value != null && value > 0

  useEffect(() => {
    if (!hasValue) {
      setDomain(null)
      setFailed(false)
      setNotFound(false)
      return
    }
    setLookingUp(true)
    setDomain(null)
    setFailed(false)
    setNotFound(false)
    lookupConceptDomain(value as number)
      .then(res => {
        if (res.found && res.domain_id) {
          setDomain(res.domain_id)
          onConceptName?.(res.concept_name ?? null)
        } else {
          setDomain(null)
          setNotFound(true)
          onConceptName?.(null)
        }
      })
      .catch(() => setFailed(true))
      .finally(() => setLookingUp(false))
  }, [value, hasValue])

  const mismatch = !!expectedDomain && !!domain && domain.toLowerCase() !== expectedDomain.toLowerCase()

  const commit = async () => {
    const id = parseInt(pending)
    if (isNaN(id) || id < 1) return
    setCommitError(null)
    if (!expectedDomain) {
      onChange(id)
      setPending('')
      return
    }
    setLookingUp(true)
    try {
      const res = await lookupConceptDomain(id)
      if (!res.found) {
        setCommitError(`Concept ${id} was not found in the vocabulary.`)
        return
      }
      if (res.domain_id && res.domain_id.toLowerCase() !== expectedDomain.toLowerCase()) {
        setCommitError(`Concept ${id} belongs to domain "${res.domain_id}", expected "${expectedDomain}".`)
        return
      }
      onChange(id)
      onConceptName?.(res.concept_name ?? null)
      setPending('')
    } catch {
      setCommitError('Domain lookup failed — please try again.')
    } finally {
      setLookingUp(false)
    }
  }

  if (hasValue) {
    const invalid = mismatch || notFound
    return (
      <div className={`flex flex-col gap-1 mt-1 ${className ?? ''}`}>
        <div className="flex items-center gap-1.5">
          <div className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs ${invalid ? 'bg-amber-50 border-amber-200 text-amber-800' : 'bg-green-50 border-green-200 text-green-800'}`}>
            {invalid ? <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" /> : <CheckCircle className="w-3.5 h-3.5 flex-shrink-0" />}
            <span className="font-semibold font-mono">{value}</span>
            {lookingUp ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin flex-shrink-0 ml-1" />
                <span className="ml-0.5">Looking up domain…</span>
              </>
            ) : domain ? (
              <span className={`ml-1 text-[10px] px-1 rounded ${mismatch ? 'text-amber-700 bg-amber-100' : 'text-indigo-700 bg-indigo-100'}`}>{domain}</span>
            ) : failed ? (
              <>
                <AlertTriangle className="w-3 h-3 flex-shrink-0 ml-1 text-amber-500" />
                <span className="ml-0.5 text-amber-700">Lookup failed</span>
              </>
            ) : null}
            <button
              type="button"
              onClick={() => onChange(null)}
              className="ml-2 text-muted-foreground hover:text-destructive"
              title="Clear"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        </div>
        {!lookingUp && mismatch && (
          <p className="text-xs text-amber-700">
            Wrong concept domain: expected "{expectedDomain}", got "{domain}". Clear it and set a valid concept.
          </p>
        )}
        {!lookingUp && notFound && (
          <p className="text-xs text-amber-700">Concept {value} was not found in the vocabulary. Clear it and set a valid concept.</p>
        )}
      </div>
    )
  }

  return (
    <div className={`flex flex-col gap-1 mt-1 ${className ?? ''}`}>
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={pending}
          onChange={e => { setPending(e.target.value); setCommitError(null) }}
          onKeyDown={e => e.key === 'Enter' && commit()}
          placeholder={placeholder}
          className="border border-border rounded px-2 py-1.5 text-sm w-40 focus:outline-none focus:ring-1 focus:ring-ring bg-background text-foreground"
        />
        <button
          type="button"
          onClick={commit}
          disabled={!pending || lookingUp}
          className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded disabled:opacity-30 hover:bg-primary/90"
        >
          {lookingUp ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Set'}
        </button>
      </div>
      {commitError && <p className="text-xs text-destructive">{commitError}</p>}
    </div>
  )
}
