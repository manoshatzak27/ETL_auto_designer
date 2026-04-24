import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { executeProject, downloadOutput } from '../../api/client'
import type { Project } from '../../types'
import WizardLayout from './WizardLayout'
import ScriptGenerator from '../../components/ScriptGenerator'
import LogStream from '../../components/LogStream'
import {
  PlayCircle, Download, RefreshCw, CheckCircle,
  AlertCircle, AlertTriangle,
} from 'lucide-react'
import { basename } from '../../utils'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const TABLES = [
  { key: 'person',             label: 'person.py',             description: 'Patient demographics' },
  { key: 'visit_occurrence',   label: 'visit_occurrence.py',   description: 'Clinical visits / timepoints' },
  { key: 'observation_period', label: 'observation_period.py', description: 'Patient observation windows' },
  { key: 'stem_table',         label: 'stem_table.py',         description: 'Clinical measurements & observations' },
  { key: 'death',              label: 'death.py',              description: 'Mortality records' },
]

export default function Step7Generate({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [executing, setExecuting] = useState(false)
  const [execResult, setExecResult] = useState<{ status: string; log: string; output_files: string[] } | null>(null)
  const [execError, setExecError] = useState('')

  const scripts: Record<string, string> = project.generated_scripts || {}
  const generatedCount = TABLES.filter(t => scripts[t.key]).length
  const allGenerated   = generatedCount === TABLES.length
  const anyGenerated   = generatedCount > 0
  const missingTables  = TABLES.filter(t => !scripts[t.key]).map(t => t.key)

  const handleExecute = async () => {
    setExecuting(true)
    setExecError('')
    try {
      const result = await executeProject(project.id)
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

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={8}
      onBack={() => navigate(`/project/${project.id}/step/7`)}
      nextLabel=""
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Execute ETL Pipeline</h2>
          <p className="text-sm text-gray-500 mt-1">
            Review your generated scripts, then run the full pipeline. Scripts are generated per table in the
            mapping steps — go back to any step to regenerate with updated settings.
          </p>
        </div>

        {/* Script status summary */}
        <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-gray-800">Script Status</h3>
            <span className={`text-xs font-medium px-2.5 py-1 rounded-full ${
              allGenerated ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'
            }`}>
              {generatedCount} / {TABLES.length} ready
            </span>
          </div>

          <div className="w-full bg-gray-100 rounded-full h-1.5">
            <div
              className="bg-green-500 h-1.5 rounded-full transition-all"
              style={{ width: `${(generatedCount / TABLES.length) * 100}%` }}
            />
          </div>

          <div className="grid grid-cols-1 gap-1.5">
            {TABLES.map(t => {
              const has = !!scripts[t.key]
              const lineCount = has ? scripts[t.key].split('\n').length : 0
              return (
                <div key={t.key} className="flex items-center gap-3 px-3 py-2 rounded-lg bg-gray-50">
                  {has
                    ? <CheckCircle className="w-4 h-4 text-green-500 flex-shrink-0" />
                    : <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
                  }
                  <span className="text-xs font-mono font-medium text-gray-800">{t.label}</span>
                  <span className="text-xs text-gray-400 ml-auto">
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
                Go back to the corresponding step and click <strong>Generate</strong> to create it.
              </span>
            </div>
          )}
        </div>

        {/* Regenerate any script inline */}
        <div className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-gray-800">Regenerate a script</h3>
          <p className="text-xs text-gray-500 -mt-1">
            You can regenerate any script here without going back to the mapping step. Changes to the
            configuration itself must be made in the corresponding step.
          </p>
          {TABLES.map(t => (
            <ScriptGenerator key={t.key} project={project} table={t.key} onUpdate={onUpdate} />
          ))}
        </div>

        {/* Execute */}
        {anyGenerated && (
          <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-4">
            <div>
              <h3 className="font-semibold text-gray-800">Run ETL Pipeline</h3>
              <p className="text-xs text-gray-500 mt-1">
                Executes all generated scripts in order:
                <span className="font-mono text-gray-600"> person → visit_occurrence → observation_period → stem_table → death</span>.
                Each script reads your source CSV and writes its OMOP output CSV.
              </p>
            </div>

            {!allGenerated && (
              <p className="text-xs text-amber-600">
                Some scripts are missing — only generated tables will run.
              </p>
            )}

            <button
              onClick={handleExecute}
              disabled={executing}
              className="flex items-center gap-2 px-6 py-3 bg-green-600 text-white rounded-lg font-medium hover:bg-green-700 disabled:opacity-50 w-fit"
            >
              {executing
                ? <><RefreshCw className="w-5 h-5 animate-spin" /> Running pipeline…</>
                : <><PlayCircle className="w-5 h-5" /> Execute ETL Pipeline</>
              }
            </button>
          </div>
        )}

        {execError && !execResult && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700 flex items-start gap-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            {execError}
          </div>
        )}

        {execResult && <LogStream log={execResult.log} status={execResult.status} />}

        {execResult?.output_files && execResult.output_files.length > 0 && (
          <div className="bg-white border border-gray-200 rounded-xl p-5 flex flex-col gap-3">
            <h3 className="font-medium text-gray-800">Output OMOP Files</h3>
            <div className="grid grid-cols-2 gap-2">
              {execResult.output_files.map(f => (
                <div key={f} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg border border-gray-100">
                  <div className="flex items-center gap-2">
                    <CheckCircle className="w-3.5 h-3.5 text-green-500" />
                    <span className="text-sm font-mono text-gray-700">{basename(f)}</span>
                  </div>
                  <button
                    onClick={() => downloadOutput(project.id, basename(f))}
                    className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium"
                  >
                    <Download className="w-3.5 h-3.5" /> Download
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </WizardLayout>
  )
}
