import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getConceptDecisions } from '../../api/client'
import type { Project, PersonConfig, RaceEthnicityMapping } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'

interface ConceptRef { concept_id: number; concept_name: string }
interface VariableDecision { value_concepts: Record<string, ConceptRef> }

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
    race_concept_id: { source_col: '', value_map: {}, default: 0 },
    ethnicity_concept_id: { source_col: '', value_map: {}, default: 0 },
  },
  required_source_cols: [],
}

export default function Step2Person({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<PersonConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [genderValues, setGenderValues] = useState<string[]>([])
  const [raceValues, setRaceValues] = useState<string[]>([])
  const [ethnicityValues, setEthnicityValues] = useState<string[]>([])
  const [extraInstructions, setExtraInstructions] = useState('')
  const [conceptDecisions, setConceptDecisions] = useState<Record<string, VariableDecision>>({})

  useEffect(() => {
    Promise.all([
      getTableConfig(project.id, 'person'),
      getConceptDecisions(project.id),
    ]).then(([existing, decisions]: [PersonConfig & { extra_instructions?: string }, Record<string, VariableDecision>]) => {
      setConceptDecisions(decisions || {})
      if (existing && Object.keys(existing).length > 0) {
        setExtraInstructions(existing.extra_instructions || '')
        // normalise old { constant: N } format to the new shape
        const m = existing.mappings
        if (m.race_concept_id && 'constant' in m.race_concept_id)
          (m as unknown as Record<string, unknown>).race_concept_id = { source_col: '', value_map: {}, default: (m.race_concept_id as { constant: number }).constant }
        if (m.ethnicity_concept_id && 'constant' in m.ethnicity_concept_id)
          (m as unknown as Record<string, unknown>).ethnicity_concept_id = { source_col: '', value_map: {}, default: (m.ethnicity_concept_id as { constant: number }).constant }
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

  const handleGenderColChange = (col: string) => {
    setField(['mappings', 'gender_concept_id', 'source_col'], col)
    const decision = conceptDecisions[col]
    const valueConcepts = decision?.value_concepts ?? {}
    const entries = Object.entries(valueConcepts)
    if (entries.length > 0) {
      const valueMap = Object.fromEntries(entries.map(([k, v]) => [k, v.concept_id]))
      setField(['mappings', 'gender_concept_id', 'value_map'], valueMap)
      setGenderValues(entries.map(([k]) => k))
    }
  }

  const addGenderValue = () => {
    const val = prompt('Enter a source gender value (e.g. 1.0, M, male):')
    if (val) setGenderValues(prev => [...new Set([...prev, val])])
  }

  const handleRaceColChange = (col: string) => {
    setField(['mappings', 'race_concept_id', 'source_col'], col)
    const entries = Object.entries(conceptDecisions[col]?.value_concepts ?? {})
    if (entries.length > 0) {
      setField(['mappings', 'race_concept_id', 'value_map'], Object.fromEntries(entries.map(([k, v]) => [k, v.concept_id])))
      setRaceValues(entries.map(([k]) => k))
    }
  }

  const addRaceValue = () => {
    const val = prompt('Enter a source race value:')
    if (val) setRaceValues(prev => [...new Set([...prev, val])])
  }

  const handleEthnicityColChange = (col: string) => {
    setField(['mappings', 'ethnicity_concept_id', 'source_col'], col)
    const entries = Object.entries(conceptDecisions[col]?.value_concepts ?? {})
    if (entries.length > 0) {
      setField(['mappings', 'ethnicity_concept_id', 'value_map'], Object.fromEntries(entries.map(([k, v]) => [k, v.concept_id])))
      setEthnicityValues(entries.map(([k]) => k))
    }
  }

  const addEthnicityValue = () => {
    const val = prompt('Enter a source ethnicity value:')
    if (val) setEthnicityValues(prev => [...new Set([...prev, val])])
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
            onChange={handleGenderColChange}
            required
            hint="The source column that indicates biological sex."
          />

          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium text-gray-700">Gender value → OMOP concept mapping</label>
              <button onClick={addGenderValue} className="text-xs text-blue-600 hover:underline">+ Add value</button>
            </div>
            <p className="text-xs text-gray-500">Common: 8507 = Male, 8532 = Female</p>
            {genderValues.length === 0 && Object.keys(cfg.mappings.gender_concept_id.value_map).length === 0 && (
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-xs text-yellow-700">
                Default mapping loaded: 1.0 → 8507 (Male), 2.0 → 8532 (Female). Click "+ Add value" to add source values.
              </div>
            )}
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

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-6">
          <h3 className="font-medium text-gray-800">Race & Ethnicity</h3>

          {/* Race */}
          <div className="flex flex-col gap-3">
            <h4 className="text-sm font-semibold text-gray-700">Race</h4>

            <FieldMapper
              label="Race column"
              sourceColumns={cols}
              value={(cfg.mappings.race_concept_id as RaceEthnicityMapping)?.source_col ?? ''}
              onChange={handleRaceColChange}
              hint="Source column for race. Leave empty to use the default concept ID for all rows."
            />

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-gray-700">Race value → OMOP concept mapping</label>
                <button onClick={addRaceValue} className="text-xs text-blue-600 hover:underline">+ Add value</button>
              </div>
              <ValueConceptMapper
                label=""
                sourceValues={raceValues.length > 0 ? raceValues : Object.keys((cfg.mappings.race_concept_id as RaceEthnicityMapping)?.value_map ?? {})}
                mapping={(cfg.mappings.race_concept_id as RaceEthnicityMapping)?.value_map ?? {}}
                onChange={m => setField(['mappings', 'race_concept_id', 'value_map'], m)}
              />
            </div>

            <div>
              <label className="text-sm font-medium text-gray-700">Default race_concept_id</label>
              <input
                type="number"
                value={(cfg.mappings.race_concept_id as RaceEthnicityMapping)?.default ?? 0}
                onChange={e => setField(['mappings', 'race_concept_id', 'default'], parseInt(e.target.value))}
                className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-500 mt-1">Used when a source value is not in the map above (0 = unknown).</p>
            </div>
          </div>

          <div className="border-t border-gray-100" />

          {/* Ethnicity */}
          <div className="flex flex-col gap-3">
            <h4 className="text-sm font-semibold text-gray-700">Ethnicity</h4>

            <FieldMapper
              label="Ethnicity column"
              sourceColumns={cols}
              value={(cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.source_col ?? ''}
              onChange={handleEthnicityColChange}
              hint="Source column for ethnicity. Leave empty to use the default concept ID for all rows."
            />

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-gray-700">Ethnicity value → OMOP concept mapping</label>
                <button onClick={addEthnicityValue} className="text-xs text-blue-600 hover:underline">+ Add value</button>
              </div>
              <ValueConceptMapper
                label=""
                sourceValues={ethnicityValues.length > 0 ? ethnicityValues : Object.keys((cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.value_map ?? {})}
                mapping={(cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.value_map ?? {}}
                onChange={m => setField(['mappings', 'ethnicity_concept_id', 'value_map'], m)}
              />
            </div>

            <div>
              <label className="text-sm font-medium text-gray-700">Default ethnicity_concept_id</label>
              <input
                type="number"
                value={(cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.default ?? 0}
                onChange={e => setField(['mappings', 'ethnicity_concept_id', 'default'], parseInt(e.target.value))}
                className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-500 mt-1">Used when a source value is not in the map above (0 = unknown).</p>
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
