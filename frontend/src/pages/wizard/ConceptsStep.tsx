import { useState, useEffect, useCallback, useMemo, useRef, createContext, useContext } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getColumnValues,
  getConceptDecisions,
  saveConceptDecisions,
  generateMappingCsvs,
  lookupConceptDomain,
  conceptSearch,
  getApiHealth,
  updateProjectSettings,
  updateTableConfig,
} from '../../api/client'
import type { Project } from '../../types'
import { getStructuralColumns } from '../../utils'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import {
  ChevronDown, ChevronUp, CheckCircle, Loader2,
  Hash, List, Layers, SkipForward, Search, X,
  AlertTriangle, Tag, Sparkles, Plus, Scale,
} from 'lucide-react'
import clsx from 'clsx'
import { useSourceFile } from '../../hooks/useSourceFile'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

// ── Types ──────────────────────────────────────────────────────────────────

type Strategy = 'map_variable' | 'map_values' | 'map_both' | 'skip'

interface ConceptRef {
  concept_id: number
  concept_name: string
  vocabulary_id?: string
  domain?: string            // OMOP domain string (e.g., "Observation")
  domain_id?: number         // numeric 1-5 → our 5 ETL tables (stem_table routing)
  // Custom-concept metadata (only set when concept was created via the form)
  is_custom?: boolean
  concept_code?: string
  concept_class_id?: string
  domain_id_str?: string     // OMOP domain string, what gets inserted into vocab.concept
  // EntityLinker result fields (only present on search results)
  score?: number
  justification?: string
}

interface UnitMapping {
  unit_col: string | null
  unit_concepts: Record<string, number>  // unit_source_value → unit_concept_id
}

interface VariableDecision {
  strategy: Strategy
  variable_concept: ConceptRef | null
  value_concepts: Record<string, ConceptRef>
  domain_id: number | null
  unit_mapping?: UnitMapping
}

// ── Constants ──────────────────────────────────────────────────────────────

const DOMAIN_OPTIONS = [
  { label: 'Measurement',          value: 1 },
  { label: 'Observation',          value: 2 },
  { label: 'Drug Exposure',        value: 3 },
  { label: 'Procedure Occurrence', value: 4 },
  { label: 'Condition Occurrence', value: 5 },
] as const

const DOMAIN_STRING_MAP: Record<string, number> = {
  'measurement': 1,
  'observation': 2,
  'drug': 3,
  'drug exposure': 3,
  'procedure': 4,
  'procedure occurrence': 4,
  'condition': 5,
  'condition occurrence': 5,
}

// OMOP domain strings used as vocab.concept.domain_id values. The first 5
// match our ETL routing; the rest are valid OMOP domains for custom concepts
// that won't be routed through the stem_table (e.g., Visit, Device).
const OMOP_DOMAIN_OPTIONS = [
  'Observation', 'Measurement', 'Condition', 'Drug', 'Procedure',
  'Device', 'Visit', 'Note', 'Specimen', 'Provider', 'Care Site', 'Geography',
] as const

// Common concept_class_id values per domain. Used as datalist hints; user
// can type anything since OMOP has many vocab-specific classes.
const COMMON_CONCEPT_CLASSES = [
  'Clinical Finding', 'Procedure', 'Observable Entity', 'Substance',
  'Lab Test', 'Survey', 'Question', 'Answer', 'Drug', 'Device', 'Visit',
  'Disorder', 'Finding', 'Event', 'Custom',
] as const

// ── Shared per-page settings (avoid prop-drilling) ─────────────────────────

interface ConceptsSettings {
  rerankerAvailable: boolean
  customVocabularyId: string
}

const ConceptsCtx = createContext<ConceptsSettings>({
  rerankerAvailable: false,
  customVocabularyId: 'CUSTOM',
})

const useConceptsSettings = () => useContext(ConceptsCtx)

interface ColumnInfo {
  distinct_values: string[]
  distinct_count: number
  null_count: number
  total_rows: number
  completion_rate: number
}

// ── Constants ──────────────────────────────────────────────────────────────

const STRATEGY_META: Record<Strategy, { label: string; icon: React.ReactNode; color: string }> = {
  map_variable: { label: 'Map variable', icon: <Hash className="w-3.5 h-3.5" />, color: 'bg-accent text-primary' },
  map_values:   { label: 'Map values',   icon: <List className="w-3.5 h-3.5" />, color: 'bg-orange-100 text-orange-700' },
  map_both:     { label: 'Map both',     icon: <Layers className="w-3.5 h-3.5" />, color: 'bg-purple-100 text-purple-700' },
  skip:         { label: 'Skip',         icon: <SkipForward className="w-3.5 h-3.5" />, color: 'bg-muted text-muted-foreground' },
}


// ── Domain picker ─────────────────────────────────────────────────────────

