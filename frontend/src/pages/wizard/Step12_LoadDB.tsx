import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getDbHealth,
  getLoadStatus,
  getVocabBundleInfo,
  getVocabStatus,
  loadDatabase,
  loadVocabulary,
  type DbHealth,
  type LoadStatus,
  type VocabBundleInfo,
  type VocabLoadStatus,
} from '../../api/client'
import type { Project } from '../../types'
import WizardLayout from './WizardLayout'
import ErrorBanner from '../../components/ErrorBanner'
import {
  Database, Loader2, RefreshCw, CheckCircle, AlertCircle,
  PlayCircle, BookOpen, Server,
} from 'lucide-react'
import clsx from 'clsx'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

function StatusPill({ status }: { status: string }) {
  const map: Record<string, { bg: string; text: string; label: string }> = {
    idle:     { bg: 'bg-gray-100',    text: 'text-gray-600',  label: 'idle' },
    pending:  { bg: 'bg-gray-100',    text: 'text-gray-600',  label: 'pending' },
    loading:  { bg: 'bg-blue-100',    text: 'text-blue-700',  label: 'loading' },
    running:  { bg: 'bg-blue-100',    text: 'text-blue-700',  label: 'running' },
    success:  { bg: 'bg-green-100',   text: 'text-green-700', label: 'success' },
    skipped:  { bg: 'bg-amber-100',   text: 'text-amber-700', label: 'skipped' },
    error:    { bg: 'bg-red-100',     text: 'text-red-700',   label: 'error' },
  }
  const cfg = map[status] ?? map.idle
  return (
    <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full', cfg.bg, cfg.text)}>
      {cfg.label}
    </span>
  )
}

