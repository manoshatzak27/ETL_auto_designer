import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getDbHealth,
  getLoadStatus,
  loadDatabase,
  loadVocabulary,
  type DbHealth,
  type LoadStatus,
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

export default function Step12LoadDB({ project, onUpdate }: Props) {
  void onUpdate
  const navigate = useNavigate()

  const [health, setHealth] = useState<DbHealth | null>(null)
  const [healthError, setHealthError] = useState('')
  const [refreshingHealth, setRefreshingHealth] = useState(false)

  const [schemaMode, setSchemaMode] = useState<'shared' | 'project'>('shared')
  const [schemaName, setSchemaName] = useState<string>('')
  const [truncate, setTruncate] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [starting, setStarting] = useState(false)

  const [status, setStatus] = useState<LoadStatus | null>(null)

  // Vocabulary
  const [bundlePath, setBundlePath] = useState('')
  const [vocabError, setVocabError] = useState('')
  const [vocabStarting, setVocabStarting] = useState(false)
  const [vocabMessage, setVocabMessage] = useState('')

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

  useEffect(() => {
    refreshHealth()
  }, [])

  // Poll load status when something is running
  useEffect(() => {
    let timer: number | null = null
    const tick = async () => {
      try {
        const s = await getLoadStatus(project.id)
        setStatus(s)
      } catch {
        // ignore — usually 200 with empty default
      }
    }
    tick()
    timer = window.setInterval(tick, 1500)
    return () => { if (timer !== null) window.clearInterval(timer) }
  }, [project.id])

  const canLoad =
    project.last_execution_status === 'success' &&
    health?.connected === true

  const handleLoad = async () => {
    setStarting(true)
    setLoadError('')
    try {
      await loadDatabase(project.id, {
        schema_mode: schemaMode,
        schema_name: schemaName || undefined,
        truncate,
      })
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setLoadError(typeof msg === 'string' ? msg : 'Failed to start load')
    } finally {
      setStarting(false)
    }
  }

  const handleLoadVocab = async () => {
    if (!bundlePath.trim()) return
    setVocabStarting(true)
    setVocabError('')
    setVocabMessage('')
    try {
      const r = await loadVocabulary({ bundle_path: bundlePath.trim() })
      setVocabMessage(`Vocabulary load started into schema "${r.schema}".`)
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setVocabError(typeof msg === 'string' ? msg : 'Failed to start vocabulary load')
    } finally {
      setVocabStarting(false)
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
          <h2 className="text-xl font-semibold text-gray-900">Load into OMOP Postgres</h2>
          <p className="text-sm text-gray-500 mt-1">
            Bulk-load the generated OMOP CSVs into a Postgres database that has the OMOP CDM v5.4
            schema applied. The loader matches CSV columns to target columns dynamically — extra
            wizard-specific columns are silently dropped.
          </p>
        </div>

        {/* Connection status */}
        <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Server className="w-4 h-4 text-gray-500" />
              <h3 className="font-medium text-gray-800">Postgres connection</h3>
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
            <div className="grid grid-cols-3 gap-3 text-sm">
              <div className="bg-gray-50 rounded-lg p-3">
                <p className="text-xs text-gray-500 mb-1">Configured</p>
                <p className="font-medium flex items-center gap-1.5">
                  {health.configured
                    ? <><CheckCircle className="w-3.5 h-3.5 text-green-500" /> Yes</>
                    : <><AlertCircle className="w-3.5 h-3.5 text-red-500" /> No</>
                  }
                </p>
              </div>
              <div className="bg-gray-50 rounded-lg p-3">
                <p className="text-xs text-gray-500 mb-1">Connected</p>
                <p className="font-medium flex items-center gap-1.5">
                  {health.connected
                    ? <><CheckCircle className="w-3.5 h-3.5 text-green-500" /> Yes</>
                    : <><AlertCircle className="w-3.5 h-3.5 text-red-500" /> No</>
                  }
                </p>
              </div>
              <div className="bg-gray-50 rounded-lg p-3">
                <p className="text-xs text-gray-500 mb-1">CDM DDL applied</p>
                <p className="font-medium flex items-center gap-1.5">
                  {health.ddl_applied
                    ? <><CheckCircle className="w-3.5 h-3.5 text-green-500" /> Yes</>
                    : <><AlertCircle className="w-3.5 h-3.5 text-amber-500" /> No</>
                  }
                </p>
              </div>
            </div>
          )}
          {health?.error && (
            <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5">{health.error}</p>
          )}
          {health && health.schemas.length > 0 && (
            <p className="text-xs text-gray-500">
              Existing schemas: <span className="font-mono">{health.schemas.join(', ')}</span>
            </p>
          )}
          {!health?.configured && (
            <p className="text-xs text-gray-500">
              Bring up the stack with <code className="bg-gray-100 px-1 rounded">docker compose up</code>{' '}
              from the repo root. The backend connects to the bundled Postgres container automatically.
            </p>
          )}
        </div>

        {/* Schema selection + load */}
        <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-4">
          <div className="flex items-center gap-2">
            <Database className="w-4 h-4 text-gray-500" />
            <h3 className="font-medium text-gray-800">Load ETL outputs</h3>
          </div>

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
                One canonical schema (default: <code className="bg-gray-100 px-1 rounded">cdm</code>) — easiest, matches OMOP conventions.
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
                Project-scoped schema
              </span>
              <span className="text-xs text-gray-500">
                Isolate per-project data (e.g. <code className="bg-gray-100 px-1 rounded">cdm_{project.id.slice(0, 8).replace(/-/g, '_')}…</code>). The schema is created on demand.
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

          <button
            onClick={handleLoad}
            disabled={!canLoad || starting || status?.overall === 'running'}
            className="flex items-center gap-2 px-4 py-2 bg-purple-600 text-white rounded-lg text-sm font-medium hover:bg-purple-700 disabled:opacity-50 w-fit"
            title={!canLoad
              ? (project.last_execution_status !== 'success'
                ? 'Run a successful ETL on Step 11 first'
                : 'Postgres is not reachable')
              : ''}
          >
            {starting || status?.overall === 'running'
              ? <><RefreshCw className="w-4 h-4 animate-spin" /> {status?.overall === 'running' ? 'Running…' : 'Starting…'}</>
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
            <div className="border border-gray-200 rounded-lg overflow-hidden mt-3">
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
            <pre className="bg-gray-900 text-gray-100 text-xs rounded-lg p-3 max-h-64 overflow-y-auto whitespace-pre-wrap">{status.log}</pre>
          )}
        </div>

        {/* Vocabulary loader */}
        <details className="bg-white border border-gray-200 rounded-xl p-5">
          <summary className="cursor-pointer flex items-center gap-2 font-medium text-gray-800">
            <BookOpen className="w-4 h-4 text-gray-500" />
            Load Athena vocabulary (optional)
          </summary>
          <div className="mt-4 flex flex-col gap-3">
            <p className="text-xs text-gray-500">
              Provide a path (inside the backend container) to a folder containing the standard
              Athena vocabulary CSVs: CONCEPT.csv, VOCABULARY.csv, DOMAIN.csv, CONCEPT_CLASS.csv,
              RELATIONSHIP.csv, CONCEPT_RELATIONSHIP.csv, CONCEPT_SYNONYM.csv, CONCEPT_ANCESTOR.csv,
              and DRUG_STRENGTH.csv. The path must be inside the configured
              <code className="mx-1 bg-gray-100 px-1 rounded">ATHENA_BUNDLE_ROOT</code> directory.
            </p>
            <input
              type="text"
              value={bundlePath}
              onChange={e => setBundlePath(e.target.value)}
              placeholder="/vocab"
              className="border border-gray-300 rounded-md px-3 py-2 text-sm font-mono w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              onClick={handleLoadVocab}
              disabled={!bundlePath.trim() || vocabStarting}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 disabled:opacity-50 w-fit"
            >
              {vocabStarting
                ? <><RefreshCw className="w-4 h-4 animate-spin" /> Starting…</>
                : <><PlayCircle className="w-4 h-4" /> Load vocabulary</>
              }
            </button>
            {vocabMessage && (
              <p className="text-xs text-green-700 bg-green-50 border border-green-200 rounded px-3 py-2">{vocabMessage}</p>
            )}
            <ErrorBanner message={vocabError} />
          </div>
        </details>

        {/* Connection hint */}
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 text-sm text-blue-800">
          <p className="font-medium">Inspecting results</p>
          <p className="mt-1 text-blue-700">
            From your host (after <code className="bg-blue-100 px-1 rounded">docker compose up</code>):
          </p>
          <pre className="mt-2 bg-blue-900 text-blue-50 text-xs rounded px-3 py-2 overflow-x-auto">
psql -h localhost -p 5432 -U omop -d omop -c "SELECT count(*) FROM {schema}.person;"
          </pre>
        </div>
      </div>
    </WizardLayout>
  )
}
