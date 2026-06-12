import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadSource, updateTableConfig } from '../../api/client'
import type { Project } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs, OPTIONAL_TABLES, isOptionalTableEnabled, type OptionalTable } from '../../wizard/steps'
import { UploadCloud, FileText, Loader2, Database, MapPin, Building2, UserCog, Skull, Rows3 } from 'lucide-react'
import { Card } from '@/components/ui/card'
import VocabLoaderCard from '../../components/VocabLoaderCard'
import clsx from 'clsx'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

// Picker entries — each maps to one of OPTIONAL_TABLES.
// The dedicated step page fills in its own defaults on first visit, so we
// only set `enabled` here. icon/description are UI-only.
const PICKER_ENTRIES: { table: OptionalTable; label: string; description: string; icon: React.ComponentType<{ className?: string }> }[] = [
  {
    table: 'location',
    label: 'Location',
    description: 'Patient & care-site addresses (city, state, zip, country).',
    icon: MapPin,
  },
  {
    table: 'care_site',
    label: 'Care Site',
    description: 'Hospital / clinic where care is delivered (name + place of service).',
    icon: Building2,
  },
  {
    table: 'provider',
    label: 'Provider',
    description: 'Healthcare providers (NPI, DEA, specialty, gender).',
    icon: UserCog,
  },
  {
    table: 'death',
    label: 'Death',
    description: 'Mortality records (one per deceased person).',
    icon: Skull,
  },
]

