import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig } from '../../api/client'
import { extractMappedCols, getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, DeathConfig } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs } from '../../wizard/steps'
import FieldMapper from '../../components/FieldMapper'
import SingleConceptInput from '../../components/SingleConceptInput'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { useSourceFile } from '../../hooks/useSourceFile'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: DeathConfig = {
  enabled: true,
  filter_col: '',
  filter_value: '',
  death_date_col: '',
  death_datetime_col: '',
  death_type_concept_id: 32879,
  cause_concept_id: null,
  cause_source_value_col: '',
  cause_source_concept_id: null,
}

const DEATH_TYPE_OPTIONS = [
  { value: 32879, label: '32879 — Registry' },
  { value: 32817, label: '32817 — EHR' },
  { value: 32810, label: '32810 — Death Certificate' },
  { value: 32823, label: '32823 — Primary Death Certificate' },
]

export default function DeathStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const { cols, filePicker, selectedFile } = useSourceFile(project, 'death', { getConfig: () => cfg, setConfig: (saved) => setCfg(saved ?? DEFAULTS) })
  const [cfg, setCfg] = useState<DeathConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'death'), [project.etl_config])
  const stepUsed = useMemo(() => extractMappedCols(cfg), [cfg])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || (!crossUsed.has(c) && !stepUsed.has(c)))

  useEffect(() => {
    getTableConfig(project.id, 'death').then((ex: DeathConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
  }, [project.id])

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'death', { ...cfg, extra_instructions: extraInstructions, source_filename: selectedFile?.filename ?? null })
    onUpdate(p)
  }

  const { prev, next } = getAdjacentSlugs(project, 'death')

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    if (next) navigate(`/project/${project.id}/step/${next}`)
  }

  const set = (field: keyof DeathConfig) => (v: string) =>
    setCfg(prev => ({ ...prev, [field]: v }))

  return (
    <WizardLayout
      project={project}
      currentSlug="death"
      onBack={prev ? () => navigate(`/project/${project.id}/step/${prev}`) : undefined}
      onNext={handleNext}
      onBeforeStepChange={saveConfig}
      nextLabel="Next →"
      saving={saving}
    >
      <div className="flex flex-col gap-6">
        {filePicker}
        <div>
          <h2 className="text-xl font-bold text-primary">Death Table Mapping</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Map source columns to the OMOP DEATH table. A person can have at most one death record.
          </p>
        </div>

        {/* Death Trigger */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Death Trigger</h3>

          <FieldMapper
            label="Filter column"
            sourceColumns={availCols(cfg.filter_col)}
            value={cfg.filter_col}
            onChange={set('filter_col')}
            hint="The source column that indicates patient death status."
          />

          <div>
            <Label>Filter value (death indicator)</Label>
            <Input
              type="text"
              value={cfg.filter_value}
              onChange={e => setCfg(prev => ({ ...prev, filter_value: e.target.value }))}
              placeholder="e.g. 5.0 or dead or D"
              className="mt-1"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              A death record is created only when the filter column equals this value.
            </p>
          </div>
        </Card>

        {/* Death Date */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Death Date</h3>

          <FieldMapper
            label="death_date (required)"
            sourceColumns={availCols(cfg.death_date_col)}
            value={cfg.death_date_col}
            onChange={set('death_date_col')}
            hint="Source column containing the date of death. If day/month unknown, December 31 is used by convention."
          />

          <FieldMapper
            label="death_datetime (optional)"
            sourceColumns={availCols(cfg.death_datetime_col)}
            value={cfg.death_datetime_col}
            onChange={set('death_datetime_col')}
            hint="Source column containing the full datetime of death. Leave empty to populate as NULL."
          />

          <div>
            <Label>Date format</Label>
            <Input
              type="text"
              value={cfg.date_format ?? '%Y-%m-%d'}
              onChange={e => setCfg(prev => ({ ...prev, date_format: e.target.value || '%Y-%m-%d' }))}
              placeholder="%Y-%m-%d"
              className="mt-1 font-mono"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Python strptime format applied to the death date column (e.g. <code className="bg-muted px-1 rounded">%d/%m/%Y</code> for <code className="bg-muted px-1 rounded">14/7/2021</code>, <code className="bg-muted px-1 rounded">%Y%m%d</code> for <code className="bg-muted px-1 rounded">20210714</code>). Parsed and re-emitted as ISO so Postgres COPY accepts it.
            </p>
          </div>
        </Card>

        {/* Death Type */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Death Type</h3>

          <div>
            <Label>
              death_type_concept_id
              <span className="ml-1 font-normal text-muted-foreground">— provenance of the death record</span>
            </Label>
            <Select
              value={cfg.death_type_concept_id}
              onChange={e => setCfg(prev => ({ ...prev, death_type_concept_id: parseInt(e.target.value) }))}
              className="mt-1"
            >
              {DEATH_TYPE_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </Select>
            <p className="mt-1 text-xs text-muted-foreground">
              Reflects the source of the death record. Do not assume it matches the visit type.
            </p>
          </div>
        </Card>

        {/* Cause of Death */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Cause of Death (optional)</h3>

          <div>
            <Label>
              cause_concept_id
              <span className="ml-1 font-normal text-muted-foreground">— Standard OMOP concept for cause of death</span>
            </Label>
            <SingleConceptInput
              value={cfg.cause_concept_id}
              onChange={v => setCfg(prev => ({ ...prev, cause_concept_id: v }))}
              placeholder="e.g. 433753"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              No domain restriction — choose the Standard concept that best represents the cause of death. Use 0 if unknown.
            </p>
          </div>

          <FieldMapper
            label="cause_source_value (optional)"
            sourceColumns={availCols(cfg.cause_source_value_col)}
            value={cfg.cause_source_value_col}
            onChange={set('cause_source_value_col')}
            hint="Source column containing the raw cause of death code (max 50 chars)."
          />

          <div>
            <Label>
              cause_source_concept_id
              <span className="ml-1 font-normal text-muted-foreground">— OMOP concept ID for the source cause code</span>
            </Label>
            <SingleConceptInput
              value={cfg.cause_source_concept_id}
              onChange={v => setCfg(prev => ({ ...prev, cause_source_concept_id: v }))}
              placeholder="CONCEPT_ID from OMOP vocabularies"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              Use if the cause of death was coded using a vocabulary present in OMOP. Use 0 if not applicable.
            </p>
          </div>
        </Card>

        <ExtraInstructions
          tableName="death"
          value={extraInstructions}
          onChange={setExtraInstructions}
          deterministic
        />

        <ScriptGenerator
          project={project}
          table="death"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
          deterministic
        />
      </div>
    </WizardLayout>
  )
}
