import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import type { Project, ProviderConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: ProviderConfig = {
  enabled: true,
  provider_name_col: '',
  npi_col: '',
  dea_col: '',
  specialty_concept_id: null,
  prefix_specialty: '',
  prefix_specialty_concept_id: null,
  year_of_birth_col: '',
  gender_concept_value_map: {},
  gender_concept_id_default: 0,
  specialty_source_value_col: '',
  gender_source_value_col: '',
}

interface ColumnInfo { distinct_values: string[] }

export default function Step7Provider({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<ProviderConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [extraInstructions, setExtraInstructions] = useState('')
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})

  useEffect(() => {
    getTableConfig(project.id, 'provider').then((ex: ProviderConfig & { extra_instructions?: string }) => {
      if (ex && Object.keys(ex).length > 0) {
        setExtraInstructions(ex.extra_instructions || '')
        setCfg(ex)
      }
    })
    getColumnValues(project.id).then(setColumnInfos)
  }, [project.id])

  const distinctVals = (col: string): string[] =>
    columnInfos[col]?.distinct_values ?? []

  const saveConfig = async () => {
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { gender_concept_id: _gid, specialty_concept_id: _sid, ...cleanCfg } = cfg as ProviderConfig & { gender_concept_id?: unknown; specialty_concept_id?: unknown }
    const p = await updateTableConfig(project.id, 'provider', { ...cleanCfg, extra_instructions: extraInstructions })
    onUpdate(p)
  }

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/5`)
  }

  const set = (field: keyof ProviderConfig) => (v: string) =>
    setCfg(prev => ({ ...prev, [field]: v }))

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={4}
      generatedScripts={project.generated_scripts}
      sourceUploaded={!!project.source_filename}
      hasMappingFiles={Object.keys(project.mapping_files || {}).length > 0}
      onBack={() => navigate(`/project/${project.id}/step/3`)}
      onNext={handleNext}
      nextLabel="Next: Person →"
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

        {/* Provider Name */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Provider Name</h3>
          <FieldMapper
            label="provider_name"
            sourceColumns={cols}
            value={cfg.provider_name_col}
            onChange={set('provider_name_col')}
            hint="Name of the provider as it appears in the source (max 255 chars)."
          />
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
            sourceColumns={cols}
            value={cfg.npi_col}
            onChange={set('npi_col')}
            hint="National Provider Identifier (US). Max 20 chars."
          />
        </Card>

        {/* DEA */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">DEA</h3>
          <FieldMapper
            label="dea"
            sourceColumns={cols}
            value={cfg.dea_col}
            onChange={set('dea_col')}
            hint="DEA identifier for controlled substance prescriptions. Max 20 chars."
          />
        </Card>

        {/* Year of Birth */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Year of Birth</h3>
          <FieldMapper
            label="year_of_birth"
            sourceColumns={cols}
            value={cfg.year_of_birth_col}
            onChange={set('year_of_birth_col')}
            hint="Column containing the provider's birth year (integer)."
          />
        </Card>

        <Card className="flex flex-col gap-5 p-6">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-foreground">Specialty</h3>
            <a href="http://athena.ohdsi.org/search-terms/terms?domain=Provider&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
          </div>

          <FieldMapper
            label="Specialty column"
            sourceColumns={cols}
            value={cfg.specialty_source_value_col}
            onChange={v => setCfg(prev => ({
              ...prev,
              specialty_source_value_col: v,
              specialty_concept_value_map: v !== prev.specialty_source_value_col ? {} : prev.specialty_concept_value_map,
            }))}
            hint="Column containing the provider's specialty. Values will populate specialty_source_value and be mapped to specialty_concept_id below."
          />

          {cfg.specialty_source_value_col && (
            <ValueConceptMapper
              label="Specialty value → specialty_concept_id"
              sourceValues={distinctVals(cfg.specialty_source_value_col)}
              mapping={cfg.specialty_concept_value_map ?? {}}
              onChange={m => setCfg(prev => ({ ...prev, specialty_concept_value_map: m }))}
              hint="Assign an OMOP Provider-domain concept ID to each specialty value."
            />
          )}

          <div className="flex flex-col gap-3 rounded-lg border border-border bg-secondary/70 p-4">
            <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">Prefix Specialty</p>
            <p className="text-xs text-muted-foreground">Specify a static specialty that applies to all providers (used as a prefix/default when no column mapping is available).</p>
            <div className="flex flex-col gap-1">
              <Label>Specialty</Label>
              <Input
                type="text"
                value={cfg.prefix_specialty ?? ''}
                onChange={e => setCfg(prev => ({ ...prev, prefix_specialty: e.target.value }))}
                placeholder="e.g. Internal Medicine"
              />
            </div>
            <div className="flex flex-col gap-1">
              <Label>Specialty concept ID</Label>
              <Input
                type="number"
                value={cfg.prefix_specialty_concept_id ?? ''}
                onChange={e => setCfg(prev => ({ ...prev, prefix_specialty_concept_id: e.target.value === '' ? null : parseInt(e.target.value) }))}
                placeholder="e.g. 38004477"
                className="w-48"
              />
              <p className="text-xs text-muted-foreground">OMOP Provider-domain concept ID for the prefix specialty.</p>
            </div>
          </div>
        </Card>

        <Card className="flex flex-col gap-5 p-6">
          <div className="flex items-center justify-between">
            <h3 className="font-semibold text-foreground">Gender</h3>
            <a href="http://athena.ohdsi.org/search-terms/terms?domain=Gender&standardConcept=Standard&page=1&pageSize=15&query=" target="_blank" rel="noreferrer" className="text-xs text-primary hover:underline">Accepted Concepts</a>
          </div>

          <FieldMapper
            label="Gender column"
            sourceColumns={cols}
            value={cfg.gender_source_value_col}
            onChange={v => setCfg(prev => ({
              ...prev,
              gender_source_value_col: v,
              gender_concept_value_map: v !== prev.gender_source_value_col ? {} : prev.gender_concept_value_map,
            }))}
            hint="Provider gender as it appears in the source. Values will populate gender_source_value and be mapped to gender_concept_id below."
          />

          {cfg.gender_source_value_col && (
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <Label>Gender value → gender_concept_id</Label>
              </div>
              <p className="text-xs text-muted-foreground">Common: 8507 = Male, 8532 = Female, 8551 = Unknown</p>
              <ValueConceptMapper
                label=""
                sourceValues={distinctVals(cfg.gender_source_value_col)}
                mapping={cfg.gender_concept_value_map ?? {}}
                onChange={m => setCfg(prev => ({ ...prev, gender_concept_value_map: m }))}
                hint="Assign an OMOP Gender-domain concept ID to each gender value."
              />
            </div>
          )}

          <div>
            <Label>Default gender_concept_id</Label>
            <Input
              type="number"
              value={cfg.gender_concept_id_default ?? 0}
              onChange={e => setCfg(prev => ({ ...prev, gender_concept_id_default: parseInt(e.target.value) }))}
              className="mt-1 w-32"
            />
            <p className="mt-1 text-xs text-muted-foreground">Used when a source value is not in the map above (0 = unknown).</p>
          </div>
        </Card>

        <ExtraInstructions
          tableName="provider"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="provider"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
