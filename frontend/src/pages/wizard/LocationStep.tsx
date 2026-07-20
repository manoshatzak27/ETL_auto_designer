import { useState, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import { extractMappedCols, getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, LocationConfig, LocationFileConfig, SourceFile } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import SingleConceptInput from '../../components/SingleConceptInput'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { FileText } from 'lucide-react'

interface ColumnInfo { distinct_values: string[] }

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULT_FILE_CFG: LocationFileConfig = {
  person_id_col: '',
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
  country_mode: 'column',
  cs_country_mode: 'column',
}

function deepCopy<T>(v: T): T {
  return JSON.parse(JSON.stringify(v))
}

function AutoComputedBadge({ cfg, fields }: {
  cfg: LocationFileConfig
  fields: (keyof LocationFileConfig)[]
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
  const files: SourceFile[] = project.source_files ?? []
  const isMultiFile = files.length > 1

  // ── Core state ─────────────────────────────────────────────────────────
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [activeFilename, setActiveFilename] = useState<string>('')
  const [fileConfigs, setFileConfigs] = useState<Record<string, LocationFileConfig>>({})
  const [activeCfg, setActiveCfg] = useState<LocationFileConfig>(deepCopy(DEFAULT_FILE_CFG))

  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [columnInfosCache, setColumnInfosCache] = useState<Record<string, Record<string, ColumnInfo>>>({})

  // Global (not per-file) patient-ID mode
  const [personIdMode, setPersonIdMode] = useState<'column' | 'auto_increment'>('column')

  // Per-file UI state
  const [countryValues, setCountryValues] = useState<string[]>([])
  const [csCountryValues, setCsCountryValues] = useState<string[]>([])
  const [countryMode, setCountryMode] = useState<'column' | 'default'>('column')
  const [csCountryMode, setCsCountryMode] = useState<'column' | 'default'>('column')

  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [initialized, setInitialized] = useState(false)

  const activeFilenameRef = useRef<string>('')
  activeFilenameRef.current = activeFilename

  // ── Derived ────────────────────────────────────────────────────────────
  const activeFile = files.find(f => f.filename === activeFilename) ?? null
  const cols = activeFile?.columns ?? []
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'location'), [project.etl_config])
  const stepUsed = useMemo(() => extractMappedCols(activeCfg), [activeCfg])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || (!crossUsed.has(c) && !stepUsed.has(c)))

  // ── Apply a LocationFileConfig into UI state ───────────────────────────
  const applyFileConfig = (fc: LocationFileConfig, infos: Record<string, ColumnInfo>) => {
    setActiveCfg(deepCopy(fc))

    const cm = fc.country_mode ?? (fc.country_col ? 'column' : 'default')
    setCountryMode(cm)
    if (cm === 'column' && fc.country_col) {
      const fresh = infos[fc.country_col]?.distinct_values ?? []
      setCountryValues(fresh.length > 0 ? fresh : Object.keys(fc.country_concept_id_map ?? {}))
    } else {
      setCountryValues([])
    }

    const csm = fc.cs_country_mode ?? (fc.cs_country_col ? 'column' : 'default')
    setCsCountryMode(csm)
    if (csm === 'column' && fc.cs_country_col) {
      const fresh = infos[fc.cs_country_col]?.distinct_values ?? []
      setCsCountryValues(fresh.length > 0 ? fresh : Object.keys(fc.cs_country_concept_id_map ?? {}))
    } else {
      setCsCountryValues([])
    }
  }

  // ── Initial load ───────────────────────────────────────────────────────
  useEffect(() => {
    if (initialized) return
    setInitialized(true)

    const bootstrap = async () => {
      const existing = await getTableConfig(project.id, 'location').catch(() => null) as
        (LocationConfig & { extra_instructions?: string }) | null

      if (!existing || Object.keys(existing).length === 0) {
        const fn = files[0]?.filename ?? ''
        if (fn) {
          setSelectedFiles([fn])
          setActiveFilename(fn)
          setFileConfigs({ [fn]: deepCopy(DEFAULT_FILE_CFG) })
          const infos = await getColumnValues(project.id, fn).catch(() => ({})) as Record<string, ColumnInfo>
          setColumnInfos(infos)
          setColumnInfosCache({ [fn]: infos })
        }
        return
      }

      setExtraInstructions(existing.extra_instructions ?? '')
      setPersonIdMode(existing.person_id_auto_increment ? 'auto_increment' : 'column')

      let sf: string[]
      let fc: Record<string, LocationFileConfig>

      if (existing.file_configs && existing.source_files?.length) {
        sf = existing.source_files
        fc = existing.file_configs
      } else {
        // Legacy single-file — migrate
        const filename = existing.source_filename ?? files[0]?.filename ?? ''
        const migrated: LocationFileConfig = {
          address_1_col: existing.address_1_col ?? '',
          address_2_col: existing.address_2_col ?? '',
          city_col: existing.city_col ?? '',
          state_col: existing.state_col ?? '',
          zip_col: existing.zip_col ?? '',
          county_col: existing.county_col ?? '',
          county_source_value: existing.county_source_value ?? '',
          country_col: existing.country_col ?? '',
          country_source_value: existing.country_source_value ?? '',
          latitude_col: existing.latitude_col ?? '',
          longitude_col: existing.longitude_col ?? '',
          cs_address_1_col: existing.cs_address_1_col ?? '',
          cs_address_2_col: existing.cs_address_2_col ?? '',
          cs_city_col: existing.cs_city_col ?? '',
          cs_state_col: existing.cs_state_col ?? '',
          cs_zip_col: existing.cs_zip_col ?? '',
          cs_county_col: existing.cs_county_col ?? '',
          cs_county_source_value: existing.cs_county_source_value ?? '',
          cs_country_col: existing.cs_country_col ?? '',
          cs_country_source_value: existing.cs_country_source_value ?? '',
          cs_latitude_col: existing.cs_latitude_col ?? '',
          cs_longitude_col: existing.cs_longitude_col ?? '',
          country_concept_id_map: existing.country_concept_id_map ?? {},
          country_concept_id_default: existing.country_concept_id_default ?? 0,
          cs_country_concept_id_map: existing.cs_country_concept_id_map ?? {},
          cs_country_concept_id_default: existing.cs_country_concept_id_default ?? 0,
          country_mode: existing.country_mode,
          cs_country_mode: existing.cs_country_mode,
        }
        sf = filename ? [filename] : (files[0]?.filename ? [files[0].filename] : [])
        fc = sf[0] ? { [sf[0]]: migrated } : {}
      }

      setSelectedFiles(sf)
      setFileConfigs(fc)

      if (sf.length > 0) {
        const firstFile = sf[0]
        setActiveFilename(firstFile)
        const infos = await getColumnValues(project.id, firstFile).catch(() => ({})) as Record<string, ColumnInfo>
        setColumnInfos(infos)
        setColumnInfosCache({ [firstFile]: infos })
        if (fc[firstFile]) applyFileConfig(fc[firstFile], infos)
      }
    }

    bootstrap()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id])

  // ── Switch between file tabs ───────────────────────────────────────────
  const switchActiveFile = (newFilename: string) => {
    if (newFilename === activeFilename) return

    const saved: LocationFileConfig = { ...activeCfg, country_mode: countryMode, cs_country_mode: csCountryMode }
    const updatedConfigs = { ...fileConfigs, [activeFilename]: saved }
    setFileConfigs(updatedConfigs)
    setActiveFilename(newFilename)

    const newCfg = updatedConfigs[newFilename] ?? deepCopy(DEFAULT_FILE_CFG)
    const cached = columnInfosCache[newFilename]

    if (cached) {
      setColumnInfos(cached)
      applyFileConfig(newCfg, cached)
    } else {
      setColumnInfos({})
      applyFileConfig(newCfg, {})
      getColumnValues(project.id, newFilename).then(infos => {
        setColumnInfosCache(prev => ({ ...prev, [newFilename]: infos }))
        if (activeFilenameRef.current !== newFilename) return
        setColumnInfos(infos)
        if (countryMode === 'column' && newCfg.country_col) {
          const fresh = infos[newCfg.country_col]?.distinct_values ?? []
          setCountryValues(fresh.length > 0 ? fresh : Object.keys(newCfg.country_concept_id_map ?? {}))
        }
        if (csCountryMode === 'column' && newCfg.cs_country_col) {
          const fresh = infos[newCfg.cs_country_col]?.distinct_values ?? []
          setCsCountryValues(fresh.length > 0 ? fresh : Object.keys(newCfg.cs_country_concept_id_map ?? {}))
        }
      }).catch(() => {})
    }
  }

  // ── Toggle file selection ──────────────────────────────────────────────
  const toggleFile = (filename: string, checked: boolean) => {
    if (checked) {
      setSelectedFiles(prev => prev.includes(filename) ? prev : [...prev, filename])
      setFileConfigs(prev => prev[filename] ? prev : { ...prev, [filename]: deepCopy(DEFAULT_FILE_CFG) })
      if (!activeFilename) {
        setActiveFilename(filename)
        const infos = columnInfosCache[filename] ?? {}
        applyFileConfig(fileConfigs[filename] ?? deepCopy(DEFAULT_FILE_CFG), infos)
        if (!columnInfosCache[filename]) {
          getColumnValues(project.id, filename).then(infos => {
            setColumnInfosCache(prev => ({ ...prev, [filename]: infos }))
            if (activeFilenameRef.current === filename) setColumnInfos(infos)
          }).catch(() => {})
        }
      }
    } else {
      setSelectedFiles(prev => {
        const next = prev.filter(f => f !== filename)
        if (filename === activeFilename && next.length > 0) {
          const fallback = next[0]
          const cfg = fileConfigs[fallback] ?? deepCopy(DEFAULT_FILE_CFG)
          const infos = columnInfosCache[fallback] ?? {}
          setActiveFilename(fallback)
          setColumnInfos(infos)
          applyFileConfig(cfg, infos)
        } else if (next.length === 0) {
          setActiveFilename('')
          setColumnInfos({})
          applyFileConfig(deepCopy(DEFAULT_FILE_CFG), {})
        }
        return next
      })
    }
  }

  // ── Field setters ──────────────────────────────────────────────────────
  const set = (field: keyof LocationFileConfig) => (v: string) =>
    setActiveCfg(prev => ({ ...prev, [field]: v }))

  const handleCountryColChange = (col: string) => {
    setActiveCfg(prev => ({ ...prev, country_col: col, country_concept_id_map: {} }))
    setCountryValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  const handleCsCountryColChange = (col: string) => {
    setActiveCfg(prev => ({ ...prev, cs_country_col: col, cs_country_concept_id_map: {} }))
    setCsCountryValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  const switchCountryMode = (mode: 'column' | 'default') => {
    setCountryMode(mode)
    if (mode === 'default') {
      setActiveCfg(prev => ({ ...prev, country_col: '', country_concept_id_map: {} }))
      setCountryValues([])
    } else {
      setActiveCfg(prev => ({ ...prev, country_concept_id_default: 0, country_source_value: '' }))
    }
  }

  const switchCsCountryMode = (mode: 'column' | 'default') => {
    setCsCountryMode(mode)
    if (mode === 'default') {
      setActiveCfg(prev => ({ ...prev, cs_country_col: '', cs_country_concept_id_map: {} }))
      setCsCountryValues([])
    } else {
      setActiveCfg(prev => ({ ...prev, cs_country_concept_id_default: 0, cs_country_source_value: '' }))
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

  // ── Save ───────────────────────────────────────────────────────────────
  const saveConfig = async () => {
    const currentCfg: LocationFileConfig = { ...activeCfg, country_mode: countryMode, cs_country_mode: csCountryMode }
    const allFileConfigs = activeFilename ? { ...fileConfigs, [activeFilename]: currentCfg } : fileConfigs

    const payload: LocationConfig = {
      enabled: true,
      source_files: selectedFiles,
      file_configs: allFileConfigs,
      person_id_auto_increment: personIdMode === 'auto_increment',
      extra_instructions: extraInstructions,
    }
    const p = await updateTableConfig(project.id, 'location', payload)
    onUpdate(p)
    setFileConfigs(allFileConfigs)
  }

  const { prev, next } = getAdjacentSlugs(project, 'location')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

  const PERSON_ADDR_FIELDS: (keyof LocationFileConfig)[] = [
    'address_1_col', 'address_2_col', 'city_col', 'state_col', 'zip_col', 'county_col', 'country_source_value',
  ]
  const CS_ADDR_FIELDS: (keyof LocationFileConfig)[] = [
    'cs_address_1_col', 'cs_address_2_col', 'cs_city_col', 'cs_state_col', 'cs_zip_col', 'cs_county_col', 'cs_country_source_value',
  ]

  const showMappings = !!activeFilename

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
        <div>
          <h2 className="text-xl font-bold text-primary">Location Mapping</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Map source columns to the OMOP LOCATION table. Locations are shared between Persons and
            Care Sites — define which columns hold each group's address below. All unique addresses
            are combined and deduplicated into a single <code className="bg-muted px-1 rounded">location.csv</code>.
          </p>
        </div>

        {isMultiFile && (
          <div className="rounded-lg border border-border bg-secondary/40 px-4 py-3 text-sm text-muted-foreground space-y-1">
            <p className="font-medium text-foreground">Building the location table from multiple files</p>
            <ul className="list-disc list-inside text-xs space-y-0.5">
              <li>Select which files should contribute location records using the checkboxes below.</li>
              <li>The <span className="font-medium text-foreground">Primary</span> file is processed first — all its records are loaded as-is.</li>
              <li>Each subsequent file is merged in: new locations are appended; existing ones (same address key) are skipped.</li>
              <li>If the same patient appears with <span className="font-medium text-foreground">conflicting values</span> across files, a warning is printed and that patient is dropped.</li>
            </ul>
          </div>
        )}

        {/* ── File selection ── */}
        {isMultiFile && (
          <Card className="flex flex-col gap-4 p-6">
            <div>
              <h3 className="font-semibold text-foreground">Source Files</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Select which files contribute to the location table. Files are processed in order —
                the first selected is the primary source; subsequent files are merged in.
              </p>
            </div>
            <div className="flex flex-col gap-2">
              {files.map(f => {
                const isSelected = selectedFiles.includes(f.filename)
                const orderIdx = selectedFiles.indexOf(f.filename)
                return (
                  <label key={f.filename} className="flex items-center gap-3 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={e => toggleFile(f.filename, e.target.checked)}
                      className="w-4 h-4 accent-primary rounded"
                    />
                    <FileText className="size-4 flex-shrink-0 text-muted-foreground" />
                    <span className="text-sm text-foreground">{f.filename}</span>
                    {isSelected && orderIdx === 0 && (
                      <span className="text-xs font-semibold bg-primary text-primary-foreground px-2 py-0.5 rounded-full">Primary</span>
                    )}
                    {isSelected && orderIdx > 0 && (
                      <span className="text-xs text-muted-foreground">Merge #{orderIdx}</span>
                    )}
                  </label>
                )
              })}
            </div>
            {selectedFiles.length === 0 && (
              <p className="text-sm text-amber-600 dark:text-amber-400">Select at least one file to configure mappings.</p>
            )}
          </Card>
        )}

        {/* ── File config tabs ── */}
        {selectedFiles.length > 1 && (
          <div className="flex flex-col gap-1.5">
            <p className="text-xs text-muted-foreground font-medium">Configure mappings per file</p>
            <div className="flex flex-wrap gap-2">
              {selectedFiles.map(filename => (
                <button
                  key={filename}
                  onClick={() => switchActiveFile(filename)}
                  className={
                    filename === activeFilename
                      ? 'flex items-center gap-1.5 rounded-full border border-primary bg-primary/10 px-3 py-1 text-xs font-semibold text-primary transition-colors'
                      : 'flex items-center gap-1.5 rounded-full border border-border bg-card px-3 py-1 text-xs text-muted-foreground hover:border-primary/50 hover:text-foreground transition-colors'
                  }
                >
                  <FileText className="size-3 flex-shrink-0" />
                  {filename}
                  {selectedFiles.indexOf(filename) === 0 && (
                    <span className="ml-0.5 opacity-60">(Primary)</span>
                  )}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* ── Mapping cards ── */}
        {showMappings && (
          <>
            {/* ── PATIENT IDENTIFIER (multi-file only) ── */}
            {selectedFiles.length > 1 && (
              <Card className="flex flex-col gap-4 p-6">
                <div>
                  <h3 className="font-semibold text-foreground">
                    Patient Identifier <span className="text-destructive">*</span>
                  </h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    Used to detect whether the same patient appears across multiple source files.
                    If the address fields agree the records are merged; if they conflict the location
                    row is dropped and a warning is printed.
                  </p>
                </div>

                <label className="flex items-center gap-2 cursor-pointer select-none w-fit">
                  <input
                    type="checkbox"
                    checked={personIdMode === 'auto_increment'}
                    onChange={e => setPersonIdMode(e.target.checked ? 'auto_increment' : 'column')}
                    className="w-4 h-4 accent-primary rounded"
                  />
                  <span className="text-sm font-medium text-foreground">Auto-increment (row index)</span>
                </label>

                {personIdMode === 'column' ? (
                  <div className="flex flex-col gap-1.5">
                    <FieldMapper
                      label="person_id"
                      sourceColumns={availCols(activeCfg.person_id_col)}
                      value={activeCfg.person_id_col}
                      onChange={set('person_id_col')}
                      hint="Must be mapped for every selected file before code can be generated."
                    />
                    {!activeCfg.person_id_col && (
                      <p className="text-xs text-destructive">
                        This column is required. Map it for each selected file before generating code.
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="rounded-md border border-border bg-secondary/40 p-3 text-sm text-muted-foreground">
                    Row index (0, 1, 2, …) is used as the patient identifier across all files.
                    Files must be <span className="font-medium text-foreground">row-aligned</span> —
                    the patient at row&nbsp;N in each file must refer to the same person.
                  </div>
                )}
              </Card>
            )}

            {/* ── PERSON LOCATION ── */}
            <div className="flex flex-col gap-4 rounded-lg border border-border bg-secondary/70 p-4">
              <p className="text-base font-bold uppercase tracking-wide text-muted-foreground">Person Location</p>

              <Card className="flex flex-col gap-5 p-6">
                <h3 className="font-semibold text-foreground">Address Lines</h3>
                <FieldMapper
                  label="address_1"
                  sourceColumns={availCols(activeCfg.address_1_col)}
                  value={activeCfg.address_1_col}
                  onChange={set('address_1_col')}
                  hint="First line of the address (max 50 chars)."
                />
                <FieldMapper
                  label="address_2"
                  sourceColumns={availCols(activeCfg.address_2_col)}
                  value={activeCfg.address_2_col}
                  onChange={set('address_2_col')}
                  hint="Second line of the address (max 50 chars)."
                />
              </Card>

              <Card className="flex flex-col gap-5 p-6">
                <h3 className="font-semibold text-foreground">City</h3>
                <FieldMapper
                  label="city"
                  sourceColumns={availCols(activeCfg.city_col)}
                  value={activeCfg.city_col}
                  onChange={set('city_col')}
                />
              </Card>

              <Card className="flex flex-col gap-5 p-6">
                <h3 className="font-semibold text-foreground">State, ZIP & County</h3>
                <FieldMapper
                  label="state"
                  sourceColumns={availCols(activeCfg.state_col)}
                  value={activeCfg.state_col}
                  onChange={set('state_col')}
                  hint="2-character state/province/district abbreviation."
                />
                <FieldMapper
                  label="zip"
                  sourceColumns={availCols(activeCfg.zip_col)}
                  value={activeCfg.zip_col}
                  onChange={set('zip_col')}
                  hint="Zip / postal code stored as a string (up to 9 chars). Leading zeros are preserved."
                />
                <FieldMapper
                  label="county"
                  sourceColumns={availCols(activeCfg.county_col)}
                  value={activeCfg.county_col}
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
                      sourceColumns={availCols(activeCfg.country_col)}
                      value={activeCfg.country_col}
                      onChange={handleCountryColChange}
                    />
                    {activeCfg.country_col && (
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
                          sourceValues={countryValues.length > 0 ? countryValues : Object.keys(activeCfg.country_concept_id_map)}
                          mapping={activeCfg.country_concept_id_map}
                          onChange={m => setActiveCfg(prev => ({ ...prev, country_concept_id_map: m }))}
                          expectedDomain="Geography"
                        />
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <Label>Default country_concept_id</Label>
                      <SingleConceptInput
                        value={activeCfg.country_concept_id_default || null}
                        onChange={v => setActiveCfg(prev => ({ ...prev, country_concept_id_default: v ?? 0 }))}
                        onConceptName={name => { if (name) setActiveCfg(prev => ({ ...prev, country_source_value: name })) }}
                        placeholder="e.g. 4330442"
                        expectedDomain="Geography"
                      />
                      <p className="text-xs text-muted-foreground mt-1">Applied to all person rows (0 = unknown).</p>
                    </div>
                    <div>
                      <Label>Default country_source_value</Label>
                      <Input
                        type="text"
                        value={activeCfg.country_source_value}
                        onChange={e => setActiveCfg(prev => ({ ...prev, country_source_value: e.target.value }))}
                        placeholder="e.g. United States"
                        className="mt-1"
                      />
                      <p className="text-xs text-muted-foreground mt-1">Applied to all person rows (max 80 chars).</p>
                    </div>
                  </div>
                )}
              </Card>

              <Card className="flex flex-col gap-3 p-6">
                <h3 className="font-semibold text-foreground">Person Location Source Value</h3>
                <AutoComputedBadge cfg={activeCfg} fields={PERSON_ADDR_FIELDS} />
              </Card>

              <Card className="flex flex-col gap-5 p-6">
                <h3 className="font-semibold text-foreground">Coordinates</h3>
                <FieldMapper
                  label="latitude"
                  sourceColumns={availCols(activeCfg.latitude_col)}
                  value={activeCfg.latitude_col}
                  onChange={set('latitude_col')}
                  hint="Decimal latitude — must be between −90 and 90."
                />
                <FieldMapper
                  label="longitude"
                  sourceColumns={availCols(activeCfg.longitude_col)}
                  value={activeCfg.longitude_col}
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
                  sourceColumns={availCols(activeCfg.cs_address_1_col)}
                  value={activeCfg.cs_address_1_col}
                  onChange={set('cs_address_1_col')}
                  hint="First line of the care site address (max 50 chars)."
                />
                <FieldMapper
                  label="address_2"
                  sourceColumns={availCols(activeCfg.cs_address_2_col)}
                  value={activeCfg.cs_address_2_col}
                  onChange={set('cs_address_2_col')}
                  hint="Second line of the care site address (max 50 chars)."
                />
              </Card>

              <Card className="flex flex-col gap-5 p-6">
                <h3 className="font-semibold text-foreground">City</h3>
                <FieldMapper
                  label="city"
                  sourceColumns={availCols(activeCfg.cs_city_col)}
                  value={activeCfg.cs_city_col}
                  onChange={set('cs_city_col')}
                />
              </Card>

              <Card className="flex flex-col gap-5 p-6">
                <h3 className="font-semibold text-foreground">State, ZIP & County</h3>
                <FieldMapper
                  label="state"
                  sourceColumns={availCols(activeCfg.cs_state_col)}
                  value={activeCfg.cs_state_col}
                  onChange={set('cs_state_col')}
                  hint="2-character state/province/district abbreviation."
                />
                <FieldMapper
                  label="zip"
                  sourceColumns={availCols(activeCfg.cs_zip_col)}
                  value={activeCfg.cs_zip_col}
                  onChange={set('cs_zip_col')}
                  hint="Zip / postal code stored as a string (up to 9 chars)."
                />
                <FieldMapper
                  label="county"
                  sourceColumns={availCols(activeCfg.cs_county_col)}
                  value={activeCfg.cs_county_col}
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
                      sourceColumns={availCols(activeCfg.cs_country_col)}
                      value={activeCfg.cs_country_col}
                      onChange={handleCsCountryColChange}
                    />
                    {activeCfg.cs_country_col && (
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
                          sourceValues={csCountryValues.length > 0 ? csCountryValues : Object.keys(activeCfg.cs_country_concept_id_map)}
                          mapping={activeCfg.cs_country_concept_id_map}
                          onChange={m => setActiveCfg(prev => ({ ...prev, cs_country_concept_id_map: m }))}
                          expectedDomain="Geography"
                        />
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <Label>Default country_concept_id</Label>
                      <SingleConceptInput
                        value={activeCfg.cs_country_concept_id_default || null}
                        onChange={v => setActiveCfg(prev => ({ ...prev, cs_country_concept_id_default: v ?? 0 }))}
                        onConceptName={name => { if (name) setActiveCfg(prev => ({ ...prev, cs_country_source_value: name })) }}
                        placeholder="e.g. 4330442"
                        expectedDomain="Geography"
                      />
                      <p className="text-xs text-muted-foreground mt-1">Applied to all care site rows (0 = unknown).</p>
                    </div>
                    <div>
                      <Label>Default country_source_value</Label>
                      <Input
                        type="text"
                        value={activeCfg.cs_country_source_value}
                        onChange={e => setActiveCfg(prev => ({ ...prev, cs_country_source_value: e.target.value }))}
                        placeholder="e.g. United States"
                        className="mt-1"
                      />
                      <p className="text-xs text-muted-foreground mt-1">Applied to all care site rows (max 80 chars).</p>
                    </div>
                  </div>
                )}
              </Card>

              <Card className="flex flex-col gap-3 p-6">
                <h3 className="font-semibold text-foreground">Care Site Location Source Value</h3>
                <AutoComputedBadge cfg={activeCfg} fields={CS_ADDR_FIELDS} />
              </Card>

              <Card className="flex flex-col gap-5 p-6">
                <h3 className="font-semibold text-foreground">Coordinates</h3>
                <FieldMapper
                  label="latitude"
                  sourceColumns={availCols(activeCfg.cs_latitude_col)}
                  value={activeCfg.cs_latitude_col}
                  onChange={set('cs_latitude_col')}
                  hint="Decimal latitude — must be between −90 and 90."
                />
                <FieldMapper
                  label="longitude"
                  sourceColumns={availCols(activeCfg.cs_longitude_col)}
                  value={activeCfg.cs_longitude_col}
                  onChange={set('cs_longitude_col')}
                  hint="Decimal longitude — must be between −180 and 180."
                />
              </Card>
            </div>
          </>
        )}

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
