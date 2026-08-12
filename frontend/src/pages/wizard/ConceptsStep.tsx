import { useState, useEffect, useCallback, useMemo, useRef, createContext, useContext, memo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getColumnValues,
  getConceptDecisions,
  saveConceptDecisions,
  generateMappingCsvs,
  downloadMappingFiles,
  downloadMappingSummary,
  lookupConceptDomain,
  conceptSearch,
  getApiHealth,
  updateTableConfig,
} from '../../api/client'
import type { Project } from '../../types'
import { getStructuralColumns, getStructuralColFileMap } from '../../utils'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import {
  ChevronDown, ChevronUp, CheckCircle, Loader2,
  Hash, List, Layers, SkipForward, X,
  AlertTriangle, Tag, Sparkles, Plus, Scale, FileText, Info, Download, Pill, Lock,
} from 'lucide-react'
import clsx from 'clsx'
import { useSourceFile } from '../../hooks/useSourceFile'
import { Textarea } from '@/components/ui/textarea'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'

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

interface RouteMapping {
  route_col: string | null
  route_concepts: Record<string, number>  // route_source_value → route_concept_id
}

const EMPTY_ROUTE_MAPPING: RouteMapping = { route_col: null, route_concepts: {} }

interface ModifierMapping {
  modifier_col: string | null
  modifier_concepts: Record<string, number>  // modifier_source_value → modifier_concept_id
}

const EMPTY_MODIFIER_MAPPING: ModifierMapping = { modifier_col: null, modifier_concepts: {} }

interface ConditionStatusMapping {
  condition_status_col: string | null
  condition_status_concepts: Record<string, number>  // condition_status_source_value → condition_status_concept_id
}

const EMPTY_CONDITION_STATUS_MAPPING: ConditionStatusMapping = { condition_status_col: null, condition_status_concepts: {} }

interface QualifierMapping {
  qualifier_col: string | null
  qualifier_concepts: Record<string, number>  // qualifier_source_value → qualifier_concept_id
}

const EMPTY_QUALIFIER_MAPPING: QualifierMapping = { qualifier_col: null, qualifier_concepts: {} }

interface OperatorMapping {
  operator_col: string | null
  operator_concepts: Record<string, number>  // operator_source_value → operator_concept_id
}

const EMPTY_OPERATOR_MAPPING: OperatorMapping = { operator_col: null, operator_concepts: {} }

interface VariableDecision {
  strategy: Strategy
  variable_concept: ConceptRef | null
  value_concepts: Record<string, ConceptRef>
  domain_id: number | null
  // Whether this variable is included in mapping at all — ticked by default. Unticking
  // excludes it from the "Variables to map" totals and locks the row so it can't be mapped,
  // independent of `strategy` (which only applies once the variable is included).
  enabled?: boolean
  unit_mapping?: UnitMapping
  // Free-text guidance for how this variable should be transformed/loaded
  // into the stem table. Folded into the AI prompt when the stem_table
  // script is generated.
  extra_instructions?: string
  // Drug Exposure (domain_id 3) only: names of sibling columns in the same
  // source file whose values are pulled in verbatim as this variable's
  // drug_exposure fields (e.g. a dosage column next to a drug-name column).
  quantity_col?: string | null
  days_supply_col?: string | null
  refills_col?: string | null
  sig_col?: string | null
  lot_number_col?: string | null
  stop_reason_col?: string | null
  // Route and Unit (dose unit — unit_mapping, shared with Measurement/Observation) each
  // support either a single fixed concept for the whole variable (col: null, one entry
  // in concepts) or a per-row lookup via a chosen column's distinct values (0 = explicitly
  // not mapped), toggled in the UI.
  route_mapping?: RouteMapping
  // Procedure Occurrence (domain_id 4) only: modifier_concept_id / modifier_source_value,
  // same fixed-or-per-column convention as Unit/Route above.
  modifier_mapping?: ModifierMapping
  // Condition Occurrence (domain_id 5) only: condition_status_concept_id /
  // condition_status_source_value, same fixed-or-per-column convention.
  condition_status_mapping?: ConditionStatusMapping
  // Observation (domain_id 2) only: qualifier_concept_id / qualifier_source_value,
  // same fixed-or-per-column convention. No domain restriction (any Standard concept).
  qualifier_mapping?: QualifierMapping
  // Measurement (domain_id 1) only: operator_concept_id / operator_source_value
  // (e.g. =, >, <), same fixed-or-per-column convention, must resolve to the OMOP
  // "Meas Value Operator" domain.
  operator_mapping?: OperatorMapping
  // Measurement (domain_id 1) only: sibling columns holding the reference range
  // low/high bounds for each row, pulled in verbatim (same unit as value_as_number).
  range_low_col?: string | null
  range_high_col?: string | null
  // Fixed only — drug_type_concept_id for every row of this variable, must resolve to
  // the OMOP "Type Concept" domain. Falls back to the pipeline default (32879) when unset.
  type_concept_id?: number | null
  type_concept_name?: string | null
  // Override the visit-derived start date/time (and set an end date/time, which otherwise
  // stays empty) from sibling columns, parsed with the given strptime format — shared by
  // both columns. Leaving the format blank falls back to '%Y-%m-%d'.
  start_datetime_col?: string | null
  end_datetime_col?: string | null
  datetime_format?: string | null
}

// ── Constants ──────────────────────────────────────────────────────────────

// Stable reference so an untouched column's fallback decision doesn't defeat
// VariableRow's memoization by being recreated on every render.
const DEFAULT_DECISION: VariableDecision = { strategy: 'skip', variable_concept: null, value_concepts: {}, domain_id: null }

// map_values has no row-level domain picker (domain is detected per value), so a
// column mapped that way never sets decision.domain_id itself — fall back to
// checking whether any mapped value resolved to the domain in question. Mirrors
// the isDrugExposure/isProcedureOccurrence/etc. checks inside VariableRow.
function decisionMatchesDomain(d: VariableDecision | undefined, domainId: number): boolean {
  if (!d) return false
  if (d.domain_id === domainId) return true
  if (d.strategy === 'map_values' || d.strategy === 'map_both') {
    return Object.values(d.value_concepts).some(vc => vc.domain_id === domainId)
  }
  return false
}

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

// Vocabulary id every newly-created custom concept starts with.
const DEFAULT_CUSTOM_VOCABULARY = 'CUSTOM'

// ── Custom concepts created project-wide ────────────────────────────────────

interface CustomConceptUsage {
  column: string
  file: string | null
  value?: string  // present when this usage is a per-value concept, not the variable concept
}

interface CustomConceptEntry {
  concept_id: number
  concept_name: string
  concept_code?: string
  vocabulary_id?: string
  domain?: string
  concept_class_id?: string
  usages: CustomConceptUsage[]
  // true if usages disagree on name/code/vocab — likely an accidental ID collision
  // from before duplicate IDs were blocked at creation time
  conflicting: boolean
}

// ── Shared per-page settings (avoid prop-drilling) ─────────────────────────

interface ConceptsSettings {
  rerankerAvailable: boolean
  usedCustomConceptIds: Map<number, CustomConceptEntry>
}

