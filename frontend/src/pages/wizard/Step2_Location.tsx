import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import type { Project, LocationConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'

interface ColumnInfo { distinct_values: string[] }

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
  country_source_value_col: '',
  cs_address_1_col: '',
  cs_address_2_col: '',
  cs_city_col: '',
  cs_state_col: '',
  cs_zip_col: '',
  cs_county_col: '',
  cs_country_source_value_col: '',
  cs_latitude_col: '',
  cs_longitude_col: '',
  country_concept_id_map: {},
  country_concept_id_default: 0,
}

function AutoComputedBadge({ cfg, fields }: {
  cfg: LocationConfig
  fields: (keyof LocationConfig)[]
}) {
  const active = fields
    .map(f => {
      const val = cfg[f] as string
      return val ? `${String(f).replace(/_col$/, '').replace(/^cs_/, '')} (${val})` : null
    })
    .filter(Boolean) as string[]

  return (
    <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 flex flex-col gap-2">
      <p className="text-sm font-medium text-blue-800">Auto-computed — no mapping required</p>
      <p className="text-sm text-blue-700">
        Constructed by joining the mapped address fields with{' '}
        <code className="bg-blue-100 px-1 rounded text-xs"> | </code>.
        Used as the deduplication key for the LOCATION table.
      </p>
      <div className="mt-1">
        <p className="text-xs font-medium text-blue-600 mb-1">Contributing columns:</p>
        <div className="flex flex-wrap gap-1">
          {active.length > 0
            ? active.map(col => (
                <span key={col} className="bg-blue-100 text-blue-800 text-xs px-2 py-0.5 rounded-full">{col}</span>
              ))
            : <span className="text-xs italic text-blue-500">Map address fields above to see contributing columns</span>
          }
        </div>
      </div>
    </div>
  )
}

