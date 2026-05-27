import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getConceptDecisions } from '../../api/client'
import type { Project, StemTableConfig, StemTableOverride, VisitOccurrenceConfig } from '../../types'
import { getStructuralColumns } from '../../utils'
import WizardLayout from './WizardLayout'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Plus, Trash2, CheckCircle, AlertCircle } from 'lucide-react'
import { Card } from '../../components/ui/card'
import { Input } from '../../components/ui/input'
import { Select } from '../../components/ui/select'

const DOMAIN_TABLES = [
  { table: 'measurement', label: 'Measurement', domainId: 1, color: 'bg-blue-50 text-blue-700 border-blue-200' },
  { table: 'observation', label: 'Observation', domainId: 2, color: 'bg-purple-50 text-purple-700 border-purple-200' },
  { table: 'drug_exposure', label: 'Drug Exposure', domainId: 3, color: 'bg-green-50 text-green-700 border-green-200' },
  { table: 'procedure_occurrence', label: 'Procedure Occurrence', domainId: 4, color: 'bg-orange-50 text-orange-700 border-orange-200' },
  { table: 'condition_occurrence', label: 'Condition Occurrence', domainId: 5, color: 'bg-red-50 text-red-700 border-red-200' },
]

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: StemTableConfig = {
  enabled: true,
  variable_groups: { onset: [], followup_10y: [] },
  concept_mapping_csvs: {},
  special_overrides: [],
}

