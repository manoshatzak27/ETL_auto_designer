import { useState, useEffect, useMemo, type Dispatch, type SetStateAction } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import { getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, VisitOccurrenceConfig, VisitDefinition } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import DomainWarning from '../../components/DomainWarning'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Plus, Trash2, ExternalLink } from 'lucide-react'
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
  visit_concept_id:       'https://athena.ohdsi.org/search-terms/terms?domain=Visit&standardConcept=Standard&page=1&pageSize=15&query=',
  visit_type_concept_id:  'https://athena.ohdsi.org/search-terms/terms?domain=Type+Concept&standardConcept=Standard&page=1&pageSize=15&query=',
  admitted_from_concept_id:  'https://athena.ohdsi.org/search-terms/terms?domain=Visit&standardConcept=Standard&page=1&pageSize=15&query=',
  discharged_to_concept_id:  'https://athena.ohdsi.org/search-terms/terms?domain=Visit&standardConcept=Standard&page=1&pageSize=15&query=',
}

const INPATIENT_CONCEPT_IDS = new Set([9201, 262, 42898160])

function AthenaLink({ href }: { href: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
    >
      Find on Athena <ExternalLink className="w-3 h-3" />
    </a>
  )
}

export default function Step3Visit({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<VisitOccurrenceConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [conceptModes, setConceptModes] = useState<Array<'column' | 'default'>>([])
  const [typeModes, setTypeModes] = useState<Array<'column' | 'default'>>([])

  const getMode = (arr: Array<'column' | 'default'>, i: number): 'column' | 'default' => arr[i] ?? 'column'

  const setMode = (
    setter: Dispatch<SetStateAction<Array<'column' | 'default'>>>,
    i: number,
    mode: 'column' | 'default',
  ) => setter(prev => { const next = [...prev]; next[i] = mode; return next })
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'visit_occurrence'), [project.etl_config])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || !crossUsed.has(c))

  const allVisitConceptIds = cfg.visit_definitions.flatMap((vd, i) =>
    getMode(conceptModes, i) === 'column' ? Object.values(vd.visit_concept_value_map ?? {}) : [],
  )
  const visitConceptViolations = useDomainValidation(allVisitConceptIds, 'Visit')

  const allTypeConceptIds = cfg.visit_definitions.flatMap((vd, i) =>
    getMode(typeModes, i) === 'column' ? Object.values(vd.visit_type_value_map ?? {}) : [],
  )
  const typeConceptViolations = useDomainValidation(allTypeConceptIds, 'Type Concept')

  useEffect(() => {
    getTableConfig(project.id, 'visit_occurrence').then((ex: VisitOccurrenceConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
        const vds = ex.visit_definitions ?? []
        setConceptModes(vds.map((vd: VisitDefinition) => vd.visit_concept_mode ?? (vd.visit_concept_source_col ? 'column' : 'default')))
        setTypeModes(vds.map((vd: VisitDefinition) => vd.visit_type_mode ?? (vd.visit_type_source_col ? 'column' : 'default')))
      }
    })
    getColumnValues(project.id).then(setColumnInfos)
  }, [project.id])

  const distinctVals = (col: string): string[] =>
    columnInfos[col]?.distinct_values ?? []

  const updateVisit = (i: number, field: keyof VisitDefinition, value: unknown) => {
    setCfg(prev => {
      const defs = [...prev.visit_definitions]
      defs[i] = { ...defs[i], [field]: value }
      return { ...prev, visit_definitions: defs }
    })
  }

  const updateVisitFields = (i: number, fields: Partial<VisitDefinition>) => {
    setCfg(prev => {
      const defs = [...prev.visit_definitions]
      defs[i] = { ...defs[i], ...fields }
      return { ...prev, visit_definitions: defs }
    })
  }

  const addVisit = () => {
    setCfg(prev => ({ ...prev, visit_definitions: [...prev.visit_definitions, { ...DEFAULT_VISIT }] }))
    setConceptModes(prev => [...prev, 'default'])
    setTypeModes(prev => [...prev, 'default'])
  }

  const removeVisit = (i: number) => {
    setCfg(prev => ({ ...prev, visit_definitions: prev.visit_definitions.filter((_, j) => j !== i) }))
    setConceptModes(prev => prev.filter((_, j) => j !== i))
    setTypeModes(prev => prev.filter((_, j) => j !== i))
  }

  const switchConceptMode = (i: number, mode: 'column' | 'default') => {
    setMode(setConceptModes, i, mode)
    if (mode === 'default') {
      updateVisitFields(i, { visit_concept_mode: mode, visit_concept_source_col: undefined, visit_concept_value_map: undefined })
    } else {
      updateVisit(i, 'visit_concept_mode', mode)
    }
  }

  const switchTypeMode = (i: number, mode: 'column' | 'default') => {
    setMode(setTypeModes, i, mode)
    if (mode === 'default') {
      updateVisitFields(i, { visit_type_mode: mode, visit_type_source_col: undefined, visit_type_value_map: undefined })
    } else {
      updateVisit(i, 'visit_type_mode', mode)
    }
  }

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'visit_occurrence', { ...cfg, extra_instructions: extraInstructions })
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
        <div>
          <h2 className="text-xl font-bold text-primary">Visit Occurrence Mapping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Define the clinical visits. Each visit definition creates one row in <code className="bg-muted px-1 rounded">visit_occurrence</code> per patient.
            Map each CDM field to a source column and assign OMOP concept IDs to the values.
          </p>
        </div>

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
                <p className="text-base font-bold uppercase tracking-wide text-muted-foreground">Visit {i + 1}</p>
                {cfg.visit_definitions.length > 1 && (
                  <button onClick={() => removeVisit(i)} className="shrink-0 text-destructive/60 hover:text-destructive">
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>

              {/* ── Group 1: Identity & dates ─────────────────────────────── */}
              <Card className="flex flex-col gap-4 p-6">
                <h3 className="font-semibold text-foreground">Visit identity &amp; dates</h3>

                <div>
                  <Label>Visit label</Label>
                  <Input
                    type="text"
                    value={vd.label}
                    onChange={e => updateVisit(i, 'label', e.target.value)}
                    placeholder="e.g. Onset, Baseline, 10y Follow-up"
                    className="mt-1"
                  />
                </div>

                <FieldMapper
                  label="visit_start_date column"
                  sourceColumns={availCols(vd.date_col)}
                  value={vd.date_col}
                  onChange={v => updateVisit(i, 'date_col', v)}
                  required={!vd.optional}
                  hint="Source column containing the visit start date"
                />

                <FieldMapper
                  label="visit_start_time column (optional)"
                  sourceColumns={availCols(vd.time_col ?? '')}
                  value={vd.time_col ?? ''}
                  onChange={v => updateVisit(i, 'time_col', v || undefined)}
                  required={false}
                  hint="Separate column with the start time. If absent, visit_start_datetime defaults to midnight (00:00:00)."
                />

                <FieldMapper
                  label="visit_end_date column (optional)"
                  sourceColumns={availCols(vd.end_date_col ?? '')}
                  value={vd.end_date_col ?? ''}
                  onChange={v => updateVisit(i, 'end_date_col', v || undefined)}
                  required={false}
                  hint="Separate end date column. Leave blank to use start date as end date (for same-day visits)."
                />

                <FieldMapper
                  label="visit_end_time column (optional)"
                  sourceColumns={availCols(vd.end_time_col ?? '')}
                  value={vd.end_time_col ?? ''}
                  onChange={v => updateVisit(i, 'end_time_col', v || undefined)}
                  required={false}
                  hint="Separate column with the end time. If absent, visit_end_datetime defaults to midnight (00:00:00)."
                />

                <div>
                  <Label>Date format</Label>
                  <Input
                    type="text"
                    value={vd.date_format ?? '%Y-%m-%d'}
                    onChange={e => updateVisit(i, 'date_format', e.target.value || '%Y-%m-%d')}
                    placeholder="%Y-%m-%d"
                    className="mt-1 font-mono"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">
                    Format applied to both start and end date columns (e.g. <code className="bg-muted px-1 rounded">%d/%m/%Y</code>, <code className="bg-muted px-1 rounded">%Y%m%d</code>).
                  </p>
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
                  <p className="mt-1 text-xs text-muted-foreground">
                    Format applied to the time column(s) (e.g. <code className="bg-muted px-1 rounded">%H:%M</code>, <code className="bg-muted px-1 rounded">%H:%M:%S</code>). If no time column is mapped, datetime defaults to midnight.
                  </p>
                </div>
              </Card>

              {/* ── Group 2: Visit source value (auto-computed) ───────────── */}
              <Card className="flex flex-col gap-2 p-6">
                <h3 className="font-semibold text-foreground">Visit source value</h3>
                <div className="rounded-lg border border-border bg-secondary/60 p-4 flex flex-col gap-2">
                  <p className="text-sm font-medium text-secondary-foreground">Auto-computed — no mapping required</p>
                  <p className="text-sm text-muted-foreground">
                    Constructed at ETL runtime by joining three parts with{' '}
                    <code className="bg-accent px-1 rounded text-xs">-</code>:
                    the person source value, the source filename stem, and the visit label (lowercased, spaces → underscores).
                    Used as the lookup key that links records in stem_table and death to this visit.
                  </p>
                  <div className="mt-1">
                    <p className="text-xs font-medium text-primary mb-1">Formula preview:</p>
                    <code className="bg-accent text-secondary-foreground text-xs px-2 py-1 rounded block">
                      {`<person_source_value>-<filename_stem>-${vd.label ? vd.label.toLowerCase().replace(/ /g, '_') : '<visit_label>'}`}
                    </code>
                  </div>
                </div>
              </Card>

              {/* ── Group 3: visit_concept_id ──────────────────────────────── */}
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
                      required={false}
                      hint="Values will be mapped to OMOP Visit concept IDs using the table below."
                    />
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
                    <Select
                      value={vd.visit_concept_id}
                      onChange={e => updateVisit(i, 'visit_concept_id', parseInt(e.target.value))}
                      className="mt-1"
                    >
                      {VISIT_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                    </Select>
                  </div>
                )}
                <DomainWarning violations={vdVisitViolations} expectedDomain="Visit" />
              </Card>

              {/* ── Group 4: visit_type_concept_id ────────────────────────── */}
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
                      required={false}
                      hint="Values will be mapped to OMOP Type concept IDs using the table below."
                    />
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
                    <Select
                      value={vd.type_concept_id}
                      onChange={e => updateVisit(i, 'type_concept_id', parseInt(e.target.value))}
                      className="mt-1"
                    >
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

                  {/* admitted_from_concept_id */}
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
                      required={false}
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
                        <Select
                          value={vd.admitted_from_concept_id ?? 0}
                          onChange={e => updateVisit(i, 'admitted_from_concept_id', parseInt(e.target.value))}
                          className="mt-1"
                        >
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

                  {/* discharged_to_concept_id */}
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
                      required={false}
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
                        <Select
                          value={vd.discharged_to_concept_id ?? 0}
                          onChange={e => updateVisit(i, 'discharged_to_concept_id', parseInt(e.target.value))}
                          className="mt-1"
                        >
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

        <button
          onClick={addVisit}
          className="flex items-center gap-2 text-sm text-primary hover:text-primary/80 font-medium"
        >
          <Plus className="w-4 h-4" /> Add another visit type
        </button>

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