export default function Step1Upload({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [togglingTable, setTogglingTable] = useState<string | null>(null)
  const [togglingMultiRow, setTogglingMultiRow] = useState(false)

  const isMultiRow = !!(project.etl_config?.dataset_options as Record<string, unknown> | undefined)?.multiple_rows_per_patient

  const toggleMultiRow = async (checked: boolean) => {
    setTogglingMultiRow(true)
    try {
      const existing = (project.etl_config?.dataset_options as Record<string, unknown> | undefined) || {}
      const updated = await updateTableConfig(project.id, 'dataset_options', { ...existing, multiple_rows_per_patient: checked })
      onUpdate(updated)
    } catch {
      // revert silently
    } finally {
      setTogglingMultiRow(false)
    }
  }

  const handleFile = async (file: File) => {
    setUploading(true)
    setError('')
    try {
      const updated = await uploadSource(project.id, file)
      onUpdate(updated)
    } catch {
      setError('Upload failed. Please try again.')
    } finally {
      setUploading(false)
    }
  }

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [project.id])

  const toggleTable = async (table: OptionalTable, checked: boolean) => {
    setTogglingTable(table)
    try {
      const existing = (project.etl_config?.[table] as Record<string, unknown> | undefined) || {}
      // Toggle enabled flag, preserve everything else. When enabling for the
      // first time, the dedicated step page will populate its own defaults
      // (column names, value maps, etc.) on first visit.
      const updated = await updateTableConfig(project.id, table, { ...existing, enabled: checked })
      onUpdate(updated)
    } catch {
      // ignore — the checkbox will revert visually since project state didn't change
    } finally {
      setTogglingTable(null)
    }
  }

  const hasSource = !!project.source_filename
  const { next } = getAdjacentSlugs(project, 'source')

  return (
    <WizardLayout
      project={project}
      currentSlug="source"
      onNext={next ? () => navigate(`/project/${project.id}/step/${next}`) : undefined}
      nextDisabled={!hasSource}
      nextLabel="Next →"
    >
      <div className="flex flex-col gap-6">
        <div>
          <h2 className="text-xl font-bold text-primary">Upload Source Dataset</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Upload your flat source CSV file. The system will auto-detect the delimiter and encoding.
          </p>
        </div>

        {/* Drop zone */}
        <div
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={clsx(
            'cursor-pointer rounded-xl border-2 border-dashed p-10 text-center transition-colors',
            dragging
              ? 'border-primary bg-secondary'
              : 'border-border bg-card hover:border-primary/50',
          )}
          onClick={() => document.getElementById('file-input')?.click()}
        >
          <UploadCloud className="mx-auto mb-3 size-10 text-muted-foreground" />
          <p className="font-medium text-foreground">Drop your CSV file here or click to browse</p>
          <p className="mt-1 text-xs text-muted-foreground">Supports CSV, TSV, TXT with any delimiter</p>
          <input
            id="file-input"
            type="file"
            accept=".csv,.tsv,.txt"
            className="hidden"
            onChange={e => e.target.files?.[0] && handleFile(e.target.files[0])}
          />
        </div>

        {uploading && (
          <div className="flex items-center gap-2 text-sm text-primary">
            <Loader2 className="size-4 animate-spin" />
            Uploading and analysing schema…
          </div>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        {/* Schema preview */}
        {hasSource && (
          <Card className="flex flex-col gap-4 p-6">
            <div className="flex items-center gap-2">
              <FileText className="size-5 text-primary" />
              <span className="font-semibold text-foreground">{project.source_filename}</span>
            </div>
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div className="rounded-lg border border-border bg-secondary/70 p-3">
                <p className="text-xs text-muted-foreground">Delimiter</p>
                <p className="font-mono font-semibold text-foreground">{project.source_delimiter === '\t' ? 'TAB' : project.source_delimiter || 'auto'}</p>
              </div>
              <div className="rounded-lg border border-border bg-secondary/70 p-3">
                <p className="text-xs text-muted-foreground">Encoding</p>
                <p className="font-mono font-semibold text-foreground">{project.source_encoding}</p>
              </div>
              <div className="rounded-lg border border-border bg-secondary/70 p-3">
                <p className="text-xs text-muted-foreground">Rows</p>
                <p className="font-semibold text-foreground">{project.source_row_count.toLocaleString()}</p>
              </div>
            </div>
            <div>
              <p className="mb-2 text-xs text-muted-foreground">Detected columns ({project.source_columns.length})</p>
              <div className="flex flex-wrap gap-1.5">
                {project.source_columns.map(col => (
                  <span key={col} className="rounded bg-secondary px-2 py-0.5 font-mono text-xs text-secondary-foreground">
                    {col}
                  </span>
                ))}
              </div>
            </div>
          </Card>
        )}

        {/* OMOP table picker — controls which optional steps appear in the wizard */}
        <Card className="flex flex-col gap-4 p-6">
          <div className="flex items-start gap-3">
            <Database className="size-5 text-primary flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="font-semibold text-foreground">Which OMOP tables do you want to populate?</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                <span className="font-semibold">Person</span>, <span className="font-semibold">Visit</span>,{' '}
                <span className="font-semibold">Observation Period</span>, and{' '}
                <span className="font-semibold">Stem Table</span> are always populated.
                Pick any of the optional tables below — a configuration step will appear in the wizard for each.
              </p>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {PICKER_ENTRIES.map(({ table, label, description, icon: Icon }) => {
              const enabled = isOptionalTableEnabled(project, table)
              const busy = togglingTable === table
              return (
                <label
                  key={table}
                  className={clsx(
                    'flex items-start gap-3 rounded-lg border p-3 cursor-pointer transition-colors',
                    enabled ? 'border-primary bg-primary/5' : 'border-border bg-card hover:border-primary/40',
                    busy && 'opacity-60 cursor-wait',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={enabled}
                    disabled={busy}
                    onChange={e => toggleTable(table, e.target.checked)}
                    className="mt-0.5 rounded accent-primary"
                  />
                  <Icon className={clsx('size-4 flex-shrink-0 mt-0.5', enabled ? 'text-primary' : 'text-muted-foreground')} />
                  <div className="flex-1">
                    <p className="text-sm font-semibold text-foreground">{label}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
                  </div>
                  {busy && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
                </label>
              )
            })}
          </div>

          <p className="text-xs text-muted-foreground">
            Tip: domain tables (Condition, Drug, Measurement, Observation, Procedure) are produced automatically by Stem Table — no setup needed.
          </p>
        </Card>

        {/* Dataset structure — one row vs. multiple rows per patient */}
        <Card className="flex flex-col gap-4 p-6">
          <div className="flex items-start gap-3">
            <Rows3 className="size-5 text-primary flex-shrink-0 mt-0.5" />
            <div>
              <h3 className="font-semibold text-foreground">Dataset structure</h3>
              <p className="text-xs text-muted-foreground mt-0.5">
                How many rows does each patient occupy in your dataset?
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-2">
            {/* One row per patient */}
            <label
              className={clsx(
                'flex items-start gap-3 rounded-lg border p-3 cursor-pointer transition-colors',
                !isMultiRow ? 'border-primary bg-primary/5' : 'border-border bg-card hover:border-primary/40',
                togglingMultiRow && 'opacity-60 cursor-wait',
              )}
            >
              <input
                type="radio"
                name="dataset-structure"
                checked={!isMultiRow}
                disabled={togglingMultiRow}
                onChange={() => toggleMultiRow(false)}
                className="mt-0.5 accent-primary"
              />
              <div className="flex-1">
                <p className="text-sm font-semibold text-foreground">One row per patient</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Each patient appears exactly once in the dataset.
                </p>
              </div>
            </label>

            {/* Multiple rows per patient */}
            <label
              className={clsx(
                'flex items-start gap-3 rounded-lg border p-3 cursor-pointer transition-colors',
                isMultiRow ? 'border-primary bg-primary/5' : 'border-border bg-card hover:border-primary/40',
                togglingMultiRow && 'opacity-60 cursor-wait',
              )}
            >
              <input
                type="radio"
                name="dataset-structure"
                checked={isMultiRow}
                disabled={togglingMultiRow}
                onChange={() => toggleMultiRow(true)}
                className="mt-0.5 accent-primary"
              />
              <div className="flex-1">
                <p className="text-sm font-semibold text-foreground">Multiple rows per patient</p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Each row is a different visit for the same patient (e.g. baseline, follow-up). A column identifies the visit type.
                </p>
              </div>
              {togglingMultiRow && <Loader2 className="size-3.5 animate-spin text-muted-foreground" />}
            </label>
          </div>
        </Card>

        {/* OMOP vocabulary — one-time shared setup. Started here so it can run
            in the background while the user configures the rest of the wizard. */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-foreground">OMOP vocabulary <span className="font-normal text-muted-foreground">(one-time setup)</span></h3>
          </div>
          <p className="text-xs text-muted-foreground">
            Load the Athena vocabulary into the shared <code className="bg-muted px-1 rounded">vocab</code> schema.
            It's shared across all projects — kick it off here and it'll keep running in the background while you set up the rest of the wizard.
          </p>
          <VocabLoaderCard />
        </div>
      </div>
    </WizardLayout>
  )
}
