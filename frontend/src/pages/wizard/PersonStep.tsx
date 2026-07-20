import { useState, useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues, detectColumnType } from '../../api/client'
import { extractMappedCols, getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, PersonConfig, PersonFileConfig, RaceEthnicityMapping, SourceFile, LocationConfig } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import SingleConceptInput from '../../components/SingleConceptInput'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { FileText, Info } from 'lucide-react'

interface ColumnInfo { distinct_values: string[] }

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULT_FILE_CFG: PersonFileConfig = {
  mappings: {
    person_id: { source_col: '', transform: 'int_float', auto_increment: false },
    gender_concept_id: { source_col: '', value_map: {}, default: 0 },
    year_of_birth: { source_col: '', date_format: '%Y-%m-%d', transform: 'date_year' },
    month_of_birth: { source_col: '', date_format: '%Y-%m-%d', transform: 'date_month' },
    day_of_birth: { source_col: '', date_format: '%Y-%m-%d', transform: 'date_day' },
    race_concept_id: { source_col: '', value_map: {}, default: 0 },
    ethnicity_concept_id: { source_col: '', value_map: {}, default: 0 },
    location_id: { source_col: '', value_map: {}, default: 0 },
  },
  gender_mode: 'column',
  race_mode: 'column',
  ethnicity_mode: 'column',
}

function deepCopy<T>(v: T): T {
  return JSON.parse(JSON.stringify(v))
}

