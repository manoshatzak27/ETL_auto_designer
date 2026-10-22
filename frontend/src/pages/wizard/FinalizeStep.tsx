// Merged final step: Generate → Execute → Load Vocab → Load DB.
//
// Merged from the old Step11_Generate.tsx and Step12_LoadDB.tsx into one
// scrollable page with sticky section headers. No new API calls — every
// control here uses the same endpoints those two pages used.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  executeProject,
  downloadOutput,
  downloadExecutionLog,
  openLogViewer,
  getExecutionLogPreview,
  getExecutionStats,
  getDbHealth,
  getLoadStatus,
  getOutputPreview,
  generateTableScript,
  loadDatabase,
  type DbHealth,
  type ExecutionLogPreview,
  type ExecutionTableStats,
  type ExecutionTableRow,
  type LoadStatus,
  type OutputPreview,
} from '../../api/client'
import type { Project } from '../../types'
import WizardLayout from './WizardLayout'
import ScriptGenerator from '../../components/ScriptGenerator'
import LogStream, { RunStatusBadge } from '../../components/LogStream'
import ErrorBanner from '../../components/ErrorBanner'
import ExecutionStatsTable from '../../components/ExecutionStatsTable'
import OmopTablePreview from '../../components/OmopTablePreview'
import { useConfirm } from '../../contexts/confirm'
import { chatEditsOverwritten, chatEditsWarning } from '../../components/chatEdits'
import { useGeneration } from '../../context/GenerationContext'
import { getAdjacentSlugs, OPTIONAL_TABLES, isOptionalTableEnabled, type OptionalTable } from '../../wizard/steps'
import {
  PlayCircle, Download, RefreshCw, CheckCircle, AlertCircle, AlertTriangle,
  Database, Loader2, Server, Code2, Play, Eye, ExternalLink, Sparkles,
} from 'lucide-react'
import { basename } from '../../utils'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Select } from '@/components/ui/select'
import clsx from 'clsx'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

// Universe of OMOP tables this page can generate. The visible subset is
// computed at render time from the project's etl_config so optional tables
// the user didn't tick on the Source step don't appear here either.
// Split a run log into each table's section. A section opens with the
// executor's banner (blank line, rule, "[TABLE: x] Starting...", rule); the
// blank line and rule before "Starting" belong to the section they open.
// Mirrors _sections in backend/app/api/execution.py.
function splitLogByTable(log: string): { tables: string[]; byTable: Record<string, string> } {
  const byTable: Record<string, string[]> = {}
  const tables: string[] = []
  let current = ''
  let pending: string[] = []
  for (const line of log.split(/(?<=\n)/)) {
    const stripped = line.replace(/\n$/, '')
    const m = /^\[TABLE: (\w+)\] Starting\.\.\.$/.exec(stripped)
    if (m) {
      current = m[1]
      if (!byTable[current]) { byTable[current] = []; tables.push(current) }
    } else if (stripped === '' || /^=+$/.test(stripped)) {
      pending.push(line)
      continue
    }
    if (current) byTable[current].push(...pending, line)
    pending = []
  }
  if (current) byTable[current].push(...pending)
  return { tables, byTable: Object.fromEntries(tables.map(t => [t, byTable[t].join('')])) }
}

// Order the pipeline runs tables in (etl_executor.EXECUTION_ORDER).
const EXECUTION_ORDER = [
  'location', 'care_site', 'provider', 'person', 'visit_occurrence', 'observation_period',
  'stem_table', 'death',
  'measurement', 'observation', 'drug_exposure', 'procedure_occurrence', 'condition_occurrence',
]

// Lines of the detailed log shown on the page; the full log is a download.
const LOG_PREVIEW_LINES = 5000
// Script output lines per table kept in the basic log (etl_executor._LINES_PER_TABLE).
const BASIC_LINES_PER_TABLE = 500

const ALL_TABLES = [
  { key: 'location',           label: 'location.py',           description: 'Physical address / location records' },
  { key: 'care_site',          label: 'care_site.py',          description: 'Institutional care site records' },
  { key: 'provider',           label: 'provider.py',           description: 'Healthcare provider records' },
  { key: 'person',             label: 'person.py',             description: 'Patient demographics' },
  { key: 'visit_occurrence',   label: 'visit_occurrence.py',   description: 'Clinical visits / timepoints' },
  { key: 'observation_period', label: 'observation_period.py', description: 'Patient observation windows' },
  { key: 'stem_table',         label: 'stem_table.py',         description: 'Clinical measurements & observations' },
  { key: 'death',              label: 'death.py',              description: 'Mortality records' },
] as const

