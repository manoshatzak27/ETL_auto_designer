import { useEffect, useRef, useState } from 'react'
import { getConceptMatcherHealth, searchDomainConcepts, type DomainSearchResult } from '../api/client'
import { Search, Loader2, X, ChevronDown, Check } from 'lucide-react'

interface Props {
  projectId: string
  /** OMOP domain the search is confined to — the field decides it, not the
   *  user. Omitted, the user picks any number from a domain filter (default:
   *  every domain, each result showing its own). */
  domain?: string
  onSelect: (concept: DomainSearchResult) => void
  onClose?: () => void
  /** Pre-filled query, e.g. the source value being mapped. Searched on open. */
  initialQuery?: string
  /** The domain filter's selection, when the parent wants to set it too (the
   *  Concepts step's domain shortcut buttons). Left out, the filter keeps its
   *  own. Either way a change re-runs the search. */
  pickedDomains?: string[]
  onPickedDomainsChange?: (domains: string[]) => void
}

// The domain filter's options: every domain the matcher's vocabulary defines,
// fetched once per page load and shared by every open search box. An empty
// list (matcher unreachable) hides the filter rather than offering guesses.
let domainOptions: Promise<string[]> | null = null
const loadDomainOptions = () => {
  domainOptions ??= getConceptMatcherHealth()
    .then(h => [...(h.domains ?? [])].sort((a, b) => a.localeCompare(b)))
    .catch(() => {
      domainOptions = null   // let the next search box try again
      return []
    })
  return domainOptions
}

