import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import type { Project, LocationConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: LocationConfig = {
  enabled: true,
  address_1_col: '',
  address_2_col: '',
  city_col: '',
  state_col: '',
  zip_col: '',
  county_col: '',
  location_source_value_col: '',
  country_concept_id: null,
  country_source_value_col: '',
  latitude_col: '',
  longitude_col: '',
}

export default function Step5Location({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<LocationConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')

  useEffect(() => {
    getTableConfig(project.id, 'location').then((ex: LocationConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
  }, [project.id])

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'location', { ...cfg, extra_instructions: extraInstructions })
    onUpdate(p)
  }

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/6`)
  }

  const set = (field: keyof LocationConfig) => (v: string) =>
    setCfg(prev => ({ ...prev, [field]: v }))

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={5}
      onBack={() => navigate(`/project/${project.id}/step/4`)}
      onNext={handleNext}
      nextLabel="Next: Care Site →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Location Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Map source columns to the OMOP LOCATION table. Each unique address is stored once
            and linked to persons or care sites. Fields marked optional can be left unmapped.
            STATE can represent province/district; ZIP also covers postal codes; COUNTY can
            represent a region for international addresses.
          </p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <FieldMapper
            label="address_1"
            sourceColumns={cols}
            value={cfg.address_1_col}
            onChange={set('address_1_col')}
            hint="First line of the address (max 50 chars)."
          />

          <FieldMapper
            label="address_2"
            sourceColumns={cols}
            value={cfg.address_2_col}
            onChange={set('address_2_col')}
            hint="Second line of the address (max 50 chars)."
          />

          <FieldMapper
            label="city"
            sourceColumns={cols}
            value={cfg.city_col}
            onChange={set('city_col')}
          />

          <FieldMapper
            label="state"
            sourceColumns={cols}
            value={cfg.state_col}
            onChange={set('state_col')}
            hint="2-character state/province/district abbreviation."
          />

          <FieldMapper
            label="zip"
            sourceColumns={cols}
            value={cfg.zip_col}
            onChange={set('zip_col')}
            hint="Zip / postal code stored as a string (up to 9 chars). Leading zeros are preserved."
          />

          <FieldMapper
            label="county"
            sourceColumns={cols}
            value={cfg.county_col}
            onChange={set('county_col')}
            hint="County or region (max 20 chars)."
          />

          <FieldMapper
            label="location_source_value"
            sourceColumns={cols}
            value={cfg.location_source_value_col}
            onChange={set('location_source_value_col')}
            hint="Verbatim location value from the source. Used as the deduplication key."
          />

          <FieldMapper
            label="country_source_value"
            sourceColumns={cols}
            value={cfg.country_source_value_col}
            onChange={set('country_source_value_col')}
            hint="Free-text country name (max 80 chars)."
          />

          <div>
            <label className="text-sm font-medium text-gray-700">
              country_concept_id
              <span className="ml-1 font-normal text-gray-400">— OMOP Geography concept ID for the country</span>
            </label>
            <input
              type="number"
              value={cfg.country_concept_id ?? ''}
              onChange={e => setCfg(prev => ({
                ...prev,
                country_concept_id: e.target.value === '' ? null : parseInt(e.target.value),
              }))}
              placeholder="e.g. 4330442 for United States"
              className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm bg-white w-full focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
          </div>

          <FieldMapper
            label="latitude"
            sourceColumns={cols}
            value={cfg.latitude_col}
            onChange={set('latitude_col')}
            hint="Decimal latitude — must be between −90 and 90."
          />

          <FieldMapper
            label="longitude"
            sourceColumns={cols}
            value={cfg.longitude_col}
            onChange={set('longitude_col')}
            hint="Decimal longitude — must be between −180 and 180."
          />
        </div>

        <ExtraInstructions
          tableName="location"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="location"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
