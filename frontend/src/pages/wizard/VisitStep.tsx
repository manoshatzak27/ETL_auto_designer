import { useState, useEffect, useMemo, type Dispatch, type SetStateAction } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import { getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, VisitOccurrenceConfig, VisitDefinition, PerFileVisitConfig } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import DomainWarning from '../../components/DomainWarning'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Plus, Trash2, ExternalLink, FileText } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { useDomainValidation } from '../../hooks/useDomainValidation'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

interface ColumnInfo { distinct_values: string[] }

const DEFAULT_VISIT: VisitDefinition = {
  label: '',
  date_col: '',
  visit_concept_id: 9202,
  type_concept_id: 32879,
  optional: false,
}

const DEFAULTS: VisitOccurrenceConfig = {
  enabled: true,
  visit_definitions: [
    { label: 'Baseline', date_col: '', visit_concept_id: 9202, type_concept_id: 32879, optional: false },
  ],
}

const VISIT_CONCEPTS = [
  { id: 9202,     label: '9202 — Outpatient Visit' },
  { id: 9201,     label: '9201 — Inpatient Visit' },
  { id: 9203,     label: '9203 — Emergency Room Visit' },
  { id: 262,      label: '262 — Emergency Room and Inpatient Visit' },
  { id: 42898160, label: '42898160 — Non-hospital Institution Visit' },
  { id: 581476,   label: '581476 — Home Visit' },
  { id: 5083,     label: '5083 — Telehealth Visit' },
  { id: 581458,   label: '581458 — Pharmacy Visit' },
  { id: 32036,    label: '32036 — Laboratory Visit' },
  { id: 581478,   label: '581478 — Ambulance Visit' },
  { id: 38004193, label: '38004193 — Case Management Visit' },
]

const TYPE_CONCEPTS = [
  { id: 32879,    label: '32879 — Registry' },
  { id: 32817,    label: '32817 — EHR' },
  { id: 44818518, label: '44818518 — Visit derived by algorithm' },
  { id: 32220,    label: '32220 — Still patient (ongoing inpatient)' },
]

const ADMITTED_FROM_CONCEPTS = [
  { id: 0,        label: '0 — Home / self-referred' },
  { id: 8765,     label: '8765 — Home' },
  { id: 8892,     label: '8892 — Emergency Room' },
  { id: 8717,     label: '8717 — Inpatient Hospital' },
  { id: 8863,     label: '8863 — Long-term Care Facility' },
  { id: 8920,     label: '8920 — Other' },
]

const DISCHARGED_TO_CONCEPTS = [
  { id: 0,        label: '0 — Home' },
  { id: 8536,     label: '8536 — Home Health Care' },
  { id: 8863,     label: '8863 — Long-term Care Facility' },
  { id: 8717,     label: '8717 — Inpatient Hospital (transfer)' },
  { id: 8892,     label: '8892 — Emergency Room (transfer)' },
  { id: 4216643,  label: '4216643 — Patient died' },
  { id: 8920,     label: '8920 — Other' },
]

const ATHENA = {
  visit_concept_id:          'https://athena.ohdsi.org/search-terms/terms?domain=Visit&standardConcept=Standard&page=1&pageSize=15&query=',
  visit_type_concept_id:     'https://athena.ohdsi.org/search-terms/terms?domain=Type+Concept&standardConcept=Standard&page=1&pageSize=15&query=',
  admitted_from_concept_id:  'https://athena.ohdsi.org/search-terms/terms?domain=Visit&standardConcept=Standard&page=1&pageSize=15&query=',
  discharged_to_concept_id:  'https://athena.ohdsi.org/search-terms/terms?domain=Visit&standardConcept=Standard&page=1&pageSize=15&query=',
}

const INPATIENT_CONCEPT_IDS = new Set([9201, 262, 42898160])

function AthenaLink({ href }: { href: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
      Find on Athena <ExternalLink className="w-3 h-3" />
    </a>
  )
}

