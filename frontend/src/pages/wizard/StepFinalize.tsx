// Merged final step: Generate → Execute → Load Vocab → Load DB.
//
// Lifted from the old Step11_Generate.tsx and Step12_LoadDB.tsx into one
// scrollable page with sticky section headers. No new API calls — every
// control here uses the same endpoints those two pages used.

import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  executeProject,
  downloadOutput,
  getDbHealth,
  getLoadStatus,
  getOutputPreview,
  loadDatabase,
  type DbHealth,
  type LoadStatus,
  type OutputPreview,
} from '../../api/client'
import type { Project } from '../../types'
import WizardLayout from './WizardLayout'
import ScriptGenerator from '../../components/ScriptGenerator'
import LogStream from '../../components/LogStream'
import ErrorBanner from '../../components/ErrorBanner'
import OmopTablePreview from '../../components/OmopTablePreview'
import { getAdjacentSlugs, OPTIONAL_TABLES, isOptionalTableEnabled, type OptionalTable } from '../../wizard/steps'
import {
  PlayCircle, Download, RefreshCw, CheckCircle, AlertCircle, AlertTriangle,
  Database, Loader2, Server, Code2, Play, Eye,
} from 'lucide-react'
import { basename } from '../../utils'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import clsx from 'clsx'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

// Universe of OMOP tables this page can generate. The visible subset is
// computed at render time from the project's etl_config so optional tables
// the user didn't tick on Step 1 don't appear here either.
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

export default function StepFinalize({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const { prev } = getAdjacentSlugs(project, 'finalize')

  // ── Generate / Execute state ─────────────────────────────────────────────
  const [executing, setExecuting] = useState(false)
  const [execResult, setExecResult] = useState<{ status: string; log: string; output_files: string[] } | null>(null)
  const [execError, setExecError] = useState('')
  const [outputMode, setOutputMode] = useState<'basic' | 'detailed'>('basic')

  // Only count / regenerate the tables the user enabled in Step 1.
  const activeTables = getActiveTables(project)
  const scripts: Record<string, string> = project.generated_scripts || {}
  const generatedCount = activeTables.filter(t => scripts[t.key]).length
  const allGenerated   = generatedCount === activeTables.length
  const anyGenerated   = generatedCount > 0
  const missingTables  = activeTables.filter(t => !scripts[t.key]).map(t => t.key)

  const handleExecute = async () => {
    setExecuting(true)
    setExecError('')
    try {
      const result = await executeProject(project.id, outputMode)
      setExecResult(result)
      onUpdate({ ...project, last_execution_status: result.status, output_files: result.output_files })
    } catch (e: unknown) {
      const err = e as { response?: { data?: { detail?: string } } }
      const msg = err?.response?.data?.detail || 'Execution failed.'
      setExecError(msg)
      setExecResult({ status: 'error', log: msg, output_files: [] })
    } finally {
      setExecuting(false)
    }
  }

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

  useEffect(() => {
    let timer: number | null = null
    const tick = async () => {
      try {
        const s = await getLoadStatus(project.id)
        setStatus(s)
        if (s.overall === 'success' || s.overall === 'error') refreshHealth()
      } catch { /* ignore */ }
    }
    tick()
    timer = window.setInterval(tick, 1500)
    return () => { if (timer !== null) window.clearInterval(timer) }
  }, [project.id])

  const etlRunning = status?.overall === 'running'
  const canLoadEtl = project.last_execution_status === 'success' && health?.connected === true

  const handleLoadEtl = async () => {
    if (applyIndices && (health?.vocab_rows ?? 0) === 0) {
      const proceed = window.confirm(
        'Vocabulary tables are empty.\n\n' +
        'FK constraints reference vocab.concept and will fail to apply against an empty ' +
        'vocabulary. Load the vocabulary first or untick "Apply indices and FK constraints" ' +
        'before proceeding.\n\nContinue anyway?',
      )
      if (!proceed) return
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
              <Badge variant={allGenerated ? 'success' : 'warning'}>
                {generatedCount} / {activeTables.length} ready
              </Badge>
            </div>

            <div className="w-full bg-muted rounded-full h-1.5">
              <div className="bg-success h-1.5 rounded-full transition-all" style={{ width: `${(generatedCount / activeTables.length) * 100}%` }} />
            </div>

            <div className="grid grid-cols-1 gap-1.5">
              {activeTables.map(t => {
                const has = !!scripts[t.key]
                const lineCount = has ? scripts[t.key].split('\n').length : 0
                return (
                  <div key={t.key} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-muted">
                    {has
                      ? <CheckCircle className="w-4 h-4 text-success flex-shrink-0" />
                      : <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
                    }
                    <span className="text-xs font-mono font-medium text-foreground">{t.label}</span>
                    <span className="text-xs text-muted-foreground ml-auto">
                      {has ? `${lineCount} lines` : 'not generated yet'}
                    </span>
                  </div>
                )
              })}
            </div>

            {!allGenerated && (
              <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2.5">
                <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                <span>
                  Missing: <span className="font-mono font-semibold">{missingTables.join(', ')}</span>.
                  Regenerate below, or go back to the corresponding wizard step.
                </span>
              </div>
            )}
          </Card>

          <div className="flex flex-col gap-3">
            <h3 className="text-sm font-semibold text-foreground">Generate / regenerate per table</h3>
            {activeTables.map(t => (
              <ScriptGenerator key={t.key} project={project} table={t.key} onUpdate={onUpdate} buttonClassName="min-w-[17rem] justify-center" />
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

              <div className="flex flex-col gap-1">
                <p className="text-xs font-medium text-foreground">Output verbosity</p>
                <div className="flex items-center gap-4 text-sm">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="outputMode"
                      value="basic"
                      checked={outputMode === 'basic'}
                      onChange={() => setOutputMode('basic')}
                      className="accent-blue-600"
                    />
                    <span>
                      <span className="font-medium">Basic</span>
                      <span className="text-muted-foreground ml-1">— only rows skipped entirely (WARNING)</span>
                    </span>
                  </label>
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="outputMode"
                      value="detailed"
                      checked={outputMode === 'detailed'}
                      onChange={() => setOutputMode('detailed')}
                      className="accent-blue-600"
                    />
                    <span>
                      <span className="font-medium">Detailed</span>
                      <span className="text-muted-foreground ml-1">— also empty/unmapped fields on written rows (INFO)</span>
                    </span>
                  </label>
                </div>
              </div>

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

          {execResult && <LogStream log={execResult.log} status={execResult.status} />}

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
