import { useState, useEffect } from 'react'
import { lookupConceptDomain } from '../api/client'
import { Loader2, AlertTriangle, CheckCircle, X } from 'lucide-react'

interface Props {
  value: number | null | undefined
  onChange: (v: number | null) => void
  placeholder?: string
  className?: string
}

export default function SingleConceptInput({ value, onChange, placeholder = 'Concept ID', className }: Props) {
  const [pending, setPending] = useState('')
  const [lookingUp, setLookingUp] = useState(false)
  const [domain, setDomain] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  const hasValue = value != null && value > 0

  useEffect(() => {
    if (!hasValue) {
      setDomain(null)
      setFailed(false)
      return
    }
    setLookingUp(true)
    setDomain(null)
    setFailed(false)
    lookupConceptDomain(value as number)
      .then(res => {
        if (res.found && res.domain_id) setDomain(res.domain_id)
        else setDomain(null)
      })
      .catch(() => setFailed(true))
      .finally(() => setLookingUp(false))
  }, [value, hasValue])

  const commit = () => {
    const id = parseInt(pending)
    if (isNaN(id) || id < 1) return
    onChange(id)
    setPending('')
  }

  if (hasValue) {
    return (
      <div className={`flex items-center gap-1.5 mt-1 ${className ?? ''}`}>
        <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-green-50 border border-green-200 text-green-800 text-xs">
          <CheckCircle className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="font-semibold font-mono">{value}</span>
          {lookingUp ? (
            <>
              <Loader2 className="w-3 h-3 animate-spin flex-shrink-0 ml-1" />
              <span className="ml-0.5">Looking up domain…</span>
            </>
          ) : domain ? (
            <span className="ml-1 text-[10px] text-indigo-700 bg-indigo-100 px-1 rounded">{domain}</span>
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
    )
  }

  return (
    <div className={`flex items-center gap-1.5 mt-1 ${className ?? ''}`}>
      <input
        type="number"
        value={pending}
        onChange={e => setPending(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && commit()}
        placeholder={placeholder}
        className="border border-border rounded px-2 py-1.5 text-sm w-40 focus:outline-none focus:ring-1 focus:ring-ring bg-background text-foreground"
      />
      <button
        type="button"
        onClick={commit}
        disabled={!pending}
        className="px-3 py-1.5 text-sm bg-primary text-primary-foreground rounded disabled:opacity-30 hover:bg-primary/90"
      >
        Set
      </button>
    </div>
  )
}