const ConceptsCtx = createContext<ConceptsSettings>({
  rerankerAvailable: false,
  usedCustomConceptIds: new Map(),
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
  onCancel,
  onCreate,
}: {
  defaultName: string
  onCancel: () => void
  onCreate: (c: ConceptRef) => void
}) {
  const { usedCustomConceptIds } = useConceptsSettings()
  // Default to the next free custom id so most users never hit a collision —
  // only fires if they manually type an id that's already taken (see isDuplicate below).
  const [conceptId, setConceptId] = useState(() => {
    const maxUsed = Math.max(2_000_000_000, ...usedCustomConceptIds.keys())
    return String(maxUsed + 1)
  })
  const [conceptName, setConceptName] = useState(defaultName)
  const [conceptCode, setConceptCode] = useState('')
  const [domain, setDomain] = useState<string>('Observation')
  const [conceptClass, setConceptClass] = useState('Clinical Finding')
  const [vocabularyId, setVocabularyId] = useState(DEFAULT_CUSTOM_VOCABULARY)

  const idNum = parseInt(conceptId, 10)
  const idValid = !isNaN(idNum) && idNum >= 2_000_000_000
  const duplicateOf = idValid ? usedCustomConceptIds.get(idNum) : undefined
  const nameValid = conceptName.trim().length > 0
  const codeValid = conceptCode.trim().length > 0
  const canCreate = idValid && nameValid && codeValid && vocabularyId.trim().length > 0 && !duplicateOf

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
          {idValid && duplicateOf && (
            <span className="text-[10px] text-red-600">Already used for "{duplicateOf.concept_name}" — choose a different ID.</span>
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
  defaultQuery,
  value,
  onSelect,
  onClear,
  validateDomain,
}: {
  projectId: string
  label: string
  defaultQuery: string
  value: ConceptRef | null
  onSelect: (c: ConceptRef) => void
  onClear: () => void
  // When provided, gates every commit path (manual ID/name, AI search pick, custom
  // concept create) on the concept's OMOP domain. Return an error message to block
  // the selection (input stays as typed, nothing is applied) or null to allow it.
  validateDomain?: (domainStr: string | null) => string | null
}) {
  const { rerankerAvailable, usedCustomConceptIds } = useConceptsSettings()
  const cs = useConceptSearch(projectId)
  const [manualId, setManualId] = useState('')
  const [idLocked, setIdLocked] = useState(false)
  const [manualName, setManualName] = useState('')
  const [lookingUpName, setLookingUpName] = useState(false)
  const [editingName, setEditingName] = useState('')
  const [showSearch, setShowSearch] = useState(false)
  const [showCustom, setShowCustom] = useState(false)
  const [useReranker, setUseReranker] = useState(rerankerAvailable)
  const [domainError, setDomainError] = useState<string | null>(null)
  const nameFieldRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (value) setEditingName(value.concept_name ?? '')
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value?.concept_id])

  // Grow the name field to fit its content, up to 4 lines, then scroll.
  useEffect(() => {
    const el = nameFieldRef.current
    if (!el) return
    el.style.height = 'auto'
    const style = getComputedStyle(el)
    const lineHeight = parseFloat(style.lineHeight) || 16
    const maxHeight = lineHeight * 4
      + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom)
      + parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth)
    const nextHeight = Math.min(el.scrollHeight, maxHeight)
    el.style.height = `${nextHeight}px`
    el.style.overflowY = el.scrollHeight > maxHeight ? 'auto' : 'hidden'
  }, [editingName])

  const commitConcept = (c: ConceptRef) => {
    onSelect(c)
    setManualId(''); setManualName(''); setIdLocked(false); setDomainError(null)
  }

  const applyId = () => {
    const id = parseInt(manualId)
    if (isNaN(id) || id < 0) return
    if (id === 0) { commitConcept({ concept_id: 0, concept_name: 'Not mapped' }); return }
    setDomainError(null)

    // Known custom concept (created elsewhere in the project) — reuse its
    // name/code/vocab instead of hitting the real OMOP vocab lookup, which
    // has never heard of it and would otherwise drop the custom metadata.
    const existingCustom = usedCustomConceptIds.get(id)
    if (existingCustom) {
      if (validateDomain) {
        const err = validateDomain(existingCustom.domain ?? null)
        if (err) { setDomainError(err); return }
      }
      commitConcept({
        concept_id: id,
        concept_name: existingCustom.concept_name,
        concept_code: existingCustom.concept_code,
        vocabulary_id: existingCustom.vocabulary_id,
        domain: existingCustom.domain,
        domain_id_str: existingCustom.domain,
        domain_id: existingCustom.domain ? DOMAIN_STRING_MAP[existingCustom.domain.toLowerCase()] : undefined,
        concept_class_id: existingCustom.concept_class_id,
        is_custom: true,
      })
      return
    }

    // Fast path: id + name both typed and nothing to validate — no lookup needed.
    if (manualName.trim() && !validateDomain) {
      commitConcept({ concept_id: id, concept_name: manualName.trim() })
      return
    }

    setIdLocked(true)
    setLookingUpName(true)
    lookupConceptDomain(id)
      .then(res => {
        if (validateDomain) {
          const err = validateDomain(res.found ? res.domain_id : null)
          if (err) { setDomainError(err); setIdLocked(false); return }
        }
        const name = manualName.trim() || res.concept_name || `Concept ${id}`
        commitConcept({ concept_id: id, concept_name: name })
      })
      .catch(() => {
        if (validateDomain) {
          setDomainError("Couldn't verify this concept's domain — try again.")
          setIdLocked(false)
          return
        }
        commitConcept({ concept_id: id, concept_name: manualName.trim() || `Concept ${id}` })
      })
      .finally(() => setLookingUpName(false))
  }

  const unlockId = () => { setIdLocked(false) }

  const applyName = () => {
    const id = parseInt(manualId)
    if (isNaN(id) || id < 0) return
    if (id === 0) { commitConcept({ concept_id: 0, concept_name: 'Not mapped' }); return }

    if (!validateDomain) {
      commitConcept({ concept_id: id, concept_name: manualName.trim() || `Concept ${id}` })
      return
    }

    setDomainError(null)
    setLookingUpName(true)
    lookupConceptDomain(id)
      .then(res => {
        const err = validateDomain(res.found ? res.domain_id : null)
        if (err) { setDomainError(err); return }
        commitConcept({ concept_id: id, concept_name: manualName.trim() || res.concept_name || `Concept ${id}` })
      })
      .catch(() => setDomainError("Couldn't verify this concept's domain — try again."))
      .finally(() => setLookingUpName(false))
  }

  const commitEditingName = () => {
    if (!value) return
    const trimmed = editingName.trim()
    if (trimmed && trimmed !== value.concept_name) onSelect({ ...value, concept_name: trimmed })
  }

  if (value) {
    const isUnmapped = value.concept_id === 0
    const isCustom = !isUnmapped && (value.is_custom || value.concept_id >= 2_000_000_000)
    const lockedCls = isUnmapped
      ? 'border border-gray-300 bg-gray-100 text-gray-600'
      : isCustom
      ? 'border border-purple-200 bg-purple-50 text-purple-800'
      : 'border border-green-200 bg-green-50 text-green-800'
    const clearId = () => { setIdLocked(false); setManualId(''); setManualName(''); onClear() }
    const clearName = () => { setEditingName('') }
    return (
      <div className="flex items-start gap-1.5 w-full">
        {/* Locked ID chip with X — clears only ID, preserves name */}
        <div className={clsx('flex items-center gap-1 px-2 py-1 text-xs rounded font-mono flex-shrink-0', lockedCls)}>
          <CheckCircle className="w-3 h-3 flex-shrink-0" />
          {isCustom && (
            <span className="bg-purple-200 text-purple-800 px-1 rounded text-[10px] font-bold uppercase tracking-wide ml-0.5">Custom</span>
          )}
          <span>{isUnmapped ? '0 · Not mapped' : value.concept_id}</span>
          {!isUnmapped && value.vocabulary_id && <span className="opacity-60 ml-0.5">· {value.vocabulary_id}</span>}
          {isCustom && value.concept_code && <span className="opacity-60 ml-0.5">· {value.concept_code}</span>}
          <button onClick={clearId} className="opacity-60 hover:opacity-100 hover:text-destructive ml-1"><X className="w-3 h-3" /></button>
        </div>
        {/* Editable name field with X — clears only name, preserves ID; fills remaining row width and wraps up to 4 lines */}
        <textarea
          ref={nameFieldRef}
          rows={1}
          value={editingName}
          onChange={e => setEditingName(e.target.value)}
          onBlur={commitEditingName}
          onKeyDown={e => {
            if (e.key === 'Enter') {
              e.preventDefault()
              commitEditingName()
              e.currentTarget.blur()
            }
          }}
          placeholder="Name"
          className={clsx(
            'px-2 py-1 text-xs rounded border focus:outline-none focus:ring-1 flex-1 min-w-0 resize-none leading-snug break-words',
            editingName.trim()
              ? clsx(lockedCls, 'focus:ring-green-300')
              : 'border-gray-300 bg-white text-gray-500 focus:ring-gray-300',
          )}
        />
        <button onClick={clearName} className="text-muted-foreground hover:text-destructive flex-shrink-0 mt-1"><X className="w-3.5 h-3.5" /></button>
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
              onChange={e => { setManualId(e.target.value); setDomainError(null) }}
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
      {domainError && (
        <p className="text-[11px] text-red-600 flex items-start gap-1">
          <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" />
          {domainError}
        </p>
      )}
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
                  onClick={() => {
                    if (validateDomain) {
                      const err = validateDomain(c.domain ?? null)
                      if (err) { setDomainError(err); return }
                    }
                    onSelect(c); setShowSearch(false); cs.clear()
                  }}
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
          onCancel={() => setShowCustom(false)}
          onCreate={c => {
            if (validateDomain) {
              const err = validateDomain(c.domain_id_str ?? null)
              if (err) { setDomainError(err); return }
            }
            onSelect(c); setShowCustom(false)
          }}
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
  siblingConcepts,
  onSelect,
  onClear,
}: {
  projectId: string
  val: string
  column: string
  concept: ConceptRef | null
  siblingConcepts: Record<string, ConceptRef>
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

    if (!concept.concept_id || concept.concept_id < 1) return

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

  // Value mapping only ever routes through the stem_table, which only knows how to
  // route 5 domains. Anything else (Gender, Race, Visit, …) would silently produce no
  // usable row downstream, so it's rejected at selection time rather than accepted and
  // flagged after the fact.
  const validateStemDomain = (domainStr: string | null): string | null => {
    if (!domainStr) return "Concept not found in CONCEPT.csv — can't verify its domain."
    const numeric = DOMAIN_STRING_MAP[domainStr.toLowerCase()]
    if (numeric === undefined) {
      return `"${domainStr}" is not a valid domain for value mapping. Allowed: Measurement, Observation, Drug Exposure, Procedure Occurrence, Condition Occurrence.`
    }

    // Also block mixing two individually-valid domains within the same variable
    // (e.g. Drug + Observation) — every value under one variable must route to
    // the same OMOP table.
    const establishedEntry = Object.entries(siblingConcepts).find(
      ([v, cv]) => v !== val && cv.domain_id !== undefined && cv.domain_id !== null,
    )
    const establishedDomainId = establishedEntry?.[1].domain_id
    if (establishedDomainId !== undefined && establishedDomainId !== numeric) {
      const newLabel = DOMAIN_OPTIONS.find(d => d.value === numeric)?.label ?? `domain ${numeric}`
      const establishedLabel = DOMAIN_OPTIONS.find(d => d.value === establishedDomainId)?.label ?? `domain ${establishedDomainId}`
      return `"${domainStr}" is ${newLabel}, but this variable is already mapped to ${establishedLabel}. Pick a ${establishedLabel} concept, or map this value under a different variable.`
    }

    return null
  }

  return (
    <div className="flex flex-col gap-1.5">
      <ConceptPicker
        projectId={projectId}
        label={val}
        defaultQuery={`${column} ${val}`}
        value={concept}
        onSelect={c => onSelect(c)}
        onClear={onClear}
        validateDomain={validateStemDomain}
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
  distinctCount,
  mapped,
  onChange,
}: {
  projectId: string
  column: string
  values: string[]
  distinctCount: number
  mapped: Record<string, ConceptRef>
  onChange: (updated: Record<string, ConceptRef>) => void
}) {
  // Domain validation (both "not a valid stem domain" and "mismatches this column's
  // already-established domain") happens upstream in ConceptPicker/ValueConceptRow,
  // inline next to the value, before onSelect ever fires — so by the time a concept
  // reaches here it's already been cleared to commit.
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

  const truncated = distinctCount > values.length

  return (
    <div className="flex flex-col gap-2">
      {truncated ? (
        <div className="flex items-start gap-1.5 text-xs text-red-700 bg-red-50 border border-red-200 rounded px-3 py-2">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
          <span>
            <span className="font-semibold">{distinctCount} distinct values total</span> — only the first{' '}
            {values.length} are shown below. The remaining {distinctCount - values.length} value
            {distinctCount - values.length > 1 ? 's are' : ' is'} not listed here and won't be mapped.
          </span>
        </div>
      ) : values.length > 10 && (
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
                    siblingConcepts={mapped}
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

// Read the single fixed concept out of a column-or-fixed mapping (Unit, Route) — or
// null if none was set, or the mapping is in per-column mode. Fixed concepts are
// stored as a one-entry dict keyed by the concept name.
function getFixedConcept(col: string | null, concepts: Record<string, number>): { id: number; name: string } | null {
  if (col) return null
  const entries = Object.entries(concepts || {}).filter(([, v]) => typeof v === 'number' && v > 0)
  if (entries.length !== 1) return null
  return { name: entries[0][0], id: entries[0][1] }
}

// Static (non-interpolated) color themes — Tailwind's JIT compiler can't see classes
// built via string interpolation, so every variant used anywhere must be spelled out
// literally somewhere in the source. These are shared by every fixed-or-per-column
// concept card (Unit, Route).
interface FixedConceptTheme {
  chipBg: string
  chipBorder: string
  chipText: string
  chipSubtext: string
  iconText: string
  inputBorder: string
  ring: string
  button: string
  clear: string
  link: string
}

const SKY_THEME: FixedConceptTheme = {
  chipBg: 'bg-sky-100', chipBorder: 'border-sky-300', chipText: 'text-sky-900', chipSubtext: 'text-sky-700',
  iconText: 'text-sky-700', inputBorder: 'border-sky-200', ring: 'focus:ring-sky-400',
  button: 'bg-sky-600 hover:bg-sky-700', clear: 'text-sky-500 hover:text-sky-700', link: 'text-sky-700 hover:text-sky-900',
}

const PURPLE_THEME: FixedConceptTheme = {
  chipBg: 'bg-purple-100', chipBorder: 'border-purple-300', chipText: 'text-purple-900', chipSubtext: 'text-purple-700',
  iconText: 'text-purple-700', inputBorder: 'border-purple-200', ring: 'focus:ring-purple-400',
  button: 'bg-purple-600 hover:bg-purple-700', clear: 'text-purple-500 hover:text-purple-700', link: 'text-purple-700 hover:text-purple-900',
}

const AMBER_THEME: FixedConceptTheme = {
  chipBg: 'bg-amber-100', chipBorder: 'border-amber-300', chipText: 'text-amber-900', chipSubtext: 'text-amber-700',
  iconText: 'text-amber-700', inputBorder: 'border-amber-200', ring: 'focus:ring-amber-400',
  button: 'bg-amber-600 hover:bg-amber-700', clear: 'text-amber-500 hover:text-amber-700', link: 'text-amber-700 hover:text-amber-900',
}

const TEAL_THEME: FixedConceptTheme = {
  chipBg: 'bg-teal-100', chipBorder: 'border-teal-300', chipText: 'text-teal-900', chipSubtext: 'text-teal-700',
  iconText: 'text-teal-700', inputBorder: 'border-teal-200', ring: 'focus:ring-teal-400',
  button: 'bg-teal-600 hover:bg-teal-700', clear: 'text-teal-500 hover:text-teal-700', link: 'text-teal-700 hover:text-teal-900',
}

const ROSE_THEME: FixedConceptTheme = {
  chipBg: 'bg-rose-100', chipBorder: 'border-rose-300', chipText: 'text-rose-900', chipSubtext: 'text-rose-700',
  iconText: 'text-rose-700', inputBorder: 'border-rose-200', ring: 'focus:ring-rose-400',
  button: 'bg-rose-600 hover:bg-rose-700', clear: 'text-rose-500 hover:text-rose-700', link: 'text-rose-700 hover:text-rose-900',
}

// Manual concept-id entry for a single fixed concept (applies to every row of the
// variable) — used by Unit and Route's "Fixed value" mode, and by Type (fixed-only).
// When `validateDomain` is given, the concept is looked up and checked before being
// accepted; otherwise a manually-typed name skips the lookup entirely (fast path).
function FixedConceptInput({
  id,
  name,
  onSet,
  onClear,
  theme,
  validateDomain,
  helpLink,
}: {
  id: number | null | undefined
  name: string | null | undefined
  onSet: (id: number, name: string) => void
  onClear: () => void
  theme: FixedConceptTheme
  validateDomain?: (domainStr: string | null) => string | null
  helpLink?: { label: string; url: (query: string) => string }
}) {
  const [manualId, setManualId] = useState('')
  const [manualName, setManualName] = useState('')
  const [lookingUp, setLookingUp] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const apply = () => {
    const parsedId = parseInt(manualId, 10)
    if (isNaN(parsedId) || parsedId < 1) return
    setError(null)

    if (!validateDomain && manualName.trim()) {
      onSet(parsedId, manualName.trim())
      setManualId(''); setManualName('')
      return
    }

    setLookingUp(true)
    lookupConceptDomain(parsedId)
      .then(res => {
        if (validateDomain) {
          const err = validateDomain(res.found ? res.domain_id : null)
          if (err) { setError(err); return }
          // This name is stored as the source_value written into the OMOP output (e.g.
          // unit_source_value) — never substitute a placeholder like "Concept 8840" for
          // it silently, that would corrupt the generated data.
          const resolvedName = manualName.trim() || res.concept_name
          if (!resolvedName) {
            setError("This concept has no name in CONCEPT.csv — type one in the Name field.")
            return
          }
          onSet(parsedId, resolvedName)
          setManualId(''); setManualName('')
          return
        }
        const resolvedName = manualName.trim() || res.concept_name || `Concept ${parsedId}`
        onSet(parsedId, resolvedName)
        setManualId(''); setManualName('')
      })
      .catch(() => {
        if (validateDomain) { setError("Couldn't verify this concept's domain — try again."); return }
        onSet(parsedId, manualName.trim() || `Concept ${parsedId}`)
        setManualId(''); setManualName('')
      })
      .finally(() => setLookingUp(false))
  }

  if (id) {
    return (
      <div className={clsx('flex items-center gap-2 px-3 py-1.5 rounded-lg border text-xs', theme.chipBg, theme.chipBorder)}>
        <CheckCircle className={clsx('w-3.5 h-3.5 flex-shrink-0', theme.iconText)} />
        <span className={clsx('font-semibold', theme.chipText)}>{name}</span>
        <span className={theme.chipSubtext}>({id})</span>
        <button onClick={onClear} className={clsx('ml-auto', theme.clear)} title="Clear">
          <X className="w-3 h-3" />
        </button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1.5 flex-wrap">
        <input
          type="number"
          value={manualId}
          onChange={e => { setManualId(e.target.value); setError(null) }}
          onKeyDown={e => e.key === 'Enter' && apply()}
          placeholder="Concept ID"
          className={clsx('border rounded px-2 py-1 text-xs w-24 bg-white focus:outline-none focus:ring-1', theme.inputBorder, theme.ring)}
        />
        <input
          type="text"
          value={manualName}
          onChange={e => setManualName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && apply()}
          placeholder="Name (optional)"
          className={clsx('border rounded px-2 py-1 text-xs flex-1 min-w-[120px] bg-white focus:outline-none focus:ring-1', theme.inputBorder, theme.ring)}
        />
        <button
          onClick={apply}
          disabled={!manualId || lookingUp}
          className={clsx('px-2 py-1 text-xs text-white rounded disabled:opacity-30 flex items-center gap-1', theme.button)}
        >
          {lookingUp && <Loader2 className="w-3 h-3 animate-spin" />}
          Set
        </button>
      </div>
      {error && (
        <p className="text-[11px] text-red-600 flex items-start gap-1">
          <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" />
          {error}
        </p>
      )}
      {helpLink && (
        <a
          href={helpLink.url(manualName.trim())}
          target="_blank"
          rel="noopener noreferrer"
          className={clsx('text-[11px] hover:underline w-fit', theme.link)}
        >
          ↗ {helpLink.label}
        </a>
      )}
    </div>
  )
}

function UnitMappingSection({
  unitMapping,
  onChange,
  columnInfos,
  fileColumns,
  excludeColumn,
  claims,
  ownVariable,
}: {
  unitMapping: UnitMapping
  onChange: (u: UnitMapping) => void
  columnInfos: Record<string, ColumnInfo>
  fileColumns: string[]
  excludeColumn: string
  claims?: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable?: string
}) {
  const [mode, setMode] = useState<'fixed' | 'column'>(unitMapping.unit_col ? 'column' : 'fixed')
  const fixed = getFixedConcept(unitMapping.unit_col, unitMapping.unit_concepts)

  const validateUnitDomain = (domainStr: string | null): string | null => {
    if (!domainStr) return "Concept not found in CONCEPT.csv — can't verify its domain."
    if (domainStr.toLowerCase() !== 'unit') {
      return `"${domainStr}" is not a Unit concept. Pick a concept from the Unit domain.`
    }
    return null
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-sky-200 bg-sky-50/40 p-3">
      <div className="flex items-center gap-2">
        <Scale className="w-4 h-4 text-sky-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-sky-800">
          Unit <span className="font-normal text-sky-600">(optional)</span>
        </p>
        <a
          href="https://athena.ohdsi.org/search-terms/terms?domain=Unit&page=1&pageSize=15&query="
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-[10px] text-sky-600 hover:text-sky-800 hover:underline flex-shrink-0"
        >
          ↗ Accepted Concepts
        </a>
      </div>
      <p className="text-[11px] text-sky-700/90">
        {mode === 'fixed'
          ? 'Hardcode the OMOP UCUM unit for this variable (e.g. mmHg, mg/dL). Leave blank if the value is unitless.'
          : 'Pick a column holding the unit for each row (e.g. mg vs. mL per record), then map each of its distinct values to a concept id — 0 marks a value as explicitly not mapped.'}
      </p>

      <div className="flex rounded border border-sky-200 overflow-hidden text-[11px] w-fit">
        <button
          type="button"
          onClick={() => setMode('fixed')}
          className={clsx('px-2 py-1', mode === 'fixed' ? 'bg-sky-600 text-white' : 'text-sky-600 hover:bg-sky-50')}
        >
          Fixed value
        </button>
        <button
          type="button"
          onClick={() => setMode('column')}
          className={clsx('px-2 py-1 border-l border-sky-200', mode === 'column' ? 'bg-sky-600 text-white' : 'text-sky-600 hover:bg-sky-50')}
        >
          From column
        </button>
      </div>

      {mode === 'fixed' ? (
        <FixedConceptInput
          id={fixed?.id}
          name={fixed?.name}
          onSet={(id, name) => onChange({ unit_col: null, unit_concepts: { [name]: id } })}
          onClear={() => onChange({ unit_col: null, unit_concepts: {} })}
          theme={SKY_THEME}
          validateDomain={validateUnitDomain}
        />
      ) : (
        <ColumnValueIdMapper
          col={unitMapping.unit_col}
          concepts={unitMapping.unit_concepts}
          onColChange={col => onChange({ unit_col: col, unit_concepts: {} })}
          onConceptsChange={concepts => onChange({ ...unitMapping, unit_concepts: concepts })}
          columnInfos={columnInfos}
          fileColumns={fileColumns}
          excludeColumn={excludeColumn}
          accentClass="bg-sky-100 text-sky-800"
          validateDomain={validateUnitDomain}
          claims={claims}
          ownVariable={ownVariable}
          fieldKey="unit_col"
        />
      )}
    </div>
  )
}

// Modifier (Procedure Occurrence, domain_id 4) — same Fixed-value / From-column toggle as
// Unit, amber-themed. Unlike Unit/Route there's no dedicated OMOP "Modifier" domain to
// validate against (CPT4/HCPCS modifier concepts live in the Procedure domain alongside
// everything else), so entries are accepted without a domain check.
function ModifierMappingSection({
  modifierMapping,
  onChange,
  columnInfos,
  fileColumns,
  excludeColumn,
  claims,
  ownVariable,
}: {
  modifierMapping: ModifierMapping
  onChange: (m: ModifierMapping) => void
  columnInfos: Record<string, ColumnInfo>
  fileColumns: string[]
  excludeColumn: string
  claims?: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable?: string
}) {
  const [mode, setMode] = useState<'fixed' | 'column'>(modifierMapping.modifier_col ? 'column' : 'fixed')
  const fixed = getFixedConcept(modifierMapping.modifier_col, modifierMapping.modifier_concepts)

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50/40 p-3">
      <div className="flex items-center gap-2">
        <Tag className="w-4 h-4 text-amber-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-amber-800">
          Modifier <span className="font-normal text-amber-600">(optional)</span>
        </p>
        <a
          href="https://athena.ohdsi.org/search-terms/terms?conceptClass=CPT4+Modifier&conceptClass=HCPCS+Modifier&standardConcept=Standard&page=1&pageSize=15&query="
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-[10px] text-amber-600 hover:text-amber-800 hover:underline flex-shrink-0"
        >
          ↗ Accepted Concepts
        </a>
      </div>
      <p className="text-[11px] text-amber-700/90">
        {mode === 'fixed'
          ? 'Hardcode the modifier for this variable (e.g. Bilateral procedure), if every record shares the same one.'
          : "Pick a column holding the modifier code for each row (e.g. a CPT4/HCPCS modifier like '-50' or '-RT'), then map each of its distinct values to a concept id — 0 marks a value as explicitly not mapped."}
      </p>

      <div className="flex rounded border border-amber-200 overflow-hidden text-[11px] w-fit">
        <button
          type="button"
          onClick={() => setMode('fixed')}
          className={clsx('px-2 py-1', mode === 'fixed' ? 'bg-amber-600 text-white' : 'text-amber-600 hover:bg-amber-50')}
        >
          Fixed value
        </button>
        <button
          type="button"
          onClick={() => setMode('column')}
          className={clsx('px-2 py-1 border-l border-amber-200', mode === 'column' ? 'bg-amber-600 text-white' : 'text-amber-600 hover:bg-amber-50')}
        >
          From column
        </button>
      </div>

      {mode === 'fixed' ? (
        <FixedConceptInput
          id={fixed?.id}
          name={fixed?.name}
          onSet={(id, name) => onChange({ modifier_col: null, modifier_concepts: { [name]: id } })}
          onClear={() => onChange({ modifier_col: null, modifier_concepts: {} })}
          theme={AMBER_THEME}
        />
      ) : (
        <ColumnValueIdMapper
          col={modifierMapping.modifier_col}
          concepts={modifierMapping.modifier_concepts}
          onColChange={col => onChange({ modifier_col: col, modifier_concepts: {} })}
          onConceptsChange={concepts => onChange({ ...modifierMapping, modifier_concepts: concepts })}
          columnInfos={columnInfos}
          fileColumns={fileColumns}
          excludeColumn={excludeColumn}
          accentClass="bg-amber-100 text-amber-800"
          claims={claims}
          ownVariable={ownVariable}
          fieldKey="modifier_col"
        />
      )}
    </div>
  )
}

// Condition Status (Condition Occurrence, domain_id 5) — same Fixed-value / From-column
// toggle as Unit/Modifier, teal-themed. Unlike Modifier, "Condition Status" IS a real
// OMOP domain, so entries are validated against it the same way Unit validates "Unit".
function ConditionStatusMappingSection({
  conditionStatusMapping,
  onChange,
  columnInfos,
  fileColumns,
  excludeColumn,
  claims,
  ownVariable,
}: {
  conditionStatusMapping: ConditionStatusMapping
  onChange: (m: ConditionStatusMapping) => void
  columnInfos: Record<string, ColumnInfo>
  fileColumns: string[]
  excludeColumn: string
  claims?: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable?: string
}) {
  const [mode, setMode] = useState<'fixed' | 'column'>(conditionStatusMapping.condition_status_col ? 'column' : 'fixed')
  const fixed = getFixedConcept(conditionStatusMapping.condition_status_col, conditionStatusMapping.condition_status_concepts)

  const validateConditionStatusDomain = (domainStr: string | null): string | null => {
    if (!domainStr) return "Concept not found in CONCEPT.csv — can't verify its domain."
    if (domainStr.toLowerCase() !== 'condition status') {
      return `"${domainStr}" is not a Condition Status concept. Pick a concept from the Condition Status domain.`
    }
    return null
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-teal-200 bg-teal-50/40 p-3">
      <div className="flex items-center gap-2">
        <Tag className="w-4 h-4 text-teal-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-teal-800">
          Condition Status <span className="font-normal text-teal-600">(optional)</span>
        </p>
        <a
          href="https://athena.ohdsi.org/search-terms/terms?domain=Condition+Status&standardConcept=Standard&page=1&pageSize=15&query="
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-[10px] text-teal-600 hover:text-teal-800 hover:underline flex-shrink-0"
        >
          ↗ Accepted Concepts
        </a>
      </div>
      <p className="text-[11px] text-teal-700/90">
        {mode === 'fixed'
          ? 'Hardcode the point in the visit this diagnosis was given (e.g. Admitting diagnosis, Final diagnosis), if every record shares the same one.'
          : "Pick a column holding the condition status for each row (e.g. admitting vs. discharge diagnosis), then map each of its distinct values to a concept id — 0 marks a value as explicitly not mapped."}
      </p>

      <div className="flex rounded border border-teal-200 overflow-hidden text-[11px] w-fit">
        <button
          type="button"
          onClick={() => setMode('fixed')}
          className={clsx('px-2 py-1', mode === 'fixed' ? 'bg-teal-600 text-white' : 'text-teal-600 hover:bg-teal-50')}
        >
          Fixed value
        </button>
        <button
          type="button"
          onClick={() => setMode('column')}
          className={clsx('px-2 py-1 border-l border-teal-200', mode === 'column' ? 'bg-teal-600 text-white' : 'text-teal-600 hover:bg-teal-50')}
        >
          From column
        </button>
      </div>

      {mode === 'fixed' ? (
        <FixedConceptInput
          id={fixed?.id}
          name={fixed?.name}
          onSet={(id, name) => onChange({ condition_status_col: null, condition_status_concepts: { [name]: id } })}
          onClear={() => onChange({ condition_status_col: null, condition_status_concepts: {} })}
          theme={TEAL_THEME}
          validateDomain={validateConditionStatusDomain}
        />
      ) : (
        <ColumnValueIdMapper
          col={conditionStatusMapping.condition_status_col}
          concepts={conditionStatusMapping.condition_status_concepts}
          onColChange={col => onChange({ condition_status_col: col, condition_status_concepts: {} })}
          onConceptsChange={concepts => onChange({ ...conditionStatusMapping, condition_status_concepts: concepts })}
          columnInfos={columnInfos}
          fileColumns={fileColumns}
          excludeColumn={excludeColumn}
          accentClass="bg-teal-100 text-teal-800"
          validateDomain={validateConditionStatusDomain}
          claims={claims}
          ownVariable={ownVariable}
          fieldKey="condition_status_col"
        />
      )}
    </div>
  )
}

// Qualifier (Observation, domain_id 2) — same Fixed-value / From-column toggle as
// Modifier. No dedicated OMOP domain to validate against (the docs are explicit: "no
// restriction on the domain of these Concepts, they just need to be Standard"), so
// entries are accepted without a domain check, rose-themed.
function QualifierMappingSection({
  qualifierMapping,
  onChange,
  columnInfos,
  fileColumns,
  excludeColumn,
  claims,
  ownVariable,
}: {
  qualifierMapping: QualifierMapping
  onChange: (m: QualifierMapping) => void
  columnInfos: Record<string, ColumnInfo>
  fileColumns: string[]
  excludeColumn: string
  claims?: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable?: string
}) {
  const [mode, setMode] = useState<'fixed' | 'column'>(qualifierMapping.qualifier_col ? 'column' : 'fixed')
  const fixed = getFixedConcept(qualifierMapping.qualifier_col, qualifierMapping.qualifier_concepts)

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-rose-200 bg-rose-50/40 p-3">
      <div className="flex items-center gap-2">
        <Tag className="w-4 h-4 text-rose-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-rose-800">
          Qualifier <span className="font-normal text-rose-600">(optional)</span>
        </p>
        <a
          href="https://athena.ohdsi.org/search-terms/terms?standardConcept=Standard&page=1&pageSize=15&query="
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-[10px] text-rose-600 hover:text-rose-800 hover:underline flex-shrink-0"
        >
          ↗ Accepted Concepts
        </a>
      </div>
      <p className="text-[11px] text-rose-700/90">
        {mode === 'fixed'
          ? 'Hardcode the qualifier for this variable (e.g. a degree or severity), if every record shares the same one.'
          : "Pick a column holding the qualifier for each row (e.g. degrees, severities), then map each of its distinct values to a concept id — 0 marks a value as explicitly not mapped."}
      </p>

      <div className="flex rounded border border-rose-200 overflow-hidden text-[11px] w-fit">
        <button
          type="button"
          onClick={() => setMode('fixed')}
          className={clsx('px-2 py-1', mode === 'fixed' ? 'bg-rose-600 text-white' : 'text-rose-600 hover:bg-rose-50')}
        >
          Fixed value
        </button>
        <button
          type="button"
          onClick={() => setMode('column')}
          className={clsx('px-2 py-1 border-l border-rose-200', mode === 'column' ? 'bg-rose-600 text-white' : 'text-rose-600 hover:bg-rose-50')}
        >
          From column
        </button>
      </div>

      {mode === 'fixed' ? (
        <FixedConceptInput
          id={fixed?.id}
          name={fixed?.name}
          onSet={(id, name) => onChange({ qualifier_col: null, qualifier_concepts: { [name]: id } })}
          onClear={() => onChange({ qualifier_col: null, qualifier_concepts: {} })}
          theme={ROSE_THEME}
        />
      ) : (
        <ColumnValueIdMapper
          col={qualifierMapping.qualifier_col}
          concepts={qualifierMapping.qualifier_concepts}
          onColChange={col => onChange({ qualifier_col: col, qualifier_concepts: {} })}
          onConceptsChange={concepts => onChange({ ...qualifierMapping, qualifier_concepts: concepts })}
          columnInfos={columnInfos}
          fileColumns={fileColumns}
          excludeColumn={excludeColumn}
          accentClass="bg-rose-100 text-rose-800"
          claims={claims}
          ownVariable={ownVariable}
          fieldKey="qualifier_col"
        />
      )}
    </div>
  )
}

// Operator (Measurement) — operator_concept_id / operator_source_value (e.g. =, >, <),
// same fixed-or-per-column convention as Qualifier above, but validated against the
// OMOP "Meas Value Operator" domain like Route/Condition Status are.
function OperatorMappingSection({
  operatorMapping,
  onChange,
  columnInfos,
  fileColumns,
  excludeColumn,
  claims,
  ownVariable,
}: {
  operatorMapping: OperatorMapping
  onChange: (m: OperatorMapping) => void
  columnInfos: Record<string, ColumnInfo>
  fileColumns: string[]
  excludeColumn: string
  claims?: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable?: string
}) {
  const [mode, setMode] = useState<'fixed' | 'column'>(operatorMapping.operator_col ? 'column' : 'fixed')
  const fixed = getFixedConcept(operatorMapping.operator_col, operatorMapping.operator_concepts)

  const validateOperatorDomain = (domainStr: string | null): string | null => {
    if (!domainStr) return "Concept not found in CONCEPT.csv — can't verify its domain."
    if (domainStr.toLowerCase() !== 'meas value operator') {
      return `"${domainStr}" is not a Meas Value Operator concept. Pick a concept from the Meas Value Operator domain (e.g. =, >, <).`
    }
    return null
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-rose-200 bg-rose-50/40 p-3">
      <div className="flex items-center gap-2">
        <Tag className="w-4 h-4 text-rose-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-rose-800">
          Operator <span className="font-normal text-rose-600">(optional)</span>
        </p>
        <a
          href="https://athena.ohdsi.org/search-terms/terms?domain=Meas+Value+Operator&standardConcept=Standard&page=1&pageSize=15&query="
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-[10px] text-rose-600 hover:text-rose-800 hover:underline flex-shrink-0"
        >
          ↗ Accepted Concepts
        </a>
      </div>
      <p className="text-[11px] text-rose-700/90">
        {mode === 'fixed'
          ? 'Hardcode the result operator for this variable (e.g. =, >), if every record shares the same one. Leave unset when results are exact numeric values.'
          : "Pick a column holding the operator for each row (e.g. <, >, =), then map each of its distinct values to a concept id — 0 marks a value as explicitly not mapped."}
      </p>

      <div className="flex rounded border border-rose-200 overflow-hidden text-[11px] w-fit">
        <button
          type="button"
          onClick={() => setMode('fixed')}
          className={clsx('px-2 py-1', mode === 'fixed' ? 'bg-rose-600 text-white' : 'text-rose-600 hover:bg-rose-50')}
        >
          Fixed value
        </button>
        <button
          type="button"
          onClick={() => setMode('column')}
          className={clsx('px-2 py-1 border-l border-rose-200', mode === 'column' ? 'bg-rose-600 text-white' : 'text-rose-600 hover:bg-rose-50')}
        >
          From column
        </button>
      </div>

      {mode === 'fixed' ? (
        <FixedConceptInput
          id={fixed?.id}
          name={fixed?.name}
          onSet={(id, name) => onChange({ operator_col: null, operator_concepts: { [name]: id } })}
          onClear={() => onChange({ operator_col: null, operator_concepts: {} })}
          theme={ROSE_THEME}
          validateDomain={validateOperatorDomain}
        />
      ) : (
        <ColumnValueIdMapper
          col={operatorMapping.operator_col}
          concepts={operatorMapping.operator_concepts}
          onColChange={col => onChange({ operator_col: col, operator_concepts: {} })}
          onConceptsChange={concepts => onChange({ ...operatorMapping, operator_concepts: concepts })}
          columnInfos={columnInfos}
          fileColumns={fileColumns}
          excludeColumn={excludeColumn}
          accentClass="bg-rose-100 text-rose-800"
          validateDomain={validateOperatorDomain}
          claims={claims}
          ownVariable={ownVariable}
          fieldKey="operator_col"
        />
      )}
    </div>
  )
}

// Sibling-column fields that get pulled verbatim into a drug_exposure row.
// Keyed by the VariableDecision field that stores the chosen source column.
// Route and Dose unit are NOT here — they get their own per-value concept
// mapping (RouteMapping / the shared UnitMapping) instead of a raw passthrough.
const DRUG_COLUMN_FIELDS: {
  key: 'quantity_col' | 'days_supply_col' | 'refills_col' | 'sig_col' | 'lot_number_col' | 'stop_reason_col'
  label: string
  hint: string
}[] = [
  { key: 'quantity_col',    label: 'Quantity',            hint: 'fills quantity' },
  { key: 'days_supply_col', label: 'Days supply',         hint: 'fills days_supply — verbatim, not calculated' },
  { key: 'refills_col',     label: 'Refills',             hint: 'fills refills' },
  { key: 'sig_col',         label: 'Sig / instructions',  hint: 'fills sig' },
  { key: 'lot_number_col',  label: 'Lot number',          hint: 'fills lot_number' },
  { key: 'stop_reason_col', label: 'Stop reason',         hint: 'fills stop_reason' },
]

// Measurement (domain_id 1) sibling-column fields — reference range bounds pulled
// in verbatim, same convention as DRUG_COLUMN_FIELDS above.
const MEASUREMENT_COLUMN_FIELDS: {
  key: 'range_low_col' | 'range_high_col'
  label: string
  hint: string
}[] = [
  { key: 'range_low_col',  label: 'Range low',  hint: 'fills range_low' },
  { key: 'range_high_col', label: 'Range high', hint: 'fills range_high' },
]

// Searchable column picker for a single Drug Exposure field — a text input lives inside
// the open dropdown itself so a column can be found by typing, rather than scrolling a
// (potentially hundreds-long) plain <select>.
function DrugFieldColumnSelect({
  value,
  onChange,
  options,
  claims = {},
  ownVariable = '',
  fieldKey = '',
}: {
  value: string | null | undefined
  onChange: (v: string | null) => void
  options: string[]
  claims?: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable?: string
  fieldKey?: string
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Focus the search input once it mounts into the DOM on open — a genuine effect
  // (imperative DOM interaction), unlike resetting the query text, which is now done
  // directly in the toggle handler below instead of reacting to `open` here.
  useEffect(() => {
    if (!open) return
    const id = requestAnimationFrame(() => inputRef.current?.focus())
    return () => cancelAnimationFrame(id)
  }, [open])

  const q = query.trim().toLowerCase()
  const filtered = q ? options.filter(c => c.toLowerCase().includes(q)) : options

  const choose = (v: string | null) => {
    onChange(v)
    setOpen(false)
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => {
          setOpen(o => !o)
          if (!open) setQuery('')
        }}
        className="w-full flex items-center justify-between gap-1 border border-purple-200 rounded px-2 py-1 text-xs bg-white focus:outline-none focus:ring-1 focus:ring-purple-400"
      >
        <span className={clsx('truncate', !value && 'text-muted-foreground')} title={value ?? undefined}>{value || '— none —'}</span>
        <ChevronDown className="w-3 h-3 flex-shrink-0 text-purple-400" />
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-[28rem] max-w-[80vw] max-h-64 rounded-lg border border-purple-200 bg-white shadow-lg flex flex-col">
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search columns…"
            className="border-b border-purple-100 px-2 py-1.5 text-xs focus:outline-none flex-shrink-0"
          />
          <div className="overflow-y-auto overflow-x-hidden">
            <div
              onClick={() => choose(null)}
              className="px-2 py-1.5 text-xs cursor-pointer hover:bg-purple-50 text-muted-foreground"
            >
              — none —
            </div>
            {filtered.map(c => {
              const claim = claims[c]
              const claimedElsewhere = !!claim && !(claim.variable === ownVariable && claim.fieldKey === fieldKey)
              const label = `${c}${claimedElsewhere ? ` (used as ${claim.label} for ${claim.variable})` : ''}`
              return (
                <div
                  key={c}
                  onClick={() => !claimedElsewhere && choose(c)}
                  title={label}
                  className={clsx(
                    'px-2 py-1.5 text-xs truncate',
                    claimedElsewhere ? 'opacity-40 cursor-not-allowed' : 'cursor-pointer hover:bg-purple-50',
                    c === value && 'bg-purple-100 font-medium',
                  )}
                >
                  {label}
                </div>
              )
            })}
            {filtered.length === 0 && (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">No matching columns</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// One row of a column-based concept mapping (Unit's "per column" mode, Route mapping):
// a single distinct value of the chosen column, with a manual concept-id entry — 0
// means "explicitly not mapped", mirroring the same convention used for value_concepts.
function SimpleConceptIdRow({
  value,
  conceptId,
  onSet,
  onClear,
  accentClass,
  validateDomain,
}: {
  value: string
  conceptId: number | null | undefined
  onSet: (id: number) => void
  onClear: () => void
  accentClass: string
  // When provided, an id > 0 is looked up and checked before being accepted — a
  // wrong/unresolvable domain is rejected with an inline message instead of set.
  // 0 ("not mapped") always skips validation.
  validateDomain?: (domainStr: string | null) => string | null
}) {
  const [manualId, setManualId] = useState('')
  const [name, setName] = useState<string | null>(null)
  const [lookingUp, setLookingUp] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // `name` is only ever read from render when conceptId is truthy (see isSet/isUnmapped
    // below), so there's nothing to reset here — skip the lookup and leave state as-is.
    if (!conceptId) return
    let cancelled = false
    // Standard loading-flag-before-fetch idiom — no render-derivable equivalent exists
    // for "a request is in flight" state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLookingUp(true)
    lookupConceptDomain(conceptId)
      .then(res => { if (!cancelled) setName(res.concept_name || null) })
      .catch(() => { if (!cancelled) setName(null) })
      .finally(() => { if (!cancelled) setLookingUp(false) })
    return () => { cancelled = true }
  }, [conceptId])

  const apply = () => {
    const id = parseInt(manualId, 10)
    if (isNaN(id) || id < 0) return
    setError(null)

    if (id === 0 || !validateDomain) {
      onSet(id)
      setManualId('')
      return
    }

    setLookingUp(true)
    lookupConceptDomain(id)
      .then(res => {
        const err = validateDomain(res.found ? res.domain_id : null)
        if (err) { setError(err); return }
        onSet(id)
        setManualId('')
      })
      .catch(() => setError("Couldn't verify this concept's domain — try again."))
      .finally(() => setLookingUp(false))
  }

  const isSet = conceptId !== null && conceptId !== undefined
  const isUnmapped = conceptId === 0

  return (
    <div className="flex flex-col gap-1 px-2 py-1.5">
      <div className="flex items-center gap-1.5 text-xs">
        <span className="font-mono flex-1 min-w-0 truncate" title={value}>{value}</span>
        {isSet && (
          <span className={clsx(
            'flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] flex-shrink-0',
            isUnmapped ? 'bg-gray-100 text-gray-600' : accentClass,
          )}>
            <span className="max-w-[9rem] truncate">
              {lookingUp ? <Loader2 className="w-3 h-3 animate-spin flex-shrink-0" /> : (isUnmapped ? '0 · Not mapped' : `${name ?? 'Concept'} (${conceptId})`)}
            </span>
            <button
              onClick={onClear}
              className="flex-shrink-0 transition-transform duration-150 hover:scale-125 hover:opacity-70 active:scale-95"
              title="Clear"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        )}
        {!isSet && (
          <>
            <input
              type="number"
              value={manualId}
              onChange={e => { setManualId(e.target.value); setError(null) }}
              onKeyDown={e => e.key === 'Enter' && apply()}
              placeholder="ID"
              className="border border-border rounded px-1.5 py-0.5 text-xs w-16 flex-shrink-0 bg-white focus:outline-none focus:ring-1 focus:ring-ring"
            />
            <button
              onClick={apply}
              disabled={!manualId || lookingUp}
              className="px-1.5 py-0.5 text-[11px] rounded flex-shrink-0 bg-primary text-primary-foreground disabled:opacity-30 hover:bg-primary/90"
            >
              Set
            </button>
          </>
        )}
      </div>
      {error && (
        <p className="text-[11px] text-red-600 flex items-start gap-1">
          <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" />
          {error}
        </p>
      )}
    </div>
  )
}

// Column picker + per-distinct-value concept-id mapping, shared by Unit's "per column"
// mode and Route mapping: pick a sibling column, then map each of ITS distinct values
// to a concept id (0 = not mapped) — resolved per row at generation time, unlike the
// other Drug Exposure fields which just pass a column's raw value straight through.
function ColumnValueIdMapper({
  col,
  concepts,
  onColChange,
  onConceptsChange,
  columnInfos,
  fileColumns,
  excludeColumn,
  accentClass,
  validateDomain,
  claims,
  ownVariable,
  fieldKey,
}: {
  col: string | null
  concepts: Record<string, number>
  onColChange: (col: string | null) => void
  onConceptsChange: (concepts: Record<string, number>) => void
  columnInfos: Record<string, ColumnInfo>
  fileColumns: string[]
  excludeColumn: string
  accentClass: string
  validateDomain?: (domainStr: string | null) => string | null
  claims?: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable?: string
  fieldKey?: string
}) {
  const options = fileColumns.filter(c => c !== excludeColumn)
  const info = col ? columnInfos[col] : null

  return (
    <div className="flex flex-col gap-2">
      <DrugFieldColumnSelect
        value={col}
        onChange={onColChange}
        options={options}
        claims={claims}
        ownVariable={ownVariable}
        fieldKey={fieldKey}
      />
      {col && !info && (
        <p className="text-[11px] text-amber-700">
          Distinct values for <code className="bg-amber-100 px-1 rounded">{col}</code> aren't loaded —
          it may belong to a different source file.
        </p>
      )}
      {col && info && info.distinct_values.length === 0 && (
        <p className="text-[11px] text-muted-foreground">This column has no distinct values to map.</p>
      )}
      {col && info && info.distinct_values.length > 0 && (() => {
        const total = info.distinct_values.length
        const mappedCount = info.distinct_values.filter(v => v in concepts).length
        const percent = Math.round((mappedCount / total) * 100)
        return (
          <>
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-muted-foreground">{mappedCount}/{total} values reviewed</span>
              <span className={clsx(
                'text-[10px] font-semibold px-1.5 py-0.5 rounded-full flex-shrink-0',
                percent === 100 ? 'bg-green-100 text-green-700' : percent > 0 ? 'bg-orange-100 text-orange-700' : 'bg-muted text-muted-foreground',
              )}>
                {percent}%
              </span>
            </div>
            <div className="flex flex-col divide-y divide-border/60 border border-border rounded-lg max-h-56 overflow-y-auto bg-white">
              {info.distinct_values.map(v => (
                <SimpleConceptIdRow
                  key={v}
                  value={v}
                  conceptId={concepts[v]}
                  onSet={id => onConceptsChange({ ...concepts, [v]: id })}
                  onClear={() => {
                    const next = { ...concepts }
                    delete next[v]
                    onConceptsChange(next)
                  }}
                  accentClass={accentClass}
                  validateDomain={validateDomain}
                />
              ))}
            </div>
          </>
        )
      })()}
    </div>
  )
}

// Route (Drug Exposure) — same Fixed-value / From-column toggle as Unit, purple-themed
// to match the rest of the Drug Exposure fields.
function RouteMappingSection({
  routeMapping,
  onChange,
  columnInfos,
  fileColumns,
  excludeColumn,
  claims,
  ownVariable,
}: {
  routeMapping: RouteMapping
  onChange: (r: RouteMapping) => void
  columnInfos: Record<string, ColumnInfo>
  fileColumns: string[]
  excludeColumn: string
  claims?: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable?: string
}) {
  const [mode, setMode] = useState<'fixed' | 'column'>(routeMapping.route_col ? 'column' : 'fixed')
  const fixed = getFixedConcept(routeMapping.route_col, routeMapping.route_concepts)

  const validateRouteDomain = (domainStr: string | null): string | null => {
    if (!domainStr) return "Concept not found in CONCEPT.csv — can't verify its domain."
    if (domainStr.toLowerCase() !== 'route') {
      return `"${domainStr}" is not a Route concept. Pick a concept from the Route domain.`
    }
    return null
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-purple-200 bg-purple-50/40 p-3">
      <div className="flex items-center gap-2">
        <Pill className="w-4 h-4 text-purple-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-purple-800">
          Route <span className="font-normal text-purple-600">(optional)</span>
        </p>
        <a
          href="https://athena.ohdsi.org/search-terms/terms?domain=Route&page=1&pageSize=15&query="
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-[10px] text-purple-600 hover:text-purple-800 hover:underline flex-shrink-0"
        >
          ↗ Accepted Concepts
        </a>
      </div>
      <p className="text-[11px] text-purple-700/90">
        {mode === 'fixed'
          ? 'Hardcode the OMOP route for this variable (e.g. Oral, Intravenous).'
          : 'Pick a column holding the route for each row (e.g. PO vs. IV per record), then map each of its distinct values to a concept id — 0 marks a value as explicitly not mapped.'}
      </p>

      <div className="flex rounded border border-purple-200 overflow-hidden text-[11px] w-fit">
        <button
          type="button"
          onClick={() => setMode('fixed')}
          className={clsx('px-2 py-1', mode === 'fixed' ? 'bg-purple-600 text-white' : 'text-purple-600 hover:bg-purple-50')}
        >
          Fixed value
        </button>
        <button
          type="button"
          onClick={() => setMode('column')}
          className={clsx('px-2 py-1 border-l border-purple-200', mode === 'column' ? 'bg-purple-600 text-white' : 'text-purple-600 hover:bg-purple-50')}
        >
          From column
        </button>
      </div>

      {mode === 'fixed' ? (
        <FixedConceptInput
          id={fixed?.id}
          name={fixed?.name}
          onSet={(id, name) => onChange({ route_col: null, route_concepts: { [name]: id } })}
          onClear={() => onChange({ route_col: null, route_concepts: {} })}
          theme={PURPLE_THEME}
          validateDomain={validateRouteDomain}
        />
      ) : (
        <ColumnValueIdMapper
          col={routeMapping.route_col}
          concepts={routeMapping.route_concepts}
          onColChange={col => onChange({ route_col: col, route_concepts: {} })}
          onConceptsChange={concepts => onChange({ ...routeMapping, route_concepts: concepts })}
          columnInfos={columnInfos}
          fileColumns={fileColumns}
          excludeColumn={excludeColumn}
          accentClass="bg-purple-100 text-purple-800"
          validateDomain={validateRouteDomain}
          claims={claims}
          ownVariable={ownVariable}
          fieldKey="route_col"
        />
      )}
    </div>
  )
}

// Type (*_type_concept_id — drug_type_concept_id, procedure_type_concept_id, ...) —
// fixed-only: one concept id applies to every row of the variable. Must resolve to the
// OMOP "Type Concept" domain; anything else is rejected before it's applied, mirroring
// the same domain-validation pattern used for value mapping. Shared across domains;
// only the description text changes to match what "provenance" means for that table.
// Marks a field that's required by the OMOP CDM for this domain (e.g. measurement_date,
// drug_exposure_start_date, *_type_concept_id) — even though the wizard itself doesn't
// block Next on it, since a fallback (visit-derived date, pipeline default type) applies.
function RequiredMark() {
  return <span className="text-red-600" title="Required by the OMOP CDM for this domain">*</span>
}

function TypeConceptCard({
  conceptId,
  conceptName,
  onSet,
  onClear,
  description,
}: {
  conceptId: number | null | undefined
  conceptName: string | null | undefined
  onSet: (id: number, name: string) => void
  onClear: () => void
  description?: React.ReactNode
}) {
  const validateDomain = (domainStr: string | null): string | null => {
    if (!domainStr) return "Concept not found in CONCEPT.csv — can't verify its domain."
    if (domainStr.toLowerCase() !== 'type concept') {
      return `"${domainStr}" is not a Type Concept. Pick a concept from the Type Concept domain (see link below).`
    }
    return null
  }

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-purple-200 bg-purple-50/40 p-3">
      <div className="flex items-center gap-2">
        <Tag className="w-4 h-4 text-purple-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-purple-800">
          Type <RequiredMark />
        </p>
        <a
          href="https://athena.ohdsi.org/search-terms/terms?domain=Type+Concept&standardConcept=Standard&page=1&pageSize=15&query="
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-[10px] text-purple-600 hover:text-purple-800 hover:underline flex-shrink-0"
        >
          ↗ Accepted Concepts
        </a>
      </div>
      <p className="text-[11px] text-purple-700/90">
        {description ?? (
          <>
            Hardcode the provenance of this variable's drug records (e.g. Prescription written,
            Physician administered). Leave blank to use the pipeline default (EHR, 32879).
          </>
        )}
      </p>
      <FixedConceptInput
        id={conceptId}
        name={conceptName}
        onSet={onSet}
        onClear={onClear}
        theme={PURPLE_THEME}
        validateDomain={validateDomain}
      />
    </div>
  )
}

// A single simple sibling-column field (Quantity, Days supply, Refills, Sig, Lot
// number, Stop reason) as its own standalone card — just a column picker, no
// per-value concept mapping.
function SimpleColumnFieldCard({
  field,
  value,
  onChange,
  options,
  claims,
  ownVariable,
}: {
  field: (typeof DRUG_COLUMN_FIELDS)[number] | (typeof MEASUREMENT_COLUMN_FIELDS)[number]
  value: string | null | undefined
  onChange: (v: string | null) => void
  options: string[]
  claims: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable: string
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-purple-200 bg-purple-50/40 p-3">
      <div className="flex items-center gap-2">
        <FileText className="w-4 h-4 text-purple-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-purple-800">
          {field.label} <span className="font-normal text-purple-600">(optional)</span>
        </p>
      </div>
      <p className="text-[11px] text-purple-700/90">
        Pull this field from another column in the same source row.
      </p>
      <DrugFieldColumnSelect
        value={value}
        onChange={onChange}
        options={options}
        claims={claims}
        ownVariable={ownVariable}
        fieldKey={field.key}
      />
      <span className="text-[10px] text-purple-500">{field.hint}</span>
    </div>
  )
}

// Start/End datetime — two sibling-column pickers plus a single strptime format shared
// by both, overriding the visit-derived start date/time and populating an end date/time
// (which otherwise stays empty). Fully generic at the data level (start_datetime_col /
// end_datetime_col / datetime_format apply to any variable regardless of domain); only
// the hint text below each picker is parameterized to name the right target columns.
function DateTimeFieldsCard({
  decision,
  onChange,
  fileColumns,
  excludeColumn,
  claims,
  ownVariable,
  startHint = 'fills drug_exposure_start_date(time)',
  endHint = 'fills drug_exposure_end_date(time)',
  showEnd = true,
  endRequired = false,
}: {
  decision: VariableDecision
  onChange: (d: VariableDecision) => void
  fileColumns: string[]
  excludeColumn: string
  claims: Record<string, { variable: string; fieldKey: string; label: string }>
  ownVariable: string
  startHint?: string
  endHint?: string
  // Observation has no end-date field in the CDM — hide the End datetime picker there
  // rather than exposing a control with no effect on the generated output.
  showEnd?: boolean
  // Only Drug Exposure requires both start and end dates in the OMOP CDM — Procedure
  // and Condition Occurrence's end dates are optional there, so their End picker
  // doesn't get a required marker even though showEnd is true for them too.
  endRequired?: boolean
}) {
  const options = fileColumns.filter(c => c !== excludeColumn)

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-purple-200 bg-purple-50/40 p-3">
      <div className="flex items-center gap-2">
        <Hash className="w-4 h-4 text-purple-600 flex-shrink-0" />
        <p className="text-xs font-semibold text-purple-800">
          {showEnd ? 'Start / End datetime' : 'Start datetime'}
        </p>
      </div>
      <p className="text-[11px] text-purple-700/90">
        {showEnd
          ? 'Pick columns holding the start/end datetime for each row, parsed with the format below (shared by both). Overrides the visit-derived start date and fills the otherwise-empty end date. Leave the format blank to use the default (%Y-%m-%d).'
          : 'Pick a column holding the datetime for each row, parsed with the format below. Overrides the visit-derived date. Leave the format blank to use the default (%Y-%m-%d).'}
      </p>
      <div className={clsx('grid gap-2.5', showEnd ? 'grid-cols-2' : 'grid-cols-1')}>
        <div className="flex flex-col gap-1">
          <label className="text-[11px] font-medium text-purple-800">{showEnd ? 'Start datetime column' : 'Datetime column'} <RequiredMark /></label>
          <DrugFieldColumnSelect
            value={decision.start_datetime_col}
            onChange={v => onChange({ ...decision, start_datetime_col: v })}
            options={options}
            claims={claims}
            ownVariable={ownVariable}
            fieldKey="start_datetime_col"
          />
          <span className="text-[10px] text-purple-500">{startHint}</span>
        </div>
        {showEnd && (
          <div className="flex flex-col gap-1">
            <label className="text-[11px] font-medium text-purple-800">End datetime column {endRequired && <RequiredMark />}</label>
            <DrugFieldColumnSelect
              value={decision.end_datetime_col}
              onChange={v => onChange({ ...decision, end_datetime_col: v })}
              options={options}
              claims={claims}
              ownVariable={ownVariable}
              fieldKey="end_datetime_col"
            />
            <span className="text-[10px] text-purple-500">{endHint}</span>
          </div>
        )}
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-[11px] font-medium text-purple-800">Datetime format</label>
        <input
          type="text"
          value={decision.datetime_format ?? ''}
          onChange={e => onChange({ ...decision, datetime_format: e.target.value || null })}
          placeholder="%Y-%m-%d %H:%M:%S"
          className="border border-purple-200 rounded px-2 py-1 text-xs bg-white font-mono focus:outline-none focus:ring-1 focus:ring-purple-400"
        />
        <span className="text-[10px] text-purple-500">{showEnd ? 'Python strptime format, applied to both columns above.' : 'Python strptime format, applied to the column above.'}</span>
      </div>
    </div>
  )
}

// Drug Exposure fields — each one its own card, in a fixed order: Type, Stop reason,
// Refills, Quantity, Days supply, Sig, Route, Unit, Lot number.
function DrugExposureFieldsSection({
  decision,
  onChange,
  fileColumns,
  columnInfos,
  excludeColumn,
  ownVariable,
  claims,
}: {
  decision: VariableDecision
  onChange: (d: VariableDecision) => void
  fileColumns: string[]
  columnInfos: Record<string, ColumnInfo>
  excludeColumn: string
  ownVariable: string
  claims: Record<string, { variable: string; fieldKey: string; label: string }>
}) {
  const options = fileColumns.filter(c => c !== excludeColumn)
  const routeMapping = decision.route_mapping ?? EMPTY_ROUTE_MAPPING
  const unitMapping = decision.unit_mapping ?? EMPTY_UNIT_MAPPING

  const fieldByKey = (key: (typeof DRUG_COLUMN_FIELDS)[number]['key']) =>
    DRUG_COLUMN_FIELDS.find(f => f.key === key)!

  const simpleField = (key: (typeof DRUG_COLUMN_FIELDS)[number]['key']) => (
    <SimpleColumnFieldCard
      field={fieldByKey(key)}
      value={decision[key]}
      onChange={v => onChange({ ...decision, [key]: v })}
      options={options}
      claims={claims}
      ownVariable={ownVariable}
    />
  )

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs font-semibold text-muted-foreground">Drug Exposure fields (optional)</p>

      <TypeConceptCard
        conceptId={decision.type_concept_id}
        conceptName={decision.type_concept_name}
        onSet={(id, name) => onChange({ ...decision, type_concept_id: id, type_concept_name: name })}
        onClear={() => onChange({ ...decision, type_concept_id: null, type_concept_name: null })}
      />
      {simpleField('stop_reason_col')}
      {simpleField('refills_col')}
      {simpleField('quantity_col')}
      {simpleField('days_supply_col')}
      {simpleField('sig_col')}
      <RouteMappingSection
        routeMapping={routeMapping}
        onChange={r => onChange({ ...decision, route_mapping: r })}
        columnInfos={columnInfos}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
      />
      <UnitMappingSection
        unitMapping={unitMapping}
        onChange={u => onChange({ ...decision, unit_mapping: u })}
        columnInfos={columnInfos}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
      />
      {simpleField('lot_number_col')}
      <DateTimeFieldsCard
        decision={decision}
        onChange={onChange}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
        endRequired
      />
    </div>
  )
}

// Procedure Occurrence fields — Type, Quantity, Modifier, Start/End datetime. Reuses the
// same shared cards as Drug Exposure (Type, the sibling-column quantity_col field, and
// Start/End datetime are fully generic at the data level — only Modifier is new).
function ProcedureFieldsSection({
  decision,
  onChange,
  fileColumns,
  columnInfos,
  excludeColumn,
  ownVariable,
  claims,
}: {
  decision: VariableDecision
  onChange: (d: VariableDecision) => void
  fileColumns: string[]
  columnInfos: Record<string, ColumnInfo>
  excludeColumn: string
  ownVariable: string
  claims: Record<string, { variable: string; fieldKey: string; label: string }>
}) {
  const options = fileColumns.filter(c => c !== excludeColumn)
  const modifierMapping = decision.modifier_mapping ?? EMPTY_MODIFIER_MAPPING
  const quantityField = DRUG_COLUMN_FIELDS.find(f => f.key === 'quantity_col')!

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs font-semibold text-muted-foreground">Procedure Occurrence fields (optional)</p>

      <TypeConceptCard
        conceptId={decision.type_concept_id}
        conceptName={decision.type_concept_name}
        onSet={(id, name) => onChange({ ...decision, type_concept_id: id, type_concept_name: name })}
        onClear={() => onChange({ ...decision, type_concept_id: null, type_concept_name: null })}
        description={
          <>
            Hardcode the provenance of this variable's procedure records (e.g. EHR encounter
            record, Facility record). Leave blank to use the pipeline default (EHR, 32879).
          </>
        }
      />
      <SimpleColumnFieldCard
        field={quantityField}
        value={decision.quantity_col}
        onChange={v => onChange({ ...decision, quantity_col: v })}
        options={options}
        claims={claims}
        ownVariable={ownVariable}
      />
      <ModifierMappingSection
        modifierMapping={modifierMapping}
        onChange={m => onChange({ ...decision, modifier_mapping: m })}
        columnInfos={columnInfos}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
      />
      <DateTimeFieldsCard
        decision={decision}
        onChange={onChange}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
        startHint="fills procedure_date(time)"
        endHint="fills procedure_end_date(time)"
      />
    </div>
  )
}