function DomainPicker({ value, onChange }: { value: number | null; onChange: (v: number | null) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const selected = DOMAIN_OPTIONS.find(d => d.value === value)

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className={clsx(
          'flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs w-full text-left transition-colors',
          value !== null
            ? 'border-indigo-300 bg-indigo-50 text-indigo-800'
            : 'border-gray-300 text-gray-400 bg-white hover:border-gray-400',
        )}
      >
        <span className="flex-1">{selected ? `${selected.label} · ${selected.value}` : 'Select domain…'}</span>
        {value !== null && (
          <span
            role="button"
            onClick={e => { e.stopPropagation(); onChange(null) }}
            className="text-gray-300 hover:text-red-400 cursor-pointer"
          ><X className="w-3 h-3" /></span>
        )}
        <ChevronDown className={clsx('w-3.5 h-3.5 flex-shrink-0 transition-transform text-gray-400', open && 'rotate-180')} />
      </button>

      {open && (
        <div className="absolute top-full left-0 mt-1 z-20 bg-white border border-gray-200 rounded-lg shadow-lg w-full overflow-hidden">
          {DOMAIN_OPTIONS.map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => { onChange(opt.value); setOpen(false) }}
              className={clsx(
                'w-full text-left px-3 py-2 text-xs hover:bg-indigo-50 border-b last:border-0 border-gray-100 flex items-center justify-between',
                value === opt.value && 'bg-indigo-50 text-indigo-700 font-medium',
              )}
            >
              <span>{opt.label}</span>
              <span className="text-gray-400 font-mono">{opt.value}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Mini concept search hook ───────────────────────────────────────────────

function useConceptSearch(projectId: string) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ConceptRef[]>([])
  const [loading, setLoading] = useState(false)
  const [unavailable, setUnavailable] = useState(false)

  const search = async (q?: string, useReranker = false) => {
    const term = q ?? query
    if (!term.trim()) return
    setLoading(true)
    setUnavailable(false)
    try {
      const data = await conceptSearch(projectId, term, 15, useReranker)
      setResults((data.conceptlinks || []).map((c: Record<string, unknown>) => ({
        concept_id: c.concept_id as number,
        concept_name: c.concept_name as string,
        vocabulary_id: c.vocabulary_id as string | undefined,
        domain: c.domain as string | undefined,
        score: typeof c.score === 'number' ? c.score : undefined,
        justification: typeof c.justification === 'string' ? c.justification : undefined,
      })))
    } catch {
      setUnavailable(true)
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  const clear = () => { setResults([]); setQuery('') }

  return { query, setQuery, results, loading, unavailable, search, clear }
}

// ── Custom concept form ───────────────────────────────────────────────────

function CustomConceptForm({
  defaultName,
  defaultVocabularyId,
  onCancel,
  onCreate,
}: {
  defaultName: string
  defaultVocabularyId: string
  onCancel: () => void
  onCreate: (c: ConceptRef) => void
}) {
  const [conceptId, setConceptId] = useState('2000000001')
  const [conceptName, setConceptName] = useState(defaultName)
  const [conceptCode, setConceptCode] = useState('')
  const [domain, setDomain] = useState<string>('Observation')
  const [conceptClass, setConceptClass] = useState('Clinical Finding')
  const [vocabularyId, setVocabularyId] = useState(defaultVocabularyId)

  const idNum = parseInt(conceptId, 10)
  const idValid = !isNaN(idNum) && idNum >= 2_000_000_000
  const nameValid = conceptName.trim().length > 0
  const codeValid = conceptCode.trim().length > 0
  const canCreate = idValid && nameValid && codeValid && vocabularyId.trim().length > 0

  const submit = () => {
    if (!canCreate) return
    const numericDomain = DOMAIN_STRING_MAP[domain.toLowerCase()]
    onCreate({
      concept_id: idNum,
      concept_name: conceptName.trim(),
      concept_code: conceptCode.trim(),
      vocabulary_id: vocabularyId.trim(),
      concept_class_id: conceptClass.trim() || 'Clinical Finding',
      domain_id_str: domain,
      domain: domain,
      domain_id: numericDomain,
      is_custom: true,
    })
  }

  return (
    <div className="flex flex-col gap-2 pl-1 border-l-2 border-purple-400/60 bg-purple-50/40 rounded-r p-2">
      <div className="flex items-center gap-2 text-xs font-semibold text-purple-800">
        <Plus className="w-3.5 h-3.5" /> New custom OMOP concept
      </div>

      <div className="grid grid-cols-2 gap-1.5">
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-purple-700 font-medium uppercase tracking-wide">Concept ID *</span>
          <input
            type="number"
            value={conceptId}
            onChange={e => setConceptId(e.target.value)}
            className="border border-purple-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-purple-400 bg-white"
            min={2_000_000_000}
          />
          {!idValid && conceptId !== '' && (
            <span className="text-[10px] text-red-600">Must be ≥ 2,000,000,000</span>
          )}
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-purple-700 font-medium uppercase tracking-wide">Concept code *</span>
          <input
            type="text"
            value={conceptCode}
            onChange={e => setConceptCode(e.target.value)}
            placeholder="e.g. VLB-HRT-001"
            className="border border-purple-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-purple-400 bg-white"
          />
        </label>
      </div>

      <label className="flex flex-col gap-0.5">
        <span className="text-[10px] text-purple-700 font-medium uppercase tracking-wide">Concept name *</span>
        <input
          type="text"
          value={conceptName}
          onChange={e => setConceptName(e.target.value)}
          placeholder="Human-readable name"
          className="border border-purple-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-purple-400 bg-white"
        />
      </label>

      <div className="grid grid-cols-3 gap-1.5">
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-purple-700 font-medium uppercase tracking-wide">Domain</span>
          <select
            value={domain}
            onChange={e => setDomain(e.target.value)}
            className="border border-purple-200 rounded px-1.5 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-purple-400 bg-white"
          >
            {OMOP_DOMAIN_OPTIONS.map(d => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-purple-700 font-medium uppercase tracking-wide">Concept class</span>
          <input
            type="text"
            list="concept-classes"
            value={conceptClass}
            onChange={e => setConceptClass(e.target.value)}
            className="border border-purple-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-purple-400 bg-white"
          />
          <datalist id="concept-classes">
            {COMMON_CONCEPT_CLASSES.map(c => <option key={c} value={c} />)}
          </datalist>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="text-[10px] text-purple-700 font-medium uppercase tracking-wide">Vocabulary</span>
          <input
            type="text"
            value={vocabularyId}
            onChange={e => setVocabularyId(e.target.value)}
            className="border border-purple-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-purple-400 bg-white"
          />
        </label>
      </div>

      <div className="flex items-center gap-2 mt-1">
        <button
          type="button"
          onClick={onCancel}
          className="text-xs text-muted-foreground hover:text-foreground px-2 py-1"
        >Cancel</button>
        <button
          type="button"
          onClick={submit}
          disabled={!canCreate}
          className="ml-auto px-3 py-1 text-xs bg-purple-600 text-white rounded hover:bg-purple-700 disabled:opacity-40 font-medium"
        >Create</button>
      </div>
    </div>
  )
}

// ── ConceptPicker — inline search + manual ID entry ────────────────────────

function ConceptPicker({
  projectId,
  label: _label,
  defaultQuery,
  value,
  onSelect,
  onClear,
}: {
  projectId: string
  label: string
  defaultQuery: string
  value: ConceptRef | null
  onSelect: (c: ConceptRef) => void
  onClear: () => void
}) {
  const { rerankerAvailable, customVocabularyId } = useConceptsSettings()
  const cs = useConceptSearch(projectId)
  const [manualId, setManualId] = useState('')
  const [idLocked, setIdLocked] = useState(false)
  const [manualName, setManualName] = useState('')
  const [lookingUpName, setLookingUpName] = useState(false)
  const [editingName, setEditingName] = useState('')
  const [showSearch, setShowSearch] = useState(false)
  const [showCustom, setShowCustom] = useState(false)
  const [useReranker, setUseReranker] = useState(rerankerAvailable)

  useEffect(() => {
    if (value) setEditingName(value.concept_name)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value?.concept_id])

  const applyId = () => {
    const id = parseInt(manualId)
    if (isNaN(id) || id < 1) return
    setIdLocked(true)
    if (manualName.trim()) {
      onSelect({ concept_id: id, concept_name: manualName.trim() })
      setManualId(''); setManualName(''); setIdLocked(false)
      return
    }
    setLookingUpName(true)
    lookupConceptDomain(id)
      .then(res => {
        const name = res.concept_name || `Concept ${id}`
        onSelect({ concept_id: id, concept_name: name })
      })
      .catch(() => {
        onSelect({ concept_id: id, concept_name: `Concept ${id}` })
      })
      .finally(() => {
        setLookingUpName(false)
        setManualId(''); setManualName(''); setIdLocked(false)
      })
  }

  const unlockId = () => { setIdLocked(false) }

  const applyName = () => {
    const id = parseInt(manualId)
    if (isNaN(id) || id < 1) return
    onSelect({ concept_id: id, concept_name: manualName.trim() || `Concept ${id}` })
    setManualId(''); setManualName(''); setIdLocked(false)
  }

  const commitEditingName = () => {
    if (!value) return
    const trimmed = editingName.trim()
    if (trimmed && trimmed !== value.concept_name) onSelect({ ...value, concept_name: trimmed })
  }

  if (value) {
    const isCustom = value.is_custom || value.concept_id >= 2_000_000_000
    const lockedCls = isCustom
      ? 'border border-purple-200 bg-purple-50 text-purple-800'
      : 'border border-green-200 bg-green-50 text-green-800'
    const clearId = () => { setIdLocked(false); setManualId(''); setManualName(''); onClear() }
    const clearName = () => { setEditingName('') }
    return (
      <div className="flex items-center gap-1.5 w-full">
        {/* Locked ID chip with X — clears only ID, preserves name */}
        <div className={clsx('flex items-center gap-1 px-2 py-1 text-xs rounded font-mono flex-shrink-0', lockedCls)}>
          <CheckCircle className="w-3 h-3 flex-shrink-0" />
          {isCustom && (
            <span className="bg-purple-200 text-purple-800 px-1 rounded text-[10px] font-bold uppercase tracking-wide ml-0.5">Custom</span>
          )}
          <span>{value.concept_id}</span>
          {value.vocabulary_id && <span className="opacity-60 ml-0.5">· {value.vocabulary_id}</span>}
          {isCustom && value.concept_code && <span className="opacity-60 ml-0.5">· {value.concept_code}</span>}
          <button onClick={clearId} className="opacity-60 hover:opacity-100 hover:text-destructive ml-1"><X className="w-3 h-3" /></button>
        </div>
        {/* Editable name input with X — clears only name, preserves ID */}
        <input
          type="text"
          value={editingName}
          onChange={e => setEditingName(e.target.value)}
          onBlur={commitEditingName}
          onKeyDown={e => e.key === 'Enter' && commitEditingName()}
          placeholder="Name"
          className={clsx(
            'px-2 py-1 text-xs rounded border focus:outline-none focus:ring-1 w-40',
            editingName.trim()
              ? clsx(lockedCls, 'focus:ring-green-300')
              : 'border-gray-300 bg-white text-gray-500 focus:ring-gray-300',
          )}
        />
        <button onClick={clearName} className="text-muted-foreground hover:text-destructive flex-shrink-0"><X className="w-3.5 h-3.5" /></button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5 w-full">
      {/* Single row: ID (input or locked chip) + ID Set + Name input + Name Set */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {idLocked ? (
          <div className="flex items-center gap-1 px-2 py-1 text-xs border border-green-200 rounded bg-green-50 text-green-800 font-mono flex-shrink-0">
            <CheckCircle className="w-3 h-3 flex-shrink-0" />
            <span>{manualId}</span>
            <button onClick={unlockId} className="text-green-600 hover:text-destructive ml-1"><X className="w-3 h-3" /></button>
          </div>
        ) : (
          <>
            <input
              type="number"
              value={manualId}
              onChange={e => setManualId(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applyId()}
              placeholder="Concept ID"
              className="border border-border rounded px-2 py-1 text-xs w-24 focus:outline-none focus:ring-1 focus:ring-ring bg-background text-foreground"
            />
            <button
              onClick={applyId}
              disabled={!manualId || lookingUpName}
              className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-30 hover:bg-primary/90 flex items-center gap-1"
            >
              {lookingUpName && <Loader2 className="w-3 h-3 animate-spin" />}
              Set
            </button>
          </>
        )}
        <input
          type="text"
          value={manualName}
          onChange={e => setManualName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyName()}
          placeholder="Name (optional)"
          className="border border-border rounded px-2 py-1 text-xs w-40 focus:outline-none focus:ring-1 focus:ring-ring bg-background text-foreground"
        />
        <button
          onClick={applyName}
          disabled={!idLocked}
          className="px-2 py-1 text-xs bg-primary text-primary-foreground rounded disabled:opacity-30 hover:bg-primary/90"
        >Set</button>
      </div>
      <div className="flex items-center gap-1.5">
        <button
          onClick={() => { setShowSearch(s => !s); setShowCustom(false); if (!cs.query) cs.setQuery(defaultQuery) }}
          className={clsx(
            'flex items-center gap-1 px-2.5 py-1 text-xs rounded border font-medium transition-colors',
            showSearch
              ? 'bg-indigo-100 border-indigo-400 text-indigo-800'
              : 'border-indigo-200 bg-white text-indigo-700 hover:border-indigo-400 hover:bg-indigo-50',
          )}
          title="Semantic search via SapBERT + FAISS"
        >
          <Sparkles className="w-3 h-3" /> AI Search
        </button>
        <button
          onClick={() => { setShowCustom(s => !s); setShowSearch(false) }}
          className={clsx(
            'flex items-center gap-1 px-2.5 py-1 text-xs rounded border font-medium transition-colors',
            showCustom
              ? 'bg-purple-100 border-purple-400 text-purple-800'
              : 'border-purple-200 bg-white text-purple-700 hover:border-purple-400 hover:bg-purple-50',
          )}
          title="Create a custom OMOP concept"
        >
          <Plus className="w-3 h-3" /> Custom
        </button>
      </div>

      {/* Search panel */}
      {showSearch && (
        <div className="flex flex-col gap-1.5 pl-2 border-l-2 border-indigo-400/60 bg-indigo-50/30 rounded-r py-1.5">
          <div className="flex items-center gap-1.5 px-1">
            <Sparkles className="w-3.5 h-3.5 text-indigo-600 flex-shrink-0" />
            <input
              type="text"
              value={cs.query}
              onChange={e => cs.setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && cs.search(undefined, useReranker)}
              placeholder={`Search "${defaultQuery}"…`}
              className="flex-1 border border-indigo-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-400 bg-white text-foreground"
              autoFocus
            />
            <button
              onClick={() => cs.search(undefined, useReranker)}
              disabled={cs.loading || !cs.query.trim()}
              className="px-3 py-1 text-xs bg-indigo-600 text-white rounded disabled:opacity-40 hover:bg-indigo-700 font-medium flex items-center gap-1"
            >
              {cs.loading ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Search'}
            </button>
          </div>
          {/* Reranker toggle */}
          <div className="flex items-center gap-2 px-1">
            <label className={clsx(
              'flex items-center gap-1.5 text-xs cursor-pointer select-none',
              !rerankerAvailable && 'opacity-50 cursor-not-allowed',
            )}>
              <input
                type="checkbox"
                checked={useReranker && rerankerAvailable}
                disabled={!rerankerAvailable}
                onChange={e => setUseReranker(e.target.checked)}
                className="rounded accent-indigo-600"
              />
              <span className="text-indigo-900 font-medium">GPT reranker</span>
              <span className="text-indigo-600 text-[10px]">
                {rerankerAvailable
                  ? useReranker ? 'slower, with justification' : 'fast cosine similarity'
                  : 'requires OPENAI_API_KEY'}
              </span>
            </label>
          </div>
          {cs.unavailable && (
            <p className="text-xs text-amber-600 flex items-center gap-1 px-1">
              <AlertTriangle className="w-3 h-3" /> EntityLinker not running — use manual ID entry or Custom
            </p>
          )}
          {cs.loading && useReranker && (
            <p className="text-xs text-indigo-600 px-1 italic">Reranking with GPT — this can take 10-30 s…</p>
          )}
          {cs.results.length > 0 && (
            <div className="border border-indigo-200 rounded bg-white max-h-72 overflow-y-auto shadow-sm">
              {cs.results.map(c => (
                <button
                  key={c.concept_id}
                  onClick={() => { onSelect(c); setShowSearch(false); cs.clear() }}
                  className="w-full text-left px-2.5 py-2 hover:bg-indigo-50 border-b last:border-0 border-indigo-100 flex flex-col gap-0.5"
                >
                  <div className="flex items-baseline gap-1.5 flex-wrap">
                    <span className="text-xs font-semibold text-foreground">{c.concept_name}</span>
                    <span className="text-[10px] text-muted-foreground font-mono">{c.concept_id}</span>
                    {c.domain && <span className="text-[10px] text-indigo-700 bg-indigo-100 px-1 rounded">{c.domain}</span>}
                    {c.vocabulary_id && <span className="text-[10px] text-muted-foreground">{c.vocabulary_id}</span>}
                    {typeof c.score === 'number' && (
                      <span className="ml-auto text-[10px] text-indigo-700 font-semibold">{(c.score * 100).toFixed(0)}%</span>
                    )}
                  </div>
                  {c.justification && c.justification !== 'Cosine similarity score' && (
                    <p className="text-[11px] text-muted-foreground italic leading-snug">↳ {c.justification}</p>
                  )}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Custom concept form */}
      {showCustom && (
        <CustomConceptForm
          defaultName={defaultQuery}
          defaultVocabularyId={customVocabularyId}
          onCancel={() => setShowCustom(false)}
          onCreate={c => { onSelect(c); setShowCustom(false) }}
        />
      )}
    </div>
  )
}

// ── Per-value concept row with domain auto-detection ──────────────────────

function ValueConceptRow({
  projectId,
  val,
  column,
  concept,
  onSelect,
  onClear,
}: {
  projectId: string
  val: string
  column: string
  concept: ConceptRef | null
  onSelect: (c: ConceptRef) => void
  onClear: () => void
}) {
  const [domainMode, setDomainMode] = useState<'auto' | 'manual'>('auto')
  const [lookingUpDomain, setLookingUpDomain] = useState(false)
  const [rawDomain, setRawDomain] = useState<string | null>(null)
  const [lookupFailed, setLookupFailed] = useState(false)
  const [conceptNotFound, setConceptNotFound] = useState(false)

  useEffect(() => {
    if (!concept) { setRawDomain(null); setLookupFailed(false); setConceptNotFound(false); return }
    if (domainMode !== 'auto') return

    setLookupFailed(false)
    setConceptNotFound(false)

    if (concept.domain) {
      setRawDomain(concept.domain)
      const numeric = DOMAIN_STRING_MAP[concept.domain.toLowerCase()]
      if (numeric !== undefined && concept.domain_id !== numeric) {
        onSelect({ ...concept, domain_id: numeric })
      }
      return
    }

    setLookingUpDomain(true)
    setRawDomain(null)
    lookupConceptDomain(concept.concept_id)
      .then(res => {
        if (res.found && res.domain_id) {
          setRawDomain(res.domain_id)
          const numeric = DOMAIN_STRING_MAP[res.domain_id.toLowerCase()]
          if (numeric !== undefined) {
            onSelect({ ...concept, domain_id: numeric })
          }
        } else if (!res.found) {
          setConceptNotFound(true)
          setRawDomain(null)
        } else {
          setRawDomain(null)
        }
      })
      .catch(() => setLookupFailed(true))
      .finally(() => setLookingUpDomain(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [concept?.concept_id, domainMode])

  const detectedDomainId = concept?.domain_id ?? null

  return (
    <div className="flex flex-col gap-1.5">
      <ConceptPicker
        projectId={projectId}
        label={val}
        defaultQuery={`${column} ${val}`}
        value={concept}
        onSelect={c => onSelect(c)}
        onClear={onClear}
      />
      {concept && (
        <div className="pl-1">
          {domainMode === 'auto' ? (
            <div className={clsx(
              'flex items-center gap-2 px-2.5 py-1 rounded border text-xs',
              detectedDomainId !== null
                ? 'border-indigo-200 bg-indigo-50 text-indigo-700'
                : lookupFailed || conceptNotFound || (rawDomain && detectedDomainId === null)
                  ? 'border-amber-200 bg-amber-50 text-amber-700'
                  : 'border-gray-200 bg-gray-50 text-gray-400',
            )}>
              {lookingUpDomain ? (
                <><Loader2 className="w-3 h-3 animate-spin flex-shrink-0" />Looking up domain…</>
              ) : detectedDomainId !== null ? (
                <>
                  <span className="font-medium">{DOMAIN_OPTIONS.find(d => d.value === detectedDomainId)?.label} · {detectedDomainId}</span>
                  <span className="ml-auto text-indigo-300 text-[10px]">auto</span>
                  <button type="button" onClick={() => setDomainMode('manual')} className="text-indigo-400 hover:text-indigo-600 text-[10px]">change</button>
                </>
              ) : lookupFailed ? (
                <>
                  <AlertTriangle className="w-3 h-3 flex-shrink-0" />
                  <span>Lookup failed</span>
                  <button type="button" onClick={() => setDomainMode('manual')} className="ml-auto text-[10px] hover:underline">Set manually</button>
                </>
              ) : rawDomain ? (
                <>
                  <AlertTriangle className="w-3 h-3 flex-shrink-0" />
                  <span>Unknown domain "{rawDomain}"</span>
                  <button type="button" onClick={() => setDomainMode('manual')} className="ml-auto text-[10px] hover:underline">Set manually</button>
                </>
              ) : conceptNotFound ? (
                <>
                  <AlertTriangle className="w-3 h-3 flex-shrink-0" />
                  <span>Concept {concept.concept_id} not found — set domain manually</span>
                  <button type="button" onClick={() => setDomainMode('manual')} className="ml-auto text-[10px] hover:underline">Set manually</button>
                </>
              ) : (
                <span>Set a concept above to auto-detect domain</span>
              )}
            </div>
          ) : (
            <div className="flex items-center gap-1.5">
              <DomainPicker
                value={concept.domain_id ?? null}
                onChange={v => onSelect({ ...concept, domain_id: v ?? undefined })}
              />
              <button type="button" onClick={() => setDomainMode('auto')} className="text-[10px] text-gray-400 hover:text-gray-600 whitespace-nowrap">auto</button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Value mapping table ────────────────────────────────────────────────────

function ValueMappingTable({
  projectId,
  column,
  values,
  mapped,
  onChange,
}: {
  projectId: string
  column: string
  values: string[]
  mapped: Record<string, ConceptRef>
  onChange: (updated: Record<string, ConceptRef>) => void
}) {
  const set = (val: string, c: ConceptRef | null) => {
    const next = { ...mapped }
    if (c) next[val] = c; else delete next[val]
    onChange(next)
  }

  const mixedDomains = (() => {
    const ids = Object.values(mapped)
      .map(c => c.domain_id)
      .filter((id): id is number => id !== undefined && id !== null)
    const unique = [...new Set(ids)]
    if (unique.length < 2) return null
    return unique.map(id => DOMAIN_OPTIONS.find(d => d.value === id)?.label ?? `Domain ${id}`)
  })()

  return (
    <div className="flex flex-col gap-2">
      {values.length > 10 && (
        <div className="flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-1.5">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
          {values.length} distinct values — map each one below
        </div>
      )}
      {mixedDomains && (
        <div className="flex items-start gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          <span>
            <span className="font-semibold">Mixed domains detected:</span> values are mapped to{' '}
            {mixedDomains.join(' and ')}. All values for a single variable should map to the same domain.
          </span>
        </div>
      )}
      <div className="border border-border rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-muted border-b border-border">
            <tr>
              <th className="text-left px-3 py-2 font-medium text-muted-foreground w-28">Source value</th>
              <th className="text-left px-3 py-2 font-medium text-muted-foreground">OMOP concept</th>
            </tr>
          </thead>
          <tbody>
            {values.map((val, i) => (
              <tr key={val} className={clsx(i % 2 === 0 ? 'bg-card' : 'bg-muted', 'border-b last:border-0 border-border')}>
                <td className="px-3 py-2 font-mono text-foreground align-top pt-3">{val}</td>
                <td className="px-3 py-2">
                  <ValueConceptRow
                    projectId={projectId}
                    val={val}
                    column={column}
                    concept={mapped[val] ?? null}
                    onSelect={c => set(val, c)}
                    onClear={() => set(val, null)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        {Object.keys(mapped).length}/{values.length} values mapped
      </p>
    </div>
  )
}

// ── Unit mapping panel (shown when domain = Measurement or Observation) ──────

const EMPTY_UNIT_MAPPING: UnitMapping = { unit_col: null, unit_concepts: {} }

// Read the single fixed-unit concept out of a UnitMapping (or null if none / legacy
// column-based). Hardcoded units are stored as a one-entry dict keyed by the
// concept name so the backend's _infer_stem_overrides picks it up cleanly.
function getFixedUnit(um: UnitMapping | undefined | null): { id: number; name: string } | null {
  if (!um || um.unit_col) return null
  const entries = Object.entries(um.unit_concepts || {}).filter(([, v]) => typeof v === 'number' && v > 0)
  if (entries.length !== 1) return null
  return { name: entries[0][0], id: entries[0][1] }
}

function UnitMappingSection({
  unitMapping,
  onChange,
}: {
  unitMapping: UnitMapping
  onChange: (u: UnitMapping) => void
}) {
  const [manualId, setManualId] = useState('')
  const [manualName, setManualName] = useState('')

  const fixed = getFixedUnit(unitMapping)

  const applyManual = () => {
    const id = parseInt(manualId, 10)
    if (isNaN(id) || id < 1) return
    const displayName = manualName.trim() || `Concept ${id}`
    onChange({ unit_col: null, unit_concepts: { [displayName]: id } })
    setManualId('')
    setManualName('')
  }

  const clear = () => {
    onChange({ unit_col: null, unit_concepts: {} })
    setManualId('')
    setManualName('')
  }

  // Pre-fill the Athena query with whatever the user has typed as the name
  // so the link jumps closer to what they're looking for.
  const athenaUrl =
    'https://athena.ohdsi.org/search-terms/terms?vocabulary=UCUM&page=1&pageSize=15&query=' +
    encodeURIComponent(manualName.trim())

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-sky-200 bg-sky-50/40 p-3">
      <div className="flex items-center gap-2">
        <Scale className="w-4 h-4 text-sky-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-sky-800">
          Unit <span className="font-normal text-sky-600">(optional)</span>
        </p>
        <span className="ml-auto text-[10px] text-sky-500">
          fills <code className="bg-sky-100 px-1 rounded">unit_concept_id</code>
        </span>
      </div>
      <p className="text-[11px] text-sky-700/90">
        Hardcode the OMOP UCUM unit for this variable (e.g. mmHg, mg/dL). Leave blank if the value is unitless.
      </p>

      {fixed ? (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-sky-100 border border-sky-300 text-xs">
          <CheckCircle className="w-3.5 h-3.5 flex-shrink-0 text-sky-700" />
          <span className="font-semibold text-sky-900">{fixed.name}</span>
          <span className="text-sky-700">({fixed.id})</span>
          <button onClick={clear} className="ml-auto text-sky-500 hover:text-sky-700" title="Clear unit">
            <X className="w-3 h-3" />
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5 flex-wrap">
            <input
              type="number"
              value={manualId}
              onChange={e => setManualId(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applyManual()}
              placeholder="Concept ID"
              className="border border-sky-200 rounded px-2 py-1 text-xs w-24 bg-white focus:outline-none focus:ring-1 focus:ring-sky-400"
            />
            <input
              type="text"
              value={manualName}
              onChange={e => setManualName(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && applyManual()}
              placeholder="Name (optional)"
              className="border border-sky-200 rounded px-2 py-1 text-xs flex-1 min-w-[120px] bg-white focus:outline-none focus:ring-1 focus:ring-sky-400"
            />
            <button
              onClick={applyManual}
              disabled={!manualId}
              className="px-2 py-1 text-xs bg-sky-600 text-white rounded disabled:opacity-30 hover:bg-sky-700"
            >Set</button>
          </div>
          <a
            href={athenaUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[11px] text-sky-700 hover:text-sky-900 hover:underline w-fit"
          >
            ↗ Find UCUM concepts on Athena
          </a>
        </div>
      )}
    </div>
  )
}

// ── Single variable expandable row ─────────────────────────────────────────

function VariableRow({
  column,
  info,
  decision,
  projectId,
  checked,
  onCheck,
  onChange,
  batchMode,
  allColumns,
  allColumnInfos,
}: {
  column: string
  info: ColumnInfo | null
  decision: VariableDecision
  projectId: string
  checked: boolean
  onCheck: (c: boolean) => void
  onChange: (d: VariableDecision) => void
  batchMode: boolean
  allColumns: string[]
  allColumnInfos: Record<string, ColumnInfo>
}) {
  const [open, setOpen] = useState(false)
  const [domainMode, setDomainMode] = useState<'auto' | 'manual'>(() =>
    decision.domain_id !== null ? 'manual' : 'auto'
  )
  const [lookingUpDomain, setLookingUpDomain] = useState(false)
  // Raw domain string from CSV/EntityLinker (may not map to our 5 tables)
  const [rawDomain, setRawDomain] = useState<string | null>(null)
  const [lookupFailed, setLookupFailed] = useState(false)

  // Auto-detect domain when variable concept changes
  useEffect(() => {
    const concept = decision.variable_concept
    if (!concept) { setRawDomain(null); setLookupFailed(false); onChange({ ...decision, domain_id: null }); return }
    if (domainMode !== 'auto') return

    setLookupFailed(false)

    // EntityLinker results already carry domain — use it directly
    if (concept.domain) {
      setRawDomain(concept.domain)
      const numeric = DOMAIN_STRING_MAP[concept.domain.toLowerCase()]
      if (numeric !== undefined) onChange({ ...decision, domain_id: numeric })
      return
    }

    // Manual concept ID entry — look up in CONCEPT.csv
    setLookingUpDomain(true)
    setRawDomain(null)
    lookupConceptDomain(concept.concept_id)
      .then(res => {
        if (res.found && res.domain_id) {
          setRawDomain(res.domain_id)
          const numeric = DOMAIN_STRING_MAP[res.domain_id.toLowerCase()]
          if (numeric !== undefined) onChange({ ...decision, domain_id: numeric })
        } else {
          setRawDomain(null)
        }
      })
      .catch(() => setLookupFailed(true))
      .finally(() => setLookingUpDomain(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [decision.variable_concept?.concept_id, domainMode])

  const sm = STRATEGY_META[decision.strategy]
  const mappedValueCount = Object.keys(decision.value_concepts).length
  const hasVariableConcept = !!decision.variable_concept

  const mappingCompleteness = (() => {
    if (decision.strategy === 'skip') return 0
    if (decision.strategy === 'map_variable') return hasVariableConcept ? 100 : 0
    if (decision.strategy === 'map_values') {
      const total = (info?.distinct_count ?? 0)
      return Math.round(((mappedValueCount) / total) * 100)
    }
    if (decision.strategy === 'map_both') {
      const total = (info?.distinct_count ?? 0) + 1
      return Math.round(((mappedValueCount + (hasVariableConcept ? 1 : 0)) / total) * 100)
    }
    return 0
  })()

  const sampleValues = info?.distinct_values.slice(0, 10) ?? []
  const extraCount = (info?.distinct_count ?? 0) - 10

  return (
    <div className={clsx(
      'border rounded-lg overflow-hidden transition-colors',
      checked && 'ring-2 ring-primary',
      decision.strategy === 'skip' ? 'border-border/50' : 'border-border',
      open ? 'shadow-sm' : '',
    )}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2.5 bg-card">
        {batchMode && (
          <input
            type="checkbox"
            checked={checked}
            onChange={e => onCheck(e.target.checked)}
            className="rounded accent-primary flex-shrink-0"
            onClick={e => e.stopPropagation()}
          />
        )}

        <button
          className="flex items-center gap-2 flex-1 min-w-0 text-left"
          onClick={() => setOpen(o => !o)}
        >
          <span className="font-mono text-sm font-medium text-foreground w-36 flex-shrink-0 truncate">{column}</span>

          <span className={clsx('flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0', sm.color)}>
            {sm.icon}{sm.label}
          </span>

          {/* Unit indicator — shown when a fixed unit_concept_id was assigned */}
          {(() => {
            const fixed = getFixedUnit(decision.unit_mapping)
            if (!fixed) return null
            return (
              <span
                className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-sky-100 text-sky-700 border border-sky-200 flex-shrink-0"
                title={`Unit concept: ${fixed.name} (${fixed.id})`}
              >
                unit: <span className="font-mono">{fixed.name}</span>
              </span>
            )
          })()}

          {/* Sample value chips (header preview) */}
          {!open && info && sampleValues.length > 0 && (
            <div className="hidden sm:flex items-center gap-1 flex-1 min-w-0 overflow-hidden">
              {sampleValues.slice(0, 4).map(v => (
                <span key={v} className="bg-muted text-muted-foreground px-1.5 py-0 rounded text-xs font-mono whitespace-nowrap">{v}</span>
              ))}
              {(info.distinct_count > 4) && (
                <span className="text-xs text-muted-foreground">+{info.distinct_count - 4} more</span>
              )}
            </div>
          )}

          <div className="flex items-center gap-2 ml-auto flex-shrink-0">
            <span className={clsx(
              'text-xs font-semibold px-2 py-0.5 rounded-full min-w-[3rem] text-center',
              mappingCompleteness === 100
                ? 'bg-green-100 text-green-700'
                : mappingCompleteness > 0
                  ? 'bg-orange-100 text-orange-700'
                  : 'bg-muted text-muted-foreground',
            )}>
              {mappingCompleteness}%
            </span>
            {open ? <ChevronUp className="w-4 h-4 text-muted-foreground" /> : <ChevronDown className="w-4 h-4 text-muted-foreground" />}
          </div>
        </button>
      </div>

      {/* Expanded body */}
      {open && (
        <div className="border-t border-border px-4 py-4 flex flex-col gap-5 bg-card">

          {/* Column stats + sample values */}
          {info && (
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-muted rounded-lg p-2.5 text-center">
                  <p className="text-lg font-bold text-foreground">{info.distinct_count}</p>
                  <p className="text-xs text-muted-foreground">Distinct values</p>
                </div>
                <div className="bg-muted rounded-lg p-2.5 text-center">
                  <p className="text-lg font-bold text-foreground">{info.null_count}</p>
                  <p className="text-xs text-muted-foreground">Null values</p>
                </div>
                <div className="bg-muted rounded-lg p-2.5 text-center">
                  <p className="text-lg font-bold text-foreground">{mappingCompleteness}%</p>
                  <p className="text-xs text-muted-foreground">Completeness</p>
                </div>
              </div>

              <div>
                <p className="text-xs font-medium text-muted-foreground mb-1.5">Sample values</p>
                <div className="flex flex-wrap gap-1.5">
                  {sampleValues.map(v => (
                    <span key={v} className="bg-secondary/60 text-primary px-2 py-0.5 rounded text-xs font-mono border border-border">{v}</span>
                  ))}
                  {extraCount > 0 && (
                    <span className="text-xs text-muted-foreground self-center">… and {extraCount} more</span>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Domain picker — row-level only for map_variable / map_both; map_values uses per-value domain detection */}
          {(decision.strategy === 'map_variable' || decision.strategy === 'map_both') && (
            <div className="flex flex-col gap-1.5">
              <div className="flex items-center justify-between">
                <p className="text-xs font-semibold text-gray-600">
                  OMOP Domain <span className="font-normal text-gray-400">(stem_table domain_id)</span>
                </p>
                <div className="flex rounded border border-gray-200 overflow-hidden text-xs">
                  <button
                    type="button"
                    onClick={() => setDomainMode('auto')}
                    className={clsx('px-2 py-0.5', domainMode === 'auto' ? 'bg-indigo-600 text-white' : 'text-gray-500 hover:bg-gray-50')}
                  >Auto</button>
                  <button
                    type="button"
                    onClick={() => { setDomainMode('manual'); onChange({ ...decision, domain_id: null }); }}
                    className={clsx('px-2 py-0.5 border-l border-gray-200', domainMode === 'manual' ? 'bg-indigo-600 text-white' : 'text-gray-500 hover:bg-gray-50')}
                  >Manual</button>
                </div>
              </div>

              {domainMode === 'auto' ? (
                <div className={clsx(
                  'flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs',
                  decision.domain_id !== null
                    ? 'border-indigo-300 bg-indigo-50 text-indigo-800'
                    : lookupFailed || (rawDomain && decision.domain_id === null)
                      ? 'border-amber-200 bg-amber-50 text-amber-800'
                      : 'border-gray-200 bg-gray-50 text-gray-400',
                )}>
                  {lookingUpDomain ? (
                    <><Loader2 className="w-3 h-3 animate-spin flex-shrink-0" /> Looking up in CONCEPT.csv…</>
                  ) : decision.domain_id !== null ? (
                    <>
                      <span className="font-medium">{DOMAIN_OPTIONS.find(d => d.value === decision.domain_id)?.label} · {decision.domain_id}</span>
                      <span className="ml-auto text-indigo-400 bg-indigo-100 px-1.5 py-0.5 rounded text-[10px]">auto</span>
                    </>
                  ) : lookupFailed ? (
                    <><AlertTriangle className="w-3 h-3 flex-shrink-0" /><span>Lookup failed — switch to Manual</span></>
                  ) : rawDomain ? (
                    <><AlertTriangle className="w-3 h-3 flex-shrink-0" /><span>Domain <strong>"{rawDomain}"</strong> is not a standard ETL table — switch to Manual to map it</span></>
                  ) : decision.variable_concept ? (
                    <><AlertTriangle className="w-3 h-3 flex-shrink-0" /><span>Concept not found in CONCEPT.csv — switch to Manual</span></>
                  ) : (
                    <span>Set a concept above to auto-detect domain</span>
                  )}
                </div>
              ) : (
                <DomainPicker
                  value={decision.domain_id ?? null}
                  onChange={v => onChange({ ...decision, domain_id: v })}
                />
              )}
            </div>
          )}

          {/* Strategy selector */}
          <div>
            <p className="text-xs font-semibold text-muted-foreground mb-2">Mapping strategy</p>
            <div className="grid grid-cols-2 gap-2">
              {(Object.entries(STRATEGY_META) as [Strategy, typeof STRATEGY_META[Strategy]][]).map(([key, meta]) => (
                <button
                  key={key}
                  onClick={() => onChange({ ...decision, strategy: key })}
                  className={clsx(
                    'flex items-center gap-2.5 px-3 py-2.5 rounded-lg border text-left transition-colors',
                    decision.strategy === key ? 'border-primary bg-secondary/60' : 'border-border hover:border-border/80'
                  )}
                >
                  <span className={clsx('flex-shrink-0', decision.strategy === key ? 'text-primary' : 'text-muted-foreground')}>{meta.icon}</span>
                  <div>
                    <p className="text-xs font-semibold text-foreground">{meta.label}</p>
                    <p className="text-xs text-muted-foreground mt-0.5 leading-tight">
                      {key === 'map_variable' && 'Numeric — variable → one concept'}
                      {key === 'map_values' && 'Each (variable, value) pair = its own concept'}
                      {key === 'map_both' && 'Variable gets a concept + each value gets value_as_concept_id'}
                      {key === 'skip' && 'Not an OMOP clinical variable (ID, date, etc.)'}
                    </p>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Variable concept picker */}
          {(decision.strategy === 'map_variable' || decision.strategy === 'map_both') && (
            <div className="flex flex-col gap-1.5">
              <p className="text-xs font-semibold text-muted-foreground">Concept for <code className="bg-muted px-1 rounded">{column}</code></p>
              <ConceptPicker
                projectId={projectId}
                label={column}
                defaultQuery={column}
                value={decision.variable_concept}
                onSelect={c => onChange({ ...decision, variable_concept: c })}
                onClear={() => onChange({ ...decision, variable_concept: null })}
              />
            </div>
          )}

          {/* Value mapping table */}
          {(decision.strategy === 'map_values' || decision.strategy === 'map_both') && (
            <div className="flex flex-col gap-1.5">
              <p className="text-xs font-semibold text-muted-foreground">
                {decision.strategy === 'map_both' ? 'value_as_concept_id for each value' : 'Concept for each (variable, value) pair'}
              </p>
              {!info ? (
                <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border border-amber-200 bg-amber-50 text-xs text-amber-800">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <div>
                    <p className="font-semibold">Distinct values not loaded for this column.</p>
                    <p className="mt-0.5">
                      The backend couldn't read the source CSV. Most likely the file was deleted (e.g., after a Docker volume reset). Go back to <strong>Source upload</strong> and re-upload your source.
                    </p>
                  </div>
                </div>
              ) : info.distinct_values.length === 0 ? (
                <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border border-amber-200 bg-amber-50 text-xs text-amber-800">
                  <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  <span>This column has no distinct non-null values to map.</span>
                </div>
              ) : (
                <ValueMappingTable
                  projectId={projectId}
                  column={column}
                  values={info.distinct_values}
                  mapped={decision.value_concepts}
                  onChange={vc => onChange({ ...decision, value_concepts: vc })}
                />
              )}
            </div>
          )}

          {/* Unit (optional) — shown for Measurement / Observation under the concept */}
          {(decision.domain_id === 1 || decision.domain_id === 2) && decision.strategy !== 'skip' && (
            <UnitMappingSection
              unitMapping={decision.unit_mapping ?? EMPTY_UNIT_MAPPING}
              onChange={u => onChange({ ...decision, unit_mapping: u })}
            />
          )}
        </div>
      )}
    </div>
  )
}

// ── Batch panel ────────────────────────────────────────────────────────────

function BatchPanel({
  projectId,
  selectedCols,
  columnInfos,
  decisions,
  onApply,
  onClear,
}: {
  projectId: string
  selectedCols: string[]
  columnInfos: Record<string, ColumnInfo>
  decisions: Record<string, VariableDecision>
  onApply: (updates: Record<string, VariableDecision>) => void
  onClear: () => void
}) {
  const [strategy, setStrategy] = useState<Strategy>('map_variable')
  const [batchConcept, setBatchConcept] = useState<ConceptRef | null>(null)
  const [overwrite, setOverwrite] = useState(false)

  // For value mapping batch: check if all selected cols share same distinct values
  const valSets = selectedCols.map(c =>
    JSON.stringify((columnInfos[c]?.distinct_values ?? []).map(v => v.trim().toLowerCase()).sort())
  )
  const allSameValues = valSets.length > 0 && valSets.every(v => v === valSets[0])
  const sharedValues = allSameValues && selectedCols.length > 0
    ? columnInfos[selectedCols[0]]?.distinct_values ?? []
    : []

  const [valueConcepts, setValueConcepts] = useState<Record<string, ConceptRef>>({})

  const apply = () => {
    const updates: Record<string, VariableDecision> = {}
    for (const col of selectedCols) {
      const existing = decisions[col]
      if (!overwrite && (
        existing.variable_concept ||
        Object.keys(existing.value_concepts).length > 0
      )) continue

      updates[col] = {
        strategy,
        variable_concept: (strategy === 'map_variable' || strategy === 'map_both') ? batchConcept : null,
        value_concepts: (strategy === 'map_values' || strategy === 'map_both') ? { ...valueConcepts } : {},
        domain_id: existing.domain_id ?? null,
      }
    }
    onApply(updates)
    onClear()
  }

  if (selectedCols.length === 0) return null

  return (
    <div className="rounded-xl border border-border bg-secondary/60 p-4 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Tag className="w-4 h-4 text-primary" />
          <span className="font-semibold text-sm text-secondary-foreground">Batch mapping — {selectedCols.length} variable{selectedCols.length > 1 ? 's' : ''} selected</span>
        </div>
        <button onClick={onClear} className="text-xs text-muted-foreground hover:text-primary">Deselect all</button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {selectedCols.map(c => (
          <span key={c} className="bg-card border border-border text-primary px-2 py-0.5 rounded text-xs font-mono">{c}</span>
        ))}
      </div>

      {/* Strategy */}
      <div className="grid grid-cols-4 gap-2">
        {(Object.entries(STRATEGY_META) as [Strategy, typeof STRATEGY_META[Strategy]][]).map(([key, meta]) => (
          <button
            key={key}
            onClick={() => setStrategy(key)}
            className={clsx(
              'flex items-center gap-1.5 px-2 py-1.5 rounded-lg border text-xs font-medium transition-colors',
              strategy === key ? 'border-primary bg-card text-primary shadow-sm' : 'border-border text-muted-foreground hover:border-primary/50'
            )}
          >
            {meta.icon} {meta.label}
          </button>
        ))}
      </div>

      {/* Concept picker for variable */}
      {(strategy === 'map_variable' || strategy === 'map_both') && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-secondary-foreground">Concept to apply to all selected variables:</p>
          <ConceptPicker
            projectId={projectId}
            label="batch"
            defaultQuery={selectedCols[0] ?? ''}
            value={batchConcept}
            onSelect={setBatchConcept}
            onClear={() => setBatchConcept(null)}
          />
        </div>
      )}

      {/* Value mapping for batch */}
      {(strategy === 'map_values' || strategy === 'map_both') && (
        allSameValues ? (
          <div className="flex flex-col gap-1">
            <p className="text-xs font-medium text-secondary-foreground">Map values (shared by all selected variables):</p>
            <ValueMappingTable
              projectId={projectId}
              column={selectedCols[0]}
              values={sharedValues}
              mapped={valueConcepts}
              onChange={setValueConcepts}
            />
          </div>
        ) : (
          <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2 flex items-center gap-2">
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
            Selected variables have different value sets — value mapping must be done per variable
          </div>
        )
      )}

      <div className="flex items-center gap-3">
        <label className="flex items-center gap-1.5 text-xs text-secondary-foreground cursor-pointer">
          <input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} className="rounded accent-primary" />
          Overwrite existing mappings
        </label>
        <button
          onClick={apply}
          disabled={
            strategy !== 'skip' &&
            (strategy === 'map_variable' || strategy === 'map_both') && !batchConcept
          }
          className="ml-auto px-4 py-1.5 bg-primary text-primary-foreground rounded-md text-xs font-medium hover:bg-primary/90 disabled:opacity-40"
        >
          Apply to {selectedCols.length} variable{selectedCols.length > 1 ? 's' : ''}
        </button>
      </div>
    </div>
  )
}

// ── Structural column extractor ────────────────────────────────────────────

export default function ConceptsStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [decisions, setDecisions] = useState<Record<string, VariableDecision>>({})
  const [colFileMap, setColFileMap] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState('')

  // Batch state
  const [batchMode, setBatchMode] = useState(false)
  const [selectedCols, setSelectedCols] = useState<string[]>([])

  // Filter
  const [filter, setFilter] = useState<'all' | 'mapped' | 'skipped'>('all')
  const [search, setSearch] = useState('')

  // AI / custom-concept settings
  const [openaiConfigured, setOpenaiConfigured] = useState(false)
  const [customVocab, setCustomVocab] = useState(project.custom_vocabulary_id || 'CUSTOM')
  const [editingVocab, setEditingVocab] = useState(false)
  const [vocabDraft, setVocabDraft] = useState(customVocab)

  useEffect(() => {
    getApiHealth()
      .then(h => setOpenaiConfigured(!!h.openai_configured))
      .catch(() => setOpenaiConfigured(false))
  }, [])

  const saveCustomVocab = async () => {
    const v = vocabDraft.trim()
    if (!v || v === customVocab) { setEditingVocab(false); return }
    try {
      const updated = await updateProjectSettings(project.id, { custom_vocabulary_id: v })
      setCustomVocab(v)
      onUpdate(updated)
    } catch {
      // ignore — keep editor open
      return
    }
    setEditingVocab(false)
  }

  const { cols, filePicker, selectedFile } = useSourceFile(project, 'concepts')

  const structuralCols = useMemo(
    () => getStructuralColumns((project.etl_config || {}) as Record<string, unknown>),
    [project.etl_config],
  )
  const conceptCols = useMemo(() => cols.filter(c => !structuralCols.has(c)), [cols, structuralCols])

  useEffect(() => {
    Promise.all([
      getColumnValues(project.id, selectedFile?.filename),
      getConceptDecisions(project.id),
    ]).then(([infos, saved]: [Record<string, ColumnInfo>, Record<string, VariableDecision>]) => {
      setColumnInfos(infos)
      setDecisions(prev => {
        // saved contains decisions from ALL files; prev contains in-memory changes.
        // Merge so neither is lost: saved is the base, prev (unsaved changes) wins.
        const next: Record<string, VariableDecision> = { ...saved, ...prev }
        // Initialize columns from ALL source files so that every file's columns
        // are present in `decisions` even if the user never switches to that file.
        const structCols = getStructuralColumns((project.etl_config || {}) as Record<string, unknown>)
        for (const sf of (project.source_files || [])) {
          for (const col of (sf.columns || [])) {
            if (!structCols.has(col) && !(col in next)) {
              next[col] = { strategy: 'skip', variable_concept: null, value_concepts: {}, domain_id: null }
            }
          }
        }
        return next
      })
    }).finally(() => setLoading(false))
  }, [project.id, selectedFile?.filename])

  // Track which source file each column was last active in, so StemTableStep
  // can filter to only show variables belonging to its selected file.
  useEffect(() => {
    const fn = selectedFile?.filename
    if (!fn) return
    setColFileMap(prev => {
      const next = { ...prev }
      for (const col of conceptCols) next[col] = fn
      return next
    })
  }, [selectedFile?.filename, conceptCols])

  const setDecision = useCallback((col: string, d: VariableDecision) => {
    setDecisions(prev => ({ ...prev, [col]: d }))
  }, [])

  const applyBatch = useCallback((updates: Record<string, VariableDecision>) => {
    setDecisions(prev => ({ ...prev, ...updates }))
  }, [])

  const toggleSelect = (col: string, checked: boolean) => {
    setSelectedCols(prev => checked ? [...prev, col] : prev.filter(c => c !== col))
  }

  const { prev: prevSlug, next: nextSlug } = getAdjacentSlugs(project, 'concepts')

  const saveConfig = async () => {
    const [updated] = await Promise.all([
      saveConceptDecisions(project.id, decisions as Record<string, unknown>),
      updateTableConfig(project.id, 'concepts', { col_file_map: colFileMap, source_filename: selectedFile?.filename ?? null }),
    ])
    onUpdate(updated)
  }

  const handleNext = async () => {
    setSaving(true)
    setGenError('')
    try {
      await Promise.all([
        saveConceptDecisions(project.id, decisions as Record<string, unknown>),
        updateTableConfig(project.id, 'concepts', { col_file_map: colFileMap, source_filename: selectedFile?.filename ?? null }),
      ])
      setGenerating(true)
      const updated = await generateMappingCsvs(project.id)
      onUpdate(updated)
      if (nextSlug) navigate(`/project/${project.id}/step/${nextSlug}`)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setGenError(err?.response?.data?.detail || 'Failed to generate mapping CSVs.')
    } finally {
      setSaving(false)
      setGenerating(false)
    }
  }

  // Stats
  const mappedCount = conceptCols.filter(c => {
    const d = decisions[c]
    return d && d.strategy !== 'skip' && (d.variable_concept || Object.keys(d.value_concepts).length > 0)
  }).length
  const skippedCount = conceptCols.filter(c => decisions[c]?.strategy === 'skip').length

  // Filter + search
  const filteredCols = conceptCols.filter(col => {
    if (search && !col.toLowerCase().includes(search.toLowerCase())) return false
    const d = decisions[col]
    if (filter === 'skipped') return d?.strategy === 'skip'
    if (filter === 'mapped') return d && d.strategy !== 'skip' && (d.variable_concept || Object.keys(d.value_concepts).length > 0)
    return true
  })

  return (
    <ConceptsCtx.Provider value={{ rerankerAvailable: openaiConfigured, customVocabularyId: customVocab }}>
    <WizardLayout
      project={project}
      currentSlug="concepts"
      onBack={prevSlug ? () => navigate(`/project/${project.id}/step/${prevSlug}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel={generating ? 'Generating CSVs…' : saving ? 'Saving…' : 'Next: Stem Table →'}
      nextDisabled={saving || generating}
      saving={saving}
    >
      <div className="flex flex-col gap-5">
        {filePicker}
        <div>
          <h2 className="text-xl font-bold text-primary">Concept Mapping</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Map each source variable to OMOP concepts. Click a variable to expand it and see its values.
            Use batch mode to map multiple variables at once.
          </p>
        </div>

        {/* AI + custom-vocab settings banner */}
        <div className="flex items-center gap-3 flex-wrap rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs">
          <div className="flex items-center gap-1.5">
            <Sparkles className={clsx('w-3.5 h-3.5', openaiConfigured ? 'text-indigo-600' : 'text-muted-foreground')} />
            <span className="font-semibold text-foreground">AI reranker:</span>
            <span className={openaiConfigured ? 'text-indigo-700' : 'text-muted-foreground'}>
              {openaiConfigured ? 'available' : 'disabled (set OPENAI_API_KEY)'}
            </span>
          </div>
          <span className="text-muted-foreground">·</span>
          <div className="flex items-center gap-1.5">
            <Plus className="w-3.5 h-3.5 text-purple-600" />
            <span className="font-semibold text-foreground">Custom vocabulary:</span>
            {editingVocab ? (
              <>
                <input
                  type="text"
                  value={vocabDraft}
                  autoFocus
                  onChange={e => setVocabDraft(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') saveCustomVocab(); if (e.key === 'Escape') { setVocabDraft(customVocab); setEditingVocab(false) } }}
                  className="border border-purple-300 rounded px-1.5 py-0.5 text-xs w-32 bg-white focus:outline-none focus:ring-1 focus:ring-purple-400"
                />
                <button onClick={saveCustomVocab} className="text-purple-700 font-medium hover:underline">save</button>
                <button onClick={() => { setVocabDraft(customVocab); setEditingVocab(false) }} className="text-muted-foreground hover:text-foreground">cancel</button>
              </>
            ) : (
              <>
                <code className="bg-purple-100 text-purple-800 px-1.5 py-0.5 rounded font-mono">{customVocab}</code>
                <button onClick={() => { setVocabDraft(customVocab); setEditingVocab(true) }} className="text-purple-700 hover:underline">edit</button>
              </>
            )}
          </div>
          <span className="text-muted-foreground ml-auto">
            Default vocabulary id applied to custom concepts (id ≥ 2,000,000,000).
          </span>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-foreground">{conceptCols.length}</p>
            <p className="text-xs text-muted-foreground">Variables to map</p>
          </div>
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-green-700">{mappedCount}</p>
            <p className="text-xs text-green-600">Mapped</p>
          </div>
          <div className="bg-muted border border-border rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-muted-foreground">{skippedCount}</p>
            <p className="text-xs text-muted-foreground">Skipped</p>
          </div>
        </div>

        {/* Source-data sanity check */}
        {!loading && conceptCols.length > 0 && Object.keys(columnInfos).length === 0 && (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border border-amber-200 bg-amber-50 text-xs text-amber-800">
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <div>
              <p className="font-semibold">Source CSV not available — value-mapping options won't render.</p>
              <p className="mt-0.5">
                The project row still references a source file that no longer exists on disk (common after a Docker volume reset).
                Go back to <strong>Source upload</strong> and re-upload the same CSV to restore distinct-value detection.
              </p>
            </div>
          </div>
        )}

        {/* Excluded structural columns */}
        {structuralCols.size > 0 && (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border border-border bg-secondary/60 text-xs text-secondary-foreground">
            <CheckCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-primary" />
            <span>
              <span className="font-semibold">{structuralCols.size} structural column{structuralCols.size > 1 ? 's' : ''} excluded</span>
              {' '}— already mapped in Person, Visit, Obs. Period, Location, Care Site, Provider, or Death steps:{' '}
              <span className="font-mono">{[...structuralCols].join(', ')}</span>
            </span>
          </div>
        )}

        {/* Toolbar */}
        <div className="flex items-center gap-3 flex-wrap">
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter columns…"
            className="border border-border rounded-md px-3 py-1.5 text-sm w-44 focus:outline-none focus:ring-2 focus:ring-ring bg-background text-foreground"
          />
          <div className="flex gap-1">
            {(['all', 'mapped', 'skipped'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={clsx('px-2.5 py-1.5 rounded-md text-xs font-medium capitalize', filter === f ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80')}
              >{f}</button>
            ))}
          </div>
          <button
            onClick={() => { setBatchMode(b => !b); setSelectedCols([]) }}
            className={clsx('ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border', batchMode ? 'bg-primary text-primary-foreground border-primary' : 'border-border text-muted-foreground hover:bg-muted')}
          >
            <Tag className="w-3.5 h-3.5" />
            {batchMode ? 'Exit batch mode' : 'Batch mode'}
          </button>
          {batchMode && filteredCols.length > 0 && (
            <button
              onClick={() => setSelectedCols(filteredCols)}
              className="text-xs text-primary hover:underline"
            >Select all visible</button>
          )}
        </div>

        {/* Batch panel */}
        {batchMode && selectedCols.length > 0 && (
          <BatchPanel
            projectId={project.id}
            selectedCols={selectedCols}
            columnInfos={columnInfos}
            decisions={decisions}
            onApply={applyBatch}
            onClear={() => setSelectedCols([])}
          />
        )}

        {genError && (
          <div className="bg-destructive/10 border border-destructive/20 rounded-lg p-3 text-sm text-destructive">{genError}</div>
        )}

        {loading && (
          <div className="flex items-center gap-2 py-8 justify-center text-muted-foreground">
            <Loader2 className="w-5 h-5 animate-spin" /> Loading column data…
          </div>
        )}

        {!loading && (
          <div className="flex flex-col gap-2">
            {filteredCols.map(col => (
              <VariableRow
                key={col}
                column={col}
                info={columnInfos[col] ?? null}
                decision={decisions[col] ?? { strategy: 'skip', variable_concept: null, value_concepts: {}, domain_id: null }}
                projectId={project.id}
                checked={selectedCols.includes(col)}
                onCheck={c => toggleSelect(col, c)}
                onChange={d => setDecision(col, d)}
                batchMode={batchMode}
                allColumns={cols}
                allColumnInfos={columnInfos}
              />
            ))}
            {filteredCols.length === 0 && (
              <p className="text-center text-sm text-muted-foreground py-8">No columns match the current filter.</p>
            )}
          </div>
        )}

        <div className="rounded-lg border border-border bg-secondary/60 p-3 text-xs text-muted-foreground">
          Clicking <strong>Next</strong> saves all decisions and auto-generates the 3 concept mapping CSV files used by the ETL engine.
        </div>
      </div>
    </WizardLayout>
    </ConceptsCtx.Provider>
  )
}
