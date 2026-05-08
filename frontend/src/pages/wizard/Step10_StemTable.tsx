import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import type { Project, StemTableConfig, StemTableOverride } from '../../types'
import WizardLayout from './WizardLayout'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Plus, Trash2, CheckCircle, AlertCircle } from 'lucide-react'

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
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<StemTableConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [newGroupName, setNewGroupName] = useState('')
  const [extraInstructions, setExtraInstructions] = useState('')

  // Check which mapping CSVs were generated in Step 2
  const mappingFiles = project.mapping_files || {}
  const hasMappings = !!mappingFiles.variable_mapping

  useEffect(() => {
    getTableConfig(project.id, 'stem_table').then((ex: StemTableConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
  }, [project.id])

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
      onBack={() => navigate(`/project/${project.id}/step/9`)}
      onNext={handleNext}
      nextLabel="Next: Generate Code →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Stem Table Configuration</h2>
          <p className="text-sm text-gray-500 mt-1">
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
                Concept mapping CSVs generated from Step 2 —{' '}
                {Object.keys(mappingFiles).join(', ').replace(/_/g, ' ')}
              </span>
            ) : (
              <span className="text-amber-700">
                No mapping CSVs yet. Go back to Step 2 and complete concept mapping.
              </span>
            )}
          </div>
        </div>

        {/* Variable Groups */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-medium text-gray-800">Variable Groups (Timepoints)</h3>
              <p className="text-xs text-gray-500 mt-0.5">
                Each group corresponds to a visit type from Step 4. Variables not assigned to any group are ignored.
                <span className="font-medium text-gray-600"> {totalAssigned}/{cols.length} columns assigned.</span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={newGroupName}
                onChange={e => setNewGroupName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && addGroup()}
                placeholder="New group name"
                className="border border-gray-300 rounded-md px-2 py-1.5 text-sm w-36 focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
              <button onClick={addGroup} className="flex items-center gap-1 text-sm text-blue-600 hover:text-blue-700 font-medium px-2 py-1.5 border border-blue-200 rounded-md">
                <Plus className="w-3.5 h-3.5" /> Add
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-4">
            {Object.entries(cfg.variable_groups).map(([group, selected]) => (
              <div key={group} className="border border-gray-100 rounded-lg p-4 flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <h4 className="text-sm font-semibold text-gray-700">{group}</h4>
                    <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded-full">
                      {selected.length} variables
                    </span>
                  </div>
                  <button onClick={() => removeGroup(group)} className="text-red-300 hover:text-red-500">
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
                <p className="text-xs text-gray-400">Click to toggle variable assignment:</p>
                <div className="flex flex-wrap gap-1.5 max-h-44 overflow-y-auto">
                  {cols.map(col => (
                    <button
                      key={col}
                      onClick={() => toggleVar(group, col)}
                      className={`px-2 py-0.5 rounded text-xs font-mono transition-colors ${
                        selected.includes(col)
                          ? 'bg-blue-600 text-white shadow-sm'
                          : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                      }`}
                    >
                      {col}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Special Overrides */}
        <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col gap-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-medium text-gray-800">Special Field Overrides</h3>
              <p className="text-xs text-gray-500 mt-0.5">
                Force a specific OMOP field value for individual variables after concept lookup.
                Example: <code className="bg-gray-100 px-1 rounded">unit_concept_id = 9580</code> (months) for DUP, DUI, DAP, DAT.
              </p>
            </div>
            <button onClick={addOverride} className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-700 font-medium">
              <Plus className="w-3.5 h-3.5" /> Add override
            </button>
          </div>

          {cfg.special_overrides.length === 0 && (
            <p className="text-xs text-gray-400 italic">No overrides defined.</p>
          )}

          <div className="flex flex-col gap-2">
            {cfg.special_overrides.map((ov, i) => (

              <div key={i} className="flex items-center gap-2 p-3 bg-gray-50 rounded-lg">
                <input
                  type="text"
                  value={ov.variable}
                  onChange={e => updateOverride(i, 'variable', e.target.value)}
                  placeholder="Variable name"
                  className="border border-gray-300 rounded px-2 py-1 text-sm font-mono w-28 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <select
                  value={ov.field || 'unit_concept_id'}
                  onChange={e => updateOverride(i, 'field', e.target.value)}
                  className="border border-gray-300 rounded px-2 py-1 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="unit_concept_id">unit_concept_id</option>
                  <option value="operator_concept_id">operator_concept_id</option>
                  <option value="value_as_string">value_as_string</option>
                  <option value="source_value">source_value</option>
                </select>
                <span className="text-gray-400 font-mono">=</span>
                <input
                  type="text"
                  value={ov.field === 'value_as_string' ? (ov.value_as_string ?? '') : (ov.value?.toString() ?? '')}
                  onChange={e => {
                    if (ov.field === 'value_as_string') updateOverride(i, 'value_as_string', e.target.value)
                    else updateOverride(i, 'value', parseInt(e.target.value) || 0)
                  }}
                  placeholder="value"
                  className="border border-gray-300 rounded px-2 py-1 text-sm w-32 focus:outline-none focus:ring-2 focus:ring-blue-500"
                />
                <button onClick={() => removeOverride(i)} className="text-red-400 hover:text-red-600 ml-auto">
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>

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
      </div>
    </WizardLayout>
  )
}
