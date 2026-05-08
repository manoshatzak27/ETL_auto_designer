import { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getColumnValues,
  getConceptDecisions,
  saveConceptDecisions,
  generateMappingCsvs,
} from '../../api/client'
import type { Project } from '../../types'
import WizardLayout from './WizardLayout'
import {
  ChevronDown, ChevronUp, CheckCircle, Loader2,
  Hash, List, Layers, SkipForward, Search, X,
  AlertTriangle, Tag,
} from 'lucide-react'
import clsx from 'clsx'
import axios from 'axios'

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
  domain?: string
}

interface VariableDecision {
  strategy: Strategy
  variable_concept: ConceptRef | null
  value_concepts: Record<string, ConceptRef>
}

interface ColumnInfo {
  distinct_values: string[]
  distinct_count: number
  null_count: number
  total_rows: number
  completion_rate: number
}

// ── Constants ──────────────────────────────────────────────────────────────

const STRATEGY_META: Record<Strategy, { label: string; icon: React.ReactNode; color: string }> = {
  map_variable: { label: 'Map variable', icon: <Hash className="w-3.5 h-3.5" />, color: 'bg-blue-100 text-blue-700' },
  map_values:   { label: 'Map values',   icon: <List className="w-3.5 h-3.5" />, color: 'bg-orange-100 text-orange-700' },
  map_both:     { label: 'Map both',     icon: <Layers className="w-3.5 h-3.5" />, color: 'bg-purple-100 text-purple-700' },
  skip:         { label: 'Skip',         icon: <SkipForward className="w-3.5 h-3.5" />, color: 'bg-gray-100 text-gray-400' },
}


// ── Mini concept search hook ───────────────────────────────────────────────

