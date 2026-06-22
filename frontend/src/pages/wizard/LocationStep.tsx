import { useState, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import { extractMappedCols, getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, LocationConfig } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import SingleConceptInput from '../../components/SingleConceptInput'
import DomainWarning from '../../components/DomainWarning'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { useDomainValidation } from '../../hooks/useDomainValidation'
import { useSourceFile } from '../../hooks/useSourceFile'

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
  county_source_value: '',
  country_col: '',
  country_source_value: '',
  latitude_col: '',
  longitude_col: '',
  cs_address_1_col: '',
  cs_address_2_col: '',
  cs_city_col: '',
  cs_state_col: '',
  cs_zip_col: '',
  cs_county_col: '',
  cs_county_source_value: '',
  cs_country_col: '',
  cs_country_source_value: '',
  cs_latitude_col: '',
  cs_longitude_col: '',
  country_concept_id_map: {},
  country_concept_id_default: 0,
  cs_country_concept_id_map: {},
  cs_country_concept_id_default: 0,
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
    <div className="rounded-lg border border-border bg-secondary/60 p-4 flex flex-col gap-2">
      <p className="text-sm font-medium text-secondary-foreground">Auto-computed — no mapping required</p>
      <p className="text-sm text-muted-foreground">
        Constructed by joining the mapped address fields with{' '}
        <code className="bg-accent px-1 rounded text-xs"> | </code>.
        Used as the deduplication key for the LOCATION table.
      </p>
      <div className="mt-1">
        <p className="text-xs font-medium text-primary mb-1">Contributing columns:</p>
        <div className="flex flex-wrap gap-1">
          {active.length > 0
            ? active.map(col => (
                <span key={col} className="bg-accent text-secondary-foreground text-xs px-2 py-0.5 rounded-full">{col}</span>
              ))
            : <span className="text-xs italic text-muted-foreground">Map address fields above to see contributing columns</span>
          }
        </div>
      </div>
    </div>
  )
}