export default function VisitStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const allFiles = project.source_files ?? []
  const isMultiFileProject = allFiles.length > 1

  // ── File selection ────────────────────────────────────────────────────────
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [activeFilename, setActiveFilename] = useState<string>('')
  const [fileConfigs, setFileConfigs] = useState<Record<string, PerFileVisitConfig>>({})

  // ── Active file config state ──────────────────────────────────────────────
  const [cfg, setCfg] = useState<VisitOccurrenceConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [conceptModes, setConceptModes] = useState<Array<'column' | 'default'>>(['column'])
  const [typeModes, setTypeModes] = useState<Array<'column' | 'default'>>(['column'])

  const isMultiRow = !!(project.etl_config?.dataset_options as Record<string, unknown> | undefined)?.multiple_rows_per_patient

  // Columns for whichever file is active
  const activeFile = allFiles.find(f => f.filename === activeFilename)
  const cols = activeFile?.columns ?? project.source_columns ?? []

  const getMode = (arr: Array<'column' | 'default'>, i: number): 'column' | 'default' => arr[i] ?? 'column'
  const setMode = (setter: Dispatch<SetStateAction<Array<'column' | 'default'>>>, i: number, mode: 'column' | 'default') =>
    setter(prev => { const next = [...prev]; next[i] = mode; return next })

  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'visit_occurrence', ['observation_period']), [project.etl_config])
  const visitSourceCol = cfg.visit_source_col
  const availCols = (currentValue: string) =>
    cols.filter(c => {
      if (c === currentValue) return true
      if (crossUsed.has(c)) return false
      if (visitSourceCol && c === visitSourceCol) return false
      return true
    })

  const allVisitConceptIds = cfg.visit_definitions.flatMap((vd, i) =>
    getMode(conceptModes, i) === 'column' ? Object.values(vd.visit_concept_value_map ?? {}) : [],
  )
  const visitConceptViolations = useDomainValidation(allVisitConceptIds, 'Visit')

  const allTypeConceptIds = cfg.visit_definitions.flatMap((vd, i) =>
    getMode(typeModes, i) === 'column' ? Object.values(vd.visit_type_value_map ?? {}) : [],
  )
  const typeConceptViolations = useDomainValidation(allTypeConceptIds, 'Type Concept')

  // ── Apply a saved per-file config to UI state ─────────────────────────────
  const applyFileConfig = (fc: PerFileVisitConfig | undefined) => {
    const vds = fc?.visit_definitions ?? DEFAULTS.visit_definitions
    setCfg(prev => ({
      ...prev,
      visit_definitions: vds,
      visit_source_col: fc?.visit_source_col,
      auto_number_visits: fc?.auto_number_visits,
    }))
    setConceptModes(vds.map(vd => vd.visit_concept_mode ?? (vd.visit_concept_source_col ? 'column' : 'default')))
    setTypeModes(vds.map(vd => vd.visit_type_mode ?? (vd.visit_type_source_col ? 'column' : 'default')))
  }

  // ── Initial load ──────────────────────────────────────────────────────────
  useEffect(() => {
    getTableConfig(project.id, 'visit_occurrence').then((ex: VisitOccurrenceConfig & { extra_instructions?: string }) => {
      setExtraInstructions(ex?.extra_instructions || '')

      if (ex?.file_configs && ex.file_configs.length > 0) {
        // Multi-file config saved
        const configs: Record<string, PerFileVisitConfig> = {}
        for (const fc of ex.file_configs) configs[fc.source_filename] = fc
        setFileConfigs(configs)
        const selected = ex.file_configs.map(fc => fc.source_filename)
        setSelectedFiles(selected)
        const first = selected[0] ?? ''
        setActiveFilename(first)
        applyFileConfig(configs[first])
      } else if (ex && Object.keys(ex).length > 0) {
        // Legacy single-file or brand-new single-file config
        const vds = ex.visit_definitions ?? DEFAULTS.visit_definitions
        setCfg({ ...DEFAULTS, ...ex })
        setConceptModes(vds.map(vd => vd.visit_concept_mode ?? (vd.visit_concept_source_col ? 'column' : 'default')))
        setTypeModes(vds.map(vd => vd.visit_type_mode ?? (vd.visit_type_source_col ? 'column' : 'default')))
        const first = allFiles[0]?.filename ?? ''
        setSelectedFiles(allFiles.map(f => f.filename))
        setActiveFilename(first)
      } else {
        // Nothing saved yet — select all files by default
        const initial = allFiles.map(f => f.filename)
        setSelectedFiles(initial)
        setActiveFilename(initial[0] ?? '')
        applyFileConfig(undefined)  // first file gets the Baseline default
        // Pre-populate subsequent files with a blank (unlabelled) template
        if (initial.length > 1) {
          const blankConfigs: Record<string, PerFileVisitConfig> = {}
          for (const fname of initial.slice(1)) {
            blankConfigs[fname] = { source_filename: fname, visit_definitions: [{ ...DEFAULT_VISIT }] }
          }
          setFileConfigs(blankConfigs)
        }
      }
    })
    getColumnValues(project.id).then(setColumnInfos)
  }, [project.id])

  const distinctVals = (col: string): string[] => columnInfos[col]?.distinct_values ?? []

  // ── Per-file switch ───────────────────────────────────────────────────────
  const buildCurrentFileConfig = (): PerFileVisitConfig => ({
    source_filename: activeFilename,
    visit_definitions: cfg.visit_definitions.map((vd, i) => ({
      ...vd,
      visit_concept_mode: getMode(conceptModes, i),
      visit_type_mode: getMode(typeModes, i),
    })),
    visit_source_col: cfg.visit_source_col,
    auto_number_visits: cfg.auto_number_visits,
  })

  const switchActiveFile = (newFilename: string) => {
    if (newFilename === activeFilename) return
    const updatedConfigs = { ...fileConfigs, [activeFilename]: buildCurrentFileConfig() }
    setFileConfigs(updatedConfigs)
    applyFileConfig(updatedConfigs[newFilename])
    setActiveFilename(newFilename)
  }

  const toggleFile = (filename: string, checked: boolean) => {
    if (checked) {
      const isFirst = selectedFiles.length === 0
      // Non-first files get a blank template if they have no saved config yet
      if (!isFirst && !fileConfigs[filename]) {
        setFileConfigs(prev => ({
          ...prev,
          [filename]: { source_filename: filename, visit_definitions: [{ ...DEFAULT_VISIT }] },
        }))
      }
      setSelectedFiles(prev => prev.includes(filename) ? prev : [...prev, filename])
      if (!activeFilename) setActiveFilename(filename)
    } else {
      const newSelected = selectedFiles.filter(f => f !== filename)
      setSelectedFiles(newSelected)
      if (activeFilename === filename) {
        const updatedConfigs = { ...fileConfigs, [activeFilename]: buildCurrentFileConfig() }
        setFileConfigs(updatedConfigs)
        const fallback = newSelected[0]
        applyFileConfig(fallback ? updatedConfigs[fallback] : undefined)
        setActiveFilename(fallback ?? '')
      }
    }
  }

  // ── Visit definition helpers ──────────────────────────────────────────────
  const updateVisit = (i: number, field: keyof VisitDefinition, value: unknown) =>
    setCfg(prev => {
      const defs = [...prev.visit_definitions]
      defs[i] = { ...defs[i], [field]: value }
      return { ...prev, visit_definitions: defs }
    })

  const updateVisitFields = (i: number, fields: Partial<VisitDefinition>) =>
    setCfg(prev => {
      const defs = [...prev.visit_definitions]
      defs[i] = { ...defs[i], ...fields }
      return { ...prev, visit_definitions: defs }
    })

  const addVisit = () => {
    setCfg(prev => ({ ...prev, visit_definitions: [...prev.visit_definitions, { ...DEFAULT_VISIT }] }))
    setConceptModes(prev => [...prev, 'column'])
    setTypeModes(prev => [...prev, 'column'])
  }

  const removeVisit = (i: number) => {
    setCfg(prev => ({ ...prev, visit_definitions: prev.visit_definitions.filter((_, j) => j !== i) }))
    setConceptModes(prev => prev.filter((_, j) => j !== i))
    setTypeModes(prev => prev.filter((_, j) => j !== i))
  }

  const switchConceptMode = (i: number, mode: 'column' | 'default') => {
    setMode(setConceptModes, i, mode)
    if (mode === 'default') updateVisitFields(i, { visit_concept_mode: mode, visit_concept_source_col: undefined, visit_concept_value_map: undefined })
    else updateVisit(i, 'visit_concept_mode', mode)
  }

  const switchTypeMode = (i: number, mode: 'column' | 'default') => {
    setMode(setTypeModes, i, mode)
    if (mode === 'default') updateVisitFields(i, { visit_type_mode: mode, visit_type_source_col: undefined, visit_type_value_map: undefined })
    else updateVisit(i, 'visit_type_mode', mode)
  }

  // ── Save ──────────────────────────────────────────────────────────────────
  const saveConfig = async () => {
    const currentFc = buildCurrentFileConfig()
    const updatedConfigs = activeFilename ? { ...fileConfigs, [activeFilename]: currentFc } : fileConfigs

    const fileConfigsArr = isMultiFileProject
      ? selectedFiles
          .filter(fname => allFiles.some(f => f.filename === fname))
          .map(fname => updatedConfigs[fname] ?? { source_filename: fname, visit_definitions: [{ ...DEFAULT_VISIT }] })
      : undefined

    const cfgToSave: VisitOccurrenceConfig & { extra_instructions: string; source_filename: string | null; file_configs?: PerFileVisitConfig[] } = {
      ...cfg,
      visit_definitions: currentFc.visit_definitions,
      extra_instructions: extraInstructions,
      source_filename: activeFilename || null,
      file_configs: fileConfigsArr,
    }
    const p = await updateTableConfig(project.id, 'visit_occurrence', cfgToSave)
    onUpdate(p)
  }

  const { prev, next } = getAdjacentSlugs(project, 'visit')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

  return (
    <WizardLayout
      project={project}
      currentSlug="visit"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel="Next →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">

        {/* ── Multi-file instructions ────────────────────────────────────── */}
        {isMultiFileProject && (
          <div className="rounded-lg border border-border bg-secondary/40 px-4 py-3 text-sm text-muted-foreground space-y-1">
            <p className="font-medium text-foreground">Building the visit table from multiple files</p>
            <ul className="list-disc list-inside text-xs space-y-0.5">
              <li>Select which files contribute visits using the checkboxes below.</li>
              <li>Each file has its own set of visit definitions — configure them independently using the file tabs.</li>
              <li>All visits across all files are written into a single <span className="font-medium text-foreground">visit_occurrence.csv</span>; IDs are assigned in file order.</li>
              <li>Duplicate <span className="font-medium text-foreground">record_source_value</span> entries (same patient + same visit label) are skipped with a warning.</li>
            </ul>
          </div>
        )}

        {/* ── File selection card ─────────────────────────────────────────── */}
        {isMultiFileProject && (
          <Card className="flex flex-col gap-4 p-6">
            <div>
              <h3 className="font-semibold text-foreground">Source Files</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Select which files contribute to the visit table. Each file has its own visit definitions configured independently.
              </p>
            </div>
            <div className="flex flex-col gap-2">
              {allFiles.map(f => {
                const orderIdx = selectedFiles.indexOf(f.filename)
                const isSelected = orderIdx !== -1
                return (
                  <label key={f.filename} className="flex items-center gap-3 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={e => toggleFile(f.filename, e.target.checked)}
                      className="w-4 h-4 accent-primary rounded"
                    />
                    <FileText className="size-4 shrink-0 text-muted-foreground" />
                    <span className="text-sm text-foreground">{f.filename}</span>
                    {isSelected && orderIdx === 0 && (
                      <span className="text-xs font-semibold bg-primary text-primary-foreground px-2 py-0.5 rounded-full">Primary</span>
                    )}
                    {isSelected && orderIdx > 0 && (
                      <span className="text-xs text-muted-foreground">File #{orderIdx + 1}</span>
                    )}
                  </label>
                )
              })}
            </div>
            {selectedFiles.length === 0 && (
              <p className="text-sm text-amber-600 dark:text-amber-400">Select at least one file to configure visit definitions.</p>
            )}
          </Card>
        )}

        {/* ── File config tabs (outside the card, like PersonStep) ─────────── */}
        {selectedFiles.length > 1 && (
          <div className="flex flex-col gap-1.5">
            <p className="text-xs text-muted-foreground font-medium">Configure visit definitions per file</p>
            <div className="flex flex-wrap gap-2">
              {selectedFiles.map(fname => (
                <button
                  key={fname}
                  onClick={() => switchActiveFile(fname)}
                  className={
                    fname === activeFilename
                      ? 'flex items-center gap-1.5 rounded-full border border-primary bg-primary/10 px-3 py-1 text-xs font-semibold text-primary transition-colors'
                      : 'flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground hover:border-primary/50 hover:text-foreground transition-colors'
                  }
                >
                  <FileText className="size-3 flex-shrink-0" />
                  {fname}
                  {selectedFiles.indexOf(fname) === 0 && (
                    <span className="ml-0.5 opacity-60">(Primary)</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        <div>
          <h2 className="text-xl font-bold text-primary">Visit Occurrence Mapping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Define the clinical visits. Each visit definition creates one row in <code className="bg-muted px-1 rounded">visit_occurrence</code> per patient.
            Map each CDM field to a source column and assign OMOP concept IDs to the values.
          </p>
        </div>

        {isMultiRow && (
          <Card className="flex flex-col gap-3 p-4 border-primary/40 bg-primary/5">
            <p className="text-sm font-semibold text-primary">Multi-row dataset mode</p>
            <p className="text-xs text-muted-foreground">
              Your dataset has multiple rows per patient. Select the column that identifies the visit type
              (e.g. "baseline", "followup"). The visit source value will be composed as{' '}
              <code className="bg-muted px-1 rounded">person_id — filename — visit_value</code>.
              One visit occurrence is created per row; the label comes from the column, not the text field below.
            </p>
            <div className="flex items-start gap-2 rounded-md border border-muted bg-muted/40 p-3">
              <input
                type="checkbox"
                id="auto-number-visits"
                checked={cfg.auto_number_visits ?? false}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  setCfg(prev => ({
                    ...prev,
                    auto_number_visits: e.target.checked,
                    visit_source_col: e.target.checked ? undefined : prev.visit_source_col,
                  }))
                }
                className="mt-0.5 h-4 w-4 cursor-pointer accent-primary"
              />
              <div className="flex flex-col gap-0.5">
                <label htmlFor="auto-number-visits" className="text-sm font-medium cursor-pointer leading-none">
                  Auto-number visits
                </label>
                <p className="text-xs text-muted-foreground">
                  Each row for the same patient will be labelled{' '}
                  <code className="bg-muted px-1 rounded">visit1</code>,{' '}
                  <code className="bg-muted px-1 rounded">visit2</code>, … in order of appearance.
                </p>
              </div>
            </div>
            {!cfg.auto_number_visits && (
              <FieldMapper
                label="Visit identifier column"
                sourceColumns={availCols(cfg.visit_source_col ?? '')}
                value={cfg.visit_source_col ?? ''}
                onChange={v => setCfg(prev => ({ ...prev, visit_source_col: v || undefined }))}
                hint="Column whose values identify the visit type (e.g. 'baseline', 'followup')."
              />
            )}
          </Card>
        )}

        <div className="flex flex-col gap-6">
          {cfg.visit_definitions.map((vd, i) => {
            const vdVisitIds = getMode(conceptModes, i) === 'column'
              ? Object.values(vd.visit_concept_value_map ?? {}) : []
            const vdVisitViolations = new Map(
              [...visitConceptViolations.entries()].filter(([id]) => vdVisitIds.includes(id)),
            )
            const vdTypeIds = getMode(typeModes, i) === 'column'
              ? Object.values(vd.visit_type_value_map ?? {}) : []
            const vdTypeViolations = new Map(
              [...typeConceptViolations.entries()].filter(([id]) => vdTypeIds.includes(id)),
            )
            return (
              <div key={i} className="flex flex-col gap-4 rounded-lg border border-border bg-secondary/70 p-4">
                {/* Visit header */}
                <div className="flex items-center justify-between">
                  <p className="text-base font-bold uppercase tracking-wide text-muted-foreground">
                    {isMultiRow ? 'Visit Template' : `Visit ${i + 1}`}
                  </p>
                  {cfg.visit_definitions.length > 1 && (
                    <button onClick={() => removeVisit(i)} className="shrink-0 text-destructive/60 hover:text-destructive">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>

                {/* ── Group 1: Identity & dates ───────────────────────────── */}
                <Card className="flex flex-col gap-3 p-4">
                  <h3 className="font-semibold text-foreground">Visit identity &amp; dates</h3>

                  <div>
                    <Label>Visit label</Label>
                    {isMultiRow ? (
                      <p className="mt-1 text-xs text-muted-foreground italic">
                        Label is derived from the visit identifier column at runtime.
                      </p>
                    ) : (
                      <Input
                        type="text"
                        value={vd.label}
                        onChange={e => updateVisit(i, 'label', e.target.value)}
                        placeholder="e.g. Onset, Baseline, 10y Follow-up"
                        className="mt-1"
                      />
                    )}
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <FieldMapper
                      label="Start date"
                      sourceColumns={availCols(vd.date_col)}
                      value={vd.date_col}
                      onChange={v => updateVisit(i, 'date_col', v)}
                      required={!vd.optional}
                    />
                    <FieldMapper
                      label="Start time (optional)"
                      sourceColumns={availCols(vd.time_col ?? '')}
                      value={vd.time_col ?? ''}
                      onChange={v => updateVisit(i, 'time_col', v || undefined)}
                    />
                    <FieldMapper
                      label="End date (optional)"
                      sourceColumns={availCols(vd.end_date_col ?? '')}
                      value={vd.end_date_col ?? ''}
                      onChange={v => updateVisit(i, 'end_date_col', v || undefined)}
                    />
                    <FieldMapper
                      label="End time (optional)"
                      sourceColumns={availCols(vd.end_time_col ?? '')}
                      value={vd.end_time_col ?? ''}
                      onChange={v => updateVisit(i, 'end_time_col', v || undefined)}
                    />
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div>
                      <Label>Date format</Label>
                      <Input
                        type="text"
                        value={vd.date_format ?? '%Y-%m-%d'}
                        onChange={e => updateVisit(i, 'date_format', e.target.value || '%Y-%m-%d')}
                        placeholder="%Y-%m-%d"
                        className="mt-1 font-mono"
                      />
                    </div>
                    <div>
                      <Label>Time format</Label>
                      <Input
                        type="text"
                        value={vd.time_format ?? '%H:%M:%S'}
                        onChange={e => updateVisit(i, 'time_format', e.target.value || '%H:%M:%S')}
                        placeholder="%H:%M:%S"
                        className="mt-1 font-mono"
                      />
                    </div>
                  </div>

                  <p className="text-xs text-muted-foreground -mt-1">
                    Python strptime formats. End date blank → same as start date. Missing time → midnight (00:00:00).
                    Examples: <code className="bg-muted px-1 rounded">%d/%m/%Y</code>, <code className="bg-muted px-1 rounded">%Y%m%d</code>, <code className="bg-muted px-1 rounded">%H:%M</code>.
                  </p>
                </Card>

                {/* ── Group 2: visit_concept_id ───────────────────────────── */}
                <Card className="flex flex-col gap-4 p-6">
                  <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-foreground">visit_concept_id</h3>
                    <AthenaLink href={ATHENA.visit_concept_id} />
                  </div>

                  <div className="flex gap-2">
                    <button onClick={() => switchConceptMode(i, 'column')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${getMode(conceptModes, i) === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Map a column</button>
                    <button onClick={() => switchConceptMode(i, 'default')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${getMode(conceptModes, i) === 'default' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Set default</button>
                  </div>

                  {getMode(conceptModes, i) === 'column' ? (
                    <div className="flex flex-col gap-3">
                      <FieldMapper
                        label="visit_concept_id column"
                        sourceColumns={availCols(vd.visit_concept_source_col ?? '')}
                        value={vd.visit_concept_source_col ?? ''}
                        onChange={v => updateVisit(i, 'visit_concept_source_col', v || undefined)}
                        hint="Values will be mapped to OMOP Visit concept IDs using the table below."
                      />
                      {!vd.visit_concept_source_col && (
                        <p className="text-xs text-muted-foreground">
                          If left as "— not mapped —", every row will be assigned the default concept ID{' '}
                          <strong>{vd.visit_concept_id}</strong> ({VISIT_CONCEPTS.find(c => c.id === vd.visit_concept_id)?.label.split(' — ')[1] ?? 'Outpatient Visit'}).
                          Use "Set default" above to pick a different value.
                        </p>
                      )}
                      {vd.visit_concept_source_col && (
                        <ValueConceptMapper
                          label="Source value → Visit concept ID"
                          sourceValues={distinctVals(vd.visit_concept_source_col)}
                          mapping={vd.visit_concept_value_map ?? {}}
                          onChange={m => updateVisit(i, 'visit_concept_value_map', m)}
                          hint="Assign an OMOP Visit concept ID to each source value."
                        />
                      )}
                    </div>
                  ) : (
                    <div>
                      <label className="text-xs text-muted-foreground">Concept ID</label>
                      <Select value={vd.visit_concept_id} onChange={e => updateVisit(i, 'visit_concept_id', parseInt(e.target.value))} className="mt-1">
                        {VISIT_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                      </Select>
                    </div>
                  )}
                  <DomainWarning violations={vdVisitViolations} expectedDomain="Visit" />
                </Card>

                {/* ── Group 3: visit_type_concept_id ─────────────────────── */}
                <Card className="flex flex-col gap-4 p-6">
                  <div className="flex items-center justify-between">
                    <h3 className="font-semibold text-foreground">visit_type_concept_id</h3>
                    <AthenaLink href={ATHENA.visit_type_concept_id} />
                  </div>

                  <div className="flex gap-2">
                    <button onClick={() => switchTypeMode(i, 'column')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${getMode(typeModes, i) === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Map a column</button>
                    <button onClick={() => switchTypeMode(i, 'default')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${getMode(typeModes, i) === 'default' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Set default</button>
                  </div>

                  {getMode(typeModes, i) === 'column' ? (
                    <div className="flex flex-col gap-3">
                      <FieldMapper
                        label="visit_type_concept_id column"
                        sourceColumns={availCols(vd.visit_type_source_col ?? '')}
                        value={vd.visit_type_source_col ?? ''}
                        onChange={v => updateVisit(i, 'visit_type_source_col', v || undefined)}
                        hint="Values will be mapped to OMOP Type concept IDs using the table below."
                      />
                      {!vd.visit_type_source_col && (
                        <p className="text-xs text-muted-foreground">
                          If left as "— not mapped —", every row will be assigned the default concept ID{' '}
                          <strong>{vd.type_concept_id}</strong> ({TYPE_CONCEPTS.find(c => c.id === vd.type_concept_id)?.label.split(' — ')[1] ?? 'Registry'}).
                          Use "Set default" above to pick a different value.
                        </p>
                      )}
                      {vd.visit_type_source_col && (
                        <ValueConceptMapper
                          label="Source value → Type concept ID"
                          sourceValues={distinctVals(vd.visit_type_source_col)}
                          mapping={vd.visit_type_value_map ?? {}}
                          onChange={m => updateVisit(i, 'visit_type_value_map', m)}
                          hint="Assign an OMOP Type concept ID to each source value."
                        />
                      )}
                    </div>
                  ) : (
                    <div>
                      <label className="text-xs text-muted-foreground">Concept ID</label>
                      <Select value={vd.type_concept_id} onChange={e => updateVisit(i, 'type_concept_id', parseInt(e.target.value))} className="mt-1">
                        {TYPE_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                      </Select>
                    </div>
                  )}
                  <DomainWarning violations={vdTypeViolations} expectedDomain="Type Concept" />
                </Card>

                {/* Inpatient fields */}
                {INPATIENT_CONCEPT_IDS.has(vd.visit_concept_id) && (
                  <Card className="flex flex-col gap-5 p-6">
                    <h3 className="font-semibold text-foreground">Inpatient / multi-day visit fields</h3>

                    <div className="flex flex-col gap-2">
                      <div className="flex items-center justify-between">
                        <Label>admitted_from_concept_id</Label>
                        <AthenaLink href={ATHENA.admitted_from_concept_id} />
                      </div>
                      <FieldMapper
                        label="Map from source column (optional)"
                        sourceColumns={availCols(vd.admitted_from_source_col ?? '')}
                        value={vd.admitted_from_source_col ?? ''}
                        onChange={v => updateVisit(i, 'admitted_from_source_col', v || undefined)}
                        hint="Column whose values will be mapped to admitted_from_concept_id and admitted_from_source_value."
                      />
                      {vd.admitted_from_source_col && (
                        <ValueConceptMapper
                          label="Source value → admitted_from concept ID"
                          sourceValues={distinctVals(vd.admitted_from_source_col)}
                          mapping={vd.admitted_from_value_map ?? {}}
                          onChange={m => updateVisit(i, 'admitted_from_value_map', m)}
                          hint="Assign an OMOP Visit concept ID. Use 0 for home / self-referred."
                        />
                      )}
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="text-xs text-muted-foreground">
                            {vd.admitted_from_source_col ? 'Default / fallback concept ID' : 'Concept ID'}
                          </label>
                          <Select value={vd.admitted_from_concept_id ?? 0} onChange={e => updateVisit(i, 'admitted_from_concept_id', parseInt(e.target.value))} className="mt-1">
                            {ADMITTED_FROM_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                          </Select>
                        </div>
                        {!vd.admitted_from_source_col && (
                          <div>
                            <Label>admitted_from_source_value</Label>
                            <Input
                              type="text"
                              value={vd.admitted_from_source_value ?? ''}
                              onChange={e => updateVisit(i, 'admitted_from_source_value', e.target.value || undefined)}
                              placeholder="e.g. HOME, ER, LTC"
                              className="mt-1"
                            />
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex flex-col gap-2">
                      <div className="flex items-center justify-between">
                        <Label>discharged_to_concept_id</Label>
                        <AthenaLink href={ATHENA.discharged_to_concept_id} />
                      </div>
                      <FieldMapper
                        label="Map from source column (optional)"
                        sourceColumns={availCols(vd.discharged_to_source_col ?? '')}
                        value={vd.discharged_to_source_col ?? ''}
                        onChange={v => updateVisit(i, 'discharged_to_source_col', v || undefined)}
                        hint="Column whose values will be mapped to discharged_to_concept_id and discharged_to_source_value."
                      />
                      {vd.discharged_to_source_col && (
                        <ValueConceptMapper
                          label="Source value → discharged_to concept ID"
                          sourceValues={distinctVals(vd.discharged_to_source_col)}
                          mapping={vd.discharged_to_value_map ?? {}}
                          onChange={m => updateVisit(i, 'discharged_to_value_map', m)}
                          hint="Assign an OMOP Visit concept ID. Use 0 for home."
                        />
                      )}
                      <div className="grid grid-cols-2 gap-4">
                        <div>
                          <label className="text-xs text-muted-foreground">
                            {vd.discharged_to_source_col ? 'Default / fallback concept ID' : 'Concept ID'}
                          </label>
                          <Select value={vd.discharged_to_concept_id ?? 0} onChange={e => updateVisit(i, 'discharged_to_concept_id', parseInt(e.target.value))} className="mt-1">
                            {DISCHARGED_TO_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                          </Select>
                        </div>
                        {!vd.discharged_to_source_col && (
                          <div>
                            <Label>discharged_to_source_value</Label>
                            <Input
                              type="text"
                              value={vd.discharged_to_source_value ?? ''}
                              onChange={e => updateVisit(i, 'discharged_to_source_value', e.target.value || undefined)}
                              placeholder="e.g. HOME, SNF, TRANSFER"
                              className="mt-1"
                            />
                          </div>
                        )}
                      </div>
                    </div>
                  </Card>
                )}

                <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={vd.optional}
                    onChange={e => updateVisit(i, 'optional', e.target.checked)}
                    className="rounded accent-primary"
                  />
                  Optional (skip if date column is empty)
                </label>
              </div>
            )
          })}
        </div>

        {!isMultiRow && (
          <button
            onClick={addVisit}
            className="flex items-center gap-2 text-sm text-primary hover:text-primary/80 font-medium"
          >
            <Plus className="w-4 h-4" /> Add another visit type
          </button>
        )}

        <ExtraInstructions
          tableName="visit_occurrence"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        <ScriptGenerator
          project={project}
          table="visit_occurrence"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
          deterministic
        />
      </div>
    </WizardLayout>
  )
}
