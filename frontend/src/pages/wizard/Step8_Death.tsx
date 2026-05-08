import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import type { Project, DeathConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: DeathConfig = {
  enabled: true,
  filter_col: '',
  filter_value: '',
  death_date_col: '',
  death_datetime_col: '',
  death_type_concept_id: 32879,
  cause_concept_id: null,
  cause_source_value_col: '',
  cause_source_concept_id: null,
}

const DEATH_TYPE_OPTIONS = [
  { value: 32879, label: '32879 — Registry' },
  { value: 32817, label: '32817 — EHR' },
  { value: 32810, label: '32810 — Death Certificate' },
  { value: 32823, label: '32823 — Primary Death Certificate' },
]

export default function Step8Death({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<DeathConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')

  useEffect(() => {
    getTableConfig(project.id, 'death').then((ex: DeathConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
  }, [project.id])

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'death', { ...cfg, extra_instructions: extraInstructions })
    onUpdate(p)
  }

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/9`)
  }

  const set = (field: keyof DeathConfig) => (v: string) =>
    setCfg(prev => ({ ...prev, [field]: v }))

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={8}
      onBack={() => navigate(`/project/${project.id}/step/7`)}
      onNext={handleNext}
      nextLabel="Next: Concepts →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Death Table Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Map source columns to the OMOP DEATH table. A person can have at most one death record.
          </p>
        </div>

        {/* Death Trigger */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Death Trigger</h3>

          <FieldMapper
            label="Filter column"
            sourceColumns={cols}
            value={cfg.filter_col}
            onChange={set('filter_col')}
            hint="The source column that indicates patient death status."
          />

          <div>
            <label className="text-sm font-medium text-gray-700">Filter value (death indicator)</label>
            <input
              type="text"
              value={cfg.filter_value}
              onChange={e => setCfg(prev => ({ ...prev, filter_value: e.target.value }))}
              placeholder="e.g. 5.0 or dead or D"
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-500 mt-1">
              A death record is created only when the filter column equals this value.
            </p>
          </div>
        </div>

        {/* Death Date */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Death Date</h3>

          <FieldMapper
            label="death_date (required)"
            sourceColumns={cols}
            value={cfg.death_date_col}
            onChange={set('death_date_col')}
            hint="Source column containing the date of death. If day/month unknown, December 31 is used by convention."
          />

          <FieldMapper
            label="death_datetime (optional)"
            sourceColumns={cols}
            value={cfg.death_datetime_col}
            onChange={set('death_datetime_col')}
            hint="Source column containing the full datetime of death. Leave empty to populate as NULL."
          />
        </div>

        {/* Death Type */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Death Type</h3>

          <div>
            <label className="text-sm font-medium text-gray-700">
              death_type_concept_id
              <span className="ml-1 font-normal text-gray-400">— provenance of the death record</span>
            </label>
            <select
              value={cfg.death_type_concept_id}
              onChange={e => setCfg(prev => ({ ...prev, death_type_concept_id: parseInt(e.target.value) }))}
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {DEATH_TYPE_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <p className="text-xs text-gray-500 mt-1">
              Reflects the source of the death record. Do not assume it matches the visit type.
            </p>
          </div>
        </div>

        {/* Cause of Death */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Cause of Death (optional)</h3>

          <div>
            <label className="text-sm font-medium text-gray-700">
              cause_concept_id
              <span className="ml-1 font-normal text-gray-400">— Standard OMOP concept for cause of death</span>
            </label>
            <input
              type="number"
              value={cfg.cause_concept_id ?? ''}
              onChange={e => setCfg(prev => ({
                ...prev,
                cause_concept_id: e.target.value === '' ? null : parseInt(e.target.value),
              }))}
              placeholder="e.g. 433753 for Neoplasm or leave blank"
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-500 mt-1">
              No domain restriction — choose the Standard concept that best represents the cause of death. Use 0 if unknown.
            </p>
          </div>

          <FieldMapper
            label="cause_source_value (optional)"
            sourceColumns={cols}
            value={cfg.cause_source_value_col}
            onChange={set('cause_source_value_col')}
            hint="Source column containing the raw cause of death code (max 50 chars)."
          />

          <div>
            <label className="text-sm font-medium text-gray-700">
              cause_source_concept_id
              <span className="ml-1 font-normal text-gray-400">— OMOP concept ID for the source cause code</span>
            </label>
            <input
              type="number"
              value={cfg.cause_source_concept_id ?? ''}
              onChange={e => setCfg(prev => ({
                ...prev,
                cause_source_concept_id: e.target.value === '' ? null : parseInt(e.target.value),
              }))}
              placeholder="CONCEPT_ID from OMOP vocabularies, or leave blank"
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-500 mt-1">
              Use if the cause of death was coded using a vocabulary present in OMOP. Use 0 if not applicable.
            </p>
          </div>
        </div>

        <ExtraInstructions
          tableName="death"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="death"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
