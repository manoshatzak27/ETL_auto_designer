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

/**
 * "Auto-match columns" for a wizard step: every unmapped field is matched
 * against the source columns; confident matches are applied, weaker ones are
 * kept as suggestions (pass `suggestions[target.key]` to that field's
 * FieldMapper), the rest stay empty.
 */
export function useColumnAutoMatch(projectId: string, table: string) {
  const [llm, setLlm] = useState<LlmStatus | null>(null)
  const [checking, setChecking] = useState(false)
  const [useLlm, setUseLlmState] = useState(readUseLlm)
  const [running, setRunning] = useState(false)
  const [summary, setSummary] = useState<ColumnAutoMatchSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Record<string, ColumnMatch>>({})
  // The fields of the last run, so a suggestion can be applied with its own
  // handler (and date format) later.
  const targetsRef = useRef<Record<string, ColumnTarget>>({})

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
  }) => {
    const todo = targets.filter(t => !t.current)
    if (todo.length === 0) return
    targetsRef.current = Object.fromEntries(todo.map(t => [t.key, t]))
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
      setSuggestions(prev => {
        const next = { ...prev }
        for (const t of todo) delete next[t.key]
        return { ...next, ...offered }
      })
      setSummary({
        filled,
        suggested: Object.keys(offered).length,
        missing,
        aiUsed: res.llm_used,
        aiError: res.llm_error,
      })
      // A failed AI call changes what the badge should say.
      if (res.llm_error) checkLlm()
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail
      setError(detail || 'Auto-match failed')
    } finally {
      setRunning(false)
    }
  }

  /** Forget suggestions (all, or one field's) — call when the active file
   *  changes, since they belong to the old file's columns. */
  const clear = useCallback((key?: string) => {
    if (key === undefined) {
      setSuggestions({})
      setSummary(null)
      setError(null)
    } else {
      setSuggestions(prev => {
        if (!(key in prev)) return prev
        const next = { ...prev }
        delete next[key]
        return next
      })
    }
  }, [])

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
    running, summary, error, suggestions, run, clear, accept,
  }
}

export type ColumnAutoMatch = ReturnType<typeof useColumnAutoMatch>
