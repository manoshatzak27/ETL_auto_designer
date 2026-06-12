import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getConceptDecisions } from '../../api/client'
import type { Project, StemTableConfig, StemTableOverride } from '../../types'
import { getStructuralColumns } from '../../utils'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Plus, Trash2, CheckCircle, AlertCircle } from 'lucide-react'
import { Card } from '../../components/ui/card'
import { Input } from '../../components/ui/input'
import { Select } from '../../components/ui/select'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: StemTableConfig = {
  enabled: true,
  variable_groups: {},
  variable_visit_map: {},
  concept_mapping_csvs: {},
  special_overrides: [],
}

export default function Step6StemTable({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [rawDecisions, setRawDecisions] = useState<Record<string, { strategy: string; variable_concept: unknown; value_concepts: Record<string, unknown>; domain_id?: number | null }>>({})
  const [cfg, setCfg] = useState<StemTableConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')

  const mappingFiles = project.mapping_files || {}
  const hasMappings = Object.values(mappingFiles).some(v => !!v)

  const isMultiRow = !!(project.etl_config?.dataset_options as Record<string, unknown> | undefined)?.multiple_rows_per_patient
  const visitSourceCol = ((project.etl_config?.visit_occurrence as Record<string, unknown> | undefined)?.visit_source_col as string | undefined) ?? ''

  const visitLabels = useMemo<string[]>(() => {
    const defs = (project.etl_config as Record<string, unknown> | undefined)
      ?.visit_occurrence as { visit_definitions?: { label?: string }[] } | undefined
    return (defs?.visit_definitions || []).map(vd => vd.label).filter((l): l is string => !!l)
  }, [project.etl_config])

  useEffect(() => {
    Promise.all([
      getTableConfig(project.id, 'stem_table'),
      getConceptDecisions(project.id),
    ]).then(([ex, decisions]: [StemTableConfig & { extra_instructions?: string }, Record<string, { strategy: string; variable_concept: unknown; value_concepts: Record<string, unknown> }>]) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg({ ...DEFAULTS, ...ex })
      }
      setRawDecisions(decisions)
    })
  }, [project.id])

  const mappedCols = useMemo(() => {
    const structuralCols = getStructuralColumns((project.etl_config || {}) as Record<string, unknown>)
    return (project.source_columns || []).filter(col => {
      if (structuralCols.has(col)) return false
      const d = rawDecisions[col]
      if (!d || d.strategy === 'skip') return false
      return !!d.variable_concept || Object.keys(d.value_concepts).length > 0
    })
  }, [rawDecisions, project.source_columns, project.etl_config])

  const variableVisitMap = cfg.variable_visit_map || {}

  const toggleAssignment = (variable: string, visitLabel: string) => {
    setCfg(prev => {
      const next = { ...(prev.variable_visit_map || {}) }
      if (next[variable] === visitLabel) {
        // clicking the same board deassigns the variable
        delete next[variable]
      } else {
        // assign to this board (removes from any previous board automatically)
        next[variable] = visitLabel
      }
      return { ...prev, variable_visit_map: next }
    })
  }

  const addOverride = () => {
    setCfg(prev => ({
      ...prev,
      special_overrides: [...prev.special_overrides, { variable: '', field: 'unit_concept_id', value: 0 }],
    }))
  }

  const updateOverride = (i: number, field: keyof StemTableOverride, value: unknown) => {
    setCfg(prev => {
      const overrides = [...prev.special_overrides]
      overrides[i] = { ...overrides[i], [field]: value }
      return { ...prev, special_overrides: overrides }
    })
  }

  const removeOverride = (i: number) => {
    setCfg(prev => ({
      ...prev,
      special_overrides: prev.special_overrides.filter((_, j) => j !== i),
    }))
  }

  const saveConfig = async () => {
    const updatedCfg = {
      ...cfg,
      concept_mapping_csvs: {
        variable_mapping: mappingFiles.variable_mapping || '',
        value_mapping: mappingFiles.value_mapping || '',
        variable_value_mapping: mappingFiles.variable_value_mapping || '',
      },
      extra_instructions: extraInstructions,
    }
    const p = await updateTableConfig(project.id, 'stem_table', updatedCfg)
    onUpdate(p)
  }

  const { prev, next } = getAdjacentSlugs(project, 'stem-table')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

  const unassignedCount = mappedCols.filter(col => !variableVisitMap[col]).length

  return (
    <WizardLayout
      project={project}
      currentSlug="stem-table"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel="Next: Generate &amp; Load →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-primary">Stem Table</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Assign each mapped variable to its visit by clicking it in the corresponding board.
            A variable can only belong to one visit. Click it again to remove the assignment.
          </p>
        </div>

        {/* Mapping CSV status */}
        <div className={`flex items-center gap-3 px-4 py-3 rounded-lg border ${hasMappings ? 'bg-green-50 border-green-200' : 'bg-amber-50 border-amber-200'}`}>
          {hasMappings
            ? <CheckCircle className="w-5 h-5 text-green-500 flex-shrink-0" />
            : <AlertCircle className="w-5 h-5 text-amber-500 flex-shrink-0" />
          }
          <div className="text-sm">
            {hasMappings ? (
              <span className="text-green-800">
                Concept mapping CSVs generated from Step 9 —{' '}
                {Object.keys(mappingFiles).join(', ').replace(/_/g, ' ')}
              </span>
            ) : (
              <span className="text-amber-700">
                No mapping CSVs yet. Go back to Step 9 and complete concept mapping.
              </span>
            )}
          </div>
        </div>

        {/* Visit Assignment Boards */}
        {isMultiRow ? (
          <Card className="flex flex-col gap-3 p-4 border-primary/40 bg-primary/5">
            <p className="text-sm font-semibold text-primary">Multi-row dataset mode</p>
            <p className="text-xs text-muted-foreground">
              All mapped variables are automatically assigned to the visit identified by the{' '}
              <code className="bg-muted px-1 rounded">{visitSourceCol || 'visit identifier column'}</code> column.
              No manual visit assignment is needed — every variable in a row is attached to that row's visit.
            </p>
            {mappedCols.length > 0 && (
              <div>
                <p className="text-xs text-muted-foreground mb-1.5">Mapped variables ({mappedCols.length}):</p>
                <div className="flex flex-wrap gap-1.5">
                  {mappedCols.map(col => (
                    <span key={col} className="px-2 py-1 rounded text-xs font-mono border bg-background text-foreground border-border">
                      {col}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </Card>
        ) : visitLabels.length === 0 ? (
          <div className="flex items-center gap-3 px-4 py-3 rounded-lg border bg-amber-50 border-amber-200 text-sm text-amber-700">
            <AlertCircle className="w-5 h-5 flex-shrink-0" />
            No visits configured. Go back to Step 6 (Visit Occurrence) and define at least one visit.
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {unassignedCount > 0 && (
              <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                {unassignedCount} variable{unassignedCount > 1 ? 's' : ''} not yet assigned to a visit — they will be skipped in the generated script.
              </p>
            )}

            <div className="flex flex-col gap-4">
              {visitLabels.map(label => {
                // show: variables assigned to THIS board + variables not assigned anywhere
                const visibleCols = mappedCols.filter(
                  col => variableVisitMap[col] === label || !variableVisitMap[col]
                )
                const assignedCount = visibleCols.filter(col => variableVisitMap[col] === label).length

                return (
                  <Card key={label} className="p-4 flex flex-col gap-3">
                    <div className="flex items-center justify-between">
                      <h3 className="font-semibold text-sm text-foreground">{label}</h3>
                      <span className="text-xs text-muted-foreground">
                        {assignedCount} / {mappedCols.length}
                      </span>
                    </div>

                    {visibleCols.length === 0 ? (
                      <p className="text-xs text-muted-foreground italic">No variables available.</p>
                    ) : (
                      <div className="flex flex-wrap gap-1.5">
                        {visibleCols.map(col => {
                          const isAssigned = variableVisitMap[col] === label
                          return (
                            <button
                              key={col}
                              onClick={() => toggleAssignment(col, label)}
                              title={isAssigned ? `Remove "${col}" from ${label}` : `Assign "${col}" to ${label}`}
                              className={[
                                'px-2 py-1 rounded text-xs font-mono border transition-colors cursor-pointer',
                                isAssigned
                                  ? 'bg-primary text-primary-foreground border-primary hover:bg-primary/80'
                                  : 'bg-background text-foreground border-border hover:border-primary hover:bg-primary/5',
                              ].join(' ')}
                            >
                              {col}
                            </button>
                          )
                        })}
                      </div>
                    )}
                  </Card>
                )
              })}
            </div>
          </div>
        )}

        {/* Special Overrides */}
        <Card className="p-6 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-foreground">Special Field Overrides</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                Force a specific OMOP field value for individual variables after concept lookup.
                Example: <code className="bg-accent px-1 rounded">unit_concept_id = 9580</code> (months) for DUP, DUI, DAP, DAT.
              </p>
            </div>
            <button onClick={addOverride} className="flex items-center gap-1 text-xs text-primary hover:text-primary/80 font-medium">
              <Plus className="w-3.5 h-3.5" /> Add override
            </button>
          </div>

          {cfg.special_overrides.length === 0 && (
            <p className="text-xs text-muted-foreground italic">No overrides defined.</p>
          )}

          <div className="flex flex-col gap-2">
            {cfg.special_overrides.map((ov, i) => {
              const variableOptions = ov.variable && !mappedCols.includes(ov.variable)
                ? [ov.variable, ...mappedCols]
                : mappedCols
              return (
                <div key={i} className="flex items-center gap-2 p-3 bg-muted rounded-lg">
                  <Select
                    value={ov.variable}
                    onChange={e => updateOverride(i, 'variable', e.target.value)}
                    className="h-8 text-sm font-mono w-48"
                  >
                    <option value="">— select a variable —</option>
                    {variableOptions.map(col => (
                      <option key={col} value={col}>{col}</option>
                    ))}
                  </Select>
                  <Select
                    value={ov.field || 'unit_concept_id'}
                    onChange={e => updateOverride(i, 'field', e.target.value)}
                    className="h-8 text-sm"
                  >
                    <option value="unit_concept_id">unit_concept_id</option>
                    <option value="operator_concept_id">operator_concept_id</option>
                  </Select>
                  <span className="text-muted-foreground font-mono">=</span>
                  <Input
                    type="number"
                    value={ov.value?.toString() ?? ''}
                    onChange={e => updateOverride(i, 'value', parseInt(e.target.value) || 0)}
                    placeholder="Concept ID"
                    className="w-32 h-8 text-sm"
                  />
                  <button onClick={() => removeOverride(i)} className="text-destructive/50 hover:text-destructive ml-auto">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              )
            })}
          </div>
        </Card>

        <ExtraInstructions
          tableName="stem_table"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        <ScriptGenerator
          project={project}
          table="stem_table"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