function HealthRow({
  label, ok, value,
}: { label: string; ok: boolean; value?: string }) {
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

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

const DEFAULT_VOCAB_PATH = '/vocab'

export default function Step12LoadDB({ project, onUpdate }: Props) {
  void onUpdate
  const navigate = useNavigate()

  // Health
  const [health, setHealth] = useState<DbHealth | null>(null)
  const [healthError, setHealthError] = useState('')
  const [refreshingHealth, setRefreshingHealth] = useState(false)

  // Vocab
  const [vocabInfo, setVocabInfo] = useState<VocabBundleInfo | null>(null)
  const [bundlePath, setBundlePath] = useState(DEFAULT_VOCAB_PATH)
  const [vocabError, setVocabError] = useState('')
  const [vocabStarting, setVocabStarting] = useState(false)
  const [vocabStatus, setVocabStatus] = useState<VocabLoadStatus | null>(null)

  // ETL
  const [schemaMode, setSchemaMode] = useState<'shared' | 'project'>('shared')
  const [schemaName, setSchemaName] = useState<string>('')
  const [truncate, setTruncate] = useState(true)
  const [applyIndices, setApplyIndices] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [starting, setStarting] = useState(false)
  const [status, setStatus] = useState<LoadStatus | null>(null)

  const refreshHealth = async () => {
    setRefreshingHealth(true)
    setHealthError('')
    try {
      const h = await getDbHealth()
      setHealth(h)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setHealthError(typeof msg === 'string' ? msg : 'Failed to query db-health')
    } finally {
      setRefreshingHealth(false)
    }
  }

  const refreshVocabInfo = async () => {
    try {
      const info = await getVocabBundleInfo(DEFAULT_VOCAB_PATH)
      setVocabInfo(info)
      if (info.exists && info.detected_files.length > 0) {
        setBundlePath(info.path)
      }
    } catch {
      setVocabInfo({ path: DEFAULT_VOCAB_PATH, exists: false, detected_files: [], total_size_bytes: 0 })
    }
  }

  useEffect(() => {
    refreshHealth()
    refreshVocabInfo()
  }, [])

  // Poll vocab status
  useEffect(() => {
    let timer: number | null = null
    const tick = async () => {
      try {
        const s = await getVocabStatus()
        setVocabStatus(s)
        if (s.overall === 'success' || s.overall === 'error') {
          // Refresh health so the vocab_rows count updates
          refreshHealth()
        }
      } catch { /* ignore */ }
    }
    tick()
    timer = window.setInterval(tick, 1500)
    return () => { if (timer !== null) window.clearInterval(timer) }
  }, [])

  // Poll ETL load status
  useEffect(() => {
    let timer: number | null = null
    const tick = async () => {
      try {
        const s = await getLoadStatus(project.id)
        setStatus(s)
        if (s.overall === 'success' || s.overall === 'error') {
          refreshHealth()
        }
      } catch { /* ignore */ }
    }
    tick()
    timer = window.setInterval(tick, 1500)
    return () => { if (timer !== null) window.clearInterval(timer) }
  }, [project.id])

  const vocabHasFiles = (vocabInfo?.detected_files?.length ?? 0) > 0
  const vocabRunning = vocabStatus?.overall === 'running'
  const etlRunning = status?.overall === 'running'

  const canLoadEtl =
    project.last_execution_status === 'success' &&
    health?.connected === true

  const handleLoadVocab = async () => {
    const path = (bundlePath || DEFAULT_VOCAB_PATH).trim()
    if (!path) return
    setVocabStarting(true)
    setVocabError('')
    try {
      await loadVocabulary({ bundle_path: path })
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setVocabError(typeof msg === 'string' ? msg : 'Failed to start vocabulary load')
    } finally {
      setVocabStarting(false)
    }
  }

  const handleLoadEtl = async () => {
    if (applyIndices && (health?.vocab_rows ?? 0) === 0) {
      const proceed = window.confirm(
        'Vocabulary tables are empty.\n\n' +
        'FK constraints reference vocab.concept and will fail to apply against an empty ' +
        'vocabulary. Load the vocabulary first (Card 1) or untick "Apply indices and FK ' +
        'constraints" before proceeding.\n\nContinue anyway?',
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
      projectId={project.id}
      projectName={project.name}
      currentStep={12}
      generatedScripts={project.generated_scripts}
      sourceUploaded={!!project.source_filename}
      hasMappingFiles={Object.keys(project.mapping_files || {}).length > 0}
      onBack={() => navigate(`/project/${project.id}/step/11`)}
      nextLabel=""
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Build the OMOP database</h2>
          <p className="text-sm text-gray-500 mt-1">
            Two steps to a ready OMOP CDM v5.4 database: load the vocabulary into the shared{' '}
            <code className="bg-gray-100 px-1 rounded font-mono">vocab</code> schema, then load this
            project's generated CSVs into the clinical schema and (optionally) apply indices + FK
            constraints.
          </p>
        </div>

        {/* Health panel */}
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
                  !health.vocab_schema_ready ? 'No DDL' :
                  health.vocab_rows === 0 ? 'Empty' :
                  `${health.vocab_rows.toLocaleString()} concepts`
                }
              />
              <HealthRow
                label="Clinical schemas"
                ok={health.clinical_schemas.length > 0}
                value={`${health.clinical_schemas.length} schema${health.clinical_schemas.length === 1 ? '' : 's'}`}
              />
              <HealthRow
                label="Default clinical DDL"
                ok={health.ddl_applied}
              />
            </div>
          )}
          {health?.error && (
            <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5">{health.error}</p>
          )}
          {health && health.clinical_schemas.length > 0 && (
            <p className="text-xs text-gray-500">
              Clinical schemas:{' '}
              {health.clinical_schemas.map((cs, i) => (
                <span key={cs.name}>
                  {i > 0 && ', '}
                  <span className="font-mono">{cs.name}</span>
                  {' '}
                  <span className="text-gray-400">({cs.person_rows.toLocaleString()} persons)</span>
                </span>
              ))}
            </p>
          )}
          {!health?.configured && (
            <p className="text-xs text-gray-500">
              Bring up the stack with <code className="bg-gray-100 px-1 rounded">docker compose up</code>{' '}
              from the repo root. The backend connects to the bundled Postgres container automatically.
            </p>
          )}
        </div>

        {/* Two cards side-by-side on lg screens */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

          {/* ── Card 1: Vocabulary ─────────────────────────────────────────── */}
          <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-gray-500" />
                <h3 className="font-medium text-gray-800">1. Vocabulary setup</h3>
              </div>
              {vocabStatus?.overall === 'success' && (
                <span className="flex items-center gap-1 text-xs text-green-700">
                  <CheckCircle className="w-3.5 h-3.5" /> Loaded
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500">
              Builds the <code className="bg-gray-100 px-1 rounded font-mono">vocab</code> schema
              and bulk-loads an Athena vocabulary bundle into it. Shared across all projects;
              re-running truncates and reloads.
            </p>

            {vocabInfo && vocabHasFiles ? (
              <div className="border border-emerald-200 bg-emerald-50 rounded-lg p-3 text-sm">
                <p className="font-medium text-emerald-900">
                  Detected {vocabInfo.detected_files.length} vocabulary file
                  {vocabInfo.detected_files.length === 1 ? '' : 's'}
                  {' '}({formatBytes(vocabInfo.total_size_bytes)})
                </p>
                <p className="text-xs text-emerald-700 mt-1">
                  Path: <code className="bg-white/60 px-1 rounded font-mono">{vocabInfo.path}</code>
                </p>
                <details className="mt-2">
                  <summary className="text-xs text-emerald-700 cursor-pointer">Show files</summary>
                  <ul className="mt-1 text-xs text-emerald-800 font-mono">
                    {vocabInfo.detected_files.map(f => <li key={f}>· {f}</li>)}
                  </ul>
                </details>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                  No Athena CSVs detected at <code className="bg-white/60 px-1 rounded font-mono">{DEFAULT_VOCAB_PATH}</code>.
                  Mount your unzipped Athena bundle to that path in <code className="bg-white/60 px-1 rounded">docker-compose.yml</code>,
                  or enter an alternative path here.
                </p>
                <input
                  type="text"
                  value={bundlePath}
                  onChange={e => setBundlePath(e.target.value)}
                  placeholder={DEFAULT_VOCAB_PATH}
                  className="border border-gray-300 rounded-md px-3 py-2 text-sm font-mono w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <button
                  onClick={refreshVocabInfo}
                  className="text-xs text-blue-600 hover:underline w-fit"
                >
                  Re-check path
                </button>
              </div>
            )}

            <button
              onClick={handleLoadVocab}
              disabled={!health?.connected || !vocabHasFiles || vocabStarting || vocabRunning}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 w-fit"
              title={!health?.connected ? 'Postgres is not reachable' : !vocabHasFiles ? 'No vocabulary files detected' : ''}
            >
              {vocabStarting || vocabRunning
                ? <><RefreshCw className="w-4 h-4 animate-spin" /> {vocabRunning ? 'Loading…' : 'Starting…'}</>
                : <><PlayCircle className="w-4 h-4" /> Load vocabulary</>
              }
            </button>

            <ErrorBanner message={vocabError} />

            {/* Per-file progress */}
            {vocabStatus && vocabStatus.files.length > 0 && (
              <div className="border border-gray-200 rounded-lg overflow-hidden">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 border-b border-gray-200">
                    <tr>
                      <th className="text-left px-3 py-2 font-medium text-gray-600">File</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-600 w-24">Status</th>
                      <th className="text-right px-3 py-2 font-medium text-gray-600 w-24">Rows</th>
                      <th className="text-right px-3 py-2 font-medium text-gray-600 w-20">Elapsed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {vocabStatus.files.map(f => (
                      <tr key={f.file} className="border-b last:border-0 border-gray-100">
                        <td className="px-3 py-2 font-mono text-gray-700">{f.table}</td>
                        <td className="px-3 py-2"><StatusPill status={f.status} /></td>
                        <td className="px-3 py-2 text-right text-gray-600">{f.rows.toLocaleString()}</td>
                        <td className="px-3 py-2 text-right text-gray-500">{f.elapsed.toFixed(1)}s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {vocabStatus?.log && (
              <pre className="bg-gray-900 text-gray-100 text-xs rounded-lg p-3 max-h-48 overflow-y-auto whitespace-pre-wrap">{vocabStatus.log}</pre>
            )}
          </div>

          {/* ── Card 2: ETL ───────────────────────────────────────────────── */}
          <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Database className="w-4 h-4 text-gray-500" />
                <h3 className="font-medium text-gray-800">2. Load ETL outputs</h3>
              </div>
              {status?.overall === 'success' && (
                <span className="flex items-center gap-1 text-xs text-green-700">
                  <CheckCircle className="w-3.5 h-3.5" /> Loaded
                </span>
              )}
            </div>
            <p className="text-xs text-gray-500">
              Builds the clinical schema if needed, loads this project's generated CSVs, and
              optionally applies indices + FK constraints for a query-ready database.
            </p>

            {health && health.vocab_rows === 0 && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                Vocabulary not loaded yet. The clinical load will succeed, but concept lookups will
                return NULL and FK constraints will fail to apply.
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
                <span className="text-xs text-gray-500">
                  Default <code className="bg-gray-100 px-1 rounded">cdm</code>.
                </span>
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
              <input
                type="checkbox"
                checked={truncate}
                onChange={e => setTruncate(e.target.checked)}
                className="rounded text-blue-600"
              />
              Truncate target tables before loading (CASCADE)
            </label>

            <label className="flex items-center gap-2 text-sm text-gray-700">
              <input
                type="checkbox"
                checked={applyIndices}
                onChange={e => setApplyIndices(e.target.checked)}
                className="rounded text-blue-600"
              />
              Apply indices and FK constraints after load (recommended for analytics)
            </label>

            <button
              onClick={handleLoadEtl}
              disabled={!canLoadEtl || starting || etlRunning}
              className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 disabled:opacity-50 w-fit"
              title={!canLoadEtl
                ? (project.last_execution_status !== 'success'
                  ? 'Run a successful ETL on Step 11 first'
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
                Run a successful ETL on Step 11 before loading into the database.
              </p>
            )}

            {/* Per-table progress */}
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
          </div>
        </div>

        {/* Inspection hint */}
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-sm text-blue-800">
          <p className="font-medium">Inspecting results</p>
          <p className="mt-1 text-blue-700">
            From your host (after <code className="bg-blue-100 px-1 rounded">docker compose up</code>):
          </p>
          <pre className="mt-2 bg-blue-900 text-blue-50 text-xs rounded px-3 py-2 overflow-x-auto">{`psql -h localhost -p 5432 -U omop -d omop -c "SELECT count(*) FROM vocab.concept;"
psql -h localhost -p 5432 -U omop -d omop -c "SELECT count(*) FROM cdm.person;"`}</pre>
        </div>
      </div>
    </WizardLayout>
  )
}
