import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import type { Project, ObservationPeriodConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: ObservationPeriodConfig = {
  enabled: true,
  start_date_col: '',
  end_date_col: '',
  end_date_fallback: 'start_date',
  period_type_concept_id: 32879,
}


export default function Step4ObsPeriod({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<ObservationPeriodConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')

  useEffect(() => {
    getTableConfig(project.id, 'observation_period').then((ex: ObservationPeriodConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
  }, [project.id])

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'observation_period', { ...cfg, extra_instructions: extraInstructions })
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
      generatedScripts={project.generated_scripts}
      sourceUploaded={!!project.source_filename}
      hasMappingFiles={Object.keys(project.mapping_files || {}).length > 0}
      onBack={() => navigate(`/project/${project.id}/step/6`)}
      onNext={handleNext}
      nextLabel="Next: Death →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-primary">Observation Period Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Define the time spans during which each patient was actively observed. Within these
            spans, clinical events are assumed to be fully recorded — absence of a record means
            the event did not occur. Each person must have at least one observation period.
            Overlapping or adjacent periods are automatically merged.
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <FieldMapper
            label="Start date column"
            sourceColumns={cols}
            value={cfg.start_date_col}
            onChange={v => setCfg(prev => ({ ...prev, start_date_col: v }))}
            required
            hint="Enrollment/study entry date. If absent in source, the earliest clinical event date per person will be used."
          />

          <FieldMapper
            label="End date column"
            sourceColumns={cols}
            value={cfg.end_date_col}
            onChange={v => setCfg(prev => ({ ...prev, end_date_col: v }))}
            hint="Enrollment end / last follow-up date. If absent, the last clinical event date or fallback below is used."
          />

          <div>
            <label className="text-sm font-medium text-gray-700">If end date is missing, fallback to:</label>
            <select
              value={cfg.end_date_fallback}
              onChange={e => setCfg(prev => ({ ...prev, end_date_fallback: e.target.value as 'start_date' | 'today' }))}
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              <option value="start_date">Start date (observation period of 1 day)</option>
              <option value="today">Today's date</option>
            </select>
          </div>

          <div>
            <label className="text-sm font-medium text-gray-700">
              period_type_concept_id
              <span className="ml-1 font-normal text-gray-400">— how the period was determined</span>
            </label>
            <input
              type="number"
              value={cfg.period_type_concept_id}
              onChange={e => setCfg(prev => ({ ...prev, period_type_concept_id: parseInt(e.target.value) || 0 }))}
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
              placeholder="e.g. 32879"
            />
            <a
              href="https://athena.ohdsi.org/search-terms/terms?domain=Type+Concept&standardConcept=Standard&page=1&pageSize=15&query="
              target="_blank"
              rel="noopener noreferrer"
              className="mt-1 inline-block text-xs text-blue-600 hover:underline"
            >
              Accepted Concepts (Athena)
            </a>
          </div>
        </div>

        <ExtraInstructions
          tableName="observation_period"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="observation_period"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