export default function Step6StemTable({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [rawDecisions, setRawDecisions] = useState<Record<string, { strategy: string; variable_concept: unknown; value_concepts: Record<string, unknown>; domain_id?: number | null }>>({})
  const [cfg, setCfg] = useState<StemTableConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [extraInstructions, setExtraInstructions] = useState('')
  const [visitLabels, setVisitLabels] = useState<string[]>([])

  // Check which mapping CSVs were generated in Step 2
  const mappingFiles = project.mapping_files || {}
  const hasMappings = Object.values(mappingFiles).some(v => !!v)

  useEffect(() => {
    Promise.all([
      getTableConfig(project.id, 'stem_table'),
      getTableConfig(project.id, 'visit_occurrence'),
      getConceptDecisions(project.id),
    ]).then(([ex, visitCfg, decisions]: [StemTableConfig & { extra_instructions?: string }, VisitOccurrenceConfig, Record<string, { strategy: string; variable_concept: unknown; value_concepts: Record<string, unknown> }>]) => {
      const labels: string[] = (visitCfg?.visit_definitions ?? [])
        .map((vd: { label: string }) => vd.label)
        .filter(Boolean)

      setVisitLabels(labels)

      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        const savedVisitLabels: string[] = ex.visit_labels ?? []
        const syncedGroups: Record<string, string[]> = {}
        // Keep manually-added groups (not derived from visit labels at save time)
        for (const [key, val] of Object.entries(ex.variable_groups)) {
          if (!savedVisitLabels.includes(key)) syncedGroups[key] = val
        }
        // Add current visit labels, carrying over any existing assignments
        for (const label of labels) {
          syncedGroups[label] = ex.variable_groups[label] ?? []
        }
        setCfg({ ...ex, variable_groups: syncedGroups })
      } else if (labels.length > 0) {
        // No saved stem_table config yet — seed groups from visit labels
        const groups: Record<string, string[]> = {}
        for (const label of labels) groups[label] = []
        setCfg(prev => ({ ...prev, variable_groups: groups }))
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

  const domainAssignments = useMemo(() => {
    const grouped: Record<number, string[]> = { 1: [], 2: [], 3: [], 4: [], 5: [] }
    for (const [col, decision] of Object.entries(rawDecisions)) {
      if (decision.strategy !== 'skip' && decision.domain_id != null) {
        const id = decision.domain_id as number
        if (id in grouped) grouped[id].push(col)
      }
    }
    return grouped
  }, [rawDecisions])

  const updateGroup = (group: string, selected: string[]) => {
    setCfg(prev => ({ ...prev, variable_groups: { ...prev.variable_groups, [group]: selected } }))
  }

  const addGroup = () => {
    if (!newGroupName.trim()) return
    setCfg(prev => ({
      ...prev,
      variable_groups: { ...prev.variable_groups, [newGroupName.trim()]: [] },
    }))
    setNewGroupName('')
  }

  const removeGroup = (name: string) => {
    setCfg(prev => {
      const g = { ...prev.variable_groups }
      delete g[name]
      return { ...prev, variable_groups: g }
    })
  }

  const toggleVar = (group: string, col: string) => {
    const current = cfg.variable_groups[group] || []
    const next = current.includes(col) ? current.filter(c => c !== col) : [...current, col]
    updateGroup(group, next)
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
      visit_labels: visitLabels,
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

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/11`)
  }

  const totalAssigned = Object.values(cfg.variable_groups).reduce((s, g) => s + g.length, 0)

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={10}
      generatedScripts={project.generated_scripts}
      sourceUploaded={!!project.source_filename}
      hasMappingFiles={Object.keys(project.mapping_files || {}).length > 0}
      onBack={() => navigate(`/project/${project.id}/step/9`)}
      onNext={handleNext}
      nextLabel="Next: Generate Code →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-primary">Stem Table Configuration</h2>
          <p className="text-sm text-muted-foreground mt-1">
            Classify source variables into visit-timepoint groups. The concept mappings you defined in Step 2 are used automatically.
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

        {/* Variable Groups */}
        <Card className="p-6 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-foreground">Variable Groups (Timepoints)</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                Each group corresponds to a visit type from Step 4. Variables not assigned to any group are ignored.
                <span className="font-medium text-gray-600"> {totalAssigned}/{mappedCols.length} columns assigned.</span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Input
                type="text"
                value={newGroupName}
                onChange={e => setNewGroupName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addGroup()}
                placeholder="New group name"
                className="w-36 h-8 text-sm"
              />
              <button onClick={addGroup} className="flex items-center gap-1 text-sm text-primary hover:text-primary/80 font-medium px-2 py-1.5 border border-border rounded-md">
                <Plus className="w-3.5 h-3.5" /> Add
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-4">
            {Object.entries(cfg.variable_groups).map(([group, selected]) => (
              <div key={group} className="border border-border rounded-lg p-4 flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <h4 className="text-sm font-semibold text-foreground">{group}</h4>
                    <span className="text-xs text-muted-foreground bg-muted px-2 py-0.5 rounded-full">
                      {selected.length} variables
                    </span>
                  </div>
                  <button onClick={() => removeGroup(group)} className="text-destructive/40 hover:text-destructive">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">Click to toggle variable assignment:</p>
                <div className="flex flex-wrap gap-1.5 max-h-44 overflow-y-auto">
                  {mappedCols.map(col => (
                    <button
                      key={col}
                      onClick={() => toggleVar(group, col)}
                      className={`px-2 py-0.5 rounded text-xs font-mono transition-colors ${
                        selected.includes(col)
                          ? 'bg-primary text-primary-foreground shadow-sm'
                          : 'bg-muted text-muted-foreground hover:bg-muted/70'
                      }`}
                    >
                      {col}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>

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
            {cfg.special_overrides.map((ov, i) => (

              <div key={i} className="flex items-center gap-2 p-3 bg-muted rounded-lg">
                <Input
                  type="text"
                  value={ov.variable}
                  onChange={e => updateOverride(i, 'variable', e.target.value)}
                  placeholder="Variable name"
                  className="w-28 h-8 text-sm font-mono"
                />
                <Select
                  value={ov.field || 'unit_concept_id'}
                  onChange={e => updateOverride(i, 'field', e.target.value)}
                  className="h-8 text-sm"
                >
                  <option value="unit_concept_id">unit_concept_id</option>
                  <option value="operator_concept_id">operator_concept_id</option>
                  <option value="value_as_string">value_as_string</option>
                  <option value="source_value">source_value</option>
                </Select>
                <span className="text-muted-foreground font-mono">=</span>
                <Input
                  type="text"
                  value={ov.field === 'value_as_string' ? (ov.value_as_string ?? '') : (ov.value?.toString() ?? '')}
                  onChange={e => {
                    if (ov.field === 'value_as_string') updateOverride(i, 'value_as_string', e.target.value)
                    else updateOverride(i, 'value', parseInt(e.target.value) || 0)
                  }}
                  placeholder="value"
                  className="w-32 h-8 text-sm"
                />
                <button onClick={() => removeOverride(i)} className="text-destructive/50 hover:text-destructive ml-auto">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </Card>

        <ExtraInstructions
          tableName="stem_table"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="stem_table"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />

        {/* Domain table script generators */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-5">
          <div>
            <h3 className="font-medium text-gray-800">Domain Table Scripts</h3>
            <p className="text-xs text-gray-500 mt-0.5">
              Each script reads <code className="bg-gray-100 px-1 rounded">stem_table.csv</code> from the output directory
              and routes rows to the appropriate OMOP table based on domain_id. Generate the stem table first.
            </p>
          </div>
          {DOMAIN_TABLES.map(({ table, label, domainId, color }) => {
            const cols = domainAssignments[domainId] || []
            return (
              <div key={table} className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-bold px-2 py-0.5 rounded-full border ${color}`}>{domainId}</span>
                  <span className="text-sm font-medium text-gray-700">{label}</span>
                  {cols.length > 0 && (
                    <span className="text-xs text-gray-400">{cols.length} variable{cols.length !== 1 ? 's' : ''}</span>
                  )}
                </div>
                <ScriptGenerator
                  project={project}
                  table={table}
                  onUpdate={onUpdate}
                  beforeGenerate={saveConfig}
                />
              </div>
            )
          })}
        </div>
      </div>
    </WizardLayout>
  )
}
