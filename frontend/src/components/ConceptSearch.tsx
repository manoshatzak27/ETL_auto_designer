import { useState } from 'react'
import { conceptSearch } from '../api/client'
import type { ConceptLink } from '../types'
import { Search, Loader2, AlertTriangle } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

interface Props {
  projectId: string
  onSelect: (concept: ConceptLink) => void
  placeholder?: string
}

export default function ConceptSearch({ projectId, onSelect, placeholder }: Props) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ConceptLink[]>([])
  const [loading, setLoading] = useState(false)
  const [unavailable, setUnavailable] = useState(false)

  const search = async () => {
    if (!query.trim()) return
    setLoading(true)
    setUnavailable(false)
    try {
      const data = await conceptSearch(projectId, query)
      setResults(data.conceptlinks || [])
    } catch {
      setUnavailable(true)
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex gap-2">
        <Input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && search()}
          placeholder={placeholder ?? 'Search OMOP concepts…'}
          className="flex-1"
        />
        <Button
          onClick={search}
          disabled={loading}
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
          Search
        </Button>
      </div>

      {unavailable && (
        <div className="bg-muted border border-border rounded-lg p-3 flex flex-col gap-1.5">
          <div className="flex items-center gap-2 text-foreground text-sm font-medium">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 text-destructive" />
            EntityLinker service is not running
          </div>
          <p className="text-xs text-muted-foreground">
            To use AI concept search, start the EntityLinker service from{' '}
            <code className="bg-card border border-border px-1 rounded">omop-docker-package</code> on port <strong>8000</strong>:
          </p>
          <pre className="text-xs bg-card border border-border rounded px-2 py-1.5 text-foreground overflow-x-auto">
            cd omop-docker-package\EntityLinker{'\n'}
            uvicorn api.main:app --port 8000
          </pre>
          <p className="text-xs text-muted-foreground">
            Or enter a concept ID manually using the field below.
          </p>
        </div>
      )}

      {results.length > 0 && (
        <div className="bg-card border border-border rounded-lg max-h-64 overflow-y-auto shadow-lg">
          {results.map(c => (
            <button
              key={c.concept_id}
              onClick={() => { onSelect(c); setResults([]) }}
              className="w-full text-left px-4 py-3 hover:bg-primary hover:text-primary-foreground border-b last:border-b-0 border-border flex flex-col gap-0.5 transition-colors"
            >
              <span className="font-medium text-sm text-foreground">{c.concept_name}</span>
              <span className="text-xs text-muted-foreground">
                ID: {c.concept_id} · {c.domain} · {c.vocabulary_id} · code: {c.concept_code}
                {c.score != null && ` · ${(c.score * 100).toFixed(0)}%`}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
