import { useState, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import { extractMappedCols, getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, CareSiteConfig, CareSiteFileConfig, LocationConfig, SourceFile } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Card } from '@/components/ui/card'
import { FileText, Info } from 'lucide-react'

interface ColumnInfo { distinct_values: string[] }

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULT_FILE_CFG: CareSiteFileConfig = {
  person_id_col: '',
  care_site_name_col: '',
  place_of_service_col: '',
  place_of_service_value_map: {},
}

function deepCopy<T>(v: T): T {
  return JSON.parse(JSON.stringify(v))
}

export default function CareSiteStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const files: SourceFile[] = project.source_files ?? []
  const isMultiFile = files.length > 1

  // ── Core state ─────────────────────────────────────────────────────────
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [activeFilename, setActiveFilename] = useState<string>('')
  const [fileConfigs, setFileConfigs] = useState<Record<string, CareSiteFileConfig>>({})
  const [activeCfg, setActiveCfg] = useState<CareSiteFileConfig>(deepCopy(DEFAULT_FILE_CFG))

  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [columnInfosCache, setColumnInfosCache] = useState<Record<string, Record<string, ColumnInfo>>>({})
  const [posValues, setPosValues] = useState<string[]>([])

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
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'care_site'), [project.etl_config])
  const stepUsed = useMemo(() => extractMappedCols(activeCfg), [activeCfg])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || (!crossUsed.has(c) && !stepUsed.has(c)))

  // ── Location-controlled patient identifier ─────────────────────────────
  const locCfg = (project.etl_config?.location ?? {}) as LocationConfig
  const locAutoIncrement = locCfg.person_id_auto_increment ?? false
  const locPidCol = !locAutoIncrement ? (locCfg.file_configs?.[activeFilename]?.person_id_col ?? '') : ''
  const pidLockedFromLocation = locAutoIncrement || !!locPidCol

  // ── Apply a CareSiteFileConfig into UI state ───────────────────────────
  const applyFileConfig = (fc: CareSiteFileConfig, infos: Record<string, ColumnInfo>) => {
    setActiveCfg(deepCopy(fc))
    if (fc.place_of_service_col) {
      const fresh = infos[fc.place_of_service_col]?.distinct_values ?? []
      setPosValues(fresh.length > 0 ? fresh : Object.keys(fc.place_of_service_value_map ?? {}))
    } else {
      setPosValues([])
    }
  }

  // ── Initial load ───────────────────────────────────────────────────────
  useEffect(() => {
    if (initialized) return
    setInitialized(true)

    const bootstrap = async () => {
      const existing = await getTableConfig(project.id, 'care_site').catch(() => null) as
        (CareSiteConfig & { extra_instructions?: string }) | null

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
      let fc: Record<string, CareSiteFileConfig>

      if (existing.file_configs && existing.source_files?.length) {
        sf = existing.source_files
        fc = existing.file_configs
      } else {
        // Legacy single-file — migrate
        const filename = existing.source_filename ?? files[0]?.filename ?? ''
        const migrated: CareSiteFileConfig = {
          person_id_col: '',
          care_site_name_col: existing.care_site_name_col ?? '',
          place_of_service_col: existing.place_of_service_col ?? '',
          place_of_service_value_map: existing.place_of_service_value_map ?? {},
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

    const updatedConfigs = { ...fileConfigs, [activeFilename]: activeCfg }
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
        if (newCfg.place_of_service_col) {
          const fresh = infos[newCfg.place_of_service_col]?.distinct_values ?? []
          setPosValues(fresh.length > 0 ? fresh : Object.keys(newCfg.place_of_service_value_map ?? {}))
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
  const set = (field: keyof CareSiteFileConfig) => (v: string) =>
    setActiveCfg(prev => ({ ...prev, [field]: v }))

  const handlePosColChange = (col: string) => {
    setActiveCfg(prev => ({ ...prev, place_of_service_col: col, place_of_service_value_map: {} }))
    setPosValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  // ── Save ───────────────────────────────────────────────────────────────
  const saveConfig = async () => {
    const snapped = activeFilename ? { ...fileConfigs, [activeFilename]: activeCfg } : fileConfigs

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

    const payload: CareSiteConfig = {
      enabled: true,
      source_files: selectedFiles,
      file_configs: allFileConfigs,
      person_id_auto_increment: locAutoIncSave || personIdMode === 'auto_increment',
      extra_instructions: extraInstructions,
    }
    const p = await updateTableConfig(project.id, 'care_site', payload)
    onUpdate(p)
    setFileConfigs(allFileConfigs)
  }

  const { prev, next } = getAdjacentSlugs(project, 'care-site')

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
      currentSlug="care-site"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel="Next →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-primary">Care Site Mapping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Map source columns to the OMOP CARE_SITE table. A Care Site is a unique combination
            of a <strong>location</strong> and the <strong>nature of the site</strong> — such as its place of service,
            name, or another characteristic. It represents institutional (physical or organizational) units
            where healthcare is delivered: offices, wards, hospitals, clinics, etc. Individual provider
            information belongs in the PROVIDER table, not here. If the source only provides generic
            information (e.g. Place of Service), pooled Care Site records are acceptable.
          </p>
        </div>

        {isMultiFile && (
          <div className="rounded-lg border border-border bg-secondary/40 px-4 py-3 text-sm text-muted-foreground space-y-1">
            <p className="font-medium text-foreground">Building the care site table from multiple files</p>
            <ul className="list-disc list-inside text-xs space-y-0.5">
              <li>Select which files should contribute care site records using the checkboxes below.</li>
              <li>The <span className="font-medium text-foreground">Primary</span> file is processed first — all its records are loaded as-is.</li>
              <li>Each subsequent file is merged in: new care sites are appended; existing ones (same source value key) are skipped.</li>
            </ul>
          </div>
        )}

        {/* ── File selection ── */}
        {isMultiFile && (
          <Card className="flex flex-col gap-4 p-6">
            <div>
              <h3 className="font-semibold text-foreground">Source Files</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Select which files contribute to the care site table. Files are processed in order —
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
                    If the care site agrees the record is skipped; if it conflicts the care site
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

            {/* Care Site Name */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">Care Site Name</h3>
              <FieldMapper
                label="care_site_name"
                sourceColumns={availCols(activeCfg.care_site_name_col)}
                value={activeCfg.care_site_name_col}
                onChange={col => setActiveCfg(prev => ({ ...prev, care_site_name_col: col }))}
                hint="The name of the care site as it appears in the source data (max 255 chars)."
              />
            </Card>

            {/* Care Site Source Value */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">Care Site Source Value</h3>
              <div className="flex flex-col gap-1">
                <div className="flex flex-col gap-1 rounded-lg border border-border bg-secondary/60 p-3">
                  <p className="text-sm font-medium text-secondary-foreground">Auto-computed — no mapping required</p>
                  <p className="text-sm text-muted-foreground">
                    Constructed as{' '}
                    <code className="rounded bg-accent px-1 text-xs">location_id + " | " + care_site_name</code>.
                    The location_id is resolved from the care site address columns mapped in the{' '}
                    <strong>Location step</strong>.
                  </p>
                </div>
              </div>
            </Card>

            {/* Place of Service */}
            <Card className="flex flex-col gap-5 p-6">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-foreground">Place of Service</h3>
                <a
                  href="https://athena.ohdsi.org/search-terms/terms?domain=Visit&standardConcept=Standard&page=2&pageSize=15&query="
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-primary underline underline-offset-2 hover:opacity-80"
                >
                  accepted concepts
                </a>
              </div>
              <p className="text-sm text-muted-foreground">
                Select the source column that represents the Place of Service. All distinct values from
                that column will appear below — assign an OMOP concept ID to each one.
                The raw value will be stored in <code className="rounded bg-accent px-1 text-xs">place_of_service_source_value</code>;
                the mapped concept ID will populate <code className="rounded bg-accent px-1 text-xs">place_of_service_concept_id</code>.
              </p>
              <FieldMapper
                label="place_of_service_col"
                sourceColumns={availCols(activeCfg.place_of_service_col)}
                value={activeCfg.place_of_service_col}
                onChange={handlePosColChange}
                hint="Source column whose values represent the place of service."
              />
              {posValues.length > 0 && (
                <ValueConceptMapper
                  label="place_of_service_value_map"
                  sourceValues={posValues}
                  mapping={activeCfg.place_of_service_value_map ?? {}}
                  onChange={map => setActiveCfg(prev => ({ ...prev, place_of_service_value_map: map }))}
                  hint="Assign an OMOP concept ID to each distinct place-of-service value."
                  expectedDomain="Visit"
                />
              )}
            </Card>
          </>
        )}

        <ExtraInstructions
          tableName="care_site"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        <ScriptGenerator
          project={project}
          table="care_site"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
          deterministic
        />
      </div>
    </WizardLayout>
  )
}