function getActiveTables(project: Project): readonly (typeof ALL_TABLES)[number][] {
  return ALL_TABLES.filter(t => {
    if (!(OPTIONAL_TABLES as readonly string[]).includes(t.key)) return true
    return isOptionalTableEnabled(project, t.key as OptionalTable)
  })
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { bg: string; text: string; label: string }> = {
    idle:     { bg: 'bg-gray-100',  text: 'text-gray-600',  label: 'idle' },
    pending:  { bg: 'bg-gray-100',  text: 'text-gray-600',  label: 'pending' },
    loading:  { bg: 'bg-blue-100',  text: 'text-blue-700',  label: 'loading' },
    running:  { bg: 'bg-blue-100',  text: 'text-blue-700',  label: 'running' },
    success:  { bg: 'bg-green-100', text: 'text-green-700', label: 'success' },
    skipped:  { bg: 'bg-amber-100', text: 'text-amber-700', label: 'skipped' },
    error:    { bg: 'bg-red-100',   text: 'text-red-700',   label: 'error' },
  }
  const cfg = map[status] ?? map.idle
  return (
    <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full', cfg.bg, cfg.text)}>
      {cfg.label}
    </span>
  )
}

function HealthRow({ label, ok, value }: { label: string; ok: boolean; value?: string }) {
  return (
    <div className="bg-gray-50 rounded-lg p-3">
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="font-medium flex items-center gap-1.5 text-sm">
        {ok
          ? <CheckCircle className="w-3.5 h-3.5 text-green-500" />
          : <AlertCircle className="w-3.5 h-3.5 text-amber-500" />}
        {value ?? (ok ? 'Yes' : 'No')}
      </p>
    </div>
  )
}

function SectionHeader({ icon: Icon, n, title, subtitle }: { icon: React.ComponentType<{ className?: string }>; n: number; title: string; subtitle?: string }) {
  return (
    <div className="sticky top-0 z-10 -mx-6 px-6 py-3 bg-background/95 backdrop-blur border-b border-border">
      <div className="flex items-center gap-2.5">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-bold">{n}</span>
        <Icon className="w-4 h-4 text-muted-foreground" />
        <h2 className="text-base font-bold text-foreground">{title}</h2>
      </div>
      {subtitle && <p className="text-xs text-muted-foreground mt-1 pl-9">{subtitle}</p>}
    </div>
  )
}

