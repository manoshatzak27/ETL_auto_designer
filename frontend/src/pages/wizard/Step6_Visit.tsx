import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import type { Project, VisitOccurrenceConfig, VisitDefinition } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Plus, Trash2, ExternalLink } from 'lucide-react'

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
  source_value: '',
  optional: false,
}

const DEFAULTS: VisitOccurrenceConfig = {
  enabled: true,
  visit_definitions: [
    { label: 'Onset', date_col: '', visit_concept_id: 9202, type_concept_id: 32879, source_value: 'ONSET Visit', optional: false },
    { label: '10y Follow-up', date_col: '', visit_concept_id: 9202, type_concept_id: 32879, source_value: '10 year follow up', optional: true },
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
      className="inline-flex items-center gap-1 text-xs text-blue-500 hover:text-blue-700 hover:underline"
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

  useEffect(() => {
    getTableConfig(project.id, 'visit_occurrence').then((ex: VisitOccurrenceConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
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

  const addVisit = () =>
    setCfg(prev => ({ ...prev, visit_definitions: [...prev.visit_definitions, { ...DEFAULT_VISIT }] }))

  const removeVisit = (i: number) =>
    setCfg(prev => ({ ...prev, visit_definitions: prev.visit_definitions.filter((_, j) => j !== i) }))

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'visit_occurrence', { ...cfg, extra_instructions: extraInstructions })
    onUpdate(p)
  }

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/7`)
  }

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={6}
      onBack={() => navigate(`/project/${project.id}/step/5`)}
      onNext={handleNext}
      nextLabel="Next: Observation Period →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Visit Occurrence Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Define the clinical visits. Each visit definition creates one row in <code className="bg-gray-100 px-1 rounded">visit_occurrence</code> per patient.
            Map each CDM field to a source column and assign OMOP concept IDs to the values.
          </p>
        </div>

        <div className="flex flex-col gap-4">
          {cfg.visit_definitions.map((vd, i) => (
            <div key={i} className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
              <div className="flex items-center justify-between">
                <h3 className="font-medium text-gray-800">Visit {i + 1}</h3>
                {cfg.visit_definitions.length > 1 && (
                  <button onClick={() => removeVisit(i)} className="text-red-400 hover:text-red-600">
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>

              {/* ── Group 1: Identity & dates ─────────────────────────────── */}
              <div className="border border-gray-100 rounded-lg p-4 flex flex-col gap-4 bg-gray-50">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Visit identity &amp; dates</p>

                <div>
                  <label className="text-sm font-medium text-gray-700">Visit label</label>
                  <input
                    type="text"
                    value={vd.label}
                    onChange={e => updateVisit(i, 'label', e.target.value)}
                    placeholder="e.g. Onset, Baseline, 10y Follow-up"
                    className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>

                <FieldMapper
                  label="visit_start_date column"
                  sourceColumns={cols}
                  value={vd.date_col}
                  onChange={v => updateVisit(i, 'date_col', v)}
                  required={!vd.optional}
                  hint="Source column containing the visit start date"
                />

                <FieldMapper
                  label="visit_end_date column (optional)"
                  sourceColumns={cols}
                  value={vd.end_date_col ?? ''}
                  onChange={v => updateVisit(i, 'end_date_col', v || undefined)}
                  required={false}
                  hint="Separate end date column. Leave blank to use start date as end date (for same-day visits)."
                />
              </div>

              {/* ── Group 2: Source value ──────────────────────────────────── */}
              <div className="border border-gray-100 rounded-lg p-4 flex flex-col gap-4 bg-gray-50">
                <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Visit source value</p>

                <div className="grid grid-cols-2 gap-4">
                  <FieldMapper
                    label="visit_source_value column (optional)"
                    sourceColumns={cols}
                    value={vd.visit_source_col ?? ''}
                    onChange={v => updateVisit(i, 'visit_source_col', v || undefined)}
                    required={false}
                    hint="Column whose value becomes visit_source_value. If not mapped, uses the static text."
                  />
                  <div>
                    <label className="text-sm font-medium text-gray-700">
                      {vd.visit_source_col ? 'visit_source_value (static fallback)' : 'visit_source_value'}
                    </label>
                    <input
                      type="text"
                      value={vd.source_value}
                      onChange={e => updateVisit(i, 'source_value', e.target.value)}
                      placeholder="e.g. ONSET Visit"
                      className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>
              </div>

              {/* ── Group 3: visit_concept_id ──────────────────────────────── */}
              <div className="border border-gray-100 rounded-lg p-4 flex flex-col gap-4 bg-gray-50">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">visit_concept_id</p>
                  <AthenaLink href={ATHENA.visit_concept_id} />
                </div>

                <FieldMapper
                  label="Map from source column (optional)"
                  sourceColumns={cols}
                  value={vd.visit_concept_source_col ?? ''}
                  onChange={v => updateVisit(i, 'visit_concept_source_col', v || undefined)}
                  required={false}
                  hint="If a column is selected, values will be mapped to concept IDs using the table below."
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
                <div>
                  <label className="text-xs text-gray-500">
                    {vd.visit_concept_source_col ? 'Default / fallback concept ID' : 'Concept ID'}
                  </label>
                  <select
                    value={vd.visit_concept_id}
                    onChange={e => updateVisit(i, 'visit_concept_id', parseInt(e.target.value))}
                    className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {VISIT_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                  </select>
                </div>
              </div>

              {/* ── Group 4: visit_type_concept_id ────────────────────────── */}
              <div className="border border-gray-100 rounded-lg p-4 flex flex-col gap-4 bg-gray-50">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">visit_type_concept_id</p>
                  <AthenaLink href={ATHENA.visit_type_concept_id} />
                </div>

                <FieldMapper
                  label="Map from source column (optional)"
                  sourceColumns={cols}
                  value={vd.visit_type_source_col ?? ''}
                  onChange={v => updateVisit(i, 'visit_type_source_col', v || undefined)}
                  required={false}
                  hint="If a column is selected, values will be mapped to Type concept IDs using the table below."
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
                <div>
                  <label className="text-xs text-gray-500">
                    {vd.visit_type_source_col ? 'Default / fallback concept ID' : 'Concept ID'}
                  </label>
                  <select
                    value={vd.type_concept_id}
                    onChange={e => updateVisit(i, 'type_concept_id', parseInt(e.target.value))}
                    className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {TYPE_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                  </select>
                </div>
              </div>

              {/* Inpatient fields */}
              {INPATIENT_CONCEPT_IDS.has(vd.visit_concept_id) && (
                <div className="border border-blue-100 bg-blue-50 rounded-lg p-4 flex flex-col gap-5">
                  <p className="text-xs font-semibold text-blue-700 uppercase tracking-wide">Inpatient / multi-day visit fields</p>

                  {/* admitted_from_concept_id */}
                  <div className="flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <label className="text-sm font-medium text-gray-700">admitted_from_concept_id</label>
                      <AthenaLink href={ATHENA.admitted_from_concept_id} />
                    </div>
                    <FieldMapper
                      label="Map from source column (optional)"
                      sourceColumns={cols}
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
                        <label className="text-xs text-gray-500">
                          {vd.admitted_from_source_col ? 'Default / fallback concept ID' : 'Concept ID'}
                        </label>
                        <select
                          value={vd.admitted_from_concept_id ?? 0}
                          onChange={e => updateVisit(i, 'admitted_from_concept_id', parseInt(e.target.value))}
                          className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                          {ADMITTED_FROM_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                        </select>
                      </div>
                      {!vd.admitted_from_source_col && (
                        <div>
                          <label className="text-sm font-medium text-gray-700">admitted_from_source_value</label>
                          <input
                            type="text"
                            value={vd.admitted_from_source_value ?? ''}
                            onChange={e => updateVisit(i, 'admitted_from_source_value', e.target.value || undefined)}
                            placeholder="e.g. HOME, ER, LTC"
                            className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                          />
                        </div>
                      )}
                    </div>
                  </div>

                  {/* discharged_to_concept_id */}
                  <div className="flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <label className="text-sm font-medium text-gray-700">discharged_to_concept_id</label>
                      <AthenaLink href={ATHENA.discharged_to_concept_id} />
                    </div>
                    <FieldMapper
                      label="Map from source column (optional)"
                      sourceColumns={cols}
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
                        <label className="text-xs text-gray-500">
                          {vd.discharged_to_source_col ? 'Default / fallback concept ID' : 'Concept ID'}
                        </label>
                        <select
                          value={vd.discharged_to_concept_id ?? 0}
                          onChange={e => updateVisit(i, 'discharged_to_concept_id', parseInt(e.target.value))}
                          className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                        >
                          {DISCHARGED_TO_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                        </select>
                      </div>
                      {!vd.discharged_to_source_col && (
                        <div>
                          <label className="text-sm font-medium text-gray-700">discharged_to_source_value</label>
                          <input
                            type="text"
                            value={vd.discharged_to_source_value ?? ''}
                            onChange={e => updateVisit(i, 'discharged_to_source_value', e.target.value || undefined)}
                            placeholder="e.g. HOME, SNF, TRANSFER"
                            className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                          />
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}

              <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
                <input
                  type="checkbox"
                  checked={vd.optional}
                  onChange={e => updateVisit(i, 'optional', e.target.checked)}
                  className="rounded"
                />
                Optional (skip if date column is empty)
              </label>
            </div>
          ))}
        </div>

        <button
          onClick={addVisit}
          className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 font-medium"
        >
          <Plus className="w-4 h-4" /> Add another visit type
        </button>

        <ExtraInstructions
          tableName="visit_occurrence"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="visit_occurrence"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
