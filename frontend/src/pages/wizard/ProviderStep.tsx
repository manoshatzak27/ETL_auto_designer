import { useState, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import { extractMappedCols, getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, ProviderConfig, ProviderFileConfig, LocationConfig, SourceFile } from '../../types'
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
import { FileText, Info } from 'lucide-react'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

interface ColumnInfo { distinct_values: string[] }

const DEFAULT_FILE_CFG: ProviderFileConfig = {
  person_id_col: '',
  provider_name_col: '',
  npi_col: '',
  dea_col: '',
  specialty_concept_id: null,
  specialty_concept_value_map: {},
  prefix_specialty: '',
  prefix_specialty_concept_id: null,
  year_of_birth_col: '',
  gender_concept_value_map: {},
  gender_concept_id_default: 0,
  specialty_source_value_col: '',
  gender_source_value_col: '',
  specialty_mode: 'column',
  gender_mode: 'column',
}

function deepCopy<T>(v: T): T {
  return JSON.parse(JSON.stringify(v))
}

export default function ProviderStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const files: SourceFile[] = project.source_files ?? []
  const isMultiFile = files.length > 1

  // ── Core state ─────────────────────────────────────────────────────────
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [activeFilename, setActiveFilename] = useState<string>('')
  const [fileConfigs, setFileConfigs] = useState<Record<string, ProviderFileConfig>>({})
  const [activeCfg, setActiveCfg] = useState<ProviderFileConfig>(deepCopy(DEFAULT_FILE_CFG))

  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [columnInfosCache, setColumnInfosCache] = useState<Record<string, Record<string, ColumnInfo>>>({})

  // Per-file UI state
  const [specialtyMode, setSpecialtyMode] = useState<'column' | 'prefix'>('column')
  const [genderMode, setGenderMode] = useState<'column' | 'default'>('column')

  // Global (not per-file) patient-ID mode
  const [personIdMode, setPersonIdMode] = useState<'column' | 'auto_increment'>('column')

  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [initialized, setInitialized] = useState(false)

  const activeFilenameRef = useRef<string>('')
  activeFilenameRef.current = activeFilename

  // ── Derived ────────────────────────────────────────────────────────────
  const activeFile = files.find(f => f.filename === activeFilename) ?? null
  const cols = activeFile?.columns ?? []
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'provider'), [project.etl_config])
  const stepUsed = useMemo(() => extractMappedCols(activeCfg), [activeCfg])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || (!crossUsed.has(c) && !stepUsed.has(c)))

  // ── Location-controlled patient identifier ─────────────────────────────
  const locCfg = (project.etl_config?.location ?? {}) as LocationConfig
  const locAutoIncrement = locCfg.person_id_auto_increment ?? false
  const locPidCol = !locAutoIncrement ? (locCfg.file_configs?.[activeFilename]?.person_id_col ?? '') : ''
  const pidLockedFromLocation = locAutoIncrement || !!locPidCol

  const distinctVals = (col: string): string[] => columnInfos[col]?.distinct_values ?? []

  // ── Apply a ProviderFileConfig into UI state ───────────────────────────
  const applyFileConfig = (fc: ProviderFileConfig) => {
    setActiveCfg(deepCopy(fc))
    if (fc.specialty_mode) setSpecialtyMode(fc.specialty_mode)
    else if (fc.specialty_source_value_col) setSpecialtyMode('column')
    else if (fc.prefix_specialty) setSpecialtyMode('prefix')
    else setSpecialtyMode('column')

    if (fc.gender_mode) setGenderMode(fc.gender_mode)
    else if (fc.gender_source_value_col) setGenderMode('column')
    else if (fc.gender_concept_id_default) setGenderMode('default')
    else setGenderMode('column')
  }

  // ── Initial load ───────────────────────────────────────────────────────
  useEffect(() => {
    if (initialized) return
    setInitialized(true)

    const bootstrap = async () => {
      const existing = await getTableConfig(project.id, 'provider').catch(() => null) as
        (ProviderConfig & { extra_instructions?: string }) | null

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
      let fc: Record<string, ProviderFileConfig>

      if (existing.file_configs && existing.source_files?.length) {
        sf = existing.source_files
        fc = existing.file_configs
      } else {
        // Legacy single-file — migrate
        const filename = existing.source_filename ?? files[0]?.filename ?? ''
        const migrated: ProviderFileConfig = {
          person_id_col: '',
          provider_name_col: existing.provider_name_col ?? '',
          npi_col: existing.npi_col ?? '',
          dea_col: existing.dea_col ?? '',
          specialty_concept_id: existing.specialty_concept_id ?? null,
          specialty_concept_value_map: existing.specialty_concept_value_map ?? {},
          prefix_specialty: existing.prefix_specialty ?? '',
          prefix_specialty_concept_id: existing.prefix_specialty_concept_id ?? null,
          year_of_birth_col: existing.year_of_birth_col ?? '',
          gender_concept_value_map: existing.gender_concept_value_map ?? {},
          gender_concept_id_default: existing.gender_concept_id_default ?? 0,
          specialty_source_value_col: existing.specialty_source_value_col ?? '',
          gender_source_value_col: existing.gender_source_value_col ?? '',
          specialty_mode: existing.specialty_mode,
          gender_mode: existing.gender_mode,
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
        if (fc[firstFile]) applyFileConfig(fc[firstFile])
      }
    }

    bootstrap()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project.id])

  // ── Switch between file tabs ───────────────────────────────────────────
  const switchActiveFile = (newFilename: string) => {
    if (newFilename === activeFilename) return

    const saved: ProviderFileConfig = { ...activeCfg, specialty_mode: specialtyMode, gender_mode: genderMode }
    const updatedConfigs = { ...fileConfigs, [activeFilename]: saved }
    setFileConfigs(updatedConfigs)
    setActiveFilename(newFilename)

    const newCfg = updatedConfigs[newFilename] ?? deepCopy(DEFAULT_FILE_CFG)
    const cached = columnInfosCache[newFilename]

    if (cached) {
      setColumnInfos(cached)
      applyFileConfig(newCfg)
    } else {
      setColumnInfos({})
      applyFileConfig(newCfg)
      getColumnValues(project.id, newFilename).then(infos => {
        setColumnInfosCache(prev => ({ ...prev, [newFilename]: infos }))
        if (activeFilenameRef.current === newFilename) setColumnInfos(infos)
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
        applyFileConfig(fileConfigs[filename] ?? deepCopy(DEFAULT_FILE_CFG))
        if (!columnInfosCache[filename]) {
          getColumnValues(project.id, filename).then(infos => {
            setColumnInfosCache(prev => ({ ...prev, [filename]: infos }))
            if (activeFilenameRef.current === filename) setColumnInfos(infos)
          }).catch(() => {})
        } else {
          setColumnInfos(columnInfosCache[filename])
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
          applyFileConfig(cfg)
        } else if (next.length === 0) {
          setActiveFilename('')
          setColumnInfos({})
          applyFileConfig(deepCopy(DEFAULT_FILE_CFG))
        }
        return next
      })
    }
  }

  // ── Field setters ──────────────────────────────────────────────────────
  const set = (field: keyof ProviderFileConfig) => (v: string) =>
    setActiveCfg(prev => ({ ...prev, [field]: v }))

  const switchGenderMode = (mode: 'column' | 'default') => {
    setGenderMode(mode)
    if (mode === 'default') {
      setActiveCfg(prev => ({ ...prev, gender_source_value_col: '', gender_concept_value_map: {} }))
    } else {
      setActiveCfg(prev => ({ ...prev, gender_concept_id_default: 0 }))
    }
  }

  const switchSpecialtyMode = (mode: 'column' | 'prefix') => {
    setSpecialtyMode(mode)
    if (mode === 'prefix') {
      setActiveCfg(prev => ({ ...prev, specialty_source_value_col: '', specialty_concept_value_map: {} }))
    } else {
      setActiveCfg(prev => ({ ...prev, prefix_specialty: '', prefix_specialty_concept_id: null }))
    }
  }

  // ── Save ───────────────────────────────────────────────────────────────
  const saveConfig = async () => {
    const currentCfg: ProviderFileConfig = { ...activeCfg, specialty_mode: specialtyMode, gender_mode: genderMode }
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { gender_concept_id: _gid, specialty_concept_id: _sid, ...cleanCfg } =
      currentCfg as ProviderFileConfig & { gender_concept_id?: unknown; specialty_concept_id?: unknown }
    const snapped = activeFilename
      ? { ...fileConfigs, [activeFilename]: cleanCfg as ProviderFileConfig }
      : fileConfigs

    const locCfgSave = (project.etl_config?.location ?? {}) as LocationConfig
    const locAutoIncSave = locCfgSave.person_id_auto_increment ?? false
    const allFileConfigs = Object.fromEntries(
      Object.entries(snapped).map(([fn, cfg]) => {
        if (locAutoIncSave) {
          return [fn, { ...cfg, person_id_col: '' }]
        }
        const filePidCol = locCfgSave.file_configs?.[fn]?.person_id_col ?? ''
        if (filePidCol) {
          return [fn, { ...cfg, person_id_col: filePidCol }]
        }
        return [fn, cfg]
      })
    )

    const payload: ProviderConfig = {
      enabled: true,
      source_files: selectedFiles,
      file_configs: allFileConfigs,
      person_id_auto_increment: locAutoIncSave || personIdMode === 'auto_increment',
      extra_instructions: extraInstructions,
    }
    const p = await updateTableConfig(project.id, 'provider', payload)
    onUpdate(p)
    setFileConfigs(allFileConfigs)
  }

  const { prev, next } = getAdjacentSlugs(project, 'provider')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

  const showMappings = !!activeFilename

  return (
    <WizardLayout
      project={project}
      currentSlug="provider"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel="Next →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-primary">Provider Mapping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Map source columns to the OMOP PROVIDER table. Providers are uniquely identified
            healthcare individuals (physicians, nurses, etc.). If the source only gives specialty
            without individual identifiers, generic pooled provider records are acceptable.
          </p>
        </div>

        {isMultiFile && (
          <div className="rounded-lg border border-border bg-secondary/40 px-4 py-3 text-sm text-muted-foreground space-y-1">
            <p className="font-medium text-foreground">Building the provider table from multiple files</p>
            <ul className="list-disc list-inside text-xs space-y-0.5">
              <li>Select which files should contribute provider records using the checkboxes below.</li>
              <li>The <span className="font-medium text-foreground">Primary</span> file is processed first — all its records are loaded as-is.</li>
              <li>Each subsequent file is merged in: new providers are appended; existing ones (same source value key) are skipped.</li>
            </ul>
          </div>
        )}

        {/* ── File selection ── */}
        {isMultiFile && (
          <Card className="flex flex-col gap-4 p-6">
            <div>
              <h3 className="font-semibold text-foreground">Source Files</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Select which files contribute to the provider table. Files are processed in order —
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
                    Patient Identifier {!pidLockedFromLocation && <span className="text-destructive">*</span>}
                  </h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    Used to detect whether the same patient appears across multiple source files.
                    If the provider agrees the record is skipped; if it conflicts the provider
                    row is dropped and a warning is printed.
                  </p>
                </div>

                {pidLockedFromLocation ? (
                  <>
                    <div className="flex items-start gap-2 rounded-md border border-primary/30 bg-primary/5 px-3 py-2.5">
                      <Info className="size-4 text-primary mt-0.5 flex-shrink-0" />
                      <p className="text-sm text-muted-foreground">
                        The patient identifier is controlled by the{' '}
                        <span className="font-medium text-foreground">Location step</span>.
                        To change it, go back to the Location step.
                      </p>
                    </div>
                    <div className="opacity-50 pointer-events-none flex flex-col gap-4">
                      {locAutoIncrement ? (
                        <label className="flex items-center gap-2 select-none">
                          <input type="checkbox" checked readOnly className="w-4 h-4 accent-primary rounded" />
                          <span className="text-sm font-medium text-foreground">Auto-increment (row index)</span>
                        </label>
                      ) : (
                        <FieldMapper
                          label="person_id"
                          sourceColumns={[locPidCol]}
                          value={locPidCol}
                          onChange={() => {}}
                          hint="Inherited from the Location step."
                        />
                      )}
                    </div>
                  </>
                ) : (
                  <>
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
                  </>
                )}
              </Card>
            )}

            {/* Provider Name */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">Provider Name</h3>
              <FieldMapper
                label="provider_name"
                sourceColumns={availCols(activeCfg.provider_name_col)}
                value={activeCfg.provider_name_col}
                onChange={set('provider_name_col')}
                hint="Name of the provider as it appears in the source (max 255 chars)."
              />
            </Card>

            {/* Provider Source Value */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">Provider Source Value</h3>
              <div className="flex flex-col gap-1">
                <Label>provider_source_value</Label>
                <div className="flex flex-col gap-1 rounded-lg border border-border bg-secondary/60 p-3">
                  <p className="text-sm font-medium text-secondary-foreground">Auto-computed — no mapping required</p>
                  <p className="text-sm text-muted-foreground">
                    Constructed as{' '}
                    <code className="rounded bg-accent px-1 text-xs">care_site_id + " | " + provider_name</code>.
                    Used as the deduplication key for the PROVIDER table.
                  </p>
                </div>
              </div>
            </Card>

            {/* NPI */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">NPI</h3>
              <FieldMapper
                label="npi"
                sourceColumns={availCols(activeCfg.npi_col)}
                value={activeCfg.npi_col}
                onChange={set('npi_col')}
                hint="National Provider Identifier (US). Max 20 chars."
              />
            </Card>

            {/* DEA */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">DEA</h3>
              <FieldMapper
                label="dea"
                sourceColumns={availCols(activeCfg.dea_col)}
                value={activeCfg.dea_col}
                onChange={set('dea_col')}
                hint="DEA identifier for controlled substance prescriptions. Max 20 chars."
              />
            </Card>

            {/* Year of Birth */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">Year of Birth</h3>
              <FieldMapper
                label="year_of_birth"
                sourceColumns={availCols(activeCfg.year_of_birth_col)}
                value={activeCfg.year_of_birth_col}
                onChange={set('year_of_birth_col')}
                hint="Column containing the provider's birth year (integer)."
              />
            </Card>

            {/* Specialty */}
            <Card className="flex flex-col gap-5 p-6">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-foreground">Specialty</h3>
                <a href="http://athena.ohdsi.org/search-terms/terms?domain=Provider&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={() => switchSpecialtyMode('column')}
                  className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${specialtyMode === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}
                >Map a column</button>
                <button
                  onClick={() => switchSpecialtyMode('prefix')}
                  className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${specialtyMode === 'prefix' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}
                >Set default</button>
              </div>

              {specialtyMode === 'column' ? (
                <div className="flex flex-col gap-3">
                  <FieldMapper
                    label="Specialty column"
                    sourceColumns={availCols(activeCfg.specialty_source_value_col)}
                    value={activeCfg.specialty_source_value_col}
                    onChange={v => setActiveCfg(prev => ({
                      ...prev,
                      specialty_source_value_col: v,
                      specialty_concept_value_map: v !== prev.specialty_source_value_col ? {} : prev.specialty_concept_value_map,
                    }))}
                    hint="Values will populate specialty_source_value and be mapped to specialty_concept_id below."
                  />
                  {activeCfg.specialty_source_value_col && (
                    <ValueConceptMapper
                      label="Specialty value → specialty_concept_id"
                      sourceValues={distinctVals(activeCfg.specialty_source_value_col)}
                      mapping={activeCfg.specialty_concept_value_map ?? {}}
                      onChange={m => setActiveCfg(prev => ({ ...prev, specialty_concept_value_map: m }))}
                      hint="Assign an OMOP Provider-domain concept ID to each specialty value."
                      expectedDomain="Provider"
                    />
                  )}
                </div>
              ) : (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-col gap-1">
                    <Label>Specialty</Label>
                    <Input
                      type="text"
                      value={activeCfg.prefix_specialty ?? ''}
                      onChange={e => setActiveCfg(prev => ({ ...prev, prefix_specialty: e.target.value }))}
                      placeholder="e.g. Internal Medicine"
                    />
                  </div>
                  <div className="flex flex-col gap-1">
                    <Label>Specialty concept ID</Label>
                    <SingleConceptInput
                      value={activeCfg.prefix_specialty_concept_id ?? null}
                      onChange={v => setActiveCfg(prev => ({ ...prev, prefix_specialty_concept_id: v }))}
                      placeholder="e.g. 38004477"
                      expectedDomain="Provider"
                    />
                    <p className="text-xs text-muted-foreground">OMOP Provider-domain concept ID for the prefix specialty.</p>
                  </div>
                </div>
              )}
            </Card>

            {/* Gender */}
            <Card className="flex flex-col gap-5 p-6">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-foreground">Gender</h3>
                <a href="http://athena.ohdsi.org/search-terms/terms?domain=Gender&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
              </div>

              <div className="flex gap-2">
                <button
                  onClick={() => switchGenderMode('column')}
                  className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${genderMode === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}
                >Map a column</button>
                <button
                  onClick={() => switchGenderMode('default')}
                  className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${genderMode === 'default' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}
                >Set default</button>
              </div>

              {genderMode === 'column' ? (
                <div className="flex flex-col gap-3">
                  <FieldMapper
                    label="Gender column"
                    sourceColumns={availCols(activeCfg.gender_source_value_col)}
                    value={activeCfg.gender_source_value_col}
                    onChange={v => setActiveCfg(prev => ({
                      ...prev,
                      gender_source_value_col: v,
                      gender_concept_value_map: v !== prev.gender_source_value_col ? {} : prev.gender_concept_value_map,
                    }))}
                    hint="Provider gender as it appears in the source. Values will populate gender_source_value and be mapped to gender_concept_id below."
                  />
                  {activeCfg.gender_source_value_col && (
                    <div className="flex flex-col gap-2">
                      <p className="text-xs text-muted-foreground">Common: 8507 = Male, 8532 = Female, 8551 = Unknown</p>
                      <ValueConceptMapper
                        label="Gender value → gender_concept_id"
                        sourceValues={distinctVals(activeCfg.gender_source_value_col)}
                        mapping={activeCfg.gender_concept_value_map ?? {}}
                        onChange={m => setActiveCfg(prev => ({ ...prev, gender_concept_value_map: m }))}
                        hint="Assign an OMOP Gender-domain concept ID to each gender value."
                        expectedDomain="Gender"
                      />
                    </div>
                  )}
                </div>
              ) : (
                <div>
                  <Label>Default gender_concept_id</Label>
                  <SingleConceptInput
                    value={activeCfg.gender_concept_id_default || null}
                    onChange={v => setActiveCfg(prev => ({ ...prev, gender_concept_id_default: v ?? 0 }))}
                    placeholder="e.g. 8507"
                    expectedDomain="Gender"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">Common: 8507 = Male, 8532 = Female, 8551 = Unknown (0 = unknown).</p>
                </div>
              )}
            </Card>
          </>
        )}

        <ExtraInstructions
          tableName="provider"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        <ScriptGenerator
          project={project}
          table="provider"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
          deterministic
        />
      </div>
    </WizardLayout>
  )
}
