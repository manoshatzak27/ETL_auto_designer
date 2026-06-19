import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues, detectColumnType } from '../../api/client'
import { extractMappedCols, getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, PersonConfig, RaceEthnicityMapping } from '../../types'
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
import { Select } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { useDomainValidation } from '../../hooks/useDomainValidation'

interface ColumnInfo { distinct_values: string[] }

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: PersonConfig = {
  enabled: true,
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
  required_source_cols: [],
}

export default function PersonStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<PersonConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'person'), [project.etl_config])
  const stepUsed = useMemo(() => extractMappedCols(cfg), [cfg])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || (!crossUsed.has(c) && !stepUsed.has(c)))
  const [genderValues, setGenderValues] = useState<string[]>([])
  const [raceValues, setRaceValues] = useState<string[]>([])
  const [ethnicityValues, setEthnicityValues] = useState<string[]>([])
  const [extraInstructions, setExtraInstructions] = useState('')
  const [genderMode, setGenderMode] = useState<'column' | 'default'>('column')
  const [raceMode, setRaceMode] = useState<'column' | 'default'>('column')
  const [ethnicityMode, setEthnicityMode] = useState<'column' | 'default'>('column')
  const [detectedTransform, setDetectedTransform] = useState<string | null>(null)

  const genderDefaultId = cfg.mappings.gender_concept_id.default ?? 0
  const genderConceptIds = genderMode === 'column'
    ? Object.values(cfg.mappings.gender_concept_id.value_map)
    : genderDefaultId > 0 ? [genderDefaultId] : []
  const genderViolations = useDomainValidation(genderConceptIds, 'Gender')

  const raceMap = cfg.mappings.race_concept_id as RaceEthnicityMapping
  const raceDefaultId = raceMap?.default ?? 0
  const raceConceptIds = raceMode === 'column'
    ? Object.values(raceMap?.value_map ?? {})
    : raceDefaultId > 0 ? [raceDefaultId] : []
  const raceViolations = useDomainValidation(raceConceptIds, 'Race')

  const ethMap = cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping
  const ethDefaultId = ethMap?.default ?? 0
  const ethConceptIds = ethnicityMode === 'column'
    ? Object.values(ethMap?.value_map ?? {})
    : ethDefaultId > 0 ? [ethDefaultId] : []
  const ethViolations = useDomainValidation(ethConceptIds, 'Ethnicity')

  const pidCol = cfg.mappings.person_id.source_col
  useEffect(() => {
    if (!pidCol || cfg.mappings.person_id.auto_increment) {
      setDetectedTransform(null)
      return
    }
    detectColumnType(project.id, pidCol).then(res => {
      setDetectedTransform(res.transform)
      setCfg(prev => ({
        ...prev,
        mappings: {
          ...prev.mappings,
          person_id: { ...prev.mappings.person_id, transform: res.transform },
        },
      }))
    }).catch(() => setDetectedTransform(null))
  }, [pidCol, project.id, cfg.mappings.person_id.auto_increment])

  useEffect(() => {
    Promise.all([
      getTableConfig(project.id, 'person'),
      getColumnValues(project.id),
    ]).then(([existing, infos]: [PersonConfig & { extra_instructions?: string }, Record<string, ColumnInfo>]) => {
      setColumnInfos(infos)
      if (existing && Object.keys(existing).length > 0) {
        setExtraInstructions(existing.extra_instructions || '')
        const m = existing.mappings
        if (m.race_concept_id && 'constant' in m.race_concept_id)
          (m as unknown as Record<string, unknown>).race_concept_id = { source_col: '', value_map: {}, default: (m.race_concept_id as { constant: number }).constant }
        if (m.ethnicity_concept_id && 'constant' in m.ethnicity_concept_id)
          (m as unknown as Record<string, unknown>).ethnicity_concept_id = { source_col: '', value_map: {}, default: (m.ethnicity_concept_id as { constant: number }).constant }

        const getValues = (col: string, savedMap: Record<string, number>) => {
          const savedKeys = Object.keys(savedMap)
          if (savedKeys.length > 0) return savedKeys
          return infos[col]?.distinct_values ?? []
        }

        const genderCol = m.gender_concept_id?.source_col
        if (existing.gender_mode) {
          setGenderMode(existing.gender_mode)
          if (existing.gender_mode === 'column' && genderCol)
            setGenderValues(getValues(genderCol, m.gender_concept_id?.value_map ?? {}))
        } else if (genderCol) { setGenderValues(getValues(genderCol, m.gender_concept_id?.value_map ?? {})); setGenderMode('column') }
        else if (m.gender_concept_id?.default) setGenderMode('default')

        const raceCol = (m.race_concept_id as RaceEthnicityMapping)?.source_col
        if (existing.race_mode) {
          setRaceMode(existing.race_mode)
          if (existing.race_mode === 'column' && raceCol)
            setRaceValues(getValues(raceCol, (m.race_concept_id as RaceEthnicityMapping)?.value_map ?? {}))
        } else if (raceCol) { setRaceValues(getValues(raceCol, (m.race_concept_id as RaceEthnicityMapping)?.value_map ?? {})); setRaceMode('column') }
        else if ((m.race_concept_id as RaceEthnicityMapping)?.default) setRaceMode('default')

        const ethCol = (m.ethnicity_concept_id as RaceEthnicityMapping)?.source_col
        if (existing.ethnicity_mode) {
          setEthnicityMode(existing.ethnicity_mode)
          if (existing.ethnicity_mode === 'column' && ethCol)
            setEthnicityValues(getValues(ethCol, (m.ethnicity_concept_id as RaceEthnicityMapping)?.value_map ?? {}))
        } else if (ethCol) { setEthnicityValues(getValues(ethCol, (m.ethnicity_concept_id as RaceEthnicityMapping)?.value_map ?? {})); setEthnicityMode('column') }
        else if ((m.ethnicity_concept_id as RaceEthnicityMapping)?.default) setEthnicityMode('default')

        setCfg({
          ...DEFAULTS,
          ...existing,
          mappings: { ...DEFAULTS.mappings, ...existing.mappings },
        })
      }
    })
  }, [project.id])

  const setField = (path: string[], value: unknown) => {
    setCfg(prev => {
      const next = JSON.parse(JSON.stringify(prev))
      let cur: Record<string, unknown> = next
      for (let i = 0; i < path.length - 1; i++) cur = cur[path[i]] as Record<string, unknown>
      cur[path[path.length - 1]] = value
      return next
    })
  }

  const handleColChange = (
    field: string[],
    setValues: (v: string[]) => void,
  ) => (col: string) => {
    setField([...field, 'source_col'], col)
    setField([...field, 'value_map'], {})
    setValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  const switchGenderMode = (mode: 'column' | 'default') => {
    setGenderMode(mode)
    if (mode === 'default') {
      setField(['mappings', 'gender_concept_id', 'source_col'], '')
      setField(['mappings', 'gender_concept_id', 'value_map'], {})
      setGenderValues([])
    } else {
      setField(['mappings', 'gender_concept_id', 'default'], 0)
    }
  }

  const switchRaceMode = (mode: 'column' | 'default') => {
    setRaceMode(mode)
    if (mode === 'default') {
      setField(['mappings', 'race_concept_id', 'source_col'], '')
      setField(['mappings', 'race_concept_id', 'value_map'], {})
      setRaceValues([])
    } else {
      setField(['mappings', 'race_concept_id', 'default'], 0)
    }
  }

  const switchEthnicityMode = (mode: 'column' | 'default') => {
    setEthnicityMode(mode)
    if (mode === 'default') {
      setField(['mappings', 'ethnicity_concept_id', 'source_col'], '')
      setField(['mappings', 'ethnicity_concept_id', 'value_map'], {})
      setEthnicityValues([])
    } else {
      setField(['mappings', 'ethnicity_concept_id', 'default'], 0)
    }
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

  const saveConfig = async () => {
    const required = [
      !cfg.mappings.person_id.auto_increment ? cfg.mappings.person_id.source_col : null,
      cfg.mappings.gender_concept_id.source_col,
      cfg.mappings.year_of_birth.source_col,
    ].filter(Boolean)
    const updated = {
      ...cfg,
      required_source_cols: required as string[],
      extra_instructions: extraInstructions,
      gender_mode: genderMode,
      race_mode: raceMode,
      ethnicity_mode: ethnicityMode,
    }
    const p = await updateTableConfig(project.id, 'person', updated)
    onUpdate(p)
  }

  const { prev, next } = getAdjacentSlugs(project, 'person')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

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

        {/* Person ID */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Person id</h3>

          <label className="flex items-center gap-3 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={cfg.mappings.person_id.auto_increment ?? false}
              onChange={e => setField(['mappings', 'person_id', 'auto_increment'], e.target.checked)}
              className="w-4 h-4 accent-primary rounded"
            />
            <span className="text-sm text-foreground">
              Auto-increment Patient ID — assign sequential IDs (1, 2, 3…) without mapping to a source column
            </span>
          </label>

          {!cfg.mappings.person_id.auto_increment && (
            <>
              <FieldMapper
                label="Patient ID column"
                sourceColumns={availCols(cfg.mappings.person_id.source_col)}
                value={cfg.mappings.person_id.source_col}
                onChange={v => setField(['mappings', 'person_id', 'source_col'], v)}
                required
                hint={`Will be cast using the transform selected below. Used as person_id.`}
              />

              <div>
                <div className="flex items-center gap-2 mb-1">
                  <Label>Patient ID transform</Label>
                  {detectedTransform && (
                    <span className="text-xs text-green-600 dark:text-green-400 font-medium">(auto-detected)</span>
                  )}
                </div>
                <Select
                  value={cfg.mappings.person_id.transform}
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
                sourceColumns={availCols(cfg.mappings.gender_concept_id.source_col)}
                value={cfg.mappings.gender_concept_id.source_col}
                onChange={handleGenderColChange}
                required
                hint="The source column that indicates biological sex."
              />
              {cfg.mappings.gender_concept_id.source_col && (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <Label>Gender value → OMOP concept mapping</Label>
                    <button onClick={addGenderValue} className="text-xs text-primary hover:underline">+ Add value</button>
                  </div>
                  <p className="text-xs text-muted-foreground">Common: 8507 = Male, 8532 = Female</p>
                  <ValueConceptMapper
                    label=""
                    sourceValues={genderValues.length > 0 ? genderValues : Object.keys(cfg.mappings.gender_concept_id.value_map)}
                    mapping={cfg.mappings.gender_concept_id.value_map}
                    onChange={m => setField(['mappings', 'gender_concept_id', 'value_map'], m)}
                  />
                </div>
              )}
            </div>
          ) : (
            <div>
              <Label>Default gender_concept_id</Label>
              <SingleConceptInput
                value={cfg.mappings.gender_concept_id.default || null}
                onChange={v => setField(['mappings', 'gender_concept_id', 'default'], v ?? 0)}
                placeholder="e.g. 8507"
              />
              <p className="mt-1 text-xs text-muted-foreground">Common: 8507 = Male, 8532 = Female, 8551 = Unknown (0 = unknown).</p>
            </div>
          )}
          <DomainWarning violations={genderViolations} expectedDomain="Gender" />
        </Card>

        {/* Date of Birth */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Date of Birth</h3>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <FieldMapper
              label="Date of birth column"
              sourceColumns={availCols(cfg.mappings.year_of_birth.source_col)}
              value={cfg.mappings.year_of_birth.source_col}
              onChange={v => {
                setField(['mappings', 'year_of_birth', 'source_col'], v)
                setField(['mappings', 'month_of_birth', 'source_col'], v)
                setField(['mappings', 'day_of_birth', 'source_col'], v)
              }}
              required
            />
            <FieldMapper
              label="Birth time column (optional)"
              sourceColumns={availCols(cfg.birth_time_col ?? '')}
              value={cfg.birth_time_col ?? ''}
              onChange={v => setField(['birth_time_col'], v || undefined)}
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <Label>Date format</Label>
              <Input
                type="text"
                value={cfg.mappings.year_of_birth.date_format}
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
                value={cfg.birth_time_format ?? '%H:%M:%S'}
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
                sourceColumns={availCols((cfg.mappings.race_concept_id as RaceEthnicityMapping)?.source_col ?? '')}
                value={(cfg.mappings.race_concept_id as RaceEthnicityMapping)?.source_col ?? ''}
                onChange={handleRaceColChange}
              />
              {(cfg.mappings.race_concept_id as RaceEthnicityMapping)?.source_col && (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <Label>Race value → OMOP concept mapping</Label>
                    <button onClick={addRaceValue} className="text-xs text-primary hover:underline">+ Add value</button>
                  </div>
                  <ValueConceptMapper
                    label=""
                    sourceValues={raceValues.length > 0 ? raceValues : Object.keys((cfg.mappings.race_concept_id as RaceEthnicityMapping)?.value_map ?? {})}
                    mapping={(cfg.mappings.race_concept_id as RaceEthnicityMapping)?.value_map ?? {}}
                    onChange={m => setField(['mappings', 'race_concept_id', 'value_map'], m)}
                  />
                </div>
              )}
            </div>
          ) : (
            <div>
              <Label>Default race_concept_id</Label>
              <SingleConceptInput
                value={(cfg.mappings.race_concept_id as RaceEthnicityMapping)?.default || null}
                onChange={v => setField(['mappings', 'race_concept_id', 'default'], v ?? 0)}
                placeholder="e.g. 8527"
              />
              <p className="mt-1 text-xs text-muted-foreground">0 = unknown.</p>
            </div>
          )}
          <DomainWarning violations={raceViolations} expectedDomain="Race" />
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
                sourceColumns={availCols((cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.source_col ?? '')}
                value={(cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.source_col ?? ''}
                onChange={handleEthnicityColChange}
              />
              {(cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.source_col && (
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between">
                    <Label>Ethnicity value → OMOP concept mapping</Label>
                    <button onClick={addEthnicityValue} className="text-xs text-primary hover:underline">+ Add value</button>
                  </div>
                  <ValueConceptMapper
                    label=""
                    sourceValues={ethnicityValues.length > 0 ? ethnicityValues : Object.keys((cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.value_map ?? {})}
                    mapping={(cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.value_map ?? {}}
                    onChange={m => setField(['mappings', 'ethnicity_concept_id', 'value_map'], m)}
                  />
                </div>
              )}
            </div>
          ) : (
            <div>
              <Label>Default ethnicity_concept_id</Label>
              <SingleConceptInput
                value={(cfg.mappings.ethnicity_concept_id as RaceEthnicityMapping)?.default || null}
                onChange={v => setField(['mappings', 'ethnicity_concept_id', 'default'], v ?? 0)}
                placeholder="e.g. 38003564"
              />
              <p className="mt-1 text-xs text-muted-foreground">0 = unknown.</p>
            </div>
          )}
          <DomainWarning violations={ethViolations} expectedDomain="Ethnicity" />
        </Card>

        {/* Provider ID */}
        <ExtraInstructions
          tableName="person"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        <ScriptGenerator
          project={project}
          table="person"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
          deterministic
        />
      </div>
    </WizardLayout>
  )
}
