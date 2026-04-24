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
  filter_value: '5.0',
  date_method: 'onset_plus_years',
  onset_col: '',
  years_offset: 10,
  death_type_concept_id: 32879,
}

export default function Step6Death({ project, onUpdate }: Props) {
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
    navigate(`/project/${project.id}/step/8`)
  }

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={7}
      onBack={() => navigate(`/project/${project.id}/step/6`)}
      onNext={handleNext}
      nextLabel="Next: Generate Code →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Death Table Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Configure when and how a death record is created for a patient.
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Death Trigger</h3>

          <FieldMapper
            label="Filter column"
            sourceColumns={cols}
            value={cfg.filter_col}
            onChange={v => setCfg(prev => ({ ...prev, filter_col: v }))}
            hint="The source column that indicates patient outcome / contact status"
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
              A death record is created only when this column equals this value (string comparison).
            </p>
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Death Date</h3>

          <div>
            <label className="text-sm font-medium text-gray-700">Date determination method</label>
            <select
              value={cfg.date_method}
              onChange={e => setCfg(prev => ({ ...prev, date_method: e.target.value as DeathConfig['date_method'] }))}
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="onset_plus_years">Onset date + N years (estimated)</option>
              <option value="direct_col">Direct date column</option>
            </select>
          </div>

          {cfg.date_method === 'onset_plus_years' && (
            <>
              <FieldMapper
                label="Onset date column"
                sourceColumns={cols}
                value={cfg.onset_col || ''}
                onChange={v => setCfg(prev => ({ ...prev, onset_col: v }))}
                hint="The study entry / onset date used as the reference"
              />
              <div>
                <label className="text-sm font-medium text-gray-700">Years offset</label>
                <input
                  type="number"
                  value={cfg.years_offset ?? 10}
                  onChange={e => setCfg(prev => ({ ...prev, years_offset: parseFloat(e.target.value) }))}
                  className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <p className="text-xs text-gray-500 mt-1">
                  Death date = onset + (years × 365.25 days)
                </p>
              </div>
            </>
          )}

          {cfg.date_method === 'direct_col' && (
            <FieldMapper
              label="Death date column"
              sourceColumns={cols}
              value={cfg.date_col || ''}
              onChange={v => setCfg(prev => ({ ...prev, date_col: v }))}
            />
          )}
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6">
          <label className="text-sm font-medium text-gray-700">death_type_concept_id</label>
          <select
            value={cfg.death_type_concept_id}
            onChange={e => setCfg(prev => ({ ...prev, death_type_concept_id: parseInt(e.target.value) }))}
            className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
          >
            <option value={32879}>32879 — Registry</option>
            <option value={32817}>32817 — EHR</option>
          </select>
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
