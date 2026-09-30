import { useEffect, useRef, useState } from 'react'
import { searchDomainConcepts, type DomainSearchResult } from '../api/client'
import { Search, Loader2, X } from 'lucide-react'

interface Props {
  projectId: string
  /** OMOP domain the search is confined to — the field decides it, not the user. */
  domain: string
  onSelect: (concept: DomainSearchResult) => void
  onClose?: () => void
  /** Pre-filled query, e.g. the source value being mapped. Searched on open. */
  initialQuery?: string
}

/** Free-text search for standard concepts in one domain, through the concept
 *  matcher — the in-app replacement for looking an id up on Athena. */
export default function DomainConceptSearch({ projectId, domain, onSelect, onClose, initialQuery = '' }: Props) {
  const [query, setQuery] = useState(initialQuery)
  const [results, setResults] = useState<DomainSearchResult[] | null>(null)
  const [term, setTerm] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const search = async (q: string) => {
    if (!q.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await searchDomainConcepts(projectId, q, domain)
      setResults(res.results)
      setTerm(res.term)
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || 'Concept search is unavailable — is the concept matcher running?')
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    inputRef.current?.focus()
    if (initialQuery.trim()) search(initialQuery)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

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
          placeholder={`Search ${domain} concepts…`}
          className="border border-border rounded px-2 py-1 text-xs flex-1 min-w-0 h-8 focus:outline-none focus:ring-1 focus:ring-ring bg-background text-foreground"
        />
        <button
          type="button"
          onClick={() => search(query)}
          disabled={!query.trim() || loading}
          className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-30 hover:bg-primary/90 flex-shrink-0 h-8"
          title={`Search standard ${domain} concepts`}
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
            No standard {domain} concept found{term && term !== query.trim() ? ` for "${term}"` : ''}. Try another word.
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
                return (
                  <div key={`${c.parent_id ?? 'root'}-${c.concept_id}`}>
                    {otherHeader && (
                      <div className="px-2.5 py-1 text-[10px] font-medium uppercase tracking-wide text-muted-foreground bg-muted border-b border-border">
                        Other matches
                      </div>
                    )}
                    <button
                      type="button"
                      onClick={() => onSelect(c)}
                      className={`w-full text-left py-1.5 pr-2.5 hover:bg-accent border-b border-border flex items-center justify-between gap-2 ${
                        c.is_parent ? 'bg-primary/5 pl-2.5' : c.parent_id != null ? 'pl-7' : 'pl-2.5'
                      }`}
                    >
                      <span className="flex items-center gap-1.5 min-w-0">
                        {c.is_parent && (
                          <span className="text-[10px] px-1 rounded bg-primary/15 text-primary font-medium flex-shrink-0">Parent</span>
                        )}
                        {c.parent_id != null && <span className="text-muted-foreground text-xs flex-shrink-0">└</span>}
                        <span className={`text-xs text-foreground truncate ${c.is_parent ? 'font-medium' : ''}`}>{c.concept_name}</span>
                      </span>
                      <span className="text-[10px] text-muted-foreground font-mono flex-shrink-0">
                        {c.concept_id} · {c.vocabulary_id}
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
