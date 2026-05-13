import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import type { Project, ProviderConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: ProviderConfig = {
  enabled: true,
  provider_name_col: '',
  npi_col: '',
  dea_col: '',
  specialty_concept_id: null,
  care_site_source_value_col: '',
  year_of_birth_col: '',
  gender_concept_id: null,
  provider_source_value_col: '',
  specialty_source_value_col: '',
  gender_source_value_col: '',
}

const GENDER_OPTIONS = [
  { id: null,   label: '— not set —' },
  { id: 8507,   label: '8507 — Male' },
  { id: 8532,   label: '8532 — Female' },
  { id: 8551,   label: '8551 — Unknown' },
]

export default function Step7Provider({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<ProviderConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')

  useEffect(() => {
    getTableConfig(project.id, 'provider').then((ex: ProviderConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
  }, [project.id])

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'provider', { ...cfg, extra_instructions: extraInstructions })
    onUpdate(p)
  }

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/5`)
  }

  const set = (field: keyof ProviderConfig) => (v: string) =>
    setCfg(prev => ({ ...prev, [field]: v }))

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={4}
      onBack={() => navigate(`/project/${project.id}/step/3`)}
      onNext={handleNext}
      nextLabel="Next: Person →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Provider Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Map source columns to the OMOP PROVIDER table. Providers are uniquely identified
            healthcare individuals (physicians, nurses, etc.). If the source only gives specialty
            without individual identifiers, generic pooled provider records are acceptable.
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Provider Identity</h3>

          <FieldMapper
            label="provider_source_value"
            sourceColumns={cols}
            value={cfg.provider_source_value_col}
            onChange={set('provider_source_value_col')}
            hint="Verbatim provider identifier from the source. Used as the deduplication key (max 50 chars)."
          />

          <FieldMapper
            label="provider_name"
            sourceColumns={cols}
            value={cfg.provider_name_col}
            onChange={set('provider_name_col')}
            hint="Name of the provider as it appears in the source (max 255 chars)."
          />

          <FieldMapper
            label="npi"
            sourceColumns={cols}
            value={cfg.npi_col}
            onChange={set('npi_col')}
            hint="National Provider Identifier (US). Max 20 chars."
          />

          <FieldMapper
            label="dea"
            sourceColumns={cols}
            value={cfg.dea_col}
            onChange={set('dea_col')}
            hint="DEA identifier for controlled substance prescriptions. Max 20 chars."
          />

          <FieldMapper
            label="year_of_birth"
            sourceColumns={cols}
            value={cfg.year_of_birth_col}
            onChange={set('year_of_birth_col')}
            hint="Column containing the provider's birth year (integer)."
          />
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-gray-800">Specialty</h3>
            <a href="http://athena.ohdsi.org/search-terms/terms?domain=Provider&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline">Accepted Concepts</a>
          </div>

          <FieldMapper
            label="specialty_source_value"
            sourceColumns={cols}
            value={cfg.specialty_source_value_col}
            onChange={set('specialty_source_value_col')}
            hint="Specialty as it appears in the source (max 50 chars)."
          />

          <div>
            <label className="text-sm font-medium text-gray-700">
              specialty_concept_id
              <span className="ml-1 font-normal text-gray-400">— standard OMOP Provider-domain concept</span>
            </label>
            <input
              type="number"
              value={cfg.specialty_concept_id ?? ''}
              onChange={e => setCfg(prev => ({
                ...prev,
                specialty_concept_id: e.target.value === '' ? null : parseInt(e.target.value),
              }))}
              placeholder="e.g. 38004477 for Internal Medicine"
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-500 mt-1">
              Used as a constant for all providers. Set to 0 or leave blank if unknown.
            </p>
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-gray-800">Gender</h3>
            <a href="http://athena.ohdsi.org/search-terms/terms?domain=Gender&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline">Accepted Concepts</a>
          </div>

          <FieldMapper
            label="gender_source_value"
            sourceColumns={cols}
            value={cfg.gender_source_value_col}
            onChange={set('gender_source_value_col')}
            hint="Provider gender as it appears in the source (max 50 chars)."
          />

          <div>
            <label className="text-sm font-medium text-gray-700">
              gender_concept_id
              <span className="ml-1 font-normal text-gray-400">— standard OMOP Gender concept</span>
            </label>
            <select
              value={cfg.gender_concept_id ?? ''}
              onChange={e => setCfg(prev => ({
                ...prev,
                gender_concept_id: e.target.value === '' ? null : parseInt(e.target.value),
              }))}
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {GENDER_OPTIONS.map(o => (
                <option key={o.id ?? 'null'} value={o.id ?? ''}>{o.label}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Care Site Link</h3>

          <FieldMapper
            label="care_site_source_value column"
            sourceColumns={cols}
            value={cfg.care_site_source_value_col}
            onChange={set('care_site_source_value_col')}
            hint="Column whose value matches care_site_source_value in care_site.csv — used to look up care_site_id."
          />
        </div>

        <ExtraInstructions
          tableName="provider"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="provider"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
