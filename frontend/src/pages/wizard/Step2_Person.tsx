import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import type { Project, PersonConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: PersonConfig = {
  enabled: true,
  mappings: {
    person_id: { source_col: '', transform: 'int_float', auto_increment: false },
    gender_concept_id: { source_col: '', value_map: { '1.0': 8507, '2.0': 8532 }, default: 0 },
    year_of_birth: { source_col: '', date_format: '%Y-%m-%d', transform: 'date_year' },
    month_of_birth: { source_col: '', date_format: '%Y-%m-%d', transform: 'date_month' },
    day_of_birth: { source_col: '', date_format: '%Y-%m-%d', transform: 'date_day' },
    race_concept_id: { constant: 0 },
    ethnicity_concept_id: { constant: 0 },
  },
  required_source_cols: [],
}

export default function Step2Person({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<PersonConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [genderValues, setGenderValues] = useState<string[]>([])
  const [extraInstructions, setExtraInstructions] = useState('')

  useEffect(() => {
    getTableConfig(project.id, 'person').then((existing: PersonConfig & { extra_instructions?: string }) => {
      if (existing && Object.keys(existing).length > 0) {
        setExtraInstructions(existing.extra_instructions || '')
        setCfg(existing)
      }
    })
  }, [project.id])

  // derive unique gender values from source columns hint
  // user can add them manually
  const setField = (path: string[], value: unknown) => {
    setCfg(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      let cur: Record<string, unknown> = next
      for (let i = 0; i < path.length - 1; i++) cur = cur[path[i]] as Record<string, unknown>
      cur[path[path.length - 1]] = value
      return next
    })
  }

  const addGenderValue = () => {
    const val = prompt('Enter a source gender value (e.g. 1.0, M, male):')
    if (val) setGenderValues(prev => [...new Set([...prev, val])])
  }

  const saveConfig = async () => {
    const required = [
      !cfg.mappings.person_id.auto_increment ? cfg.mappings.person_id.source_col : null,
      cfg.mappings.gender_concept_id.source_col,
      cfg.mappings.year_of_birth.source_col,
    ].filter(Boolean)
    const updated = { ...cfg, required_source_cols: required as string[], extra_instructions: extraInstructions }
    const p = await updateTableConfig(project.id, 'person', updated)
    onUpdate(p)
  }

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/4`)
  }

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={3}
      onBack={() => navigate(`/project/${project.id}/step/2`)}
      onNext={handleNext}
      nextLabel="Next: Visit Occurrence →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Person Table Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Map source columns to OMOP <code className="bg-gray-100 px-1 rounded">person</code> table fields.
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Core Identifiers</h3>

          <label className="flex items-center gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={cfg.mappings.person_id.auto_increment ?? false}
              onChange={e => setField(['mappings', 'person_id', 'auto_increment'], e.target.checked)}
              className="w-4 h-4 accent-blue-600 rounded"
            />
            <span className="text-sm text-gray-700">
              Auto-increment Patient ID — assign sequential IDs (1, 2, 3…) without mapping to a source column
            </span>
          </label>

          {!cfg.mappings.person_id.auto_increment && (
            <>
              <FieldMapper
                label="Patient ID column"
                sourceColumns={cols}
                value={cfg.mappings.person_id.source_col}
                onChange={v => setField(['mappings', 'person_id', 'source_col'], v)}
                required
                hint="Will be cast to int(float(value)). Used as person_id."
              />

              <div>
                <label className="text-sm font-medium text-gray-700">Patient ID transform</label>
                <select
                  value={cfg.mappings.person_id.transform}
                  onChange={e => setField(['mappings', 'person_id', 'transform'], e.target.value)}
                  className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="int_float">int(float(x)) — for "1.0", "2.0" style IDs</option>
                  <option value="int">int(x) — for "1", "2" style IDs</option>
                  <option value="str">str(x) — keep as string</option>
                </select>
              </div>
            </>
          )}
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Demographics</h3>

          <FieldMapper
            label="Gender column"
            sourceColumns={cols}
            value={cfg.mappings.gender_concept_id.source_col}
            onChange={v => setField(['mappings', 'gender_concept_id', 'source_col'], v)}
            required
            hint="The source column that indicates biological sex."
          />

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium text-gray-700">Gender value → OMOP concept mapping</label>
              <button onClick={addGenderValue} className="text-xs text-blue-600 hover:underline">+ Add value</button>
            </div>
            <p className="text-xs text-gray-500">Common: 8507 = Male, 8532 = Female</p>
            {genderValues.length === 0 && Object.keys(cfg.mappings.gender_concept_id.value_map).length === 0 ? (
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-xs text-yellow-700">
                Default mapping loaded: 1.0 → 8507 (Male), 2.0 → 8532 (Female). Click "+ Add value" to add source values.
              </div>
            ) : null}
            <ValueConceptMapper
              label=""
              sourceValues={genderValues.length > 0 ? genderValues : Object.keys(cfg.mappings.gender_concept_id.value_map)}
              mapping={cfg.mappings.gender_concept_id.value_map}
              onChange={m => setField(['mappings', 'gender_concept_id', 'value_map'], m)}
            />
          </div>

          <FieldMapper
            label="Date of birth column"
            sourceColumns={cols}
            value={cfg.mappings.year_of_birth.source_col}
            onChange={v => {
              setField(['mappings', 'year_of_birth', 'source_col'], v)
              setField(['mappings', 'month_of_birth', 'source_col'], v)
              setField(['mappings', 'day_of_birth', 'source_col'], v)
            }}
            required
            hint="Single date column. Year, month, and day will be extracted from it."
          />

          <div>
            <label className="text-sm font-medium text-gray-700">Date format</label>
            <input
              type="text"
              value={cfg.mappings.year_of_birth.date_format}
              onChange={e => {
                setField(['mappings', 'year_of_birth', 'date_format'], e.target.value)
                setField(['mappings', 'month_of_birth', 'date_format'], e.target.value)
                setField(['mappings', 'day_of_birth', 'date_format'], e.target.value)
              }}
              placeholder="%Y-%m-%d"
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm font-mono w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-500 mt-1">Python strptime format, e.g. %Y-%m-%d or %d/%m/%Y</p>
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-4">
          <h3 className="font-medium text-gray-800">Race & Ethnicity</h3>
          <p className="text-sm text-gray-500">Currently set to 0 (unknown). Expand if your source data contains race/ethnicity columns.</p>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-sm font-medium text-gray-700">race_concept_id constant</label>
              <input
                type="number"
                value={(cfg.mappings.race_concept_id as { constant: number })?.constant ?? 0}
                onChange={e => setField(['mappings', 'race_concept_id', 'constant'], parseInt(e.target.value))}
                className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
            <div>
              <label className="text-sm font-medium text-gray-700">ethnicity_concept_id constant</label>
              <input
                type="number"
                value={(cfg.mappings.ethnicity_concept_id as { constant: number })?.constant ?? 0}
                onChange={e => setField(['mappings', 'ethnicity_concept_id', 'constant'], parseInt(e.target.value))}
                className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        </div>

        <ExtraInstructions
          tableName="person"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="person"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