// Condition Occurrence fields — Type, Stop reason, Condition Status, Start/End datetime.
// Type/Stop reason/Start-End-datetime reuse fully generic backend plumbing (same as
// Procedure); Condition Status is new end-to-end, mirroring Modifier's mechanism.
function ConditionOccurrenceFieldsSection({
  decision,
  onChange,
  fileColumns,
  columnInfos,
  excludeColumn,
  ownVariable,
  claims,
}: {
  decision: VariableDecision
  onChange: (d: VariableDecision) => void
  fileColumns: string[]
  columnInfos: Record<string, ColumnInfo>
  excludeColumn: string
  ownVariable: string
  claims: Record<string, { variable: string; fieldKey: string; label: string }>
}) {
  const options = fileColumns.filter(c => c !== excludeColumn)
  const conditionStatusMapping = decision.condition_status_mapping ?? EMPTY_CONDITION_STATUS_MAPPING
  const stopReasonField = DRUG_COLUMN_FIELDS.find(f => f.key === 'stop_reason_col')!

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs font-semibold text-muted-foreground">Condition Occurrence fields (optional)</p>

      <TypeConceptCard
        conceptId={decision.type_concept_id}
        conceptName={decision.type_concept_name}
        onSet={(id, name) => onChange({ ...decision, type_concept_id: id, type_concept_name: name })}
        onClear={() => onChange({ ...decision, type_concept_id: null, type_concept_name: null })}
        description={
          <>
            Hardcode the provenance of this variable's condition records (e.g. EHR encounter
            record, Claim). Leave blank to use the pipeline default (EHR, 32879).
          </>
        }
      />
      <SimpleColumnFieldCard
        field={stopReasonField}
        value={decision.stop_reason_col}
        onChange={v => onChange({ ...decision, stop_reason_col: v })}
        options={options}
        claims={claims}
        ownVariable={ownVariable}
      />
      <ConditionStatusMappingSection
        conditionStatusMapping={conditionStatusMapping}
        onChange={m => onChange({ ...decision, condition_status_mapping: m })}
        columnInfos={columnInfos}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
      />
      <DateTimeFieldsCard
        decision={decision}
        onChange={onChange}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
        startHint="fills condition_start_date(time)"
        endHint="fills condition_end_date(time)"
      />
    </div>
  )
}