export default function Step5Location({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<LocationConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [countyValues, setCountyValues] = useState<string[]>([])

  useEffect(() => {
    Promise.all([
      getTableConfig(project.id, 'location'),
      getColumnValues(project.id),
    ]).then(([ex, infos]: [LocationConfig & { extra_instructions?: string }, Record<string, ColumnInfo>]) => {
      setColumnInfos(infos)
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        const loaded: LocationConfig = {
          ...DEFAULTS,
          ...ex,
          country_concept_id_map: ex.country_concept_id_map ?? {},
          country_concept_id_default: ex.country_concept_id_default ?? 0,
        }
        if (loaded.county_col) {
          const savedKeys = Object.keys(loaded.country_concept_id_map)
          setCountyValues(savedKeys.length > 0 ? savedKeys : (infos[loaded.county_col]?.distinct_values ?? []))
        }
        setCfg(loaded)
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
    navigate(`/project/${project.id}/step/3`)
  }

  const set = (field: keyof LocationConfig) => (v: string) =>
    setCfg(prev => ({ ...prev, [field]: v }))

  const handleCountyColChange = (col: string) => {
    setCfg(prev => ({ ...prev, county_col: col, country_concept_id_map: {} }))
    setCountyValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  const addCountyValue = () => {
    const val = prompt('Enter a source country value (e.g. US, GR, United States):')
    if (val) setCountyValues(prev => [...new Set([...prev, val])])
  }

  const PERSON_ADDR_FIELDS: (keyof LocationConfig)[] = [
    'address_1_col', 'address_2_col', 'city_col', 'state_col', 'zip_col', 'county_col', 'country_source_value_col',
  ]
  const CS_ADDR_FIELDS: (keyof LocationConfig)[] = [
    'cs_address_1_col', 'cs_address_2_col', 'cs_city_col', 'cs_state_col', 'cs_zip_col', 'cs_county_col', 'cs_country_source_value_col',
  ]

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={2}
      onBack={() => navigate(`/project/${project.id}/step/1`)}
      onNext={handleNext}
      nextLabel="Next: Care Site →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Location Mapping</h2>
          <p className="text-sm text-gray-500 mt-1">
            Map source columns to the OMOP LOCATION table. Locations are shared between Persons and
            Care Sites — define which columns hold each group's address below. All unique addresses
            are combined and deduplicated into a single <code className="bg-gray-100 px-1 rounded">location.csv</code>.
          </p>
        </div>

        {/* ── PERSON ADDRESS ── */}
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-gray-200" />
            <span className="text-sm font-semibold text-gray-600 uppercase tracking-wide">Person Address</span>
            <div className="flex-1 h-px bg-gray-200" />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">Address Lines</h3>
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
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">City</h3>
            <FieldMapper
              label="city"
              sourceColumns={cols}
              value={cfg.city_col}
              onChange={set('city_col')}
            />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">State & ZIP</h3>
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
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">Country</h3>
            <FieldMapper
              label="county"
              sourceColumns={cols}
              value={cfg.county_col}
              onChange={handleCountyColChange}
              hint="County or region (max 20 chars). Source values are stored in the county field; each value can be mapped to a country_concept_id below."
            />
            {cfg.county_col && (
              <div className="flex flex-col gap-3 pl-2 border-l-2 border-blue-100">
                <div className="flex items-center justify-between">
                  <div>
                    <label className="text-sm font-medium text-gray-700">Country value → OMOP concept ID mapping</label>
                    <p className="text-xs text-gray-500 mt-0.5">e.g. 4330442 = United States, 4079432 = Greece</p>
                  </div>
                  <button onClick={addCountyValue} className="text-xs text-blue-600 hover:underline">+ Add value</button>
                </div>
                <ValueConceptMapper
                  label=""
                  sourceValues={countyValues.length > 0 ? countyValues : Object.keys(cfg.country_concept_id_map)}
                  mapping={cfg.country_concept_id_map}
                  onChange={m => setCfg(prev => ({ ...prev, country_concept_id_map: m }))}
                />
              </div>
            )}
            <div>
              <label className="text-sm font-medium text-gray-700">Default country_concept_id</label>
              <input
                type="number"
                value={cfg.country_concept_id_default ?? 0}
                onChange={e => setCfg(prev => ({ ...prev, country_concept_id_default: parseInt(e.target.value) || 0 }))}
                className="mt-1 border border-gray-300 rounded-md px-3 py-2 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <p className="text-xs text-gray-500 mt-1">
                {cfg.county_col
                  ? 'Used when a source value is not in the map above (0 = unknown). Also applies to Care Site addresses.'
                  : 'Applied to all rows when no county column is mapped (0 = unknown). Also applies to Care Site addresses.'}
              </p>
            </div>
            <FieldMapper
              label="country_source_value"
              sourceColumns={cols}
              value={cfg.country_source_value_col}
              onChange={set('country_source_value_col')}
              hint="Free-text country name (max 80 chars)."
            />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-3">
            <h3 className="font-medium text-gray-800">Person Location Source Value</h3>
            <AutoComputedBadge cfg={cfg} fields={PERSON_ADDR_FIELDS} />
          </div>
        </div>

        {/* ── CARE SITE ADDRESS ── */}
        <div className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-gray-200" />
            <span className="text-sm font-semibold text-gray-600 uppercase tracking-wide">Care Site Address</span>
            <div className="flex-1 h-px bg-gray-200" />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">Address Lines</h3>
            <FieldMapper
              label="address_1"
              sourceColumns={cols}
              value={cfg.cs_address_1_col}
              onChange={set('cs_address_1_col')}
              hint="First line of the care site address (max 50 chars)."
            />
            <FieldMapper
              label="address_2"
              sourceColumns={cols}
              value={cfg.cs_address_2_col}
              onChange={set('cs_address_2_col')}
              hint="Second line of the care site address (max 50 chars)."
            />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">City</h3>
            <FieldMapper
              label="city"
              sourceColumns={cols}
              value={cfg.cs_city_col}
              onChange={set('cs_city_col')}
            />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">State & ZIP</h3>
            <FieldMapper
              label="state"
              sourceColumns={cols}
              value={cfg.cs_state_col}
              onChange={set('cs_state_col')}
              hint="2-character state/province/district abbreviation."
            />
            <FieldMapper
              label="zip"
              sourceColumns={cols}
              value={cfg.cs_zip_col}
              onChange={set('cs_zip_col')}
              hint="Zip / postal code stored as a string (up to 9 chars)."
            />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">Country</h3>
            <FieldMapper
              label="county"
              sourceColumns={cols}
              value={cfg.cs_county_col}
              onChange={set('cs_county_col')}
              hint="County or region (max 20 chars). Uses the same country_concept_id mapping defined in the Person Address section above."
            />
            <FieldMapper
              label="country_source_value"
              sourceColumns={cols}
              value={cfg.cs_country_source_value_col}
              onChange={set('cs_country_source_value_col')}
              hint="Free-text country name (max 80 chars)."
            />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
            <h3 className="font-medium text-gray-800">Coordinates</h3>
            <FieldMapper
              label="latitude"
              sourceColumns={cols}
              value={cfg.cs_latitude_col}
              onChange={set('cs_latitude_col')}
              hint="Decimal latitude — must be between −90 and 90."
            />
            <FieldMapper
              label="longitude"
              sourceColumns={cols}
              value={cfg.cs_longitude_col}
              onChange={set('cs_longitude_col')}
              hint="Decimal longitude — must be between −180 and 180."
            />
          </div>

          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-3">
            <h3 className="font-medium text-gray-800">Care Site Location Source Value</h3>
            <AutoComputedBadge cfg={cfg} fields={CS_ADDR_FIELDS} />
          </div>
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
