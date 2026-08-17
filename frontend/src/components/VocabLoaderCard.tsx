// Self-contained vocabulary-loader card. Detects the Athena bundle at
// /vocab, displays Postgres + vocab-schema health, runs `loadVocabulary`,
// and polls `getVocabStatus` for per-file progress. Originally lived
// inside FinalizeStep.tsx; lifted out so SourceStep can also offer
// the load so users can kick it off early and let it run while they
// configure the rest of the wizard.

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getDbHealth,
  getVocabBundleInfo,
  getVocabStatus,
  loadVocabulary,
  type DbHealth,
  type VocabBundleInfo,
  type VocabFileStatus,
  type VocabLoadStatus,
} from '../api/client'
import ErrorBanner from './ErrorBanner'
import {
  Loader2, RefreshCw, CheckCircle, AlertCircle, PlayCircle, BookOpen, Server,
} from 'lucide-react'
import clsx from 'clsx'

const DEFAULT_VOCAB_PATH = '/vocab'

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

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`
  return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`
}

export default function VocabLoaderCard() {
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

  const refreshVocabInfo = async (pathToCheck?: string) => {
    const probe = (pathToCheck ?? bundlePath ?? DEFAULT_VOCAB_PATH).trim() || DEFAULT_VOCAB_PATH
    try {
      const info = await getVocabBundleInfo(probe)
      setVocabInfo(info)
      if (info.exists && info.detected_files.length > 0) setBundlePath(info.path)
    } catch {
      setVocabInfo({ path: probe, exists: false, detected_files: [], total_size_bytes: 0 })
    }
  }

  useEffect(() => { refreshHealth(); refreshVocabInfo(DEFAULT_VOCAB_PATH) }, [])

  // Poll vocab status only while a load is actually running; refresh health
  // on completion so vocab_rows updates. A single fetch on mount covers the
  // idle/success/error cases (and picks up a load already in progress after
  // a page refresh) without polling forever.
  const pollTimerRef = useRef<number | null>(null)

  const pollVocabStatus = useCallback(async () => {
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
    try {
      const s = await getVocabStatus()
      setVocabStatus(s)
      if (s.overall === 'success' || s.overall === 'error') refreshHealth()
      if (s.overall === 'running') {
        pollTimerRef.current = window.setTimeout(pollVocabStatus, 1500)
      }
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    pollVocabStatus()
    return () => {
      if (pollTimerRef.current !== null) window.clearTimeout(pollTimerRef.current)
    }
  }, [pollVocabStatus])

  const vocabHasFiles = (vocabInfo?.detected_files?.length ?? 0) > 0
  const vocabRunning = vocabStatus?.overall === 'running'

  // Tick a local clock while a file is actively loading so its elapsed time
  // counts up live between status polls, instead of jumping only once the
  // file finishes and the backend reports a final value.
  const [now, setNow] = useState(() => Date.now())
  const activeFile = vocabStatus?.files.find(f => f.status === 'loading')
  useEffect(() => {
    if (!activeFile) return
    const id = window.setInterval(() => setNow(Date.now()), 200)
    return () => window.clearInterval(id)
  }, [activeFile])

  const displayElapsed = (f: VocabFileStatus) =>
    f.status === 'loading' && f.started_at > 0
      ? Math.max(0, now / 1000 - f.started_at)
      : f.elapsed

  const handleLoadVocab = async () => {
    const path = (bundlePath || DEFAULT_VOCAB_PATH).trim()
    if (!path) return
    setVocabStarting(true)
    setVocabError('')
    try {
      await loadVocabulary({ bundle_path: path })
      pollVocabStatus()
    } catch (e: unknown) {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setVocabError(typeof msg === 'string' ? msg : 'Failed to start vocabulary load')
    } finally {
      setVocabStarting(false)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Postgres health panel — informational */}
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
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
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
            <HealthRow label="DDL applied" ok={health.ddl_applied} />
          </div>
        )}
        {health?.error && (
          <p className="text-xs text-red-700 bg-red-50 border border-red-200 rounded px-2 py-1.5">{health.error}</p>
        )}
      </div>

      {/* Vocab loader */}
      <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-gray-500" />
            <h3 className="font-medium text-gray-800">Load vocabulary</h3>
          </div>
          {vocabStatus?.overall === 'success' && (
            <span className="flex items-center gap-1 text-xs text-green-700">
              <CheckCircle className="w-3.5 h-3.5" /> Loaded
            </span>
          )}
        </div>
        <p className="text-xs text-gray-500">
          Bulk-loads an Athena vocabulary bundle into the shared{' '}
          <code className="bg-gray-100 px-1 rounded font-mono">{health?.vocab_schema || 'vocab'}</code> schema.
          Shared across all projects; safe to run while you configure the rest of the wizard.
          Re-running truncates and reloads.
        </p>

        {vocabInfo && vocabHasFiles ? (
          <div className="border border-emerald-200 bg-emerald-50 rounded-lg p-3 text-sm">
            <p className="font-medium text-emerald-900">
              Detected {vocabInfo.detected_files.length} vocabulary file{vocabInfo.detected_files.length === 1 ? '' : 's'}
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
              No Athena CSVs detected at <code className="bg-white/60 px-1 rounded font-mono">{vocabInfo?.path || DEFAULT_VOCAB_PATH}</code>.
              Mount your unzipped Athena bundle to that path in <code className="bg-white/60 px-1 rounded">docker-compose.yml</code>,
              or enter an alternative path here.
            </p>
            <input
              autoComplete="off"
              type="text"
              value={bundlePath}
              onChange={e => setBundlePath(e.target.value)}
              placeholder={DEFAULT_VOCAB_PATH}
              className="border border-gray-300 rounded-md px-3 py-2 text-sm font-mono w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button onClick={() => refreshVocabInfo(bundlePath)} className="text-xs text-blue-600 hover:underline w-fit">Re-check path</button>
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
                    <td className="px-3 py-2 text-right text-gray-500">{displayElapsed(f).toFixed(1)}s</td>
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
    </div>
  )
}
