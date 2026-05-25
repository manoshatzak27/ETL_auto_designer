import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { updateTableConfig, getTableConfig, getColumnValues } from '../../api/client'
import { extractMappedCols, getCrossStepUsedCols } from '../../utils/usedColumns'
import type { Project, CareSiteConfig } from '../../types'
import WizardLayout from './WizardLayout'
import FieldMapper from '../../components/FieldMapper'
import ValueConceptMapper from '../../components/ValueConceptMapper'
import ExtraInstructions from '../../components/ExtraInstructions'
import ScriptGenerator from '../../components/ScriptGenerator'
import { Card } from '@/components/ui/card'

interface ColumnInfo { distinct_values: string[] }

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

const DEFAULTS: CareSiteConfig = {
  enabled: true,
  care_site_name_col: '',
  place_of_service_col: '',
  place_of_service_value_map: {},
}

export default function Step6CareSite({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const cols = project.source_columns || []
  const [cfg, setCfg] = useState<CareSiteConfig>(DEFAULTS)
  const [saving, setSaving] = useState(false)
  const [columnInfos, setColumnInfos] = useState<Record<string, ColumnInfo>>({})
  const [posValues, setPosValues] = useState<string[]>([])
  const crossUsed = useMemo(() => getCrossStepUsedCols(project.etl_config, 'care_site'), [project.etl_config])
  const stepUsed = useMemo(() => extractMappedCols(cfg), [cfg])
  const availCols = (currentValue: string) =>
    cols.filter(c => c === currentValue || (!crossUsed.has(c) && !stepUsed.has(c)))
  const [extraInstructions, setExtraInstructions] = useState('')

  useEffect(() => {
    Promise.all([
      getTableConfig(project.id, 'care_site'),
      getColumnValues(project.id),
    ]).then(([existing, infos]: [CareSiteConfig & { extra_instructions?: string }, Record<string, ColumnInfo>]) => {
      setColumnInfos(infos)
      if (existing && Object.keys(existing).length > 0) {
        setExtraInstructions(existing.extra_instructions || '')
        setCfg(existing)
        const posCol = existing.place_of_service_col
        if (posCol) {
          const savedKeys = Object.keys(existing.place_of_service_value_map ?? {})
          setPosValues(savedKeys.length > 0 ? savedKeys : (infos[posCol]?.distinct_values ?? []))
        }
      }
    })
  }, [project.id])

  const saveConfig = async () => {
    const p = await updateTableConfig(project.id, 'care_site', { ...cfg, extra_instructions: extraInstructions })
    onUpdate(p)
  }

  const handleNext = async () => {
    setSaving(true)
    await saveConfig()
    setSaving(false)
    navigate(`/project/${project.id}/step/4`)
  }

  const handlePosColChange = (col: string) => {
    setCfg(prev => ({ ...prev, place_of_service_col: col, place_of_service_value_map: {} }))
    setPosValues(col ? (columnInfos[col]?.distinct_values ?? []) : [])
  }

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={3}
      generatedScripts={project.generated_scripts}
      sourceUploaded={!!project.source_filename}
      hasMappingFiles={Object.keys(project.mapping_files || {}).length > 0}
      onBack={() => navigate(`/project/${project.id}/step/2`)}
      onNext={handleNext}
      nextLabel="Next: Provider →"
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

        {/* Care Site Name */}
        <Card className="flex flex-col gap-5 p-6">
          <h3 className="font-semibold text-foreground">Care Site Name</h3>
          <FieldMapper
            label="care_site_name"
            sourceColumns={availCols(cfg.care_site_name_col)}
            value={cfg.care_site_name_col}
            onChange={col => setCfg(prev => ({ ...prev, care_site_name_col: col }))}
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
            sourceColumns={availCols(cfg.place_of_service_col)}
            value={cfg.place_of_service_col}
            onChange={handlePosColChange}
            hint="Source column whose values represent the place of service."
          />
          {posValues.length > 0 && (
            <ValueConceptMapper
              label="place_of_service_value_map"
              sourceValues={posValues}
              mapping={cfg.place_of_service_value_map ?? {}}
              onChange={map => setCfg(prev => ({ ...prev, place_of_service_value_map: map }))}
              hint="Assign an OMOP concept ID to each distinct place-of-service value."
            />
          )}
        </Card>

        <ExtraInstructions
          tableName="care_site"
          value={extraInstructions}
          onChange={setExtraInstructions}
        />

        <ScriptGenerator
          project={project}
          table="care_site"
          onUpdate={onUpdate}
          beforeGenerate={saveConfig}
        />
      </div>
    </WizardLayout>
  )
}