/** The domain filter: a checklist in a dropdown. None checked is every domain. */
function DomainFilter({ options, picked, onChange }: {
  options: string[]
  picked: string[]
  onChange: (domains: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  // Kept in option order, so the label and the request don't depend on the
  // order the boxes were ticked in.
  const toggle = (d: string) => {
    const next = new Set(picked)
    if (next.has(d)) next.delete(d)
    else next.add(d)
    onChange(options.filter(o => next.has(o)))
  }

  const label = picked.length === 0 ? 'All domains'
    : picked.length === 1 ? picked[0]
    : `${picked.length} domains`

  return (
    <div
      ref={rootRef}
      className="relative flex-shrink-0"
      onKeyDown={e => {
        // Escape closes the list, not the whole search box around it.
        if (e.key === 'Escape' && open) { e.stopPropagation(); setOpen(false) }
      }}
    >
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        title={picked.length > 1 ? picked.join(', ') : 'Only search these OMOP domains'}
        className={`border rounded px-2 text-xs w-36 h-8 flex items-center justify-between gap-1 bg-background focus:outline-none focus:ring-1 focus:ring-ring ${
          picked.length ? 'border-primary text-primary' : 'border-border text-foreground'
        }`}
      >
        <span className="truncate">{label}</span>
        <ChevronDown className="w-3.5 h-3.5 flex-shrink-0 opacity-60" />
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-20 w-56 max-h-64 overflow-y-auto rounded border border-border bg-card shadow-md py-1">
          <button
            type="button"
            onClick={() => onChange([])}
            className="w-full text-left px-2.5 py-1 text-xs hover:bg-accent flex items-center gap-2 border-b border-border mb-1"
          >
            <span className="w-3.5 flex-shrink-0">{picked.length === 0 && <Check className="w-3.5 h-3.5" />}</span>
            All domains
          </button>
          {options.map(d => (
            <label key={d} className="w-full px-2.5 py-1 text-xs hover:bg-accent flex items-center gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={picked.includes(d)}
                onChange={() => toggle(d)}
                className="w-3.5 h-3.5 accent-primary flex-shrink-0"
              />
              {d}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

/** Free-text search for standard concepts in one domain (or several, or all),
 *  through the concept matcher — the in-app replacement for looking an id up
 *  on Athena. */
export default function DomainConceptSearch({
  projectId, domain, onSelect, onClose, initialQuery = '', pickedDomains, onPickedDomainsChange,
}: Props) {
  const [query, setQuery] = useState(initialQuery)
  const [results, setResults] = useState<DomainSearchResult[] | null>(null)
  const [term, setTerm] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // The user's domain filter, used only when the field doesn't fix one; empty
  // is every domain. Passed to the matcher as its own domain restriction, so it
  // narrows the search itself rather than filtering a shortlist afterwards.
  const [ownPicked, setOwnPicked] = useState<string[]>([])
  const picked = pickedDomains ?? ownPicked
  const [options, setOptions] = useState<string[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const effective: string | string[] | undefined = domain ?? (picked.length ? picked : undefined)
  // The one domain every result is in, if there is one.
  const single = domain ?? (picked.length === 1 ? picked[0] : undefined)
  // How the domain reads in prose: "Condition concepts" vs plain "concepts".
  const scope = single ? `${single} ` : ''
  // Only the latest search may update the list: the one run on open for the
  // source value can finish after a search the user typed, and would
  // otherwise replace its results.
  const latest = useRef(0)

  const search = async (q: string, d = effective) => {
    if (!q.trim()) return
    const id = ++latest.current
    setLoading(true)
    setError(null)
    try {
      const res = await searchDomainConcepts(projectId, q, d)
      if (id !== latest.current) return
      setResults(res.results)
      setTerm(res.term)
    } catch (e) {
      if (id !== latest.current) return
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || 'Concept search is unavailable — is the concept matcher running?')
      setResults(null)
    } finally {
      if (id === latest.current) setLoading(false)
    }
  }

  useEffect(() => {
    inputRef.current?.focus()
    if (initialQuery.trim()) search(initialQuery)
    if (!domain) loadDomainOptions().then(setOptions)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const pickDomains = (ds: string[]) => {
    setOwnPicked(ds)
    onPickedDomainsChange?.(ds)
  }

  // Re-run what's in the box whenever the filter changes — from the checklist
  // or from the parent — so the list on screen always matches the filter shown
  // beside it. Not on mount: the open search above already used it.
  const pickedKey = picked.join('\n')
  const lastPickedKey = useRef(pickedKey)
  useEffect(() => {
    if (pickedKey === lastPickedKey.current) return
    lastPickedKey.current = pickedKey
    if (query.trim()) search(query)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickedKey])

  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-card p-2 shadow-sm">
      <div className="flex items-center gap-1.5">
        <input
          ref={inputRef}
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') search(query)
            if (e.key === 'Escape') onClose?.()
          }}
          placeholder={`Search ${scope}concepts…`}
          className="border border-border rounded px-2 py-1 text-xs flex-1 min-w-0 h-8 focus:outline-none focus:ring-1 focus:ring-ring bg-background text-foreground"
        />
        {!domain && options.length > 0 && (
          <DomainFilter options={options} picked={picked} onChange={pickDomains} />
        )}
        <button
          type="button"
          onClick={() => search(query)}
          disabled={!query.trim() || loading}
          className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-30 hover:bg-primary/90 flex-shrink-0 h-8"
          title={`Search standard ${scope}concepts`}
        >
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
        </button>
        {onClose && (
          <button type="button" onClick={onClose} className="text-muted-foreground hover:text-foreground flex-shrink-0" title="Close search">
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {error && <p className="text-[11px] text-destructive">{error}</p>}

      {results && (
        results.length === 0 ? (
          <p className="text-[11px] text-muted-foreground px-1">
            No standard {scope}concept found{term && term !== query.trim() ? ` for "${term}"` : ''}. Try another word.
          </p>
        ) : (
          <>
            {term && term.toLowerCase() !== query.trim().toLowerCase() && (
              <p className="text-[11px] text-muted-foreground px-1">Searched as "{term}"</p>
            )}
            <div className="max-h-72 overflow-y-auto rounded border border-border">
              {results.map((c, i) => {
                const prev = results[i - 1]
                // Results come grouped (parent, its children, …, then the
                // parentless ones), so a header is needed only where the
                // parentless tail starts after at least one group.
                const otherHeader = !c.is_parent && c.parent_id == null && !!prev && (prev.is_parent || prev.parent_id != null)
                // A concept with two unrelated parents appears under each.
                const key = `${c.parent_id ?? 'root'}-${c.concept_id}-${i}`
                return (
                  <div key={key}>
                    {otherHeader && (
                      <div className="px-2.5 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground bg-muted border-b border-border">
                        Other matches
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={() => onSelect(c)}
                      className={`w-full text-left py-1.5 pr-2.5 hover:bg-accent border-b border-border flex items-center justify-between gap-2 ${
                        c.is_parent ? 'bg-primary/5' : ''
                      }`}
                      style={{ paddingLeft: `${10 + (c.depth ?? 0) * 18}px` }}
                    >
                      <span className="flex items-center gap-1.5 min-w-0">
                        {c.parent_id != null && <span className="text-muted-foreground text-xs flex-shrink-0">└</span>}
                        {c.is_parent && (
                          <span className="text-[10px] px-1 rounded bg-primary/15 text-primary font-medium flex-shrink-0">Parent</span>
                        )}
                        <span className={`text-xs text-foreground truncate ${c.is_parent ? 'font-medium' : ''}`}>{c.concept_name}</span>
                      </span>
                      <span className="flex items-center gap-1.5 flex-shrink-0">
                        {!domain && c.domain_id && (
                          <span className="text-[10px] px-1 rounded bg-secondary text-secondary-foreground">{c.domain_id}</span>
                        )}
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {c.concept_id} · {c.vocabulary_id}
                        </span>
                      </span>
                    </button>
                  </div>
                )
              })}
            </div>
          </>
        )
      )}
    </div>
  )
}