// Observation fields — Type, Qualifier, Start datetime. Unit is NOT here — it's already
// shared with Measurement via the top-level Unit block (domain_id 1 or 2). Observation
// has no end-date field in the CDM, so DateTimeFieldsCard renders with showEnd={false}.
function ObservationFieldsSection({
  decision,
  onChange,
  fileColumns,
  columnInfos,
  excludeColumn,
  ownVariable,
  claims,
}: {
  decision: VariableDecision
  onChange: (d: VariableDecision) => void
  fileColumns: string[]
  columnInfos: Record<string, ColumnInfo>
  excludeColumn: string
  ownVariable: string
  claims: Record<string, { variable: string; fieldKey: string; label: string }>
}) {
  const qualifierMapping = decision.qualifier_mapping ?? EMPTY_QUALIFIER_MAPPING

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs font-semibold text-muted-foreground">Observation fields (optional)</p>

      <TypeConceptCard
        conceptId={decision.type_concept_id}
        conceptName={decision.type_concept_name}
        onSet={(id, name) => onChange({ ...decision, type_concept_id: id, type_concept_name: name })}
        onClear={() => onChange({ ...decision, type_concept_id: null, type_concept_name: null })}
        description={
          <>
            Hardcode the provenance of this variable's observation records (e.g. EHR
            encounter record, Patient reported). Leave blank to use the pipeline default (EHR, 32879).
          </>
        }
      />
      <QualifierMappingSection
        qualifierMapping={qualifierMapping}
        onChange={m => onChange({ ...decision, qualifier_mapping: m })}
        columnInfos={columnInfos}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
      />
      <DateTimeFieldsCard
        decision={decision}
        onChange={onChange}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
        startHint="fills observation_date(time)"
        showEnd={false}
      />
    </div>
  )
}