export default function LocationStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const { cols, filePicker, selectedFile } = useSourceFile(project, 'location', { getConfig: () => cfg, setConfig: (saved) => setCfg(saved ?? DEFAULTS) })
  const [cfg, setCfg] = useState<LocationConfig>(DEFAULTS)
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'location'), [project.etl_config])
  const stepUsed = useMemo(() => extractMappedCols(cfg), [cfg])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || (!crossUsed.has(c) && !stepUsed.has(c)))
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [countryValues, setCountryValues] = useState<string[]>([])
  const [csCountryValues, setCsCountryValues] = useState<string[]>([])
  const [countryMode, setCountryMode] = useState<'column' | 'default'>('column')
  const [csCountryMode, setCsCountryMode] = useState<'column' | 'default'>('column')
  const colValuesLoaded = useRef(false)

  const personCountryIds = countryMode === 'column'
    ? Object.values(cfg.country_concept_id_map)
    : cfg.country_concept_id_default > 0 ? [cfg.country_concept_id_default] : []
  const personCountryViolations = useDomainValidation(personCountryIds, 'Geography')

  const csCountryIds = csCountryMode === 'column'
    ? Object.values(cfg.cs_country_concept_id_map)
    : cfg.cs_country_concept_id_default > 0 ? [cfg.cs_country_concept_id_default] : []
  const csCountryViolations = useDomainValidation(csCountryIds, 'Geography')

  useEffect(() => {
    Promise.all([
      getTableConfig(project.id, 'location'),
      getColumnValues(project.id, selectedFile?.filename),
    ]).then(([ex, infos]: [LocationConfig & { extra_instructions?: string }, Record<string, ColumnInfo>]) => {
      setColumnInfos(infos)
      colValuesLoaded.current = true
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        const loaded: LocationConfig = {
          ...DEFAULTS,
          ...ex,
          country_concept_id_map: ex.country_concept_id_map ?? {},
          country_concept_id_default: ex.country_concept_id_default ?? 0,
          cs_country_concept_id_map: ex.cs_country_concept_id_map ?? {},
          cs_country_concept_id_default: ex.cs_country_concept_id_default ?? 0,
          country_source_value: ex.country_source_value ?? '',
          cs_country_source_value: ex.cs_country_source_value ?? '',
          county_source_value: ex.county_source_value ?? '',
          cs_county_source_value: ex.cs_county_source_value ?? '',
        }
        if (loaded.country_mode) {
          setCountryMode(loaded.country_mode)
          if (loaded.country_mode === 'column' && loaded.country_col) {
            const savedKeys = Object.keys(loaded.country_concept_id_map)
            setCountryValues(savedKeys.length > 0 ? savedKeys : (infos[loaded.country_col]?.distinct_values ?? []))
          }
        } else if (loaded.country_col) {
          const savedKeys = Object.keys(loaded.country_concept_id_map)
          setCountryValues(savedKeys.length > 0 ? savedKeys : (infos[loaded.country_col]?.distinct_values ?? []))
          setCountryMode('column')
        } else if (loaded.country_concept_id_default || loaded.country_source_value) {
          setCountryMode('default')
        }
        if (loaded.cs_country_mode) {
          setCsCountryMode(loaded.cs_country_mode)
          if (loaded.cs_country_mode === 'column' && loaded.cs_country_col) {
            const savedKeys = Object.keys(loaded.cs_country_concept_id_map)
            setCsCountryValues(savedKeys.length > 0 ? savedKeys : (infos[loaded.cs_country_col]?.distinct_values ?? []))
          }
        } else if (loaded.cs_country_col) {
          const savedKeys = Object.keys(loaded.cs_country_concept_id_map)
          setCsCountryValues(savedKeys.length > 0 ? savedKeys : (infos[loaded.cs_country_col]?.distinct_values ?? []))
          setCsCountryMode('column')
        } else if (loaded.cs_country_concept_id_default || loaded.cs_country_source_value) {
          setCsCountryMode('default')
        }
        setCfg(loaded)
      }
    })
  }, [project.id])

  useEffect(() => {
    if (!colValuesLoaded.current) return
    getColumnValues(project.id, selectedFile?.filename).then(setColumnInfos)
  }, [project.id, selectedFile?.filename])

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'location', {
      ...cfg,
      extra_instructions: extraInstructions,
      country_mode: countryMode,
      cs_country_mode: csCountryMode,
      source_filename: selectedFile?.filename ?? null,
    })
    onUpdate(p)
  }

  const { prev, next } = getAdjacentSlugs(project, 'location')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

  const set = (field: keyof LocationConfig) => (v: string) =>
    setCfg(prev => ({ ...prev, [field]: v }))

  const handleCountryColChange = (col: string) => {
    setCfg(prev => ({ ...prev, country_col: col, country_concept_id_map: {} }))
    setCountryValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  const handleCsCountryColChange = (col: string) => {
    setCfg(prev => ({ ...prev, cs_country_col: col, cs_country_concept_id_map: {} }))
    setCsCountryValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  const switchCountryMode = (mode: 'column' | 'default') => {
    setCountryMode(mode)
    if (mode === 'default') {
      setCfg(prev => ({ ...prev, country_col: '', country_concept_id_map: {} }))
      setCountryValues([])
    } else {
      setCfg(prev => ({ ...prev, country_concept_id_default: 0, country_source_value: '' }))
    }
  }

  const switchCsCountryMode = (mode: 'column' | 'default') => {
    setCsCountryMode(mode)
    if (mode === 'default') {
      setCfg(prev => ({ ...prev, cs_country_col: '', cs_country_concept_id_map: {} }))
      setCsCountryValues([])
    } else {
      setCfg(prev => ({ ...prev, cs_country_concept_id_default: 0, cs_country_source_value: '' }))
    }
  }

  const addCountryValue = () => {
    const val = prompt('Enter a source country value (e.g. US, GR, United States):')
    if (val) setCountryValues(prev => [...new Set([...prev, val])])
  }

  const addCsCountryValue = () => {
    const val = prompt('Enter a source country value (e.g. US, GR, United States):')
    if (val) setCsCountryValues(prev => [...new Set([...prev, val])])
  }

  const PERSON_ADDR_FIELDS: (keyof LocationConfig)[] = [
    'address_1_col', 'address_2_col', 'city_col', 'state_col', 'zip_col', 'county_col', 'country_source_value',
  ]
  const CS_ADDR_FIELDS: (keyof LocationConfig)[] = [
    'cs_address_1_col', 'cs_address_2_col', 'cs_city_col', 'cs_state_col', 'cs_zip_col', 'cs_county_col', 'cs_country_source_value',
  ]

  return (
    <WizardLayout
      project={project}
      currentSlug="location"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel="Next →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        {filePicker}
        <div>
          <h2 className="text-xl font-bold text-primary">Location Mapping</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Map source columns to the OMOP LOCATION table. Locations are shared between Persons and
            Care Sites — define which columns hold each group's address below. All unique addresses
            are combined and deduplicated into a single <code className="bg-muted px-1 rounded">location.csv</code>.
          </p>
        </div>

        {/* ── PERSON LOCATION ── */}
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-secondary/70 p-4">
          <p className="text-base font-bold uppercase tracking-wide text-muted-foreground">Person Location</p>

          <Card className="flex flex-col gap-5 p-6">
            <h3 className="font-semibold text-foreground">Address Lines</h3>
            <FieldMapper
              label="address_1"
              sourceColumns={availCols(cfg.address_1_col)}
              value={cfg.address_1_col}
              onChange={set('address_1_col')}
              hint="First line of the address (max 50 chars)."
            />
            <FieldMapper
              label="address_2"
              sourceColumns={availCols(cfg.address_2_col)}
              value={cfg.address_2_col}
              onChange={set('address_2_col')}
              hint="Second line of the address (max 50 chars)."
            />
          </Card>

          <Card className="flex flex-col gap-5 p-6">
            <h3 className="font-semibold text-foreground">City</h3>
            <FieldMapper
              label="city"
              sourceColumns={availCols(cfg.city_col)}
              value={cfg.city_col}
              onChange={set('city_col')}
            />
          </Card>

          <Card className="flex flex-col gap-5 p-6">
            <h3 className="font-semibold text-foreground">State, ZIP & County</h3>
            <FieldMapper
              label="state"
              sourceColumns={availCols(cfg.state_col)}
              value={cfg.state_col}
              onChange={set('state_col')}
              hint="2-character state/province/district abbreviation."
            />
            <FieldMapper
              label="zip"
              sourceColumns={availCols(cfg.zip_col)}
              value={cfg.zip_col}
              onChange={set('zip_col')}
              hint="Zip / postal code stored as a string (up to 9 chars). Leading zeros are preserved."
            />
            <FieldMapper
              label="county"
              sourceColumns={availCols(cfg.county_col)}
              value={cfg.county_col}
              onChange={set('county_col')}
              hint="County or sub-region (max 20 chars)."
            />
          </Card>

          <Card className="flex flex-col gap-5 p-6">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-foreground">Country</h3>
              <a href="https://athena.ohdsi.org/search-terms/terms?domain=Geography&standardConcept=Standard&page=1&pageSize=15&query=&boosts" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => switchCountryMode('column')}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${countryMode === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}
              >Map a column</button>
              <button
                onClick={() => switchCountryMode('default')}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${countryMode === 'default' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}
              >Set default</button>
            </div>
            {countryMode === 'column' ? (
              <div className="flex flex-col gap-3">
                <FieldMapper
                  label="country"
                  sourceColumns={availCols(cfg.country_col)}
                  value={cfg.country_col}
                  onChange={handleCountryColChange}
                />
                {cfg.country_col && (
                  <div className="flex flex-col gap-3 pl-2 border-l-2 border-primary/30">
                    <div className="flex items-center justify-between">
                      <div>
                        <Label>Country value → OMOP concept ID mapping</Label>
                        <p className="text-xs text-muted-foreground mt-0.5">e.g. 4330442 = United States, 4079432 = Greece</p>
                      </div>
                      <button onClick={addCountryValue} className="text-xs text-primary hover:underline">+ Add value</button>
                    </div>
                    <ValueConceptMapper
                      label=""
                      sourceValues={countryValues.length > 0 ? countryValues : Object.keys(cfg.country_concept_id_map)}
                      mapping={cfg.country_concept_id_map}
                      onChange={m => setCfg(prev => ({ ...prev, country_concept_id_map: m }))}
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label>Default country_concept_id</Label>
                  <SingleConceptInput
                    value={cfg.country_concept_id_default || null}
                    onChange={v => setCfg(prev => ({ ...prev, country_concept_id_default: v ?? 0 }))}
                    onConceptName={name => { if (name) setCfg(prev => ({ ...prev, country_source_value: name })) }}
                    placeholder="e.g. 4330442"
                  />
                  <p className="text-xs text-muted-foreground mt-1">Applied to all person rows (0 = unknown).</p>
                </div>
                <div>
                  <Label>Default country_source_value</Label>
                  <Input
                    type="text"
                    value={cfg.country_source_value}
                    onChange={e => setCfg(prev => ({ ...prev, country_source_value: e.target.value }))}
                    placeholder="e.g. United States"
                    className="mt-1"
                  />
                  <p className="text-xs text-muted-foreground mt-1">Applied to all person rows (max 80 chars).</p>
                </div>
              </div>
            )}
            <DomainWarning violations={personCountryViolations} expectedDomain="Geography" />
          </Card>

          <Card className="flex flex-col gap-3 p-6">
            <h3 className="font-semibold text-foreground">Person Location Source Value</h3>
            <AutoComputedBadge
              cfg={cfg}
              fields={PERSON_ADDR_FIELDS}
            />
          </Card>

          <Card className="flex flex-col gap-5 p-6">
            <h3 className="font-semibold text-foreground">Coordinates</h3>
            <FieldMapper
              label="latitude"
              sourceColumns={availCols(cfg.latitude_col)}
              value={cfg.latitude_col}
              onChange={set('latitude_col')}
              hint="Decimal latitude — must be between −90 and 90."
            />
            <FieldMapper
              label="longitude"
              sourceColumns={availCols(cfg.longitude_col)}
              value={cfg.longitude_col}
              onChange={set('longitude_col')}
              hint="Decimal longitude — must be between −180 and 180."
            />
          </Card>
        </div>

        {/* ── CARE SITE LOCATION ── */}
        <div className="flex flex-col gap-4 rounded-lg border border-border bg-secondary/70 p-4">
          <p className="text-base font-bold uppercase tracking-wide text-muted-foreground">Care Site Location</p>

          <Card className="flex flex-col gap-5 p-6">
            <h3 className="font-semibold text-foreground">Address Lines</h3>
            <FieldMapper
              label="address_1"
              sourceColumns={availCols(cfg.cs_address_1_col)}
              value={cfg.cs_address_1_col}
              onChange={set('cs_address_1_col')}
              hint="First line of the care site address (max 50 chars)."
            />
            <FieldMapper
              label="address_2"
              sourceColumns={availCols(cfg.cs_address_2_col)}
              value={cfg.cs_address_2_col}
              onChange={set('cs_address_2_col')}
              hint="Second line of the care site address (max 50 chars)."
            />
          </Card>

          <Card className="flex flex-col gap-5 p-6">
            <h3 className="font-semibold text-foreground">City</h3>
            <FieldMapper
              label="city"
              sourceColumns={availCols(cfg.cs_city_col)}
              value={cfg.cs_city_col}
              onChange={set('cs_city_col')}
            />
          </Card>

          <Card className="flex flex-col gap-5 p-6">
            <h3 className="font-semibold text-foreground">State, ZIP & County</h3>
            <FieldMapper
              label="state"
              sourceColumns={availCols(cfg.cs_state_col)}
              value={cfg.cs_state_col}
              onChange={set('cs_state_col')}
              hint="2-character state/province/district abbreviation."
            />
            <FieldMapper
              label="zip"
              sourceColumns={availCols(cfg.cs_zip_col)}
              value={cfg.cs_zip_col}
              onChange={set('cs_zip_col')}
              hint="Zip / postal code stored as a string (up to 9 chars)."
            />
            <FieldMapper
              label="county"
              sourceColumns={availCols(cfg.cs_county_col)}
              value={cfg.cs_county_col}
              onChange={set('cs_county_col')}
              hint="County or sub-region (max 20 chars)."
            />
          </Card>

          <Card className="flex flex-col gap-5 p-6">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-foreground">Country</h3>
              <a href="https://athena.ohdsi.org/search-terms/terms?domain=Geography&standardConcept=Standard&page=1&pageSize=15&query=&boosts" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
            </div>
            <div className="flex gap-2">
              <button
                onClick={() => switchCsCountryMode('column')}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${csCountryMode === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}
              >Map a column</button>
              <button
                onClick={() => switchCsCountryMode('default')}
                className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${csCountryMode === 'default' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}
              >Set default</button>
            </div>
            {csCountryMode === 'column' ? (
              <div className="flex flex-col gap-3">
                <FieldMapper
                  label="country"
                  sourceColumns={availCols(cfg.cs_country_col)}
                  value={cfg.cs_country_col}
                  onChange={handleCsCountryColChange}
                />
                {cfg.cs_country_col && (
                  <div className="flex flex-col gap-3 pl-2 border-l-2 border-primary/30">
                    <div className="flex items-center justify-between">
                      <div>
                        <Label>Country value → OMOP concept ID mapping</Label>
                        <p className="text-xs text-muted-foreground mt-0.5">e.g. 4330442 = United States, 4079432 = Greece</p>
                      </div>
                      <button onClick={addCsCountryValue} className="text-xs text-primary hover:underline">+ Add value</button>
                    </div>
                    <ValueConceptMapper
                      label=""
                      sourceValues={csCountryValues.length > 0 ? csCountryValues : Object.keys(cfg.cs_country_concept_id_map)}
                      mapping={cfg.cs_country_concept_id_map}
                      onChange={m => setCfg(prev => ({ ...prev, cs_country_concept_id_map: m }))}
                    />
                  </div>
                )}
              </div>
            ) : (
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <Label>Default country_concept_id</Label>
                  <SingleConceptInput
                    value={cfg.cs_country_concept_id_default || null}
                    onChange={v => setCfg(prev => ({ ...prev, cs_country_concept_id_default: v ?? 0 }))}
                    onConceptName={name => { if (name) setCfg(prev => ({ ...prev, cs_country_source_value: name })) }}
                    placeholder="e.g. 4330442"
                  />
                  <p className="text-xs text-muted-foreground mt-1">Applied to all care site rows (0 = unknown).</p>
                </div>
                <div>
                  <Label>Default country_source_value</Label>
                  <Input
                    type="text"
                    value={cfg.cs_country_source_value}
                    onChange={e => setCfg(prev => ({ ...prev, cs_country_source_value: e.target.value }))}
                    placeholder="e.g. United States"
                    className="mt-1"
                  />
                  <p className="text-xs text-muted-foreground mt-1">Applied to all care site rows (max 80 chars).</p>
                </div>
              </div>
            )}
            <DomainWarning violations={csCountryViolations} expectedDomain="Geography" />
          </Card>

          <Card className="flex flex-col gap-3 p-6">
            <h3 className="font-semibold text-foreground">Care Site Location Source Value</h3>
            <AutoComputedBadge
              cfg={cfg}
              fields={CS_ADDR_FIELDS}
            />
          </Card>

          <Card className="flex flex-col gap-5 p-6">
            <h3 className="font-semibold text-foreground">Coordinates</h3>
            <FieldMapper
              label="latitude"
              sourceColumns={availCols(cfg.cs_latitude_col)}
              value={cfg.cs_latitude_col}
              onChange={set('cs_latitude_col')}
              hint="Decimal latitude — must be between −90 and 90."
            />
            <FieldMapper
              label="longitude"
              sourceColumns={availCols(cfg.cs_longitude_col)}
              value={cfg.cs_longitude_col}
              onChange={set('cs_longitude_col')}
              hint="Decimal longitude — must be between −180 and 180."
            />
          </Card>
        </div>

        <ExtraInstructions
          tableName="location"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        <ScriptGenerator
          project={project}
          table="location"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
          deterministic
        />
      </div>
    </WizardLayout>
  )
}
