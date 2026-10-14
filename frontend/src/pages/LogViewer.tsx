/**
 * Full-page reader for the last pipeline run's log, opened in its own window
 * from the Finalize step. The whole log (after the filters) scrolls as one
 * list, but it can be millions of lines, so only the lines on screen are
 * rendered and they're fetched from the backend in chunks as they scroll into
 * view. Every filter lives in the URL, so a reload or a shared link shows the
 * same lines.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { ArrowDownToLine, ArrowUpToLine, Download, Loader2, Search } from 'lucide-react'
import clsx from 'clsx'
import {
  downloadExecutionLog,
  getExecutionLogLines,
  getExecutionStats,
  getProject,
  type ExecutionTableStats,
  type LogKind,
  type LogView,
} from '../api/client'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'

type Line = { n: number; text: string }

// Lines fetched per request, and how many such chunks are kept in memory.
const CHUNK = 1000
const MAX_CHUNKS = 60
// Fixed height of a line in px. Browsers cap an element's height (~33M px in
// Chrome), so past MAX_SCROLL_PX the scrollbar maps proportionally onto lines.
const LINE_H = 20
const MAX_SCROLL_PX = 10_000_000

const VIEWS: [LogView, string, string][] = [
  ['basic', 'Basic', 'Dropped rows only'],
  ['detailed', 'Detailed', 'Dropped rows, plus written rows that have empty or unmapped fields'],
]

const KINDS: [LogKind, string][] = [
  ['all', 'All lines'],
  ['dropped', 'Dropped rows only'],
  ['notes', 'Empty/unmapped field notes only'],
]

// Colour of a log line by what it reports.
function lineClass(text: string) {
  if (text.startsWith('[TABLE:') || /^=+$/.test(text)) return 'text-sky-300 font-semibold'
  if (text.startsWith('ERROR:') || text.startsWith('Traceback') || /\bFAILED\b/.test(text)) return 'text-red-400'
  if (text.startsWith('WARNING:')) return 'text-amber-300'
  if (text.startsWith('INFO:')) return 'text-gray-400'
  return 'text-green-400'
}

// `text` with every case-insensitive occurrence of `needle` marked.
function highlight(text: string, needle: string) {
  if (!needle) return text
  const lower = text.toLowerCase()
  const parts: React.ReactNode[] = []
  let from = 0
  for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, from)) {
    parts.push(text.slice(from, at), <mark key={at} className="bg-yellow-300 text-gray-950 rounded-sm">{text.slice(at, at + needle.length)}</mark>)
    from = at + needle.length
  }
  parts.push(text.slice(from))
  return parts
}

export default function LogViewer() {
  const { projectId = '' } = useParams<{ projectId: string }>()
  const [params, setParams] = useSearchParams()
  const view: LogView = params.get('view') === 'basic' ? 'basic' : 'detailed'
  const table = params.get('table') ?? ''
  const rawKind = params.get('kind') as LogKind | null
  // Basic logs have no field notes, so that filter falls back to all lines.
  const kind: LogKind = rawKind && KINDS.some(([k]) => k === rawKind) && !(view === 'basic' && rawKind === 'notes') ? rawKind : 'all'
  const q = params.get('q') ?? ''
  const filterKey = JSON.stringify([view, table, kind, q])

  const [projectName, setProjectName] = useState('')
  const [stats, setStats] = useState<ExecutionTableStats[]>([])
  const [search, setSearch] = useState(q)
  // Lines that pass the filters, in all; null until the first chunk arrives.
  const [total, setTotal] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(0)
  // Bumped when a chunk arrives, to re-render with it.
  const [, setChunkVersion] = useState(0)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportH, setViewportH] = useState(0)
  const chunks = useRef(new Map<number, Line[]>())
  const inFlight = useRef(new Set<number>())
  const currentKey = useRef(filterKey)
  const scrollRef = useRef<HTMLDivElement>(null)

  const update = (changes: Record<string, string>) => {
    setParams(prev => {
      const next = new URLSearchParams(prev)
      for (const [k, v] of Object.entries(changes)) {
        if (v) next.set(k, v)
        else next.delete(k)
      }
      return next
    })
  }

  useEffect(() => {
    getProject(projectId).then(p => setProjectName(p.name)).catch(() => {})
    getExecutionStats(projectId)
      .then(s => setStats(s.filter(t => t.status === 'ok' || t.status === 'failed')))
      .catch(() => {})
  }, [projectId])

  useEffect(() => {
    document.title = `Pipeline messages${projectName ? ` — ${projectName}` : ''}`
  }, [projectName])

  // Typing in the search box applies after a short pause.
  useEffect(() => {
    if (search === q) return
    const timer = setTimeout(() => update({ q: search }), 350)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search])

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const observer = new ResizeObserver(() => setViewportH(el.clientHeight))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const loadChunk = useCallback((c: number) => {
    if (chunks.current.has(c) || inFlight.current.has(c)) return
    const key = currentKey.current
    inFlight.current.add(c)
    setPending(n => n + 1)
    getExecutionLogLines(projectId, { view, table, kind, q, offset: c * CHUNK, limit: CHUNK })
      .then(d => {
        if (key !== currentKey.current) return
        chunks.current.set(c, d.lines)
        // Drop the oldest chunks; they're fetched again if scrolled back to.
        while (chunks.current.size > MAX_CHUNKS) {
          chunks.current.delete(chunks.current.keys().next().value!)
        }
        setTotal(d.total)
        setError('')
        setChunkVersion(v => v + 1)
      })
      .catch(e => {
        if (key !== currentKey.current) return
        setError(e?.response?.data?.detail ?? 'Could not load the log.')
      })
      .finally(() => {
        if (key === currentKey.current) inFlight.current.delete(c)
        setPending(n => n - 1)
      })
  }, [projectId, view, table, kind, q])

  // New filters: start over from the top.
  useEffect(() => {
    currentKey.current = filterKey
    chunks.current = new Map()
    inFlight.current = new Set()
    setTotal(null)
    setError('')
    setScrollTop(0)
    scrollRef.current?.scrollTo({ top: 0 })
    loadChunk(0)
  }, [filterKey, loadChunk])

  // Which lines are on screen. Up to MAX_SCROLL_PX a line sits at its own
  // offset; past it, the scroll position picks the first line proportionally
  // and the visible lines are drawn at the scroll position.
  const count = total ?? 0
  const fullH = count * LINE_H
  const scaled = fullH > MAX_SCROLL_PX
  const spacerH = scaled ? MAX_SCROLL_PX : fullH
  const onScreen = Math.ceil(viewportH / LINE_H) + 1
  let first: number
  let top: number
  if (scaled) {
    const range = Math.max(1, spacerH - viewportH)
    const maxFirst = Math.max(0, count - Math.floor(viewportH / LINE_H))
    first = Math.min(maxFirst, Math.round((scrollTop / range) * maxFirst))
    top = scrollTop
  } else {
    first = Math.floor(scrollTop / LINE_H)
    top = first * LINE_H
  }
  const last = Math.min(count, first + onScreen)

  useEffect(() => {
    if (!count) return
    for (let c = Math.floor(first / CHUNK); c <= Math.floor(Math.max(first, last - 1) / CHUNK); c++) loadChunk(c)
  }, [first, last, count, loadChunk])

  const lineAt = (i: number): Line | undefined => chunks.current.get(Math.floor(i / CHUNK))?.[i % CHUNK]
  const scrollToLine = (i: number) => {
    const el = scrollRef.current
    if (!el) return
    if (!scaled) el.scrollTo({ top: i * LINE_H })
    else {
      const maxFirst = Math.max(1, count - Math.floor(viewportH / LINE_H))
      el.scrollTo({ top: (i / maxFirst) * (spacerH - viewportH) })
    }
  }

  const needle = q.toLowerCase()
  const numberWidth = `${String(lineAt(Math.max(0, last - 1))?.n ?? count).length + 1}ch`
  const rows: React.ReactNode[] = []
  for (let i = first; i < last; i++) {
    const l = lineAt(i)
    rows.push(
      <div key={i} className="flex hover:bg-white/5" style={{ height: LINE_H, lineHeight: `${LINE_H}px` }}>
        <span className="select-none text-right text-gray-600 pl-4 pr-3 tabular-nums flex-shrink-0 box-content" style={{ width: numberWidth }}>
          {l?.n ?? ''}
        </span>
        <span className={clsx('pr-4 whitespace-pre', l ? lineClass(l.text) : 'text-gray-700')}>
          {l ? highlight(l.text, needle) : '…'}
        </span>
      </div>,
    )
  }

  return (
    <div className="h-screen flex flex-col bg-background">
      {/* Header + filters */}
      <header className="border-b border-border bg-card px-4 py-3 flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-base font-semibold text-foreground">Pipeline messages</h1>
          {projectName && <span className="text-sm text-muted-foreground">{projectName}</span>}
          <span className="text-xs text-muted-foreground">
            What each table's script reported in the last run: dropped rows, and in the detailed log also empty or unmapped fields on rows that were written.
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <div className="inline-flex rounded-lg border border-border p-0.5 bg-muted/50">
            {VIEWS.map(([value, label, hint]) => (
              <button
                key={value}
                type="button"
                title={hint}
                onClick={() => update({ view: value })}
                className={clsx(
                  'px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
                  view === value ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground hover:text-foreground',
                )}
              >
                {label}
              </button>
            ))}
          </div>

          <Select value={table} onChange={e => update({ table: e.target.value })} className="w-64">
            <option value="">All tables</option>
            {stats.map(s => (
              <option key={s.table} value={s.table}>
                {s.table}{s.dropped ? ` — ${s.dropped.toLocaleString()} dropped` : ''}{s.status === 'failed' ? ' (failed)' : ''}
              </option>
            ))}
          </Select>

          <Select value={kind} onChange={e => update({ kind: e.target.value === 'all' ? '' : e.target.value })} className="w-64">
            {KINDS.map(([value, label]) => (
              <option key={value} value={value} disabled={view === 'basic' && value === 'notes'}>{label}</option>
            ))}
          </Select>

          <div className="relative w-72">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search (e.g. a person id or column)"
              className="pl-9"
            />
          </div>

          <Button variant="outline" size="sm" className="ml-auto" onClick={() => downloadExecutionLog(projectId, view)}>
            <Download className="w-3.5 h-3.5" /> Download {view} log
          </Button>
        </div>
      </header>

      {/* Lines: the whole filtered log as one scrolling list */}
      <div className="flex-1 min-h-0 relative">
        <div
          ref={scrollRef}
          onScroll={e => setScrollTop(e.currentTarget.scrollTop)}
          className="absolute inset-0 overflow-auto bg-gray-950 font-mono text-xs"
        >
          {error ? (
            <p className="p-6 text-sm text-red-400 font-sans">{error}</p>
          ) : total === 0 ? (
            <p className="p-6 text-sm text-gray-400 font-sans">No lines match these filters.</p>
          ) : (
            <div className="relative min-w-full w-max" style={{ height: spacerH }}>
              <div className="absolute left-0 min-w-full" style={{ top, overflowY: 'clip', maxHeight: spacerH - top }}>
                {rows}
              </div>
            </div>
          )}
        </div>
        {pending > 0 && (
          <div className="absolute top-3 right-6 flex items-center gap-1.5 text-xs text-gray-300 bg-gray-800/90 rounded-md px-2.5 py-1.5">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
          </div>
        )}
      </div>

      <footer className="border-t border-border bg-card px-4 py-2 flex items-center gap-2 text-xs text-muted-foreground">
        <span>
          {total
            ? `Lines ${(first + 1).toLocaleString()}–${last.toLocaleString()} of ${total.toLocaleString()} matching`
            : total === 0 ? 'No matching lines' : ''}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <Button variant="outline" size="sm" disabled={!total || first === 0} onClick={() => scrollToLine(0)} title="Go to the first line">
            <ArrowUpToLine className="w-3.5 h-3.5" /> Top
          </Button>
          <Button variant="outline" size="sm" disabled={!total || last >= count} onClick={() => scrollToLine(count)} title="Go to the last line">
            <ArrowDownToLine className="w-3.5 h-3.5" /> Bottom
          </Button>
        </div>
      </footer>
    </div>
  )
}