function MeasurementFieldsSection({
  decision,
  onChange,
  fileColumns,
  columnInfos,
  excludeColumn,
  ownVariable,
  claims,
}: {
  decision: VariableDecision
  onChange: (d: VariableDecision) => void
  fileColumns: string[]
  columnInfos: Record<string, ColumnInfo>
  excludeColumn: string
  ownVariable: string
  claims: Record<string, { variable: string; fieldKey: string; label: string }>
}) {
  const options = fileColumns.filter(c => c !== excludeColumn)
  const operatorMapping = decision.operator_mapping ?? EMPTY_OPERATOR_MAPPING
  const rangeLowField = MEASUREMENT_COLUMN_FIELDS.find(f => f.key === 'range_low_col')!
  const rangeHighField = MEASUREMENT_COLUMN_FIELDS.find(f => f.key === 'range_high_col')!

  return (
    <div className="flex flex-col gap-2.5">
      <p className="text-xs font-semibold text-muted-foreground">Measurement fields (optional)</p>

      <TypeConceptCard
        conceptId={decision.type_concept_id}
        conceptName={decision.type_concept_name}
        onSet={(id, name) => onChange({ ...decision, type_concept_id: id, type_concept_name: name })}
        onClear={() => onChange({ ...decision, type_concept_id: null, type_concept_name: null })}
        description={
          <>
            Hardcode the provenance of this variable's measurement records (e.g. Lab result,
            Vital sign recorded from a device). Leave blank to use the pipeline default (EHR, 32879).
          </>
        }
      />
      <OperatorMappingSection
        operatorMapping={operatorMapping}
        onChange={m => onChange({ ...decision, operator_mapping: m })}
        columnInfos={columnInfos}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
      />
      <div className="grid grid-cols-2 gap-2.5">
        <SimpleColumnFieldCard
          field={rangeLowField}
          value={decision.range_low_col}
          onChange={v => onChange({ ...decision, range_low_col: v })}
          options={options}
          claims={claims}
          ownVariable={ownVariable}
        />
        <SimpleColumnFieldCard
          field={rangeHighField}
          value={decision.range_high_col}
          onChange={v => onChange({ ...decision, range_high_col: v })}
          options={options}
          claims={claims}
          ownVariable={ownVariable}
        />
      </div>
      <DateTimeFieldsCard
        decision={decision}
        onChange={onChange}
        fileColumns={fileColumns}
        excludeColumn={excludeColumn}
        claims={claims}
        ownVariable={ownVariable}
        startHint="fills measurement_date(time)"
        showEnd={false}
      />
    </div>
  )
}

