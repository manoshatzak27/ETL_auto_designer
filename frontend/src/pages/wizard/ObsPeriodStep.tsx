import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import { getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, ObservationPeriodConfig, ObsPeriodFallbackEntry } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import SingleConceptInput from '../../components/SingleConceptInput'
import DomainWarning from '../../components/DomainWarning'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { useDomainValidation } from '../../hooks/useDomainValidation'
import { useSourceFile } from '../../hooks/useSourceFile'
import { Plus, X } from 'lucide-react'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: ObservationPeriodConfig = {
  enabled: true,
  start_date_col: '',
  end_date_col: '',
  end_date_fallbacks: [{ type: 'start_date' }],
  period_type_concept_id: 32879,
}

function migrateConfig(ex: ObservationPeriodConfig & { extra_instructions?: string }): ObservationPeriodConfig & { extra_instructions?: string } {
  if (!ex.end_date_fallbacks && ex.end_date_fallback) {
    ex.end_date_fallbacks = [{ type: ex.end_date_fallback }]
  }
  if (!ex.end_date_fallbacks) {
    ex.end_date_fallbacks = [{ type: 'start_date' }]
  }
  return ex
}

export default function ObsPeriodStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const { cols: singleFileCols, files } = useSourceFile(project, 'obs-period')
  const isMultiFile = files.length > 1
  const [cfg, setCfg] = useState<ObservationPeriodConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const periodTypeViolations = useDomainValidation(
    cfg.period_type_concept_id > 0 ? [cfg.period_type_concept_id] : [],
    'Type Concept',
  )
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'observation_period', ['visit_occurrence']), [project.etl_config])

  useEffect(() => {
    getTableConfig(project.id, 'observation_period').then((ex: ObservationPeriodConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(migrateConfig(ex))
      }
    })
  }, [project.id])

  // Columns for a specific file. In multi-file mode, returns [] when no file is selected.
  const colsForFile = (filename?: string): string[] => {
    if (!isMultiFile) return singleFileCols
    if (!filename) return []
    const f = files.find(f => f.filename === filename)
    return f?.columns ?? []
  }

  // Columns available for a given field value, filtering cross-step used columns
  const availCols = (filename: string | undefined, currentValue: string): string[] => {
    return colsForFile(filename).filter(c => {
      if (c === currentValue) return true
      if (crossUsed.has(c)) return false
      return true
    })
  }

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'observation_period', {
      ...cfg,
      extra_instructions: extraInstructions,
      source_filename: isMultiFile ? null : (files[0]?.filename ?? null),
    })
    onUpdate(p)
  }

  const { prev, next } = getAdjacentSlugs(project, 'obs-period')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

  // Fallback chain helpers
  const fallbacks: ObsPeriodFallbackEntry[] = cfg.end_date_fallbacks ?? []
  const lastFallbackIsTerminal = fallbacks.length > 0 &&
    (fallbacks[fallbacks.length - 1].type === 'start_date' || fallbacks[fallbacks.length - 1].type === 'today')

  const addFallback = () => {
    const newEntry: ObsPeriodFallbackEntry = {
      type: 'column',
      col: '',
      source_filename: files[0]?.filename ?? '',
    }
    setCfg(prev => ({ ...prev, end_date_fallbacks: [...(prev.end_date_fallbacks ?? []), newEntry] }))
  }

  const removeFallback = (idx: number) => {
    setCfg(prev => ({
      ...prev,
      end_date_fallbacks: (prev.end_date_fallbacks ?? []).filter((_, i) => i !== idx),
    }))
  }

  const updateFallback = (idx: number, patch: Partial<ObsPeriodFallbackEntry>) => {
    setCfg(prev => {
      const fbs = [...(prev.end_date_fallbacks ?? [])]
      fbs[idx] = { ...fbs[idx], ...patch }
      // Reset col when switching away from 'column' type
      if (patch.type && patch.type !== 'column') {
        fbs[idx] = { type: patch.type }
      }
      // Reset col when file changes
      if (patch.source_filename !== undefined) {
        fbs[idx].col = ''
      }
      return { ...prev, end_date_fallbacks: fbs }
    })
  }

  const startDateCols = availCols(cfg.start_date_file, cfg.start_date_col)
  const endDateCols = availCols(cfg.end_date_file, cfg.end_date_col)

  return (
    <WizardLayout
      project={project}
      currentSlug="obs-period"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel="Next →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-primary">Observation Period Mapping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Define the time spans during which each patient was actively observed. Within these
            spans, clinical events are assumed to be fully recorded — absence of a record means
            the event did not occur. Each person must have at least one observation period.
            Overlapping or adjacent periods are automatically merged.
          </p>
        </div>

        <Card className="flex flex-col gap-5 p-6">

          {/* Start date */}
          <div className="flex flex-col gap-1">
            <Label>
              Start date column
              <span className="ml-1 text-destructive text-base font-bold leading-none">*</span>
            </Label>
            <p className="text-xs text-muted-foreground">
              Enrollment/study entry date. If absent in source, the earliest clinical event date per person will be used.
            </p>
            {isMultiFile && (
              <Select
                value={cfg.start_date_file ?? ''}
                onChange={e => setCfg(prev => ({ ...prev, start_date_file: e.target.value, start_date_col: '' }))}
                className="mt-1"
              >
                <option value="">— select file —</option>
                {files.map(f => <option key={f.filename} value={f.filename}>{f.filename}</option>)}
              </Select>
            )}
            <Select
              value={startDateCols.includes(cfg.start_date_col) ? cfg.start_date_col : ''}
              onChange={e => setCfg(prev => ({ ...prev, start_date_col: e.target.value }))}
              className="mt-1"
            >
              <option value="">— not mapped —</option>
              {startDateCols.map(c => <option key={c} value={c}>{c}</option>)}
            </Select>
          </div>

          {/* End date */}
          <div className="flex flex-col gap-1">
            <Label>End date column</Label>
            <p className="text-xs text-muted-foreground">
              Enrollment end / last follow-up date. If absent, the fallback chain below is used.
            </p>
            {isMultiFile && (
              <Select
                value={cfg.end_date_file ?? ''}
                onChange={e => setCfg(prev => ({ ...prev, end_date_file: e.target.value, end_date_col: '' }))}
                className="mt-1"
              >
                <option value="">— select file —</option>
                {files.map(f => <option key={f.filename} value={f.filename}>{f.filename}</option>)}
              </Select>
            )}
            <Select
              value={endDateCols.includes(cfg.end_date_col) ? cfg.end_date_col : ''}
              onChange={e => setCfg(prev => ({ ...prev, end_date_col: e.target.value }))}
              className="mt-1"
            >
              <option value="">— not mapped —</option>
              {endDateCols.map(c => <option key={c} value={c}>{c}</option>)}
            </Select>
          </div>

          {/* Fallback chain */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <Label>If end date is missing, fallback to:</Label>
              <button
                type="button"
                disabled={lastFallbackIsTerminal}
                onClick={addFallback}
                className="flex items-center gap-1 rounded px-2 py-1 text-xs font-medium text-primary border border-primary/40 hover:bg-primary/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
              >
                <Plus className="size-3" />
                Add fallback
              </button>
            </div>

            {fallbacks.length === 0 && (
              <p className="text-xs text-muted-foreground">
                No fallback configured — rows with missing end dates will use the start date.
              </p>
            )}

            {fallbacks.map((fb, idx) => (
              <div key={idx} className="flex flex-col gap-1">
                {/* Type row */}
                <div className="flex items-center gap-2">
                  <span className="w-5 text-right text-xs text-muted-foreground shrink-0">{idx + 1}.</span>
                  <Select
                    value={fb.type}
                    onChange={e => updateFallback(idx, { type: e.target.value as ObsPeriodFallbackEntry['type'] })}
                    className="w-52 shrink-0"
                  >
                    <option value="column">Column from file</option>
                    <option value="start_date">Start date (1-day period)</option>
                    <option value="today">Today's date</option>
                  </Select>
                  {fb.type !== 'column' && (
                    <span className="text-xs text-muted-foreground">
                      {fb.type === 'start_date' ? 'observation period of 1 day' : "uses today's date"}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => removeFallback(idx)}
                    className="ml-auto rounded p-1 text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors shrink-0"
                  >
                    <X className="size-3.5" />
                  </button>
                </div>

                {/* Column selection row — indented, only shown for 'column' type */}
                {fb.type === 'column' && (
                  <div className="ml-7 flex gap-2">
                    {isMultiFile && (
                      <Select
                        value={fb.source_filename ?? ''}
                        onChange={e => updateFallback(idx, { source_filename: e.target.value })}
                        className="w-44 shrink-0"
                      >
                        <option value="">— file —</option>
                        {files.map(f => <option key={f.filename} value={f.filename}>{f.filename}</option>)}
                      </Select>
                    )}
                    <Select
                      value={availCols(fb.source_filename, fb.col ?? '').includes(fb.col ?? '') ? (fb.col ?? '') : ''}
                      onChange={e => updateFallback(idx, { col: e.target.value })}
                      className="flex-1"
                    >
                      <option value="">— not mapped —</option>
                      {availCols(fb.source_filename, fb.col ?? '').map(c => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </Select>
                  </div>
                )}
              </div>
            ))}
          </div>

          {/* Date format */}
          <div>
            <Label>Date format</Label>
            <Input
              type="text"
              value={cfg.date_format ?? '%Y-%m-%d'}
              onChange={e => setCfg(prev => ({ ...prev, date_format: e.target.value || '%Y-%m-%d' }))}
              placeholder="%Y-%m-%d"
              className="mt-1 font-mono"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Python strptime format applied to all date columns (e.g. <code className="bg-muted px-1 rounded">%d/%m/%Y</code>, <code className="bg-muted px-1 rounded">%Y%m%d</code>).
            </p>
          </div>

          {/* Period type concept */}
          <div>
            <Label>
              period_type_concept_id
              <span className="ml-1 font-normal text-muted-foreground">— how the period was determined</span>
            </Label>
            <SingleConceptInput
              value={cfg.period_type_concept_id || null}
              onChange={v => setCfg(prev => ({ ...prev, period_type_concept_id: v ?? 0 }))}
              placeholder="e.g. 32879"
            />
            <a
              href="https://athena.ohdsi.org/search-terms/terms?domain=Type+Concept&standardConcept=Standard&page=1&pageSize=15&query="
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-block text-xs text-primary hover:underline"
            >
              Accepted Concepts (Athena)
            </a>
            <DomainWarning violations={periodTypeViolations} expectedDomain="Type Concept" />
          </div>
        </Card>

        <ExtraInstructions
          tableName="observation_period"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        <ScriptGenerator
          project={project}
          table="observation_period"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
          deterministic
        />
      </div>
    </WizardLayout>
  )
}
