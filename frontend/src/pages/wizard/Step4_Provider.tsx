import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import type { Project, ProviderConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
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
  gender_concept_value_map: {},
  gender_concept_id_default: 0,
  provider_source_value_col: '',
  specialty_source_value_col: '',
  gender_source_value_col: '',
}

interface ColumnInfo { distinct_values: string[] }

export default function Step7Provider({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<ProviderConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})

  useEffect(() => {
    getTableConfig(project.id, 'provider').then((ex: ProviderConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
    getColumnValues(project.id).then(setColumnInfos)
  }, [project.id])

  const distinctVals = (col: string): string[] =>
    columnInfos[col]?.distinct_values ?? []

  const saveConfig = async () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { gender_concept_id: _gid, specialty_concept_id: _sid, ...cleanCfg } = cfg as ProviderConfig & { gender_concept_id?: unknown; specialty_concept_id?: unknown }
    const p = await updateTableConfig(project.id, 'provider', { ...cleanCfg, extra_instructions: extraInstructions })
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
            label="Specialty column"
            sourceColumns={cols}
            value={cfg.specialty_source_value_col}
            onChange={v => setCfg(prev => ({
              ...prev,
              specialty_source_value_col: v,
              specialty_concept_value_map: v !== prev.specialty_source_value_col ? {} : prev.specialty_concept_value_map,
            }))}
            hint="Column containing the provider's specialty. Values will populate specialty_source_value and be mapped to specialty_concept_id below."
          />

          {cfg.specialty_source_value_col && (
            <ValueConceptMapper
              label="Specialty value → specialty_concept_id"
              sourceValues={distinctVals(cfg.specialty_source_value_col)}
              mapping={cfg.specialty_concept_value_map ?? {}}
              onChange={m => setCfg(prev => ({ ...prev, specialty_concept_value_map: m }))}
              hint="Assign an OMOP Provider-domain concept ID to each specialty value."
            />
          )}
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <div className="flex items-center justify-between">
            <h3 className="font-medium text-gray-800">Gender</h3>
            <a href="http://athena.ohdsi.org/search-terms/terms?domain=Gender&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline">Accepted Concepts</a>
          </div>

          <FieldMapper
            label="Gender column"
            sourceColumns={cols}
            value={cfg.gender_source_value_col}
            onChange={v => setCfg(prev => ({
              ...prev,
              gender_source_value_col: v,
              gender_concept_value_map: v !== prev.gender_source_value_col ? {} : prev.gender_concept_value_map,
            }))}
            hint="Provider gender as it appears in the source. Values will populate gender_source_value and be mapped to gender_concept_id below."
          />

          {cfg.gender_source_value_col && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium text-gray-700">Gender value → gender_concept_id</label>
              </div>
              <p className="text-xs text-gray-500">Common: 8507 = Male, 8532 = Female, 8551 = Unknown</p>
              <ValueConceptMapper
                label=""
                sourceValues={distinctVals(cfg.gender_source_value_col)}
                mapping={cfg.gender_concept_value_map ?? {}}
                onChange={m => setCfg(prev => ({ ...prev, gender_concept_value_map: m }))}
                hint="Assign an OMOP Gender-domain concept ID to each gender value."
              />
            </div>
          )}

          <div>
            <label className="text-sm font-medium text-gray-700">Default gender_concept_id</label>
            <input
              type="number"
              value={cfg.gender_concept_id_default ?? 0}
              onChange={e => setCfg(prev => ({ ...prev, gender_concept_id_default: parseInt(e.target.value) }))}
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <p className="text-xs text-gray-500 mt-1">Used when a source value is not in the map above (0 = unknown).</p>
          </div>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <h3 className="font-medium text-gray-800">Care Site Link</h3>

          <FieldMapper
            label="care_site_source_value column"
            sourceColumns={cols}
            value={cfg.care_site_source_value_col}
            onChange={set('care_site_source_value_col')}
            hint="Column whose value matches the care_site_source_value in care_site.csv (composite: location_col + '_' + name_col) — used to look up care_site_id."
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