// ── Extra instructions (AI) — locally-buffered textarea ────────────────────
//
// With ~1000 columns on the page, committing every keystroke straight to the
// parent's `decisions` state re-renders the entire column list on each key
// press (VariableRow isn't memoized and its callback/decision props are
// recreated every render anyway). Buffer locally and only flush upstream on
// blur or after a short idle debounce, matching the commit-on-blur pattern
// ConceptPicker already uses for its manual ID/name inputs.

function ExtraInstructionsInput({
  value,
  onCommit,
  column,
}: {
  value: string
  onCommit: (v: string) => void
  column: string
}) {
  const [draft, setDraft] = useState(value)
  const [syncedValue, setSyncedValue] = useState(value)
  const [focused, setFocused] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Resync local draft when the parent's `value` changes for a reason other than
  // our own edits — but never while focused, since a debounce round-trip (or an
  // unrelated field's onChange spreading a stale `decision`) can otherwise echo
  // back an older string mid-edit and snap the caret to the end. Done during
  // render (not an effect) per React's "adjusting state when a prop changes".
  if (value !== syncedValue && !focused) {
    setSyncedValue(value)
    setDraft(value)
  }

  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  const scheduleCommit = (v: string) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => { timerRef.current = null; onCommit(v) }, 500)
  }

  const flush = () => {
    if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
    onCommit(draft)
  }

  return (
    <Textarea
      value={draft}
      onChange={e => { setDraft(e.target.value); scheduleCommit(e.target.value) }}
      onFocus={() => setFocused(true)}
      onBlur={() => { setFocused(false); flush() }}
      rows={4}
      placeholder={`e.g. "Convert ${column} from Fahrenheit to Celsius before loading."`}
      className="font-mono text-xs resize-y"
    />
  )
}

// ── Single variable expandable row ─────────────────────────────────────────