export default function PersonStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const files: SourceFile[] = project.source_files ?? []
  const isMultiFile = files.length > 1

  // ── Core state ────────────────────────────────────────────────────────
  const [selectedFiles, setSelectedFiles] = useState<string[]>([])
  const [activeFilename, setActiveFilename] = useState<string>('')
  const [fileConfigs, setFileConfigs] = useState<Record<string, PersonFileConfig>>({})
  const [activeCfg, setActiveCfg] = useState<PersonFileConfig>(deepCopy(DEFAULT_FILE_CFG))

  // Column info: per-file cache + the currently-active file's infos
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [columnInfosCache, setColumnInfosCache] = useState<Record<string, Record<string, ColumnInfo>>>({})

  // Per-file UI state (value lists + modes for gender/race/ethnicity)
  const [genderValues, setGenderValues] = useState<string[]>([])
  const [raceValues, setRaceValues] = useState<string[]>([])
  const [ethnicityValues, setEthnicityValues] = useState<string[]>([])
  const [genderMode, setGenderMode] = useState<'column' | 'default'>('column')
  const [raceMode, setRaceMode] = useState<'column' | 'default'>('column')
  const [ethnicityMode, setEthnicityMode] = useState<'column' | 'default'>('column')
  const [detectedTransform, setDetectedTransform] = useState<string | null>(null)

  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [initialized, setInitialized] = useState(false)
  const [pidMissingFiles, setPidMissingFiles] = useState<string[]>([])

  // Ref so async callbacks can check the current active filename
  const activeFilenameRef = useRef<string>('')
  activeFilenameRef.current = activeFilename

  // ── Derived ───────────────────────────────────────────────────────────
  const activeFile = files.find(f => f.filename === activeFilename) ?? null
  const cols = activeFile?.columns ?? []
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'person'), [project.etl_config])
  const stepUsed = useMemo(() => extractMappedCols(activeCfg), [activeCfg])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || (!crossUsed.has(c) && !stepUsed.has(c)))

  // ── Location-step patient identifier lock ────────────────────────────
  const locCfg = (project.etl_config?.location ?? {}) as LocationConfig
  const locAutoIncrement = locCfg.person_id_auto_increment ?? false
  const locPidCol = !locAutoIncrement ? (locCfg.file_configs?.[activeFilename]?.person_id_col ?? '') : ''
  const pidLockedFromLocation = locAutoIncrement || !!locPidCol

  // ── Auto-detect person_id transform ──────────────────────────────────
  const pidCol = activeCfg.mappings.person_id.source_col
  useEffect(() => {
    if (!pidCol || activeCfg.mappings.person_id.auto_increment) {
      setDetectedTransform(null)
      return
    }
    detectColumnType(project.id, pidCol, activeFilename || undefined).then(res => {
      setDetectedTransform(res.transform)
      setActiveCfg(prev => ({
        ...prev,
        mappings: { ...prev.mappings, person_id: { ...prev.mappings.person_id, transform: res.transform } },
      }))
    }).catch(() => setDetectedTransform(null))
  }, [pidCol, project.id, activeCfg.mappings.person_id.auto_increment, activeFilename])

  // ── Apply a PersonFileConfig into the UI state ────────────────────────
  const applyFileConfig = (fc: PersonFileConfig, infos: Record<string, ColumnInfo>) => {
    const m = fc.mappings
    const getVals = (col: string, map: Record<string, number>) => {
      const fresh = infos[col]?.distinct_values ?? []
      return fresh.length > 0 ? fresh : Object.keys(map ?? {})
    }

    setActiveCfg(deepCopy(fc))
    setDetectedTransform(null)

    const gm = fc.gender_mode ?? (m.gender_concept_id?.source_col ? 'column' : 'default')
    setGenderMode(gm)
    setGenderValues(gm === 'column' && m.gender_concept_id?.source_col
      ? getVals(m.gender_concept_id.source_col, m.gender_concept_id.value_map)
      : [])

    const raceM = m.race_concept_id as RaceEthnicityMapping | undefined
    const rm = fc.race_mode ?? (raceM?.source_col ? 'column' : 'default')
    setRaceMode(rm)
    setRaceValues(rm === 'column' && raceM?.source_col
      ? getVals(raceM.source_col, raceM.value_map ?? {})
      : [])

    const ethM = m.ethnicity_concept_id as RaceEthnicityMapping | undefined
    const em = fc.ethnicity_mode ?? (ethM?.source_col ? 'column' : 'default')
    setEthnicityMode(em)
    setEthnicityValues(em === 'column' && ethM?.source_col
      ? getVals(ethM.source_col, ethM.value_map ?? {})
      : [])
  }

  // ── Initial load ──────────────────────────────────────────────────────
  useEffect(() => {
    if (initialized) return
    setInitialized(true)

    const bootstrap = async () => {
      const existing = await getTableConfig(project.id, 'person').catch(() => null) as
        (PersonConfig & { extra_instructions?: string }) | null

      if (!existing || Object.keys(existing).length === 0) {
        // Fresh config — auto-select first file
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

      let sf: string[]
      let fc: Record<string, PersonFileConfig>

      if (existing.file_configs && existing.source_files?.length) {
        // New multi-file format
        sf = existing.source_files
        fc = existing.file_configs
      } else if (existing.mappings) {
        // Legacy single-file format — migrate
        const filename = existing.source_filename ?? files[0]?.filename ?? ''
        const m = deepCopy(existing.mappings)
        // Migrate ConstantMapping → RaceEthnicityMapping
        if (m.race_concept_id && 'constant' in m.race_concept_id)
          (m as Record<string, unknown>).race_concept_id = { source_col: '', value_map: {}, default: (m.race_concept_id as { constant: number }).constant }
        if (m.ethnicity_concept_id && 'constant' in m.ethnicity_concept_id)
          (m as Record<string, unknown>).ethnicity_concept_id = { source_col: '', value_map: {}, default: (m.ethnicity_concept_id as { constant: number }).constant }

        const migrated: PersonFileConfig = {
          mappings: m,
          birth_time_col: existing.birth_time_col,
          birth_time_format: existing.birth_time_format,
          gender_mode: existing.gender_mode,
          race_mode: existing.race_mode,
          ethnicity_mode: existing.ethnicity_mode,
        }
        sf = filename ? [filename] : (files[0]?.filename ? [files[0].filename] : [])
        fc = sf[0] ? { [sf[0]]: migrated } : {}
      } else {
        // Empty / unrecognised — use defaults
        sf = files[0]?.filename ? [files[0].filename] : []
        fc = sf[0] ? { [sf[0]]: deepCopy(DEFAULT_FILE_CFG) } : {}
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

  // ── Switch between file tabs ──────────────────────────────────────────
  const switchActiveFile = (newFilename: string) => {
    if (newFilename === activeFilename) return

    // Persist current state
    const saved: PersonFileConfig = { ...activeCfg, gender_mode: genderMode, race_mode: raceMode, ethnicity_mode: ethnicityMode }
    const updatedConfigs = { ...fileConfigs, [activeFilename]: saved }
    setFileConfigs(updatedConfigs)
    setActiveFilename(newFilename)

    // Restore new file
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
        // Refresh only the value lists (avoid overwriting user edits to the config)
        const m = newCfg.mappings
        const getVals = (col: string, map: Record<string, number>) => {
          const fresh = infos[col]?.distinct_values ?? []
          return fresh.length > 0 ? fresh : Object.keys(map ?? {})
        }
        if (genderMode === 'column' && m.gender_concept_id?.source_col)
          setGenderValues(getVals(m.gender_concept_id.source_col, m.gender_concept_id.value_map))
        const raceM = m.race_concept_id as RaceEthnicityMapping | undefined
        if (raceMode === 'column' && raceM?.source_col)
          setRaceValues(getVals(raceM.source_col, raceM.value_map ?? {}))
        const ethM = m.ethnicity_concept_id as RaceEthnicityMapping | undefined
        if (ethnicityMode === 'column' && ethM?.source_col)
          setEthnicityValues(getVals(ethM.source_col, ethM.value_map ?? {}))
      }).catch(() => {})
    }
  }

  // ── Toggle file selection ─────────────────────────────────────────────
  const toggleFile = (filename: string, checked: boolean) => {
    if (checked) {
      setSelectedFiles(prev => {
        if (prev.includes(filename)) return prev
        return [...prev, filename]
      })
      setFileConfigs(prev => prev[filename] ? prev : { ...prev, [filename]: deepCopy(DEFAULT_FILE_CFG) })
      // If nothing is active yet, switch to this file
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

  // ── Field setters ─────────────────────────────────────────────────────
  const setField = (path: string[], value: unknown) => {
    setActiveCfg(prev => {
      const next = deepCopy(prev)
      let cur: Record<string, unknown> = next as unknown as Record<string, unknown>
      for (let i = 0; i < path.length - 1; i++) cur = cur[path[i]] as Record<string, unknown>
      cur[path[path.length - 1]] = value
      return next
    })
  }

  const handleColChange = (field: string[], setValues: (v: string[]) => void) => (col: string) => {
    setField([...field, 'source_col'], col)
    setField([...field, 'value_map'], {})
    setValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  const switchGenderMode = (mode: 'column' | 'default') => {
    setGenderMode(mode)
    if (mode === 'default') { setField(['mappings', 'gender_concept_id', 'source_col'], ''); setField(['mappings', 'gender_concept_id', 'value_map'], {}); setGenderValues([]) }
    else setField(['mappings', 'gender_concept_id', 'default'], 0)
  }

  const switchRaceMode = (mode: 'column' | 'default') => {
    setRaceMode(mode)
    if (mode === 'default') { setField(['mappings', 'race_concept_id', 'source_col'], ''); setField(['mappings', 'race_concept_id', 'value_map'], {}); setRaceValues([]) }
    else setField(['mappings', 'race_concept_id', 'default'], 0)
  }

  const switchEthnicityMode = (mode: 'column' | 'default') => {
    setEthnicityMode(mode)
    if (mode === 'default') { setField(['mappings', 'ethnicity_concept_id', 'source_col'], ''); setField(['mappings', 'ethnicity_concept_id', 'value_map'], {}); setEthnicityValues([]) }
    else setField(['mappings', 'ethnicity_concept_id', 'default'], 0)
  }

  const handleGenderColChange = (col: string) => {
    setField(['mappings', 'gender_concept_id', 'source_col'], col)
    setField(['mappings', 'gender_concept_id', 'value_map'], {})
    setGenderValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  const addGenderValue = () => {
    const val = prompt('Enter a source gender value (e.g. 1.0, M, male):')
    if (val) setGenderValues(prev => [...new Set([...prev, val])])
  }

  const handleRaceColChange = handleColChange(['mappings', 'race_concept_id'], setRaceValues)
  const addRaceValue = () => {
    const val = prompt('Enter a source race value:')
    if (val) setRaceValues(prev => [...new Set([...prev, val])])
  }

  const handleEthnicityColChange = handleColChange(['mappings', 'ethnicity_concept_id'], setEthnicityValues)
  const addEthnicityValue = () => {
    const val = prompt('Enter a source ethnicity value:')
    if (val) setEthnicityValues(prev => [...new Set([...prev, val])])
  }

  // ── Save ──────────────────────────────────────────────────────────────
  const saveConfig = async () => {
    const currentCfg: PersonFileConfig = { ...activeCfg, gender_mode: genderMode, race_mode: raceMode, ethnicity_mode: ethnicityMode }
    const snapped = activeFilename ? { ...fileConfigs, [activeFilename]: currentCfg } : fileConfigs

    // Inject the location step's patient identifier into every file config where it is controlled
    const locCfgSave = (project.etl_config?.location ?? {}) as LocationConfig
    const locAutoIncSave = locCfgSave.person_id_auto_increment ?? false
    const allFileConfigs = Object.fromEntries(
      Object.entries(snapped).map(([fn, cfg]) => {
        const filePidCol = locAutoIncSave ? '' : (locCfgSave.file_configs?.[fn]?.person_id_col ?? '')
        if (locAutoIncSave || filePidCol) {
          const injected = locAutoIncSave
            ? { source_col: '', transform: 'int_float' as const, auto_increment: true }
            : { source_col: filePidCol, transform: cfg.mappings.person_id.transform ?? 'int_float' as const, auto_increment: false }
          return [fn, { ...cfg, mappings: { ...cfg.mappings, person_id: injected } }]
        }
        return [fn, cfg]
      })
    )

    const primaryCfg = selectedFiles.length > 0 ? (allFileConfigs[selectedFiles[0]] ?? DEFAULT_FILE_CFG) : DEFAULT_FILE_CFG
    const required = [
      !primaryCfg.mappings.person_id.auto_increment ? primaryCfg.mappings.person_id.source_col : null,
      primaryCfg.mappings.gender_concept_id.source_col,
      primaryCfg.mappings.year_of_birth.source_col,
    ].filter(Boolean) as string[]

    const payload: PersonConfig = {
      enabled: true,
      source_files: selectedFiles,
      file_configs: allFileConfigs,
      required_source_cols: required,
      extra_instructions: extraInstructions,
    }
    const p = await updateTableConfig(project.id, 'person', payload)
    onUpdate(p)
    setFileConfigs(allFileConfigs)
  }

  const validatePidMapped = (allFileConfigs: Record<string, PersonFileConfig>): string[] => {
    const locCfgVal = (project.etl_config?.location ?? {}) as LocationConfig
    if (locCfgVal.person_id_auto_increment) return []
    return selectedFiles.filter(filename => {
      if (locCfgVal.file_configs?.[filename]?.person_id_col) return false
      const pid = (allFileConfigs[filename] ?? DEFAULT_FILE_CFG).mappings.person_id
      return !pid.auto_increment && !pid.source_col
    })
  }

  const beforeGenerate = async () => {
    const currentCfg: PersonFileConfig = { ...activeCfg, gender_mode: genderMode, race_mode: raceMode, ethnicity_mode: ethnicityMode }
    const allFileConfigs = activeFilename ? { ...fileConfigs, [activeFilename]: currentCfg } : fileConfigs
    const missing = validatePidMapped(allFileConfigs)
    setPidMissingFiles(missing)
    if (missing.length > 0) {
      throw new Error(`Patient ID column is not mapped in: ${missing.map(f => `"${f}"`).join(', ')}`)
    }
    await saveConfig()
  }

  const { prev, next } = getAdjacentSlugs(project, 'person')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

  // ── Render ────────────────────────────────────────────────────────────
  const showMappings = !!activeFilename

  return (
    <WizardLayout
      project={project}
      currentSlug="person"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel="Next →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-primary">Person Table Mapping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Map source columns to OMOP <code className="rounded bg-accent px-1">person</code> table fields.
          </p>
        </div>

        {isMultiFile && (
          <div className="rounded-lg border border-border bg-secondary/40 px-4 py-3 text-sm text-muted-foreground space-y-1">
            <p className="font-medium text-foreground">Building the person table from multiple files</p>
            <ul className="list-disc list-inside text-xs space-y-0.5">
              <li>Select which files should contribute patients using the checkboxes below.</li>
              <li>The <span className="font-medium text-foreground">Primary</span> file is processed first — all its patients are loaded as-is.</li>
              <li>Each subsequent file is merged in: new patient IDs are appended; existing ones are enriched with any extra information they carry.</li>
              <li>If the same patient appears with <span className="font-medium text-foreground">conflicting values</span> across files, a warning is printed and that patient is dropped.</li>
              <li>A <span className="font-medium text-foreground">Patient ID column</span> must be mapped in every selected file.</li>
            </ul>
          </div>
        )}

        {/* ── File selection (multi-file projects only) ── */}
        {isMultiFile && (
          <Card className="flex flex-col gap-4 p-6">
            <div>
              <h3 className="font-semibold text-foreground">Source Files</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                Select which files contribute to the person table. Files are processed in order —
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

        {/* ── File config tabs (when multiple files selected) ── */}
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
            {/* Person ID */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">Person id</h3>

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
                      <label className="flex items-center gap-3 select-none">
                        <input type="checkbox" checked readOnly className="w-4 h-4 accent-primary rounded" />
                        <span className="text-sm text-foreground">
                          Auto-increment Patient ID — assign sequential IDs (1, 2, 3…) without mapping to a source column
                        </span>
                      </label>
                    ) : (
                      <FieldMapper
                        label="Patient ID column"
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
                  <label className="flex items-center gap-3 cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={activeCfg.mappings.person_id.auto_increment ?? false}
                      onChange={e => setField(['mappings', 'person_id', 'auto_increment'], e.target.checked)}
                      className="w-4 h-4 accent-primary rounded"
                    />
                    <span className="text-sm text-foreground">
                      Auto-increment Patient ID — assign sequential IDs (1, 2, 3…) without mapping to a source column
                    </span>
                  </label>

                  {!activeCfg.mappings.person_id.auto_increment && (
                    <>
                      <FieldMapper
                        label="Patient ID column"
                        sourceColumns={availCols(activeCfg.mappings.person_id.source_col)}
                        value={activeCfg.mappings.person_id.source_col}
                        onChange={v => setField(['mappings', 'person_id', 'source_col'], v)}
                        required
                        hint="Will be cast using the transform selected below. Used as person_id."
                      />

                      <div>
                        <div className="flex items-center gap-2 mb-1">
                          <Label>Patient ID transform</Label>
                          {detectedTransform && (
                            <span className="text-xs text-green-600 dark:text-green-400 font-medium">(auto-detected)</span>
                          )}
                        </div>
                        <Select
                          value={activeCfg.mappings.person_id.transform}
                          onChange={e => { setDetectedTransform(null); setField(['mappings', 'person_id', 'transform'], e.target.value) }}
                          className="mt-1"
                        >
                          <option value="int_float">int(float(x)) — for "1.0", "2.0" style IDs</option>
                          <option value="int">int(x) — for "1", "2" style IDs</option>
                          <option value="str">str(x) — keep as string</option>
                        </Select>
                      </div>
                    </>
                  )}
                </>
              )}
            </Card>

            {/* Gender */}
            <Card className="flex flex-col gap-5 p-6">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-foreground">Gender</h3>
                <a href="https://athena.ohdsi.org/search-terms/terms?domain=Gender&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
              </div>

              <div className="flex gap-2">
                <button onClick={() => switchGenderMode('column')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${genderMode === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Map a column</button>
                <button onClick={() => switchGenderMode('default')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${genderMode === 'default' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Set default</button>
              </div>

              {genderMode === 'column' ? (
                <div className="flex flex-col gap-3">
                  <FieldMapper
                    label="Gender column"
                    sourceColumns={availCols(activeCfg.mappings.gender_concept_id.source_col)}
                    value={activeCfg.mappings.gender_concept_id.source_col}
                    onChange={handleGenderColChange}
                    required
                    hint="The source column that indicates biological sex."
                  />
                  {activeCfg.mappings.gender_concept_id.source_col && (
                    <div className="flex flex-col gap-2">
                      <div className="flex items-center justify-between">
                        <Label>Gender value → OMOP concept mapping</Label>
                        <button onClick={addGenderValue} className="text-xs text-primary hover:underline">+ Add value</button>
                      </div>
                      <p className="text-xs text-muted-foreground">Common: 8507 = Male, 8532 = Female</p>
                      <ValueConceptMapper
                        label=""
                        sourceValues={genderValues.length > 0 ? genderValues : Object.keys(activeCfg.mappings.gender_concept_id.value_map)}
                        mapping={activeCfg.mappings.gender_concept_id.value_map}
                        onChange={m => setField(['mappings', 'gender_concept_id', 'value_map'], m)}
                        expectedDomain="Gender"
                      />
                    </div>
                  )}
                </div>
              ) : (
                <div>
                  <Label>Default gender_concept_id</Label>
                  <SingleConceptInput
                    value={activeCfg.mappings.gender_concept_id.default || null}
                    onChange={v => setField(['mappings', 'gender_concept_id', 'default'], v ?? 0)}
                    placeholder="e.g. 8507"
                    expectedDomain="Gender"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">Common: 8507 = Male, 8532 = Female, 8551 = Unknown (0 = unknown).</p>
                </div>
              )}
            </Card>

            {/* Date of Birth */}
            <Card className="flex flex-col gap-5 p-6">
              <h3 className="font-semibold text-foreground">Date of Birth</h3>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <FieldMapper
                  label="Date of birth column"
                  sourceColumns={availCols(activeCfg.mappings.year_of_birth.source_col)}
                  value={activeCfg.mappings.year_of_birth.source_col}
                  onChange={v => {
                    setField(['mappings', 'year_of_birth', 'source_col'], v)
                    setField(['mappings', 'month_of_birth', 'source_col'], v)
                    setField(['mappings', 'day_of_birth', 'source_col'], v)
                  }}
                  required
                />
                <FieldMapper
                  label="Birth time column (optional)"
                  sourceColumns={availCols(activeCfg.birth_time_col ?? '')}
                  value={activeCfg.birth_time_col ?? ''}
                  onChange={v => setField(['birth_time_col'], v || undefined)}
                />
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <Label>Date format</Label>
                  <Input
                    type="text"
                    value={activeCfg.mappings.year_of_birth.date_format}
                    onChange={e => {
                      setField(['mappings', 'year_of_birth', 'date_format'], e.target.value)
                      setField(['mappings', 'month_of_birth', 'date_format'], e.target.value)
                      setField(['mappings', 'day_of_birth', 'date_format'], e.target.value)
                    }}
                    placeholder="%Y-%m-%d"
                    className="mt-1 font-mono"
                  />
                </div>
                <div>
                  <Label>Time format</Label>
                  <Input
                    type="text"
                    value={activeCfg.birth_time_format ?? '%H:%M:%S'}
                    onChange={e => setField(['birth_time_format'], e.target.value || '%H:%M:%S')}
                    placeholder="%H:%M:%S"
                    className="mt-1 font-mono"
                  />
                </div>
              </div>

              <p className="text-xs text-muted-foreground -mt-1">
                Python strptime formats. Missing or empty birth time → birth_datetime defaults to midnight (00:00:00).
                Examples: <code className="bg-muted px-1 rounded">%d/%m/%Y</code>, <code className="bg-muted px-1 rounded">%Y%m%d</code>, <code className="bg-muted px-1 rounded">%H:%M</code>.
              </p>
            </Card>

            {/* Race */}
            <Card className="flex flex-col gap-5 p-6">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-foreground">Race</h3>
                <a href="https://athena.ohdsi.org/search-terms/terms?domain=Race&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
              </div>

              <div className="flex gap-2">
                <button onClick={() => switchRaceMode('column')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${raceMode === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Map a column</button>
                <button onClick={() => switchRaceMode('default')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${raceMode === 'default' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Set default</button>
              </div>

              {raceMode === 'column' ? (
                <div className="flex flex-col gap-3">
                  <FieldMapper
                    label="Race column"
                    sourceColumns={availCols((activeCfg.mappings.race_concept_id as RaceEthnicityMapping)?.source_col ?? '')}
                    value={(activeCfg.mappings.race_concept_id as RaceEthnicityMapping)?.source_col ?? ''}
                    onChange={handleRaceColChange}
                  />
                  {(activeCfg.mappings.race_concept_id as RaceEthnicityMapping)?.source_col && (
                    <div className="flex flex-col gap-2">
                      <div className="flex items-center justify-between">
                        <Label>Race value → OMOP concept mapping</Label>
                        <button onClick={addRaceValue} className="text-xs text-primary hover:underline">+ Add value</button>
                      </div>
                      <ValueConceptMapper
                        label=""
                        sourceValues={raceValues.length > 0 ? raceValues : Object.keys((activeCfg.mappings.race_concept_id as RaceEthnicityMapping)?.value_map ?? {})}
                        mapping={(activeCfg.mappings.race_concept_id as RaceEthnicityMapping)?.value_map ?? {}}
                        onChange={m => setField(['mappings', 'race_concept_id', 'value_map'], m)}
                        expectedDomain="Race"
                      />
                    </div>
                  )}
                </div>
              ) : (
                <div>
                  <Label>Default race_concept_id</Label>
                  <SingleConceptInput
                    value={(activeCfg.mappings.race_concept_id as RaceEthnicityMapping)?.default || null}
                    onChange={v => setField(['mappings', 'race_concept_id', 'default'], v ?? 0)}
                    placeholder="e.g. 8527"
                    expectedDomain="Race"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">0 = unknown.</p>
                </div>
              )}
            </Card>

            {/* Ethnicity */}
            <Card className="flex flex-col gap-5 p-6">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold text-foreground">Ethnicity</h3>
                <a href="https://athena.ohdsi.org/search-terms/terms?domain=Ethnicity&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
              </div>

              <div className="flex gap-2">
                <button onClick={() => switchEthnicityMode('column')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${ethnicityMode === 'column' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Map a column</button>
                <button onClick={() => switchEthnicityMode('default')} className={`px-3 py-1.5 rounded text-sm font-medium transition-colors ${ethnicityMode === 'default' ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground hover:bg-secondary/80'}`}>Set default</button>
              </div>

              {ethnicityMode === 'column' ? (
                <div className="flex flex-col gap-3">
                  <FieldMapper
                    label="Ethnicity column"
                    sourceColumns={availCols((activeCfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.source_col ?? '')}
                    value={(activeCfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.source_col ?? ''}
                    onChange={handleEthnicityColChange}
                  />
                  {(activeCfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.source_col && (
                    <div className="flex flex-col gap-2">
                      <div className="flex items-center justify-between">
                        <Label>Ethnicity value → OMOP concept mapping</Label>
                        <button onClick={addEthnicityValue} className="text-xs text-primary hover:underline">+ Add value</button>
                      </div>
                      <ValueConceptMapper
                        label=""
                        sourceValues={ethnicityValues.length > 0 ? ethnicityValues : Object.keys((activeCfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.value_map ?? {})}
                        mapping={(activeCfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.value_map ?? {}}
                        onChange={m => setField(['mappings', 'ethnicity_concept_id', 'value_map'], m)}
                        expectedDomain="Ethnicity"
                      />
                    </div>
                  )}
                </div>
              ) : (
                <div>
                  <Label>Default ethnicity_concept_id</Label>
                  <SingleConceptInput
                    value={(activeCfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.default || null}
                    onChange={v => setField(['mappings', 'ethnicity_concept_id', 'default'], v ?? 0)}
                    placeholder="e.g. 38003564"
                    expectedDomain="Ethnicity"
                  />
                  <p className="mt-1 text-xs text-muted-foreground">0 = unknown.</p>
                </div>
              )}
            </Card>
          </>
        )}

        <ExtraInstructions
          tableName="person"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        {pidMissingFiles.length > 0 && (
          <div className="rounded-lg border border-destructive/50 bg-destructive/10 p-4 flex flex-col gap-1.5">
            <p className="text-sm font-semibold text-destructive">Patient ID column is required in all selected files</p>
            <ul className="list-disc list-inside text-xs text-destructive/90 space-y-0.5">
              {pidMissingFiles.map(f => (
                <li key={f}><span className="font-mono">{f}</span> — Patient ID not mapped</li>
              ))}
            </ul>
            <p className="text-xs text-destructive/80 mt-0.5">
              Select a Patient ID column (or enable Auto-increment) for {pidMissingFiles.length === 1 ? 'this file' : 'each of these files'} before generating.
            </p>
          </div>
        )}

        <ScriptGenerator
          project={project}
          table="person"
          onUpdate={onUpdate}
          beforeGenerate={beforeGenerate}
          deterministic
        />
      </div>
    </WizardLayout>
  )
}
