import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import type { Project, VisitOccurrenceConfig, VisitDefinition } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Plus, Trash2 } from 'lucide-react'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

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
  { id: 9202, label: '9202 — Outpatient Visit' },
  { id: 9201, label: '9201 — Inpatient Visit' },
  { id: 9203, label: '9203 — Emergency Room Visit' },
  { id: 32693, label: '32693 — Telehealth Visit' },
]

const TYPE_CONCEPTS = [
  { id: 32879, label: '32879 — Registry' },
  { id: 32817, label: '32817 — EHR' },
  { id: 44818518, label: '44818518 — Visit derived by algorithm' },
]

export default function Step3Visit({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<VisitOccurrenceConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')

  useEffect(() => {
    getTableConfig(project.id, 'visit_occurrence').then((ex: VisitOccurrenceConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
  }, [project.id])

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
    navigate(`/project/${project.id}/step/5`)
  }

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={4}
      onBack={() => navigate(`/project/${project.id}/step/3`)}
      onNext={handleNext}
      nextLabel="Next: Observation Period →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Visit Occurrence Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Define the clinical visits. Each visit definition creates one row in <code className="bg-gray-100 px-1 rounded">visit_occurrence</code> per patient.
          </p>
        </div>

        <div className="flex flex-col gap-4">
          {cfg.visit_definitions.map((vd, i) => (
            <div key={i} className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <h3 className="font-medium text-gray-800">Visit {i + 1}</h3>
                {cfg.visit_definitions.length > 1 && (
                  <button onClick={() => removeVisit(i)} className="text-red-400 hover:text-red-600">
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>

              <div className="grid grid-cols-2 gap-4">
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
                <div>
                  <label className="text-sm font-medium text-gray-700">visit_source_value</label>
                  <input
                    type="text"
                    value={vd.source_value}
                    onChange={e => updateVisit(i, 'source_value', e.target.value)}
                    placeholder="e.g. ONSET Visit"
                    className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                </div>
              </div>

              <FieldMapper
                label="Date column"
                sourceColumns={cols}
                value={vd.date_col}
                onChange={v => updateVisit(i, 'date_col', v)}
                required={!vd.optional}
                hint="Source column containing the visit date for this visit type"
              />

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-sm font-medium text-gray-700">visit_concept_id</label>
                  <select
                    value={vd.visit_concept_id}
                    onChange={e => updateVisit(i, 'visit_concept_id', parseInt(e.target.value))}
                    className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {VISIT_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-sm font-medium text-gray-700">type_concept_id</label>
                  <select
                    value={vd.type_concept_id}
                    onChange={e => updateVisit(i, 'type_concept_id', parseInt(e.target.value))}
                    className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                  >
                    {TYPE_CONCEPTS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                  </select>
                </div>
              </div>

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