export default function FinalizeStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const { prev } = getAdjacentSlugs(project, 'finalize')

  // ── Generate / Execute state ─────────────────────────────────────────────
  const [executing, setExecuting] = useState(false)
  // Hydrate from the project prop so the log survives navigating away and back
  // to this step (project state lives in the parent wizard and isn't reset
  // when this component unmounts/remounts).
  const [execResult, setExecResult] = useState<{ status: string; log: string; output_files: string[] } | null>(
    () => project.last_execution_log
      ? { status: project.last_execution_status, log: project.last_execution_log, output_files: project.output_files || [] }
      : null,
  )
  const [execError, setExecError] = useState('')
  // Every run captures both views: Basic is the log stored on the project,
  // Detailed is a preview of the run's full log file, fetched on demand.
  const [logView, setLogView] = useState<'basic' | 'detailed'>('basic')
  // Which table's section of the log to show; '' = the whole run.
  const [logTable, setLogTable] = useState('')
  // Detailed previews keyed by logTable, fetched on demand.
  const [detailedLogs, setDetailedLogs] = useState<Record<string, ExecutionLogPreview | { error: string } | null>>({})
  const detailedLog = detailedLogs[logTable]

  useEffect(() => {
    if (logView !== 'detailed' || logTable in detailedLogs || executing || !execResult) return
    setDetailedLogs(prev => ({ ...prev, [logTable]: null }))
    getExecutionLogPreview(project.id, LOG_PREVIEW_LINES, logTable)
      .then(preview => setDetailedLogs(prev => ({ ...prev, [logTable]: preview })))
      .catch((e: unknown) => {
        const err = e as { response?: { data?: { detail?: string } } }
        const error = err?.response?.data?.detail || 'Could not load the detailed log.'
        setDetailedLogs(prev => ({ ...prev, [logTable]: { error } }))
      })
  }, [logView, logTable, detailedLogs, executing, execResult, project.id])

  const basicSections = useMemo(() => splitLogByTable(execResult?.log ?? ''), [execResult?.log])

  // Per-table counts of the last run (or of the one in progress); null until
  // fetched, [] when the run left none (a run from before stats were recorded,
  // or a request that failed).
  const [runStats, setRunStats] = useState<ExecutionTableStats[] | null>(null)

  useEffect(() => {
    if (runStats || executing || !execResult) return
    getExecutionStats(project.id).then(setRunStats).catch(() => setRunStats([]))
  }, [runStats, executing, execResult, project.id])

  // While the pipeline runs, poll its counts so each table's row fills in as
  // it finishes instead of all at once at the end.
  useEffect(() => {
    if (!executing) return
    const id = setInterval(() => {
      getExecutionStats(project.id, true).then(setRunStats).catch(() => {})
    }, 1500)
    return () => clearInterval(id)
  }, [executing, project.id])

  // Only count / regenerate the tables the user enabled on the Source step.
  const activeTables = getActiveTables(project)
  const scripts: Record<string, string> = project.generated_scripts || {}
  const generatedCount = activeTables.filter(t => scripts[t.key]).length
  const allGenerated   = generatedCount === activeTables.length
  const anyGenerated   = generatedCount > 0
  const missingTables  = activeTables.filter(t => !scripts[t.key]).map(t => t.key)
  const staleTables    = activeTables.filter(t => scripts[t.key] && project.stale_scripts?.includes(t.key)).map(t => t.key)

  // ── Generate several scripts in one go ───────────────────────────────────
  const confirm = useConfirm()
  const { isAnyGenerating, acquireLock, releaseLock } = useGeneration()
  const [bulk, setBulk] = useState<{ done: number; total: number; current: string } | null>(null)
  const [bulkFailures, setBulkFailures] = useState<{ table: string; error: string }[]>([])

  const generateMany = async (tables: string[]) => {
    if (tables.length === 0) return
    const overwritten = chatEditsOverwritten(project, tables)
    if (overwritten.length > 0 && !(await confirm(chatEditsWarning(overwritten)))) return
    if (!acquireLock()) return
    setBulkFailures([])
    const failures: { table: string; error: string }[] = []
    try {
      for (let i = 0; i < tables.length; i++) {
        setBulk({ done: i, total: tables.length, current: tables[i] })
        try {
          // Each response is the whole project, so the last one carries every script.
          onUpdate(await generateTableScript(project.id, tables[i]))
        } catch (e: unknown) {
          const err = e as { response?: { data?: { detail?: string } } }
          failures.push({ table: tables[i], error: err?.response?.data?.detail || 'Generation failed.' })
        }
      }
    } finally {
      setBulk(null)
      setBulkFailures(failures)
      releaseLock()
    }
  }

  const handleExecute = async () => {
    if ((staleTables.length > 0 || project.mapping_files_stale) && !(await confirm({
      title: 'Run with out-of-date inputs?',
      description: (
        <>
          {staleTables.length > 0 && (
            <p>
              The settings changed since these scripts were generated, so they would run the old
              mapping: <span className="font-mono text-foreground">{staleTables.map(t => `${t}.py`).join(', ')}</span>.
              Use "Regenerate outdated" first to run the current settings.
            </p>
          )}
          {project.mapping_files_stale && (
            <p>
              The concept mapping CSVs were generated from earlier Concepts decisions. Press Next on
              the Concepts step to rebuild them.
            </p>
          )}
        </>
      ),
      confirmLabel: 'Run anyway',
      tone: 'default',
    }))) return
    setExecuting(true)
    setExecError('')
    setRunStats(null)
    try {
      const result = await executeProject(project.id)
      setExecResult(result)
      setDetailedLogs({})
      setRunStats(result.stats ?? null)
      onUpdate({ ...project, last_execution_status: result.status, last_execution_log: result.log, output_files: result.output_files })
      // Bust cached previews so re-execution shows fresh data
      setPreviewCache({})
      setActivePreviewTab(null)
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      const msg = err?.response?.data?.detail || 'Execution failed.'
      setExecError(msg)
      setExecResult({ status: 'error', log: msg, output_files: [] })
      setDetailedLogs({})
      setRunStats(null)
      onUpdate({ ...project, last_execution_status: 'error', last_execution_log: msg })
    } finally {
      setExecuting(false)
    }
  }

  // Rows of the stats table: the last run's counts, or — before a run, while
  // one is in progress, or for a run from before counts were recorded — the
  // tables the pipeline would run (same filter as the execute endpoint).
  const statsRows: ExecutionTableRow[] = useMemo(() => {
    if (runStats && runStats.length > 0) return runStats
    const config = (project.etl_config || {}) as Record<string, { enabled?: boolean } | undefined>
    const status = executing || !execResult ? 'not_run' : 'unknown'
    return EXECUTION_ORDER
      .filter(t => scripts[t] && config[t]?.enabled !== false)
      .map(table => ({ table, status, rows_written: null, dropped: null, warnings: null, info: null }))
  }, [executing, runStats, execResult, project.etl_config, scripts])

  // ── Preview state ────────────────────────────────────────────────────────
  // Cached output previews keyed by csv filename. `null` while in flight,
  // {error: string} on failure, OutputPreview on success.
  type PreviewCacheEntry = OutputPreview | { error: string } | null
  const [previewCache, setPreviewCache] = useState<Record<string, PreviewCacheEntry>>({})
  const [activePreviewTab, setActivePreviewTab] = useState<string | null>(null)

  // Output files (from current execResult OR project state after page reload).
  // basenames only, deduped, filtered to .csv. Ordered by activeTables first
  // then any other CSV (domain-routed tables fall through here).
  const outputCsvs = useMemo(() => {
    const raw = (execResult?.output_files ?? project.output_files ?? []) as string[]
    const basenames = Array.from(new Set(raw.map(p => p.split(/[\\/]/).pop() || ''))).filter(n => n.endsWith('.csv'))
    const expected = activeTables.map(t => `${t.key}.csv`)
    const ordered: string[] = []
    for (const name of expected) {
      if (basenames.includes(name)) ordered.push(name)
    }
    for (const name of basenames) {
      if (!ordered.includes(name)) ordered.push(name)
    }
    return ordered
  }, [execResult?.output_files, project.output_files, activeTables])

  // When the file list changes (e.g., after an execute) and no tab is selected
  // or the selected tab disappears, default to the first available CSV.
  useEffect(() => {
    if (outputCsvs.length === 0) {
      if (activePreviewTab !== null) setActivePreviewTab(null)
      return
    }
    if (!activePreviewTab || !outputCsvs.includes(activePreviewTab)) {
      setActivePreviewTab(outputCsvs[0])
    }
  }, [outputCsvs, activePreviewTab])

  // Lazy-load + cache the preview for the active tab.
  useEffect(() => {
    if (!activePreviewTab) return
    if (activePreviewTab in previewCache) return   // already cached (success or error)
    setPreviewCache(prev => ({ ...prev, [activePreviewTab]: null }))
    getOutputPreview(project.id, activePreviewTab, 20)
      .then(data => setPreviewCache(prev => ({ ...prev, [activePreviewTab]: data })))
      .catch((e: unknown) => {
        const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
        setPreviewCache(prev => ({ ...prev, [activePreviewTab]: { error: typeof msg === 'string' ? msg : 'Failed to load preview' } }))
      })
  }, [activePreviewTab, project.id])

  // ── DB health state ──────────────────────────────────────────────────────
  const [health, setHealth] = useState<DbHealth | null>(null)
  const [healthError, setHealthError] = useState('')
  const [refreshingHealth, setRefreshingHealth] = useState(false)

  const refreshHealth = async () => {
    setRefreshingHealth(true)
    setHealthError('')
    try {
      setHealth(await getDbHealth())
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setHealthError(typeof msg === 'string' ? msg : 'Failed to query db-health')
    } finally {
      setRefreshingHealth(false)
    }
  }

  // ── ETL load state ──────────────────────────────────────────────────────
  const [schemaMode, setSchemaMode] = useState<'shared' | 'project'>('shared')
  const [schemaName, setSchemaName] = useState('')
  const [truncate, setTruncate] = useState(true)
  const [applyIndices, setApplyIndices] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [starting, setStarting] = useState(false)
  const [status, setStatus] = useState<LoadStatus | null>(null)

  // ── Initial fetch + polling ──────────────────────────────────────────────
  useEffect(() => { refreshHealth() }, [])

  const fetchLoadStatus = useCallback(async () => {
    try {
      setStatus(await getLoadStatus(project.id))
    } catch { /* ignore */ }
  }, [project.id])

  // Once on open, to show the last load's result (or pick up one still running).
  useEffect(() => { fetchLoadStatus() }, [fetchLoadStatus])

  const etlRunning = status?.overall === 'running'

  // Polled only while a load runs: the backend keeps the last load's result
  // until it restarts, so polling regardless would never stop.
  useEffect(() => {
    if (!etlRunning) return
    const timer = window.setInterval(fetchLoadStatus, 1500)
    return () => window.clearInterval(timer)
  }, [etlRunning, fetchLoadStatus])

  // The row counts in the health card change when a load finishes, so refresh
  // it then — once, not on every later poll that still sees "finished".
  const prevOverallRef = useRef(status?.overall)
  useEffect(() => {
    const prev = prevOverallRef.current
    prevOverallRef.current = status?.overall
    if (prev === 'running' && (status?.overall === 'success' || status?.overall === 'error')) refreshHealth()
  }, [status?.overall])
  const canLoadEtl = project.last_execution_status === 'success' && health?.connected === true

  const targetSchema = schemaName.trim() || (schemaMode === 'shared' ? 'cdm' : `cdm_${project.id.replace(/-/g, '_')}`)

  const handleLoadEtl = async () => {
    const emptyVocabWithFks = applyIndices && (health?.vocab_rows ?? 0) === 0
    if (truncate || emptyVocabWithFks) {
      const ok = await confirm({
        title: truncate ? `Empty and reload the tables in ${targetSchema}?` : 'Load with an empty vocabulary?',
        description: (
          <>
            {truncate && (
              <p>
                Every OMOP table this project loads into <span className="font-mono text-foreground">{targetSchema}</span> is
                emptied first (TRUNCATE … CASCADE), so all rows already there are deleted, including
                {schemaMode === 'shared' ? ' rows other projects loaded into this shared schema,' : ''} and
                rows of any table that references them. This can't be undone.
              </p>
            )}
            {emptyVocabWithFks && (
              <p>
                The vocabulary tables are empty. FK constraints reference vocab.concept and will fail
                to apply. Load the vocabulary first or untick "Apply indices and FK constraints".
              </p>
            )}
          </>
        ),
        confirmLabel: truncate ? 'Empty and load' : 'Load anyway',
      })
      if (!ok) return
    }
    setStarting(true)
    setLoadError('')
    try {
      await loadDatabase(project.id, {
        schema_mode: schemaMode,
        schema_name: schemaName || undefined,
        truncate,
        apply_indices: applyIndices,
      })
      // The backend marks the load running before it answers, so this starts the polling.
      await fetchLoadStatus()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setLoadError(typeof msg === 'string' ? msg : 'Failed to start load')
    } finally {
      setStarting(false)
    }
  }

  return (
    <WizardLayout
      project={project}
      currentSlug="finalize"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      nextLabel=""
    >
      <div className="flex flex-col gap-8">
        {/* Intro */}
        <div>
          <h2 className="text-xl font-bold text-primary">Generate &amp; load to OMOP DB</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Final step. (1) Generate the Python scripts per table, (2) execute the pipeline
            against your source CSV, (3) preview the generated OMOP CSVs, then (4) bulk-load
            them into Postgres. (The shared OMOP vocabulary was loaded back on the Source step.)
          </p>
        </div>

        {/* ── 1. Generate ────────────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <SectionHeader icon={Code2} n={1} title="Generate scripts" subtitle="One Python script per OMOP table, generated from your wizard config." />

          <Card className="p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold text-foreground">Script Status</h3>
              <Badge variant={allGenerated && staleTables.length === 0 ? 'success' : 'warning'}>
                {generatedCount} / {activeTables.length} ready
                {staleTables.length > 0 && ` · ${staleTables.length} out of date`}
              </Badge>
            </div>

            <div className="w-full bg-muted rounded-full h-1.5">
              <div className="bg-success h-1.5 rounded-full transition-all" style={{ width: `${(generatedCount / activeTables.length) * 100}%` }} />
            </div>

            <div className="grid grid-cols-1 gap-1.5">
              {activeTables.map(t => {
                const has = !!scripts[t.key]
                const stale = staleTables.includes(t.key)
                const lineCount = has ? scripts[t.key].split('\n').length : 0
                return (
                  <div key={t.key} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-muted">
                    {has && !stale
                      ? <CheckCircle className="w-4 h-4 text-success flex-shrink-0" />
                      : <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
                    }
                    <span className="text-xs font-mono font-medium text-foreground">{t.label}</span>
                    <span className={clsx('text-xs ml-auto', stale ? 'text-amber-700 font-medium' : 'text-muted-foreground')}>
                      {!has ? 'not generated yet' : stale ? `out of date · ${lineCount} lines` : `${lineCount} lines`}
                    </span>
                  </div>
                )
              })}
            </div>

            {!allGenerated && !bulk && (
              <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5">
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span>
                  Missing: <span className="font-mono font-semibold">{missingTables.join(', ')}</span>.
                  Generate them here, or go back to the corresponding wizard step.
                </span>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2">
              {missingTables.length > 0 && (
                <Button size="sm" onClick={() => generateMany(missingTables)} disabled={isAnyGenerating}>
                  <Sparkles className="w-3.5 h-3.5" />
                  Generate missing ({missingTables.length})
                </Button>
              )}
              {staleTables.length > 0 && (
                <Button
                  size="sm"
                  onClick={() => generateMany(staleTables)}
                  disabled={isAnyGenerating}
                  title="Regenerate the scripts whose settings changed since they were generated"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Regenerate outdated ({staleTables.length})
                </Button>
              )}
              {anyGenerated && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => generateMany(activeTables.map(t => t.key))}
                  disabled={isAnyGenerating}
                  title="Build every script again from the current wizard settings"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Regenerate all ({activeTables.length})
                </Button>
              )}
              {bulk && (
                <span className="flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  Generating <span className="font-mono text-foreground">{bulk.current}.py</span> · {bulk.done + 1} of {bulk.total}
                </span>
              )}
            </div>

            {bulkFailures.length > 0 && (
              <div className="flex flex-col gap-1 text-xs text-destructive bg-destructive/10 border border-destructive/30 rounded-lg px-3 py-2.5">
                <p className="font-semibold">
                  {bulkFailures.length} script{bulkFailures.length > 1 ? 's' : ''} could not be generated:
                </p>
                {bulkFailures.map(f => (
                  <p key={f.table}><span className="font-mono">{f.table}.py</span>: {f.error}</p>
                ))}
              </div>
            )}
          </Card>

          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">Generate / regenerate per table</h3>
            {activeTables.map(t => (
              <ScriptGenerator key={t.key} project={project} table={t.key} onUpdate={onUpdate} buttonClassName="min-w-[17rem] justify-center" deterministic revealChanges={false} />
            ))}
          </div>
        </section>

        {/* ── 2. Execute ─────────────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <SectionHeader icon={Play} n={2} title="Execute pipeline" subtitle="Runs every generated script against your source CSV; writes one OMOP CSV per table." />

          {anyGenerated ? (
            <Card className="p-5 flex flex-col gap-4">
              <div>
                <p className="text-xs text-muted-foreground">
                  Order: <span className="font-mono text-foreground">{
                    [
                      ...activeTables.map(t => t.key),
                      ...(activeTables.some(t => t.key === 'stem_table') ? ['(domain tables)'] : []),
                    ].join(' → ')
                  }</span>.
                </p>
              </div>

              {!allGenerated && (
                <p className="text-xs text-amber-600">
                  Some scripts are missing — only generated tables will run.
                </p>
              )}
              {staleTables.length > 0 && (
                <p className="text-xs text-amber-600">
                  {staleTables.length} script{staleTables.length > 1 ? 's are' : ' is'} out of date and would run the old settings.
                </p>
              )}

              <Button onClick={handleExecute} disabled={executing} size="lg" className="w-fit">
                {executing
                  ? <><RefreshCw className="w-5 h-5 animate-spin" /> Running pipeline…</>
                  : <><PlayCircle className="w-5 h-5" /> Execute ETL Pipeline</>
                }
              </Button>
            </Card>
          ) : (
            <p className="text-xs text-muted-foreground italic">Generate at least one script above to enable execution.</p>
          )}

          {execError && !execResult && (
            <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive flex items-start gap-2">
              <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              {execError}
            </div>
          )}

          {execResult && <RunStatusBadge status={execResult.status} />}

          {statsRows.length > 0 && (
            <ExecutionStatsTable
              stats={statsRows}
              executing={executing}
              selected={logTable}
              onSelect={t => setLogTable(prev => (prev === t ? '' : t))}
            />
          )}

          {execResult && (
            <Card className="p-4 flex flex-col gap-3">
              {/* Toolbar: which table, which verbosity, download the full log of that verbosity. */}
              <div className="flex flex-wrap items-center gap-3">
                <div className="mr-auto">
                  <h3 className="text-sm font-semibold text-foreground">Pipeline messages</h3>
                  <p className="text-xs text-muted-foreground">
                    What each table's script reported while running: dropped rows, unmapped values and errors.
                  </p>
                </div>

                {basicSections.tables.length > 1 && (
                  <Select
                    value={logTable}
                    onChange={e => setLogTable(e.target.value)}
                    className="w-48"
                  >
                    <option value="">All tables</option>
                    {basicSections.tables.map(t => <option key={t} value={t}>{t}</option>)}
                  </Select>
                )}

                <div className="inline-flex rounded-lg border border-border p-0.5 bg-muted/50">
                  {([
                    ['basic', 'Basic', 'Dropped rows only'],
                    ['detailed', 'Detailed', 'Dropped rows, plus written rows that have empty or unmapped fields'],
                  ] as const).map(([value, label, hint]) => (
                    <button
                      key={value}
                      type="button"
                      title={hint}
                      onClick={() => setLogView(value)}
                      className={clsx(
                        'px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
                        logView === value
                          ? 'bg-card text-foreground shadow-sm'
                          : 'text-muted-foreground hover:text-foreground',
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                {!(logView === 'basic' && execError) && (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => openLogViewer(project.id, logView, logTable)}
                      title="Open the full log in a new window, with search and filters"
                    >
                      <ExternalLink className="w-3.5 h-3.5" /> Open full log
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => downloadExecutionLog(project.id, logView)}
                      title={`Download the full ${logView} log of every table`}
                    >
                      <Download className="w-3.5 h-3.5" /> Download
                    </Button>
                  </>
                )}
              </div>

              {/* One line describing what the preview below shows. */}
              <p className="text-xs text-muted-foreground flex items-center gap-1.5">
                {logView === 'basic' && (
                  <>
                    Rows that were dropped and not written to the output, with the reason.
                    Shows up to {BASIC_LINES_PER_TABLE} per table, then a count of each reason.
                  </>
                )}
                {logView === 'detailed' && !detailedLog && (
                  <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading detailed log…</>
                )}
                {logView === 'detailed' && detailedLog && 'error' in detailedLog && (
                  <span className="text-destructive">{detailedLog.error}</span>
                )}
                {logView === 'detailed' && detailedLog && !('error' in detailedLog) && (
                  <>
                    Dropped rows, plus rows that were written but have empty or unmapped fields.{' '}
                    {detailedLog.truncated
                      ? `First ${LOG_PREVIEW_LINES.toLocaleString()} of ${detailedLog.total_lines.toLocaleString()} lines`
                      : `${detailedLog.total_lines.toLocaleString()} lines`}
                    {' '}· {detailedLog.info_lines.toLocaleString()} empty/unmapped field notes · {(detailedLog.size_bytes / 1024 / 1024).toFixed(1)} MB
                  </>
                )}
              </p>

              <LogStream
                log={logView === 'basic'
                  ? (logTable ? basicSections.byTable[logTable] ?? '' : execResult.log)
                  : (detailedLog && !('error' in detailedLog) ? detailedLog.log : '')}
              />
            </Card>
          )}

          {execResult?.output_files && execResult.output_files.length > 0 && (
            <Card className="p-5 flex flex-col gap-3">
              <h3 className="font-semibold text-foreground">Output OMOP Files</h3>
              <div className="grid grid-cols-2 gap-2">
                {execResult.output_files.map(f => (
                  <div key={f} className="flex items-center justify-between p-3 bg-muted rounded-lg border border-border">
                    <div className="flex items-center gap-2">
                      <CheckCircle className="w-3.5 h-3.5 text-success" />
                      <span className="text-sm font-mono text-foreground">{basename(f)}</span>
                    </div>
                    <button
                      onClick={() => downloadOutput(project.id, basename(f))}
                      className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 font-medium"
                    >
                      <Download className="w-3.5 h-3.5" /> Download
                    </button>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </section>

        {/* ── 3. Preview output ──────────────────────────────────────────── */}
        {outputCsvs.length > 0 && (
          <section className="flex flex-col gap-4">
            <SectionHeader icon={Eye} n={3} title="Preview output" subtitle="Peek at the top 20 rows of each generated OMOP CSV before loading them into Postgres." />

            <Card className="p-5 flex flex-col gap-4">
              {/* Tab strip */}
              <div className="flex flex-wrap gap-1 border-b border-border -mb-px">
                {outputCsvs.map(name => {
                  const isActive = name === activePreviewTab
                  return (
                    <button
                      key={name}
                      onClick={() => setActivePreviewTab(name)}
                      className={clsx(
                        'px-3 py-1.5 text-xs font-mono rounded-t-md border-b-2 -mb-px transition-colors',
                        isActive
                          ? 'border-primary text-primary bg-secondary/40 font-semibold'
                          : 'border-transparent text-muted-foreground hover:text-foreground hover:bg-muted/50',
                      )}
                    >
                      {name}
                    </button>
                  )
                })}
              </div>

              {/* Active tab body */}
              {activePreviewTab && (() => {
                const entry = previewCache[activePreviewTab]
                if (entry === null || entry === undefined) {
                  return (
                    <OmopTablePreview
                      columns={[]}
                      rows={[]}
                      totalRows={0}
                      filename={activePreviewTab}
                      projectId={project.id}
                      loading
                    />
                  )
                }
                if ('error' in entry) {
                  return (
                    <OmopTablePreview
                      columns={[]}
                      rows={[]}
                      totalRows={0}
                      filename={activePreviewTab}
                      projectId={project.id}
                      error={entry.error}
                    />
                  )
                }
                return (
                  <OmopTablePreview
                    columns={entry.columns}
                    rows={entry.rows}
                    totalRows={entry.total_rows}
                    filename={activePreviewTab}
                    projectId={project.id}
                  />
                )
              })()}
            </Card>
          </section>
        )}

        {/* ── DB health (informational for section 4) ───────────────────── */}
        <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Server className="w-4 h-4 text-gray-500" />
              <h3 className="font-medium text-gray-800">Postgres health</h3>
            </div>
            <button
              onClick={refreshHealth}
              disabled={refreshingHealth}
              className="text-xs flex items-center gap-1 text-blue-600 hover:underline disabled:opacity-50"
            >
              {refreshingHealth ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
              Refresh
            </button>
          </div>
          <ErrorBanner message={healthError} />
          {health && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
              <HealthRow label="Connected" ok={health.connected} />
              <HealthRow
                label={`Vocab schema (${health.vocab_schema || 'vocab'})`}
                ok={health.vocab_schema_ready && health.vocab_rows > 0}
                value={
                  !health.vocab_schema_ready ? 'No DDL'
                    : health.vocab_rows === 0 ? 'Empty'
                    : `${health.vocab_rows.toLocaleString()} concepts`
                }
              />
              <HealthRow
                label="Clinical schemas"
                ok={health.clinical_schemas.length > 0}
                value={`${health.clinical_schemas.length} schema${health.clinical_schemas.length === 1 ? '' : 's'}`}
              />
              <HealthRow label="Default clinical DDL" ok={health.ddl_applied} />
            </div>
          )}
          {health?.error && (
            <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5">{health.error}</p>
          )}
        </div>

        {/* ── 4. Load DB ─────────────────────────────────────────────────── */}
        <section className="flex flex-col gap-4">
          <SectionHeader icon={Database} n={4} title="Load to OMOP DB" subtitle="Loads this project's generated CSVs into the clinical schema. Optionally applies indices + FK constraints for a query-ready database." />

          <Card className="p-5 flex flex-col gap-4">
            {health && health.vocab_rows === 0 && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                Vocabulary not loaded yet. The clinical load will succeed, but concept lookups will return NULL and FK constraints will fail to apply.
              </p>
            )}

            <div className="grid grid-cols-2 gap-3">
              <label className={clsx(
                'flex flex-col gap-1 border rounded-lg p-3 cursor-pointer',
                schemaMode === 'shared' ? 'border-blue-500 bg-blue-50' : 'border-gray-200',
              )}>
                <span className="flex items-center gap-2 text-sm font-medium">
                  <input
                    type="radio"
                    name="schema-mode"
                    checked={schemaMode === 'shared'}
                    onChange={() => setSchemaMode('shared')}
                    className="accent-blue-600"
                  />
                  Shared schema
                </span>
                <span className="text-xs text-gray-500">Default <code className="bg-gray-100 px-1 rounded">cdm</code>.</span>
              </label>

              <label className={clsx(
                'flex flex-col gap-1 border rounded-lg p-3 cursor-pointer',
                schemaMode === 'project' ? 'border-blue-500 bg-blue-50' : 'border-gray-200',
              )}>
                <span className="flex items-center gap-2 text-sm font-medium">
                  <input
                    type="radio"
                    name="schema-mode"
                    checked={schemaMode === 'project'}
                    onChange={() => setSchemaMode('project')}
                    className="accent-blue-600"
                  />
                  Project-scoped
                </span>
                <span className="text-xs text-gray-500">
                  <code className="bg-gray-100 px-1 rounded">cdm_{project.id.slice(0, 8).replace(/-/g, '_')}…</code>
                </span>
              </label>
            </div>

            <div>
              <label className="text-sm font-medium text-gray-700">Schema name (optional)</label>
              <input
                type="text"
                value={schemaName}
                onChange={e => setSchemaName(e.target.value)}
                placeholder={schemaMode === 'shared' ? 'cdm' : `cdm_${project.id.replace(/-/g, '_')}`}
                className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full font-mono focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>

            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={truncate} onChange={e => setTruncate(e.target.checked)} className="rounded text-blue-600" />
              Truncate target tables before loading (CASCADE)
            </label>

            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={applyIndices} onChange={e => setApplyIndices(e.target.checked)} className="rounded text-blue-600" />
              Apply indices and FK constraints after load (recommended for analytics)
            </label>

            <button
              onClick={handleLoadEtl}
              disabled={!canLoadEtl || starting || etlRunning}
              className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 disabled:opacity-50 w-fit"
              title={!canLoadEtl
                ? (project.last_execution_status !== 'success'
                  ? 'Execute the pipeline successfully above first'
                  : 'Postgres is not reachable')
                : ''}
            >
              {starting || etlRunning
                ? <><RefreshCw className="w-4 h-4 animate-spin" /> {etlRunning ? 'Loading…' : 'Starting…'}</>
                : <><PlayCircle className="w-4 h-4" /> Load ETL outputs into Postgres</>
              }
            </button>

            <ErrorBanner message={loadError} />

            {project.last_execution_status !== 'success' && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                Execute the pipeline successfully (section 2) before loading into the database.
              </p>
            )}

            {status && status.tables.length > 0 && (
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium text-gray-600">Table</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-24">Status</th>
                      <th className="text-right px-3 py-2 font-medium text-gray-600 w-20">Rows</th>
                      <th className="text-right px-3 py-2 font-medium text-gray-600 w-20">Elapsed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {status.tables.map(t => (
                      <tr key={t.table} className="border-b last:border-0 border-gray-100">
                        <td className="px-3 py-2 font-mono text-gray-700">{t.table}</td>
                        <td className="px-3 py-2"><StatusPill status={t.status} /></td>
                        <td className="px-3 py-2 text-right text-gray-600">{t.rows.toLocaleString()}</td>
                        <td className="px-3 py-2 text-right text-gray-500">{t.elapsed.toFixed(1)}s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {status?.log && (
              <pre className="bg-gray-900 text-gray-100 text-xs rounded-lg p-3 max-h-48 overflow-y-auto whitespace-pre-wrap">{status.log}</pre>
            )}
          </Card>
        </section>
      </div>
    </WizardLayout>
  )
}
