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
}

function ConceptCell({
  conceptId,
  onChange,
}: {
  conceptId: number | undefined
  onChange: (v: number | undefined) => void
}) {
  const [pending, setPending] = useState('')
  const [lookingUp, setLookingUp] = useState(false)
  const [domain, setDomain] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!conceptId || conceptId < 1) {
      setDomain(null)
      setFailed(false)
      return
    }
    setLookingUp(true)
    setDomain(null)
    setFailed(false)
    lookupConceptDomain(conceptId)
      .then(res => {
        if (res.found && res.domain_id) setDomain(res.domain_id)
        else setDomain(null)
      })
      .catch(() => setFailed(true))
      .finally(() => setLookingUp(false))
  }, [conceptId])

  const commit = () => {
    const id = parseInt(pending)
    if (isNaN(id) || id < 1) return
    onChange(id)
    setPending('')
  }

  if (conceptId && conceptId > 0) {
    return (
      <div className="flex items-center gap-1.5">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-green-50 border border-green-200 text-green-800 text-xs flex-1 min-w-0">
          <CheckCircle className="w-3 h-3 flex-shrink-0" />
          <span className="font-semibold font-mono">{conceptId}</span>
          {lookingUp ? (
            <Loader2 className="w-3 h-3 animate-spin flex-shrink-0 ml-1 text-green-600" />
          ) : domain ? (
            <span className="ml-1 text-[10px] text-indigo-700 bg-indigo-100 px-1 rounded">{domain}</span>
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
    )
  }

  return (
    <div className="flex items-center gap-1.5">
      <input
        type="number"
        value={pending}
        onChange={e => setPending(e.target.value)}
        onKeyDown={e => e.key === 'Enter' && commit()}
        placeholder="e.g. 8507"
        className="border border-border rounded px-2 py-1 text-xs flex-1 min-w-0 h-8 focus:outline-none focus:ring-1 focus:ring-ring bg-background text-foreground"
      />
      <button
        type="button"
        onClick={commit}
        disabled={!pending}
        className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-30 hover:bg-primary/90 flex-shrink-0 h-8"
      >
        Set
      </button>
    </div>
  )
}

export default function ValueConceptMapper({ label, sourceValues, mapping, onChange, hint }: Props) {
  const handleChange = (val: string, conceptId: number | undefined) => {
    const next = { ...mapping }
    if (conceptId !== undefined && conceptId > 0) {
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
