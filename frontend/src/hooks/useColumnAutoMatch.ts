import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getColumnMatcherHealth,
  suggestColumnMapping,
  type ColumnMatch,
  type ColumnMatchField,
  type LlmStatus,
} from '../api/client'

/** One field of a step whose source column auto-match can pick. */
export interface ColumnTarget extends ColumnMatchField {
  label: string
  /** The column mapped now; only empty fields are matched, so never changed. */
  current: string
  /** Map the field to a column — the same handler a manual pick goes through,
   *  so whatever follows a pick (value lists, cleared value maps) still does.
   *  `dateFormat`, for date fields, is the step date format to set with it. */
  apply: (column: string, filename: string, dateFormat: string | null) => void
}

export interface ColumnAutoMatchSummary {
  filled: { label: string; column: string; ai: boolean; dateFormat: string | null; warning: string | null }[]
  suggested: number
  missing: number
  aiUsed: boolean
  aiError: string | null
}

const USE_LLM_KEY = 'columnAutoMatch.useLlm'

function readUseLlm(): boolean {
  try {
    return localStorage.getItem(USE_LLM_KEY) !== 'false'
  } catch {
    return true
  }
}

type Suggestions = Record<string, ColumnMatch>

const NO_SUGGESTIONS: Suggestions = {}

// Suggestions outlive the step component: "Auto-map all steps" leaves each
// step (and each of its files) as soon as it has saved, and they must still be
// there when the user opens it. Keyed by project, table and file.
const savedSuggestions = new Map<string, Suggestions>()

/**
 * "Auto-match columns" for a wizard step: every unmapped field is matched
 * against the source columns; confident matches are applied, weaker ones are
 * kept as suggestions (pass `suggestions[target.key]` to that field's
 * FieldMapper), the rest stay empty. `scope` is the step's active file — each
 * file keeps its own suggestions; '' for a step with one view.
 */
export function useColumnAutoMatch(projectId: string, table: string, scope = '') {
  const [llm, setLlm] = useState<LlmStatus | null>(null)
  const [checking, setChecking] = useState(false)
  const [useLlm, setUseLlmState] = useState(readUseLlm)
  const [running, setRunning] = useState(false)
  const [summary, setSummary] = useState<ColumnAutoMatchSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [, setVersion] = useState(0)
  const storeKey = `${projectId}\u0000${table}\u0000${scope}`
  const suggestions = savedSuggestions.get(storeKey) ?? NO_SUGGESTIONS
  // The step's current fields, so a suggestion is applied with its own
  // handler (and date format) — see setTargets.
  const targetsRef = useRef<Record<string, ColumnTarget>>({})

  const updateSuggestions = useCallback((key: string, update: (prev: Suggestions) => Suggestions) => {
    const prev = savedSuggestions.get(key) ?? NO_SUGGESTIONS
    const next = update(prev)
    if (next === prev) return
    if (Object.keys(next).length === 0) savedSuggestions.delete(key)
    else savedSuggestions.set(key, next)
    setVersion(v => v + 1)
  }, [])

  const checkLlm = useCallback((refresh = false) => {
    setChecking(true)
    getColumnMatcherHealth(refresh)
      .then(setLlm)
      .catch(() => setLlm({ status: 'error', model: '', detail: 'Backend unreachable' }))
      .finally(() => setChecking(false))
  }, [])

  useEffect(() => { checkLlm() }, [checkLlm])

  const setUseLlm = (on: boolean) => {
    setUseLlmState(on)
    try { localStorage.setItem(USE_LLM_KEY, String(on)) } catch { /* private window */ }
  }

  const llmReady = llm?.status === 'ready'

  const run = async ({ filenames, targets, exclude }: {
    filenames: string[]
    targets: ColumnTarget[]
    exclude: Iterable<string>
  }): Promise<ColumnAutoMatchSummary | null> => {
    const todo = targets.filter(t => !t.current)
    if (todo.length === 0) return null
    // The file this run is for — auto-run may have moved on when it returns.
    const runKey = storeKey
    setRunning(true)
    setError(null)
    setSummary(null)
    try {
      const res = await suggestColumnMapping(projectId, {
        table,
        filenames,
        fields: todo.map(({ key, spec, hint, filenames, date_format, format_group, format_locked }) =>
          ({ key, spec, hint, filenames, date_format, format_group, format_locked })),
        exclude_columns: [...new Set(exclude)].filter(Boolean),
        use_llm: useLlm && llmReady,
      })
      const filled: ColumnAutoMatchSummary['filled'] = []
      const offered: Record<string, ColumnMatch> = {}
      let missing = 0
      for (const t of todo) {
        const m = res.matches[t.key]
        if (!m?.column) { missing++; continue }
        if (m.status === 'auto') {
          t.apply(m.column, m.filename ?? '', m.date_format)
          filled.push({ label: t.label, column: m.column, ai: m.source === 'llm', dateFormat: m.date_format, warning: m.warning })
        } else {
          offered[t.key] = m
        }
      }
      updateSuggestions(runKey, prev => {
        const next = { ...prev }
        for (const t of todo) delete next[t.key]
        return { ...next, ...offered }
      })
      const result: ColumnAutoMatchSummary = {
        filled,
        suggested: Object.keys(offered).length,
        missing,
        aiUsed: res.llm_used,
        aiError: res.llm_error,
      }
      setSummary(result)
      // A failed AI call changes what the badge should say.
      if (res.llm_error) checkLlm()
      return result
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || 'Auto-match failed')
      throw new Error(detail || 'Auto-match failed', { cause: e })
    } finally {
      setRunning(false)
    }
  }

  /** Forget the active file's suggestions (all, or one field's) — e.g. when
   *  the keys they are stored under no longer mean the same fields. Another
   *  file's are kept: switching files needs no clear. */
  const clear = useCallback((key?: string) => {
    if (key === undefined) {
      updateSuggestions(storeKey, () => NO_SUGGESTIONS)
      setSummary(null)
      setError(null)
    } else {
      updateSuggestions(storeKey, prev => {
        if (!(key in prev)) return prev
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }, [storeKey, updateSuggestions])

  /** The step's fields as of this render — call on every render with the same
   *  targets passed to `run`, so `accept` applies a suggestion through the
   *  current handlers, also after the step was left and opened again. */
  const setTargets = (targets: ColumnTarget[]) => {
    targetsRef.current = Object.fromEntries(targets.map(t => [t.key, t]))
  }

  /** Take a field's suggestion — the column, and the date format with it. */
  const accept = (key: string) => {
    const m = suggestions[key]
    const t = targetsRef.current[key]
    if (!m?.column || !t) return
    t.apply(m.column, m.filename ?? '', m.date_format)
    clear(key)
  }

  return {
    llm, llmReady, checking, checkLlm, useLlm, setUseLlm,
    running, summary, error, suggestions, run, clear, setTargets, accept,
  }
}

export type ColumnAutoMatch = ReturnType<typeof useColumnAutoMatch>
