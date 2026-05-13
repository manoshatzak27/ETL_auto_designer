import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import type { Project, CareSiteConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: CareSiteConfig = {
  enabled: true,
  care_site_name_col: '',
  place_of_service_concept_id: null,
  place_of_service_source_value_col: '',
}

const PLACE_OF_SERVICE_OPTIONS = [
  { id: null,   label: '— not set —' },
  { id: 9202,   label: '9202 — Outpatient Visit' },
  { id: 9201,   label: '9201 — Inpatient Visit' },
  { id: 9203,   label: '9203 — Emergency Room Visit' },
  { id: 262,    label: '262 — Emergency Room and Inpatient Visit' },
  { id: 5083,   label: '5083 — Telehealth Visit' },
  { id: 581476, label: '581476 — Home Visit' },
  { id: 8756,   label: '8756 — Outpatient Hospital' },
]

export default function Step6CareSite({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<CareSiteConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')

  useEffect(() => {
    getTableConfig(project.id, 'care_site').then((ex: CareSiteConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
  }, [project.id])

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'care_site', { ...cfg, extra_instructions: extraInstructions })
    onUpdate(p)
  }

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/4`)
  }

  const set = (field: keyof CareSiteConfig) => (v: string) =>
    setCfg(prev => ({ ...prev, [field]: v }))

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={3}
      onBack={() => navigate(`/project/${project.id}/step/2`)}
      onNext={handleNext}
      nextLabel="Next: Provider →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Care Site Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Map source columns to the OMOP CARE_SITE table. A Care Site is a unique combination
            of a <strong>location</strong> and the <strong>nature of the site</strong> — such as its place of service,
            name, or another characteristic. It represents institutional (physical or organizational) units
            where healthcare is delivered: offices, wards, hospitals, clinics, etc. Individual provider
            information belongs in the PROVIDER table, not here. If the source only provides generic
            information (e.g. Place of Service), pooled Care Site records are acceptable.
          </p>
        </div>

        {/* Identifiers */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Identifiers</h3>
          <FieldMapper
            label="care_site_name"
            sourceColumns={cols}
            value={cfg.care_site_name_col}
            onChange={set('care_site_name_col')}
            hint="The name of the care site as it appears in the source data (max 255 chars)."
          />
          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-gray-700">care_site_source_value</label>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 flex flex-col gap-1">
              <p className="text-sm font-medium text-blue-800">Auto-computed — no mapping required</p>
              <p className="text-sm text-blue-700">
                Constructed as{' '}
                <code className="bg-blue-100 px-1 rounded text-xs">cs_location_source_value + "_" + care_site_name</code>.
                The location part comes from the care site address columns mapped in the{' '}
                <strong>Location step</strong>.
              </p>
            </div>
          </div>
        </div>

        {/* Place of Service */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Place of Service</h3>
          <div>
            <label className="text-sm font-medium text-gray-700">
              place_of_service_concept_id
              <span className="ml-1 font-normal text-gray-400">— predominant care setting (Visit domain)</span>
            </label>
            <p className="text-xs text-gray-500 mt-0.5 mb-1">
              A high-level characterization of the Care Site. Choose the Visit-domain standard concept
              that best represents the setting in which most care is delivered here. If visits vary widely,
              leave unset and rely on the visit-level concept instead.
            </p>
            <select
              value={cfg.place_of_service_concept_id ?? ''}
              onChange={e => setCfg(prev => ({
                ...prev,
                place_of_service_concept_id: e.target.value === '' ? null : parseInt(e.target.value),
              }))}
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {PLACE_OF_SERVICE_OPTIONS.map(o => (
                <option key={o.id ?? 'null'} value={o.id ?? ''}>{o.label}</option>
              ))}
            </select>
          </div>
          <FieldMapper
            label="place_of_service_source_value"
            sourceColumns={cols}
            value={cfg.place_of_service_source_value_col}
            onChange={set('place_of_service_source_value_col')}
            hint="Verbatim place-of-service value from the source data (max 50 chars)."
          />
        </div>

        <ExtraInstructions
          tableName="care_site"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="care_site"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