function useConceptSearch(projectId: string) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<ConceptRef[]>([])
  const [loading, setLoading] = useState(false)
  const [unavailable, setUnavailable] = useState(false)

  const search = async (q?: string) => {
    const term = q ?? query
    if (!term.trim()) return
    setLoading(true)
    setUnavailable(false)
    try {
      const res = await axios.post(`/api/projects/${projectId}/concept-search?query=${encodeURIComponent(term)}&top_k=15`)
      setResults((res.data.conceptlinks || []).map((c: Record<string, unknown>) => ({
        concept_id: c.concept_id,
        concept_name: c.concept_name,
        vocabulary_id: c.vocabulary_id,
        domain: c.domain,
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
  const cs = useConceptSearch(projectId)
  const [manualId, setManualId] = useState('')
  const [manualName, setManualName] = useState('')
  const [showSearch, setShowSearch] = useState(false)

  const applyManual = () => {
    const id = parseInt(manualId)
    if (isNaN(id) || id < 1) return
    onSelect({ concept_id: id, concept_name: manualName || `Concept ${id}` })
    setManualId(''); setManualName('')
  }

  if (value) {
    const isCustom = value.concept_id >= 2_000_000_000
    return (
      <div className={clsx(
        'flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs w-full',
        isCustom ? 'bg-purple-50 border border-purple-200 text-purple-800' : 'bg-green-50 border border-green-200 text-green-800'
      )}>
        <CheckCircle className="w-3.5 h-3.5 flex-shrink-0" />
        <span className="font-semibold">{value.concept_name}</span>
        <span className="text-gray-400 ml-1">({value.concept_id})</span>
        {value.vocabulary_id && <span className="text-gray-400">· {value.vocabulary_id}</span>}
        <button onClick={onClear} className="ml-auto text-gray-300 hover:text-red-500"><X className="w-3 h-3" /></button>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5 w-full">
      {/* Manual entry row */}
      <div className="flex items-center gap-1.5">
        <input
          type="number"
          value={manualId}
          onChange={e => setManualId(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyManual()}
          placeholder="Concept ID"
          className="border border-gray-300 rounded px-2 py-1 text-xs w-24 focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
        <input
          type="text"
          value={manualName}
          onChange={e => setManualName(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && applyManual()}
          placeholder="Name (optional)"
          className="border border-gray-300 rounded px-2 py-1 text-xs flex-1 focus:outline-none focus:ring-1 focus:ring-blue-400"
        />
        <button
          onClick={applyManual}
          disabled={!manualId}
          className="px-2 py-1 text-xs bg-blue-600 text-white rounded disabled:opacity-30 hover:bg-blue-700"
        >Set</button>
        <button
          onClick={() => { setShowSearch(s => !s); if (!cs.query) cs.setQuery(defaultQuery) }}
          className={clsx('px-2 py-1 text-xs rounded border', showSearch ? 'bg-blue-50 border-blue-400 text-blue-700' : 'border-gray-300 text-gray-500 hover:border-blue-400')}
          title="Search EntityLinker"
        >
          <Search className="w-3 h-3" />
        </button>
      </div>

      {/* Search panel */}
      {showSearch && (
        <div className="flex flex-col gap-1.5 pl-1 border-l-2 border-blue-200">
          <div className="flex gap-1.5">
            <input
              type="text"
              value={cs.query}
              onChange={e => cs.setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && cs.search()}
              placeholder={`Search "${defaultQuery}"…`}
              className="flex-1 border border-gray-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
              autoFocus
            />
            <button
              onClick={() => cs.search()}
              disabled={cs.loading}
              className="px-2 py-1 text-xs bg-blue-600 text-white rounded disabled:opacity-40 hover:bg-blue-700"
            >
              {cs.loading ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Go'}
            </button>
          </div>
          {cs.unavailable && (
            <p className="text-xs text-amber-600 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3" /> EntityLinker not running — use manual ID entry
            </p>
          )}
          {cs.results.length > 0 && (
            <div className="border border-gray-200 rounded max-h-36 overflow-y-auto shadow-sm">
              {cs.results.map(c => (
                <button
                  key={c.concept_id}
                  onClick={() => { onSelect(c); setShowSearch(false); cs.clear() }}
                  className="w-full text-left px-2.5 py-2 hover:bg-blue-50 border-b last:border-0 border-gray-100"
                >
                  <span className="text-xs font-medium text-gray-900">{c.concept_name}</span>
                  <span className="text-xs text-gray-400 ml-1.5">{c.concept_id} · {c.domain} · {c.vocabulary_id}</span>
                </button>
              ))}
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

  return (
    <div className="flex flex-col gap-2">
      {values.length > 10 && (
        <div className="flex items-center gap-1.5 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-1.5">
          <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0" />
          {values.length} distinct values — map each one below
        </div>
      )}
      <div className="border border-gray-200 rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="text-left px-3 py-2 font-medium text-gray-600 w-28">Source value</th>
              <th className="text-left px-3 py-2 font-medium text-gray-600">OMOP concept</th>
            </tr>
          </thead>
          <tbody>
            {values.map((val, i) => (
              <tr key={val} className={clsx(i % 2 === 0 ? 'bg-white' : 'bg-gray-50', 'border-b last:border-0 border-gray-100')}>
                <td className="px-3 py-2 font-mono text-gray-700 align-top pt-3">{val}</td>
                <td className="px-3 py-2">
                  <ConceptPicker
                    projectId={projectId}
                    label={val}
                    defaultQuery={`${column} ${val}`}
                    value={mapped[val] ?? null}
                    onSelect={c => set(val, c)}
                    onClear={() => set(val, null)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-gray-400">
        {Object.keys(mapped).length}/{values.length} values mapped
      </p>
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
}: {
  column: string
  info: ColumnInfo | null
  decision: VariableDecision
  projectId: string
  checked: boolean
  onCheck: (c: boolean) => void
  onChange: (d: VariableDecision) => void
  batchMode: boolean
}) {
  const [open, setOpen] = useState(false)

  const sm = STRATEGY_META[decision.strategy]
  const mappedValueCount = Object.keys(decision.value_concepts).length
  const hasVariableConcept = !!decision.variable_concept

  const mappingCompleteness = (() => {
    if (decision.strategy === 'skip') return 100
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

  const isMapped =
    decision.strategy === 'skip' ||
    (decision.strategy === 'map_variable' && hasVariableConcept) ||
    (decision.strategy === 'map_values' && mappedValueCount > 0) ||
    (decision.strategy === 'map_both' && hasVariableConcept)

  const sampleValues = info?.distinct_values.slice(0, 10) ?? []
  const extraCount = (info?.distinct_count ?? 0) - 10

  return (
    <div className={clsx(
      'border rounded-lg overflow-hidden transition-colors',
      checked && 'ring-2 ring-blue-400',
      decision.strategy === 'skip' ? 'border-gray-100' : 'border-gray-200',
      open ? 'shadow-sm' : '',
    )}>
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-2.5 bg-white">
        {batchMode && (
          <input
            type="checkbox"
            checked={checked}
            onChange={e => onCheck(e.target.checked)}
            className="rounded text-blue-600 flex-shrink-0"
            onClick={e => e.stopPropagation()}
          />
        )}

        <button
          className="flex items-center gap-2 flex-1 min-w-0 text-left"
          onClick={() => setOpen(o => !o)}
        >
          <span className="font-mono text-sm font-medium text-gray-800 w-36 flex-shrink-0 truncate">{column}</span>

          <span className={clsx('flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium flex-shrink-0', sm.color)}>
            {sm.icon}{sm.label}
          </span>

          {/* Sample value chips (header preview) */}
          {!open && info && sampleValues.length > 0 && (
            <div className="hidden sm:flex items-center gap-1 flex-1 min-w-0 overflow-hidden">
              {sampleValues.slice(0, 4).map(v => (
                <span key={v} className="bg-gray-100 text-gray-500 px-1.5 py-0 rounded text-xs font-mono whitespace-nowrap">{v}</span>
              ))}
              {(info.distinct_count > 4) && (
                <span className="text-xs text-gray-400">+{info.distinct_count - 4} more</span>
              )}
            </div>
          )}

          <div className="flex items-center gap-2 ml-auto flex-shrink-0">
            {isMapped && <CheckCircle className="w-4 h-4 text-green-500" />}
            {open ? <ChevronUp className="w-4 h-4 text-gray-400" /> : <ChevronDown className="w-4 h-4 text-gray-400" />}
          </div>
        </button>
      </div>

      {/* Expanded body */}
      {open && (
        <div className="border-t border-gray-100 px-4 py-4 flex flex-col gap-5 bg-white">

          {/* Column stats + sample values */}
          {info && (
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-3 gap-3">
                <div className="bg-gray-50 rounded-lg p-2.5 text-center">
                  <p className="text-lg font-bold text-gray-800">{info.distinct_count}</p>
                  <p className="text-xs text-gray-500">Distinct values</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-2.5 text-center">
                  <p className="text-lg font-bold text-gray-800">{info.null_count}</p>
                  <p className="text-xs text-gray-500">Null values</p>
                </div>
                <div className="bg-gray-50 rounded-lg p-2.5 text-center">
                  <p className="text-lg font-bold text-gray-800">{mappingCompleteness}%</p>
                  <p className="text-xs text-gray-500">Completeness</p>
                </div>
              </div>

              <div>
                <p className="text-xs font-medium text-gray-500 mb-1.5">Sample values</p>
                <div className="flex flex-wrap gap-1.5">
                  {sampleValues.map(v => (
                    <span key={v} className="bg-blue-50 text-blue-700 px-2 py-0.5 rounded text-xs font-mono border border-blue-100">{v}</span>
                  ))}
                  {extraCount > 0 && (
                    <span className="text-xs text-gray-400 self-center">… and {extraCount} more</span>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Strategy selector */}
          <div>
            <p className="text-xs font-semibold text-gray-600 mb-2">Mapping strategy</p>
            <div className="grid grid-cols-2 gap-2">
              {(Object.entries(STRATEGY_META) as [Strategy, typeof STRATEGY_META[Strategy]][]).map(([key, meta]) => (
                <button
                  key={key}
                  onClick={() => onChange({ ...decision, strategy: key })}
                  className={clsx(
                    'flex items-center gap-2.5 px-3 py-2.5 rounded-lg border text-left transition-colors',
                    decision.strategy === key ? 'border-blue-500 bg-blue-50' : 'border-gray-200 hover:border-gray-300'
                  )}
                >
                  <span className={clsx('flex-shrink-0', decision.strategy === key ? 'text-blue-600' : 'text-gray-400')}>{meta.icon}</span>
                  <div>
                    <p className="text-xs font-semibold text-gray-800">{meta.label}</p>
                    <p className="text-xs text-gray-400 mt-0.5 leading-tight">
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
              <p className="text-xs font-semibold text-gray-600">Concept for <code className="bg-gray-100 px-1 rounded">{column}</code></p>
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
          {(decision.strategy === 'map_values' || decision.strategy === 'map_both') && info && (
            <div className="flex flex-col gap-1.5">
              <p className="text-xs font-semibold text-gray-600">
                {decision.strategy === 'map_both' ? 'value_as_concept_id for each value' : 'Concept for each (variable, value) pair'}
              </p>
              <ValueMappingTable
                projectId={projectId}
                column={column}
                values={info.distinct_values}
                mapped={decision.value_concepts}
                onChange={vc => onChange({ ...decision, value_concepts: vc })}
              />
            </div>
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
      }
    }
    onApply(updates)
    onClear()
  }

  if (selectedCols.length === 0) return null

  return (
    <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Tag className="w-4 h-4 text-blue-600" />
          <span className="font-semibold text-sm text-blue-900">Batch mapping — {selectedCols.length} variable{selectedCols.length > 1 ? 's' : ''} selected</span>
        </div>
        <button onClick={onClear} className="text-xs text-blue-400 hover:text-blue-700">Deselect all</button>
      </div>

      <div className="flex flex-wrap gap-1.5">
        {selectedCols.map(c => (
          <span key={c} className="bg-white border border-blue-200 text-blue-700 px-2 py-0.5 rounded text-xs font-mono">{c}</span>
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
              strategy === key ? 'border-blue-500 bg-white text-blue-700 shadow-sm' : 'border-blue-100 text-blue-400 hover:border-blue-300'
            )}
          >
            {meta.icon} {meta.label}
          </button>
        ))}
      </div>

      {/* Concept picker for variable */}
      {(strategy === 'map_variable' || strategy === 'map_both') && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-blue-800">Concept to apply to all selected variables:</p>
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
            <p className="text-xs font-medium text-blue-800">Map values (shared by all selected variables):</p>
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
        <label className="flex items-center gap-1.5 text-xs text-blue-700 cursor-pointer">
          <input type="checkbox" checked={overwrite} onChange={e => setOverwrite(e.target.checked)} className="rounded" />
          Overwrite existing mappings
        </label>
        <button
          onClick={apply}
          disabled={
            strategy !== 'skip' &&
            (strategy === 'map_variable' || strategy === 'map_both') && !batchConcept
          }
          className="ml-auto px-4 py-1.5 bg-blue-600 text-white rounded-md text-xs font-medium hover:bg-blue-700 disabled:opacity-40"
        >
          Apply to {selectedCols.length} variable{selectedCols.length > 1 ? 's' : ''}
        </button>
      </div>
    </div>
  )
}

// ── Structural column extractor ────────────────────────────────────────────

function getStructuralColumns(etlConfig: Record<string, unknown>): Set<string> {
  const mapped = new Set<string>()

  // observation_period, location, care_site, provider, death: direct *_col string fields
  for (const table of ['observation_period', 'location', 'care_site', 'provider', 'death']) {
    const cfg = etlConfig[table] as Record<string, unknown> | undefined
    if (!cfg) continue
    for (const [key, value] of Object.entries(cfg)) {
      if (key.endsWith('_col') && typeof value === 'string' && value.trim()) {
        mapped.add(value.trim())
      }
    }
  }

  // person: nested under mappings.*.source_col
  const personCfg = etlConfig['person'] as { mappings?: Record<string, Record<string, unknown>> } | undefined
  if (personCfg?.mappings) {
    for (const m of Object.values(personCfg.mappings)) {
      if (m && typeof m.source_col === 'string' && m.source_col.trim()) {
        mapped.add(m.source_col.trim())
      }
    }
  }

  // visit_occurrence: visit_definitions[].date_col / end_date_col
  const visitCfg = etlConfig['visit_occurrence'] as { visit_definitions?: Array<Record<string, unknown>> } | undefined
  if (visitCfg?.visit_definitions) {
    for (const def of visitCfg.visit_definitions) {
      if (typeof def.date_col === 'string' && def.date_col.trim()) mapped.add(def.date_col.trim())
      if (typeof def.end_date_col === 'string' && def.end_date_col.trim()) mapped.add(def.end_date_col.trim())
    }
  }

  return mapped
}

// ── Main Step 2 page ────────────────────────────────────────────────────────

export default function Step2ConceptMapping({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [decisions, setDecisions] = useState<Record<string, VariableDecision>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState('')

  // Batch state
  const [batchMode, setBatchMode] = useState(false)
  const [selectedCols, setSelectedCols] = useState<string[]>([])

  // Filter
  const [filter, setFilter] = useState<'all' | 'unmapped' | 'mapped' | 'skipped'>('all')
  const [search, setSearch] = useState('')

  const cols = project.source_columns || []

  const structuralCols = useMemo(
    () => getStructuralColumns((project.etl_config || {}) as Record<string, unknown>),
    [project.etl_config],
  )
  const conceptCols = useMemo(() => cols.filter(c => !structuralCols.has(c)), [cols, structuralCols])

  useEffect(() => {
    Promise.all([
      getColumnValues(project.id),
      getConceptDecisions(project.id),
    ]).then(([infos, saved]: [Record<string, ColumnInfo>, Record<string, VariableDecision>]) => {
      setColumnInfos(infos)
      const init: Record<string, VariableDecision> = {}
      for (const col of conceptCols) {
        init[col] = saved[col] ?? { strategy: 'skip', variable_concept: null, value_concepts: {} }
      }
      setDecisions(init)
    }).finally(() => setLoading(false))
  }, [project.id])

  const setDecision = useCallback((col: string, d: VariableDecision) => {
    setDecisions(prev => ({ ...prev, [col]: d }))
  }, [])

  const applyBatch = useCallback((updates: Record<string, VariableDecision>) => {
    setDecisions(prev => ({ ...prev, ...updates }))
  }, [])

  const toggleSelect = (col: string, checked: boolean) => {
    setSelectedCols(prev => checked ? [...prev, col] : prev.filter(c => c !== col))
  }

  const handleNext = async () => {
    setSaving(true)
    setGenError('')
    try {
      await saveConceptDecisions(project.id, decisions as Record<string, unknown>)
      setGenerating(true)
      const updated = await generateMappingCsvs(project.id)
      onUpdate(updated)
      navigate(`/project/${project.id}/step/10`)
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
    if (filter === 'unmapped') return d && d.strategy !== 'skip' && !d.variable_concept && Object.keys(d.value_concepts).length === 0
    return true
  })

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={9}
      onBack={() => navigate(`/project/${project.id}/step/8`)}
      onNext={handleNext}
      nextLabel={generating ? 'Generating CSVs…' : saving ? 'Saving…' : 'Next: Stem Table →'}
      nextDisabled={saving || generating}
      saving={saving}
    >
      <div className="flex flex-col gap-5">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Concept Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Map each source variable to OMOP concepts. Click a variable to expand it and see its values.
            Use batch mode to map multiple variables at once.
          </p>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-3">
          <div className="bg-white border border-gray-200 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-gray-900">{conceptCols.length}</p>
            <p className="text-xs text-gray-500">Variables to map</p>
          </div>
          <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-green-700">{mappedCount}</p>
            <p className="text-xs text-green-600">Mapped</p>
          </div>
          <div className="bg-gray-50 border border-gray-200 rounded-lg p-3 text-center">
            <p className="text-xl font-bold text-gray-500">{skippedCount}</p>
            <p className="text-xs text-gray-500">Skipped</p>
          </div>
        </div>

        {/* Excluded structural columns */}
        {structuralCols.size > 0 && (
          <div className="flex items-start gap-2 px-3 py-2.5 bg-blue-50 border border-blue-200 rounded-lg text-xs text-blue-800">
            <CheckCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 text-blue-500" />
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
            className="border border-gray-300 rounded-md px-3 py-1.5 text-sm w-44 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <div className="flex gap-1">
            {(['all', 'unmapped', 'mapped', 'skipped'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={clsx('px-2.5 py-1.5 rounded-md text-xs font-medium capitalize', filter === f ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200')}
              >{f}</button>
            ))}
          </div>
          <button
            onClick={() => { setBatchMode(b => !b); setSelectedCols([]) }}
            className={clsx('ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border', batchMode ? 'bg-blue-600 text-white border-blue-600' : 'border-gray-300 text-gray-600 hover:bg-gray-50')}
          >
            <Tag className="w-3.5 h-3.5" />
            {batchMode ? 'Exit batch mode' : 'Batch mode'}
          </button>
          {batchMode && filteredCols.length > 0 && (
            <button
              onClick={() => setSelectedCols(filteredCols)}
              className="text-xs text-blue-600 hover:underline"
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
          <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-sm text-red-700">{genError}</div>
        )}

        {loading && (
          <div className="flex items-center gap-2 py-8 justify-center text-gray-400">
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
                decision={decisions[col] ?? { strategy: 'map_variable', variable_concept: null, value_concepts: {} }}
                projectId={project.id}
                checked={selectedCols.includes(col)}
                onCheck={c => toggleSelect(col, c)}
                onChange={d => setDecision(col, d)}
                batchMode={batchMode}
              />
            ))}
            {filteredCols.length === 0 && (
              <p className="text-center text-sm text-gray-400 py-8">No columns match the current filter.</p>
            )}
          </div>
        )}

        <div className="bg-blue-50 border border-blue-100 rounded-lg p-3 text-xs text-blue-700">
          Clicking <strong>Next</strong> saves all decisions and auto-generates the 3 concept mapping CSV files used by the ETL engine.
        </div>
      </div>
    </WizardLayout>
  )
}