const VariableRow = memo(function VariableRow({
  column,
  info,
  decision,
  projectId,
  checked,
  onCheck,
  onChange,
  batchMode,
  fileColumns,
  columnInfos,
  drugFieldClaims,
}: {
  column: string
  info: ColumnInfo | null
  decision: VariableDecision
  projectId: string
  checked: boolean
  onCheck: (c: boolean) => void
  onChange: (d: VariableDecision) => void
  batchMode: boolean
  fileColumns: string[]
  columnInfos: Record<string, ColumnInfo>
  drugFieldClaims: Record<string, { variable: string; fieldKey: string; label: string }>
}) {
  const lockedBy = drugFieldClaims[column] ?? null
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

    if (!concept.concept_id || concept.concept_id < 1) return

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

  // map_values has no row-level domain picker (domain is detected per value), so a
  // Drug Exposure variable mapped this way never sets decision.domain_id itself —
  // fall back to checking whether any mapped value was itself detected as Drug Exposure.
  const isDrugExposure =
    decision.domain_id === 3 ||
    (decision.strategy === 'map_values' && Object.values(decision.value_concepts).some(vc => vc.domain_id === 3))

  // Same map_values fallback as isDrugExposure above, for Procedure Occurrence.
  const isProcedureOccurrence =
    decision.domain_id === 4 ||
    (decision.strategy === 'map_values' && Object.values(decision.value_concepts).some(vc => vc.domain_id === 4))

  // Same map_values fallback as isDrugExposure above, for Condition Occurrence.
  const isConditionOccurrence =
    decision.domain_id === 5 ||
    (decision.strategy === 'map_values' && Object.values(decision.value_concepts).some(vc => vc.domain_id === 5))

  // Same map_values fallback as isDrugExposure above, for Observation.
  const isObservation =
    decision.domain_id === 2 ||
    (decision.strategy === 'map_values' && Object.values(decision.value_concepts).some(vc => vc.domain_id === 2))

  // Same map_values fallback as isDrugExposure above, for Measurement.
  const isMeasurement =
    decision.domain_id === 1 ||
    (decision.strategy === 'map_values' && Object.values(decision.value_concepts).some(vc => vc.domain_id === 1))

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

  // Fields the OMOP CDM requires for this variable's domain (see RequiredMark) that
  // haven't been mapped yet — surfaced in the header so it's visible whether the row
  // is expanded or collapsed, not just when looking at the field itself.
  // Start datetime is excluded from this error list: when unmapped, the ETL falls
  // back to the row's visit_occurrence date, so it's a heads-up rather than a
  // problem — see `startDatetimeUnmapped` below.
  const missingRequiredFields: string[] = (() => {
    if (decision.strategy === 'skip') return []
    const inRoutedDomain = isMeasurement || isDrugExposure || isProcedureOccurrence || isConditionOccurrence || isObservation
    if (!inRoutedDomain) return []
    const missing: string[] = []
    if (isDrugExposure && !decision.end_datetime_col) missing.push('End datetime')
    if (!decision.type_concept_id) missing.push('Type')
    return missing
  })()

  const startDatetimeUnmapped = (() => {
    if (decision.strategy === 'skip') return false
    const inRoutedDomain = isMeasurement || isDrugExposure || isProcedureOccurrence || isConditionOccurrence || isObservation
    if (!inRoutedDomain) return false
    return !decision.start_datetime_col
  })()

  const sampleValues = info?.distinct_values.slice(0, 10) ?? []
  const extraCount = (info?.distinct_count ?? 0) - 10

  // Claimed as a sibling-column field (quantity, sig, …) by another variable's
  // Drug Exposure mapping — visible but not independently mappable.
  if (lockedBy) {
    return (
      <div
        className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-dashed border-purple-200 bg-purple-50/30 opacity-70"
        title={`${column} is consumed as the ${lockedBy.label} field for "${lockedBy.variable}" — clear it there to map this column on its own.`}
      >
        <span className="font-mono text-sm font-medium text-muted-foreground w-64 flex-shrink-0 truncate" title={column}>{column}</span>
        <span className="flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full font-medium bg-purple-100 text-purple-700 border border-purple-200 flex-shrink-0">
          <Lock className="w-3 h-3 flex-shrink-0" />
          Used as {lockedBy.label} for <span className="font-mono">{lockedBy.variable}</span>
        </span>
        <span className="ml-auto text-[11px] text-muted-foreground flex-shrink-0">Cannot be mapped separately</span>
      </div>
    )
  }

  // Unticked by the user — excluded from mapping entirely and from the "Variables to
  // map" totals. Rendered as a locked, collapsed row (re-tick to restore full editing).
  if (decision.enabled === false) {
    return (
      <div
        className="flex items-center gap-2 px-3 py-2.5 rounded-lg border border-dashed border-border bg-muted/30 opacity-60"
        title={`${column} is excluded from mapping — tick the box to include it again.`}
      >
        <input
          type="checkbox"
          checked={false}
          onChange={e => onChange({ ...decision, enabled: e.target.checked })}
          className="rounded accent-primary flex-shrink-0"
          title="Include this variable in mapping"
        />
        <span className="font-mono text-sm font-medium text-muted-foreground w-64 flex-shrink-0 truncate" title={column}>{column}</span>
        <span className="text-xs text-muted-foreground">Excluded from mapping</span>
      </div>
    )
  }

  return (
    <div className="relative">
    <div className={clsx(
      'border rounded-lg transition-colors',
      !open && 'overflow-hidden',
      checked && 'ring-2 ring-primary',
      decision.strategy === 'skip' ? 'border-border/50' : 'border-border',
      open ? 'shadow-sm' : '',
    )}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2.5 bg-card">
        <input
          type="checkbox"
          checked={true}
          onChange={e => onChange({ ...decision, enabled: e.target.checked })}
          className="rounded accent-primary flex-shrink-0"
          onClick={e => e.stopPropagation()}
          title="Include this variable in mapping"
        />

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
          <span className="font-mono text-sm font-medium text-foreground w-64 flex-shrink-0 truncate" title={column}>{column}</span>

          <span className={clsx('flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0', sm.color)}>
            {sm.icon}{sm.label}
          </span>

          {/* Unit indicator — shown when a unit mapping (fixed or from-column) is configured */}
          {(() => {
            const um = decision.unit_mapping
            if (!um) return null
            const fixed = getFixedConcept(um.unit_col, um.unit_concepts)
            if (fixed) {
              return (
                <span
                  className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-sky-100 text-sky-700 border border-sky-200 flex-shrink-0"
                  title={`Unit concept: ${fixed.name} (${fixed.id})`}
                >
                  unit: <span className="font-mono">{fixed.name}</span>
                </span>
              )
            }
            if (um.unit_col) {
              const mappedCount = Object.keys(um.unit_concepts).length
              const total = columnInfos[um.unit_col]?.distinct_values.length ?? 0
              const percent = total > 0 ? Math.round((mappedCount / total) * 100) : 0
              return (
                <span
                  className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-sky-100 text-sky-700 border border-sky-200 flex-shrink-0"
                  title={`Unit mapped from column "${um.unit_col}" — ${mappedCount}/${total} value${total === 1 ? '' : 's'} reviewed`}
                >
                  unit: {percent}%
                </span>
              )
            }
            return null
          })()}

          {/* Route indicator — shown when a route mapping (fixed or from-column) is configured */}
          {(() => {
            const rm = decision.route_mapping
            if (!rm) return null
            const fixed = getFixedConcept(rm.route_col, rm.route_concepts)
            if (fixed) {
              return (
                <span
                  className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-purple-100 text-purple-700 border border-purple-200 flex-shrink-0"
                  title={`Route concept: ${fixed.name} (${fixed.id})`}
                >
                  route: <span className="font-mono">{fixed.name}</span>
                </span>
              )
            }
            if (rm.route_col) {
              const mappedCount = Object.keys(rm.route_concepts).length
              const total = columnInfos[rm.route_col]?.distinct_values.length ?? 0
              const percent = total > 0 ? Math.round((mappedCount / total) * 100) : 0
              return (
                <span
                  className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-purple-100 text-purple-700 border border-purple-200 flex-shrink-0"
                  title={`Route mapped from column "${rm.route_col}" — ${mappedCount}/${total} value${total === 1 ? '' : 's'} reviewed`}
                >
                  route: {percent}%
                </span>
              )
            }
            return null
          })()}

          {/* Extra instructions (AI) indicator */}
          {!!decision.extra_instructions?.trim() && (
            <span
              className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-200 flex-shrink-0"
              title={`Extra instructions (AI): ${decision.extra_instructions}`}
            >
              <Sparkles className="w-3 h-3" /> instructions
            </span>
          )}

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
                  distinctCount={info.distinct_count}
                  mapped={decision.value_concepts}
                  onChange={vc => onChange({ ...decision, value_concepts: vc })}
                />
              )}
            </div>
          )}

          {/* Unit (optional) — shown for Measurement / Observation. For Drug Exposure it's
              rendered inside DrugExposureFieldsSection instead, in the requested field order. */}
          {(decision.domain_id === 1 || decision.domain_id === 2) && decision.strategy !== 'skip' && (
            <UnitMappingSection
              unitMapping={decision.unit_mapping ?? EMPTY_UNIT_MAPPING}
              onChange={u => onChange({ ...decision, unit_mapping: u })}
              columnInfos={columnInfos}
              fileColumns={fileColumns}
              excludeColumn={column}
              claims={drugFieldClaims}
              ownVariable={column}
            />
          )}

          {/* Drug Exposure fields (optional) — sibling-column pulls, shown for Drug Exposure */}
          {isDrugExposure && decision.strategy === 'map_values' && (
            <DrugExposureFieldsSection
              decision={decision}
              onChange={onChange}
              fileColumns={fileColumns}
              columnInfos={columnInfos}
              excludeColumn={column}
              ownVariable={column}
              claims={drugFieldClaims}
            />
          )}

          {/* Procedure Occurrence fields (optional) — Type, Quantity, Modifier, Start/End datetime */}
          {isProcedureOccurrence && decision.strategy !== 'skip' && (
            <ProcedureFieldsSection
              decision={decision}
              onChange={onChange}
              fileColumns={fileColumns}
              columnInfos={columnInfos}
              excludeColumn={column}
              ownVariable={column}
              claims={drugFieldClaims}
            />
          )}

          {/* Condition Occurrence fields (optional) — Type, Stop reason, Condition Status, Start/End datetime */}
          {isConditionOccurrence && decision.strategy !== 'skip' && (
            <ConditionOccurrenceFieldsSection
              decision={decision}
              onChange={onChange}
              fileColumns={fileColumns}
              columnInfos={columnInfos}
              excludeColumn={column}
              ownVariable={column}
              claims={drugFieldClaims}
            />
          )}

          {/* Observation fields (optional) — Type, Qualifier, Start datetime. Unit is shown
              separately above (shared with Measurement). */}
          {isObservation && decision.strategy !== 'skip' && (
            <ObservationFieldsSection
              decision={decision}
              onChange={onChange}
              fileColumns={fileColumns}
              columnInfos={columnInfos}
              excludeColumn={column}
              ownVariable={column}
              claims={drugFieldClaims}
            />
          )}

          {/* Measurement fields (optional) — Type, Operator, Range low/high, Start datetime.
              Unit is shown separately above (shared with Observation). */}
          {isMeasurement && decision.strategy !== 'skip' && (
            <MeasurementFieldsSection
              decision={decision}
              onChange={onChange}
              fileColumns={fileColumns}
              columnInfos={columnInfos}
              excludeColumn={column}
              ownVariable={column}
              claims={drugFieldClaims}
            />
          )}

          {/* Extra instructions (AI) — per-variable free-text guidance */}
          {decision.strategy !== 'skip' && (
            <div className="flex flex-col gap-1.5">
              <p className="text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5 text-amber-500" />
                Extra instructions (AI)
              </p>
              <ExtraInstructionsInput
                value={decision.extra_instructions ?? ''}
                onCommit={v => onChange({ ...decision, extra_instructions: v })}
                column={column}
              />
              <p className="text-[11px] text-muted-foreground">
                Describe any special transformation or loading logic for <code className="bg-muted px-1 rounded">{column}</code>. Sent to the AI when generating the stem_table script.
              </p>
            </div>
          )}
        </div>
      )}
    </div>

    {/* Missing required field warning — absolutely positioned outside the panel's
        border so it never takes horizontal space from the panel itself (which would
        otherwise shrink every row, warning or not, to keep list widths consistent). */}
    {(missingRequiredFields.length > 0 || startDatetimeUnmapped) && (
      <div className="absolute left-full top-0 ml-2 w-44 flex flex-col gap-1">
        {startDatetimeUnmapped && (
          <div className="flex items-start gap-1 text-[11px] font-medium text-amber-700 leading-snug">
            <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <span>Start datetime not mapped — parsed from visit occurrence</span>
          </div>
        )}
        {missingRequiredFields.length > 0 && (
          <div className="flex items-start gap-1 text-[11px] font-medium text-red-700 leading-snug">
            <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
            <span>{missingRequiredFields.join(', ')} required</span>
          </div>
        )}
      </div>
    )}
    </div>
  )
})
VariableRow.displayName = 'VariableRow'

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
              distinctCount={columnInfos[selectedCols[0]]?.distinct_count ?? sharedValues.length}
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
  const [downloadingMappings, setDownloadingMappings] = useState(false)
  const [downloadingSummary, setDownloadingSummary] = useState(false)

  // Batch state
  const [batchMode, setBatchMode] = useState(false)
  const [selectedCols, setSelectedCols] = useState<string[]>([])

  // Filter
  const [filter, setFilter] = useState<'all' | 'mapped' | 'skipped'>('all')
  const [domainFilter, setDomainFilter] = useState<'all' | number>('all')
  const [search, setSearch] = useState('')

  // AI settings
  const [openaiConfigured, setOpenaiConfigured] = useState(false)
  const [customConceptsOpen, setCustomConceptsOpen] = useState(false)

  useEffect(() => {
    getApiHealth()
      .then(h => setOpenaiConfigured(!!h.openai_configured))
      .catch(() => setOpenaiConfigured(false))
  }, [])

  const { cols, selectedFile, files, changeFile } = useSourceFile(project, 'concepts')

  const structuralCols = useMemo(
    () => getStructuralColumns((project.etl_config || {}) as Record<string, unknown>),
    [project.etl_config],
  )
  const structuralColFileMap = useMemo(
    () => getStructuralColFileMap((project.etl_config || {}) as Record<string, unknown>),
    [project.etl_config],
  )

  // Exclude a structural column only from the file it was configured in.
  // If file attribution is unknown (null) fall back to global exclusion.
  const conceptCols = useMemo(() => cols.filter(col => {
    if (!structuralCols.has(col)) return true
    const ownerFile = structuralColFileMap.get(col)
    if (ownerFile === undefined) return true   // not in map at all → not structural
    if (ownerFile === null) return false        // structural, file unknown → exclude globally
    return ownerFile !== selectedFile?.filename // exclude only from the owning file
  }), [cols, structuralCols, structuralColFileMap, selectedFile?.filename])

  // Set to true right before every programmatic (non-user) decisions update, so the
  // autosave effect below can tell "data just loaded" apart from "user changed something".
  const skipNextAutosaveRef = useRef(true)

  useEffect(() => {
    Promise.all([
      getColumnValues(project.id, selectedFile?.filename),
      getConceptDecisions(project.id),
    ]).then(([infos, saved]: [Record<string, ColumnInfo>, Record<string, VariableDecision>]) => {
      setColumnInfos(infos)
      skipNextAutosaveRef.current = true
      setDecisions(prev => {
        // saved contains decisions from ALL files; prev contains in-memory changes.
        // Merge so neither is lost: saved is the base, prev (unsaved changes) wins.
        const next: Record<string, VariableDecision> = { ...saved, ...prev }
        // Initialize columns from ALL source files so that every file's columns
        // are present in `decisions` even if the user never switches to that file.
        const structColFileMap = getStructuralColFileMap((project.etl_config || {}) as Record<string, unknown>)
        for (const sf of (project.source_files || [])) {
          for (const col of (sf.columns || [])) {
            if (col in next) continue
            const ownerFile = structColFileMap.get(col)
            // Exclude only when this file is the owning file (or file is unknown → global exclusion)
            const excludeHere = ownerFile !== undefined && (ownerFile === null || ownerFile === sf.filename)
            if (!excludeHere) {
              next[col] = { strategy: 'skip', variable_concept: null, value_concepts: {}, domain_id: null }
            }
          }
        }
        return next
      })
    }).finally(() => setLoading(false))
  // project.etl_config/project.source_files are read here but deliberately excluded —
  // this should only reload from the server on project switch or file change, not every
  // time the project object gets a new reference from an unrelated edit elsewhere in the wizard.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id, selectedFile?.filename])

  // Autosave: persist decisions shortly after any change (concept set/cleared, strategy
  // change, domain override, batch apply, …) so mapping work survives a refresh or crash
  // without requiring the user to click Next. Skipped right after data loads in, since
  // that update isn't a user edit.
  useEffect(() => {
    if (loading) return
    if (skipNextAutosaveRef.current) { skipNextAutosaveRef.current = false; return }
    const timer = setTimeout(() => {
      saveConceptDecisions(project.id, decisions as Record<string, unknown>).catch((e: unknown) => {
        const err = e as { response?: { data?: { detail?: string } } }
        setGenError(err?.response?.data?.detail || 'Failed to save concept decisions.')
      })
    }, 800)
    return () => clearTimeout(timer)
  }, [decisions, loading, project.id])

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

  const toggleSelect = useCallback((col: string, checked: boolean) => {
    setSelectedCols(prev => checked ? [...prev, col] : prev.filter(c => c !== col))
  }, [])

  // Stable per-column callbacks so VariableRow (wrapped in memo) doesn't re-render
  // every row on every parent state change (e.g. toggling the status filter) — with
  // large column counts, recreating these closures on every render was the whole list.
  const onCheckCache = useRef<Map<string, (c: boolean) => void>>(new Map())
  const onChangeCache = useRef<Map<string, (d: VariableDecision) => void>>(new Map())
  const getOnCheck = (col: string) => {
    let fn = onCheckCache.current.get(col)
    if (!fn) { fn = (c: boolean) => toggleSelect(col, c); onCheckCache.current.set(col, fn) }
    return fn
  }
  const getOnChange = (col: string) => {
    let fn = onChangeCache.current.get(col)
    if (!fn) { fn = (d: VariableDecision) => setDecision(col, d); onChangeCache.current.set(col, fn) }
    return fn
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

  const handleDownloadMappings = async () => {
    setDownloadingMappings(true)
    setGenError('')
    try {
      await Promise.all([
        saveConceptDecisions(project.id, decisions as Record<string, unknown>),
        updateTableConfig(project.id, 'concepts', { col_file_map: colFileMap, source_filename: selectedFile?.filename ?? null }),
      ])
      const withMappings = await generateMappingCsvs(project.id)
      onUpdate(withMappings)
      downloadMappingFiles(project.id)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setGenError(err?.response?.data?.detail || 'Failed to generate mapping files.')
    } finally {
      setDownloadingMappings(false)
    }
  }

  const handleDownloadSummary = async () => {
    setDownloadingSummary(true)
    setGenError('')
    try {
      await Promise.all([
        saveConceptDecisions(project.id, decisions as Record<string, unknown>),
        updateTableConfig(project.id, 'concepts', { col_file_map: colFileMap, source_filename: selectedFile?.filename ?? null }),
      ])
      downloadMappingSummary(project.id)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      setGenError(err?.response?.data?.detail || 'Failed to generate mapping summary.')
    } finally {
      setDownloadingSummary(false)
    }
  }

  // Columns claimed as a Drug Exposure sibling-column field (quantity_col, sig_col, …) by
  // some variable — those columns can be seen but not mapped on their own (main list), and
  // are disabled in every OTHER Drug Exposure field picker (their own claiming field/variable
  // stays selectable so the current selection still renders).
  // Recomputed from the whole `decisions` map, so it would otherwise get a new
  // object reference on every single-field commit anywhere on the page (e.g.
  // typing extra instructions in one row) — and since it's handed to every
  // VariableRow, that reference change alone would defeat memo() for the
  // entire list on every commit. Keep the previous reference when the actual
  // claim contents haven't changed so unaffected rows don't re-render.
  const drugFieldClaimsRef = useRef<Record<string, { variable: string; fieldKey: string; label: string }>>({})
  const drugFieldClaims = useMemo(() => {
    const claims: Record<string, { variable: string; fieldKey: string; label: string }> = {}
    for (const [variable, d] of Object.entries(decisions)) {
      if (!d || d.strategy === 'skip') continue
      for (const f of DRUG_COLUMN_FIELDS) {
        const col = d[f.key]
        if (col && !claims[col]) claims[col] = { variable, fieldKey: f.key, label: f.label }
      }
      for (const f of MEASUREMENT_COLUMN_FIELDS) {
        const col = d[f.key]
        if (col && !claims[col]) claims[col] = { variable, fieldKey: f.key, label: f.label }
      }
      const unitCol = d.unit_mapping?.unit_col
      if (unitCol && !claims[unitCol]) claims[unitCol] = { variable, fieldKey: 'unit_col', label: 'Unit' }
      const routeCol = d.route_mapping?.route_col
      if (routeCol && !claims[routeCol]) claims[routeCol] = { variable, fieldKey: 'route_col', label: 'Route' }
      const modifierCol = d.modifier_mapping?.modifier_col
      if (modifierCol && !claims[modifierCol]) claims[modifierCol] = { variable, fieldKey: 'modifier_col', label: 'Modifier' }
      const conditionStatusCol = d.condition_status_mapping?.condition_status_col
      if (conditionStatusCol && !claims[conditionStatusCol]) {
        claims[conditionStatusCol] = { variable, fieldKey: 'condition_status_col', label: 'Condition Status' }
      }
      const qualifierCol = d.qualifier_mapping?.qualifier_col
      if (qualifierCol && !claims[qualifierCol]) claims[qualifierCol] = { variable, fieldKey: 'qualifier_col', label: 'Qualifier' }
      const operatorCol = d.operator_mapping?.operator_col
      if (operatorCol && !claims[operatorCol]) claims[operatorCol] = { variable, fieldKey: 'operator_col', label: 'Operator' }
      if (d.start_datetime_col && !claims[d.start_datetime_col]) {
        claims[d.start_datetime_col] = { variable, fieldKey: 'start_datetime_col', label: 'Start datetime' }
      }
      if (d.end_datetime_col && !claims[d.end_datetime_col]) {
        claims[d.end_datetime_col] = { variable, fieldKey: 'end_datetime_col', label: 'End datetime' }
      }
    }
    const prev = drugFieldClaimsRef.current
    const prevKeys = Object.keys(prev)
    const sameContent =
      prevKeys.length === Object.keys(claims).length &&
      prevKeys.every(k => {
        const a = prev[k]
        const b = claims[k]
        return !!b && a.variable === b.variable && a.fieldKey === b.fieldKey && a.label === b.label
      })
    if (sameContent) return prev
    drugFieldClaimsRef.current = claims
    return claims
  }, [decisions])

  // Same formula as VariableRow's own `mappingCompleteness` badge, so a row only
  // counts as mapped here once its badge actually reads 100% — a map_values/map_both
  // column with some but not all distinct values reviewed is still in progress.
  const columnMappingCompleteness = (col: string): number => {
    const d = decisions[col]
    if (!d || d.strategy === 'skip') return 0
    const mappedValueCount = Object.keys(d.value_concepts).length
    if (d.strategy === 'map_variable') return d.variable_concept ? 100 : 0
    if (d.strategy === 'map_values') {
      const total = columnInfos[col]?.distinct_count ?? 0
      return Math.round((mappedValueCount / total) * 100)
    }
    const total = (columnInfos[col]?.distinct_count ?? 0) + 1
    return Math.round(((mappedValueCount + (d.variable_concept ? 1 : 0)) / total) * 100)
  }

  // A column is "mapped" either the normal way (fully reviewed under a non-skip
  // strategy) or by being claimed as a Drug Exposure sibling-column field for another
  // variable — its data is still consumed by the ETL, just not through its own decision.
  const isColumnMapped = (col: string) => {
    if (drugFieldClaims[col]) return true
    return columnMappingCompleteness(col) === 100
  }

  // Variables the user unticked are excluded from mapping entirely, so they don't
  // count toward "Variables to map" or any of the stats derived from that pool.
  const enabledConceptCols = conceptCols.filter(c => decisions[c]?.enabled !== false)

  // Stats
  const mappedCount = enabledConceptCols.filter(isColumnMapped).length
  const skippedCount = enabledConceptCols.filter(c => !drugFieldClaims[c] && decisions[c]?.strategy === 'skip').length
  const idsAddedCount = enabledConceptCols.reduce((sum, c) => {
    const d = decisions[c]
    if (!d || d.strategy === 'skip') return sum
    // 0 is the "explicitly not mapped" placeholder (see SimpleConceptIdRow) — it
    // shouldn't count as an added concept id.
    const countMapped = (concepts: Record<string, number>) =>
      Object.values(concepts).filter(id => id !== 0).length
    let n = 0
    if ((d.strategy === 'map_variable' || d.strategy === 'map_both') && d.variable_concept && d.variable_concept.concept_id !== 0) n += 1
    if (d.strategy === 'map_values' || d.strategy === 'map_both') {
      n += Object.values(d.value_concepts).filter(vc => vc.concept_id !== 0).length
    }
    if (d.type_concept_id) n += 1
    if (d.route_mapping) n += countMapped(d.route_mapping.route_concepts)
    if (d.unit_mapping) n += countMapped(d.unit_mapping.unit_concepts)
    if (d.modifier_mapping) n += countMapped(d.modifier_mapping.modifier_concepts)
    if (d.condition_status_mapping) n += countMapped(d.condition_status_mapping.condition_status_concepts)
    if (d.qualifier_mapping) n += countMapped(d.qualifier_mapping.qualifier_concepts)
    return sum + n
  }, 0)

  // Every custom concept created anywhere in the project (all files, not just the
  // active one — `decisions` already holds every file's columns). Grouped by
  // concept_id so a reused id shows as one entry with multiple usages, which also
  // doubles as the duplicate-id lookup passed to CustomConceptForm via context.
  const customConceptsById = useMemo(() => {
    const colToFile = new Map<string, string>()
    for (const sf of (project.source_files || [])) {
      for (const col of (sf.columns || [])) colToFile.set(col, sf.filename)
    }

    const isCustomRef = (c: ConceptRef) => c.concept_id !== 0 && (c.is_custom || c.concept_id >= 2_000_000_000)

    const byId = new Map<number, CustomConceptEntry>()
    const record = (c: ConceptRef, usage: CustomConceptUsage) => {
      const existing = byId.get(c.concept_id)
      if (!existing) {
        byId.set(c.concept_id, {
          concept_id: c.concept_id,
          concept_name: c.concept_name,
          concept_code: c.concept_code,
          vocabulary_id: c.vocabulary_id,
          domain: c.domain,
          concept_class_id: c.concept_class_id,
          usages: [usage],
          conflicting: false,
        })
        return
      }
      existing.usages.push(usage)
      if (
        existing.concept_name !== c.concept_name ||
        existing.concept_code !== c.concept_code ||
        existing.vocabulary_id !== c.vocabulary_id
      ) {
        existing.conflicting = true
      }
    }

    for (const [col, d] of Object.entries(decisions)) {
      if (!d || d.strategy === 'skip') continue
      const file = colToFile.get(col) ?? null
      if (d.variable_concept && isCustomRef(d.variable_concept)) {
        record(d.variable_concept, { column: col, file })
      }
      for (const [value, c] of Object.entries(d.value_concepts)) {
        if (isCustomRef(c)) record(c, { column: col, file, value })
      }
    }

    return byId
  }, [decisions, project.source_files])

  const customConceptsList = useMemo(
    () => Array.from(customConceptsById.values()).sort((a, b) => a.concept_id - b.concept_id),
    [customConceptsById],
  )

  // Filter + search
  const filteredCols = conceptCols.filter(col => {
    if (search && !col.toLowerCase().includes(search.toLowerCase())) return false
    // A domain is only ever recorded on a mapped column (skipped/untouched columns
    // carry no meaningful domain_id), so picking a domain implies "mapped" and
    // overrides the all/mapped/skipped status filter.
    if (domainFilter !== 'all') return isColumnMapped(col) && decisionMatchesDomain(decisions[col], domainFilter)
    if (filter === 'skipped') return !drugFieldClaims[col] && decisions[col]?.strategy === 'skip'
    if (filter === 'mapped') return isColumnMapped(col)
    return true
  })

  return (
    <ConceptsCtx.Provider value={{ rerankerAvailable: openaiConfigured, usedCustomConceptIds: customConceptsById }}>
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
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-xl font-bold text-primary">Concept Mapping</h2>
            <p className="text-sm text-muted-foreground mt-1">
              Map each source variable to OMOP concepts. Click a variable to expand it and see its values.
              Use batch mode to map multiple variables at once.
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <button
              onClick={handleDownloadSummary}
              disabled={downloadingSummary}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-border text-muted-foreground hover:bg-muted disabled:opacity-50 flex-shrink-0"
              title="Download an Excel summary of every variable's mapping choices (included or not) — concepts, units, routes, type, and start/end datetime"
            >
              {downloadingSummary ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileText className="w-3.5 h-3.5" />}
              {downloadingSummary ? 'Preparing…' : 'Download mapping summary (Excel)'}
            </button>
            <button
              onClick={handleDownloadMappings}
              disabled={downloadingMappings}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-border text-muted-foreground hover:bg-muted disabled:opacity-50 flex-shrink-0"
              title="Generate (if needed) and download variable_mapping.csv, value_mapping.csv, variable_value_mapping.csv, custom_mappings.csv and unit_mapping.csv as a zip"
            >
              {downloadingMappings ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
              {downloadingMappings ? 'Preparing…' : 'Download mapping files'}
            </button>
          </div>
        </div>

        {/* Concept ID 0 explainer */}
        <div className="flex items-start gap-1.5 rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs text-muted-foreground">
          <Info className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-muted-foreground" />
          <span>
            <span className="font-semibold text-foreground">Tip:</span> set a concept ID to{' '}
            <code className="bg-gray-100 text-gray-700 px-1 rounded font-mono">0</code> to explicitly mark a value as
            not mapped. It behaves exactly like leaving the value unmapped — no row is generated for it — but it
            shows up as reviewed instead of pending.
          </span>
        </div>

        {/* AI reranker status + custom concepts summary */}
        <div className="flex items-center gap-3 flex-wrap rounded-lg border border-border bg-secondary/40 px-3 py-2 text-xs">
          <div className="flex items-center gap-1.5">
            <Sparkles className={clsx('w-3.5 h-3.5', openaiConfigured ? 'text-indigo-600' : 'text-muted-foreground')} />
            <span className="font-semibold text-foreground">AI reranker:</span>
            <span className={openaiConfigured ? 'text-indigo-700' : 'text-muted-foreground'}>
              {openaiConfigured ? 'available' : 'disabled (set OPENAI_API_KEY)'}
            </span>
          </div>
          {customConceptsList.length > 0 && (
            <>
              <span className="text-muted-foreground">·</span>
              <button
                type="button"
                onClick={() => setCustomConceptsOpen(true)}
                className="flex items-center gap-1 font-semibold text-purple-700 hover:underline"
              >
                <Tag className="w-3.5 h-3.5" /> Custom concepts created: {customConceptsList.length}
              </button>
            </>
          )}
        </div>

        {/* Custom concepts review dialog */}
        <Dialog open={customConceptsOpen} onOpenChange={setCustomConceptsOpen}>
          <DialogContent className="max-w-3xl w-[90vw] max-h-[80vh] overflow-hidden flex flex-col">
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <Tag className="w-4 h-4 text-purple-600" /> Custom concepts created
              </DialogTitle>
              <DialogDescription>
                Every custom OMOP concept (id ≥ 2,000,000,000) created in this project, and where it's used.
              </DialogDescription>
            </DialogHeader>
            <div className="overflow-y-auto -mx-6 px-6">
              <table className="w-full text-xs border-collapse">
                <thead className="sticky top-0 bg-card">
                  <tr className="text-left text-muted-foreground border-b border-border">
                    <th className="py-1.5 pr-3 font-medium">ID</th>
                    <th className="py-1.5 pr-3 font-medium">Name</th>
                    <th className="py-1.5 pr-3 font-medium">Code</th>
                    <th className="py-1.5 pr-3 font-medium">Vocabulary</th>
                    <th className="py-1.5 pr-3 font-medium">Domain</th>
                    <th className="py-1.5 pr-3 font-medium">Used in</th>
                  </tr>
                </thead>
                <tbody>
                  {customConceptsList.map(c => (
                    <tr
                      key={c.concept_id}
                      className={clsx(
                        'border-b border-border last:border-0 align-top',
                        c.conflicting && 'bg-amber-50',
                      )}
                    >
                      <td className="py-1.5 pr-3 font-mono text-purple-800">
                        {c.conflicting && (
                          <AlertTriangle
                            className="inline w-3 h-3 text-amber-600 mr-1 -mt-0.5"
                          />
                        )}
                        {c.concept_id}
                      </td>
                      <td className="py-1.5 pr-3">{c.concept_name}</td>
                      <td className="py-1.5 pr-3 text-muted-foreground">{c.concept_code || '—'}</td>
                      <td className="py-1.5 pr-3 text-muted-foreground">{c.vocabulary_id || '—'}</td>
                      <td className="py-1.5 pr-3 text-muted-foreground">{c.domain || '—'}</td>
                      <td className="py-1.5 pr-3">
                        <div className="flex flex-col gap-0.5">
                          {c.usages.map((u, i) => (
                            <span key={i} className="text-muted-foreground">
                              {u.file && files.length > 1 && <span className="opacity-70">{u.file} · </span>}
                              <code className="bg-muted px-1 rounded">{u.column}</code>
                              {u.value && <> → <span className="italic">"{u.value}"</span></>}
                            </span>
                          ))}
                        </div>
                        {c.conflicting && (
                          <span className="text-[10px] text-amber-700">reused with different name/code/vocabulary across mappings</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </DialogContent>
        </Dialog>

        {/* File selector — shown only when project has multiple source files */}
        {files.length > 1 && (
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-secondary/40 px-4 py-3">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">Source files</p>
            <div className="flex flex-wrap gap-2">
              {files.map((f, idx) => {
                const isActive = f.filename === selectedFile?.filename
                const fileCols = f.columns ?? []
                const fileConceptCols = fileCols.filter(c => !structuralCols.has(c) && decisions[c]?.enabled !== false)
                // Not isColumnMapped: columnInfos (needed for map_values/map_both
                // completeness %) is only ever loaded for the currently active file, so
                // that check would read as incomplete for every other file's columns.
                const fileMapped = fileConceptCols.filter(c => {
                  const d = decisions[c]
                  return d && d.strategy !== 'skip' && (d.variable_concept || Object.keys(d.value_concepts).length > 0)
                }).length
                return (
                  <button
                    key={f.filename}
                    onClick={() => changeFile(idx)}
                    className={clsx(
                      'flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-medium transition-colors',
                      isActive
                        ? 'border-primary bg-primary/10 text-primary'
                        : 'border-border bg-card text-muted-foreground hover:border-primary/50 hover:text-foreground',
                    )}
                  >
                    <FileText className="w-3.5 h-3.5 flex-shrink-0" />
                    <span>{f.filename}</span>
                    <span className={clsx(
                      'ml-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold',
                      isActive ? 'bg-primary/20 text-primary' : 'bg-muted text-muted-foreground',
                    )}>
                      {fileMapped}/{fileConceptCols.length}
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        )}

        {/* Stats */}
        <div className="grid grid-cols-4 gap-3">
          <div className="bg-card border border-border rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-foreground">{enabledConceptCols.length - mappedCount}</p>
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
          <div className="bg-indigo-50 border border-indigo-200 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-indigo-700">{idsAddedCount}</p>
            <p className="text-xs text-indigo-600">Concept IDs added</p>
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

        {/* Excluded structural columns — scoped to the currently selected file */}
        {(() => {
          const fileColumns = selectedFile?.columns ?? (project.source_columns ?? [])
          const fileStructuralCols = [...structuralCols].filter(c => {
            if (!fileColumns.includes(c)) return false
            const ownerFile = structuralColFileMap.get(c)
            return ownerFile === null || ownerFile === (selectedFile?.filename ?? null)
          })
          if (fileStructuralCols.length === 0) return null
          return (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-lg border border-border bg-secondary/60 text-xs text-secondary-foreground">
            <CheckCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-primary" />
            <span>
              <span className="font-semibold">{fileStructuralCols.length} structural column{fileStructuralCols.length > 1 ? 's' : ''} excluded</span>
              {' '}— already mapped in Person, Visit, Obs. Period, Location, Care Site, Provider, or Death steps:{' '}
              <span className="font-mono">{fileStructuralCols.join(', ')}</span>
            </span>
          </div>
          )
        })()}

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
                onClick={() => { setFilter(f); if (f !== 'mapped') setDomainFilter('all') }}
                className={clsx('px-2.5 py-1.5 rounded-md text-xs font-medium capitalize', filter === f ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:bg-muted/80')}
              >{f}</button>
            ))}
          </div>
          {filter === 'mapped' && (
            <select
              value={domainFilter === 'all' ? 'all' : String(domainFilter)}
              onChange={e => setDomainFilter(e.target.value === 'all' ? 'all' : Number(e.target.value))}
              className="border border-border rounded-md px-2.5 py-1.5 text-xs font-medium focus:outline-none focus:ring-2 focus:ring-ring bg-background text-foreground"
            >
              <option value="all">All domains</option>
              {DOMAIN_OPTIONS.map(d => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
          )}
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
                decision={decisions[col] ?? DEFAULT_DECISION}
                projectId={project.id}
                checked={selectedCols.includes(col)}
                onCheck={getOnCheck(col)}
                onChange={getOnChange(col)}
                batchMode={batchMode}
                fileColumns={cols}
                columnInfos={columnInfos}
                drugFieldClaims={drugFieldClaims}
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
