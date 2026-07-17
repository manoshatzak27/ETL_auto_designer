import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadSources, deleteSourceFile, updateTableConfig, type UploadConflict } from '../../api/client'
import type { Project, SourceFile } from '../../types'
import WizardLayout from './WizardLayout'
import { getAdjacentSlugs, OPTIONAL_TABLES, isOptionalTableEnabled, type OptionalTable } from '../../wizard/steps'
import { UploadCloud, FileText, Loader2, Database, MapPin, Building2, UserCog, Skull, Rows3, Trash2, Table2 } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from '@/components/ui/dialog'
import VocabLoaderCard from '../../components/VocabLoaderCard'
import SourceFileGridEditor from '../../components/SourceFileGridEditor'
import clsx from 'clsx'

const WARN_ROW_THRESHOLD = 20
const PREVIEW_ROW_LIMIT = 20

function formatFileSize(bytes: number | undefined): string {
  if (bytes == null) return 'unknown size'
  const mb = bytes / (1024 * 1024)
  return mb >= 0.1 ? `${mb.toFixed(2)} MB` : `${(bytes / 1024).toFixed(1)} KB`
}

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

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

export default function SourceStep({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [conflicts, setConflicts] = useState<UploadConflict[]>([])
  const [togglingTable, setTogglingTable] = useState<string | null>(null)
  const [togglingMultiRow, setTogglingMultiRow] = useState(false)
  const [deletingIndex, setDeletingIndex] = useState<number | null>(null)
  const [openFile, setOpenFile] = useState<{ file: SourceFile; preview: boolean } | null>(null)
  const [pendingEditFile, setPendingEditFile] = useState<SourceFile | null>(null)

  const isMultiRow = !!(project.etl_config?.dataset_options as Record<string, unknown> | undefined)?.multiple_rows_per_patient

  const sourceFiles: SourceFile[] = project.source_files ?? []
  const hasSource = sourceFiles.length > 0

  const handleFiles = useCallback(async (files: File[]) => {
    if (!files.length) return
    setUploading(true)
    setError('')
    setConflicts([])
    try {
      const { project: updated, conflicts: newConflicts } = await uploadSources(project.id, files)
      onUpdate(updated)
      setConflicts(newConflicts)
    } catch {
      setError('Upload failed. Please try again.')
    } finally {
      setUploading(false)
    }
  }, [project.id, onUpdate])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length) handleFiles(files)
  }, [handleFiles])

  const onInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    if (files.length) handleFiles(files)
    e.target.value = ''
  }, [handleFiles])

  const handleDelete = async (index: number) => {
    setDeletingIndex(index)
    try {
      const updated = await deleteSourceFile(project.id, index)
      onUpdate(updated)
    } catch {
      // ignore — file card stays
    } finally {
      setDeletingIndex(null)
    }
  }

  const handleView = (file: SourceFile) => {
    if (file.row_count > WARN_ROW_THRESHOLD) setPendingEditFile(file)
    else setOpenFile({ file, preview: false })
  }

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

  const toggleTable = async (table: OptionalTable, checked: boolean) => {
    setTogglingTable(table)
    try {
      const existing = (project.etl_config?.[table] as Record<string, unknown> | undefined) || {}
      const updated = await updateTableConfig(project.id, table, { ...existing, enabled: checked })
      onUpdate(updated)
    } catch {
      // ignore
    } finally {
      setTogglingTable(null)
    }
  }

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
            Upload one or more CSV files, or a ZIP archive containing CSVs. The system will auto-detect delimiters and encoding for each file.
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
          <p className="font-medium text-foreground">
            {hasSource ? 'Drop more files here to add them' : 'Drop CSV files or a ZIP here, or click to browse'}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Accepts multiple CSV / TSV files or a single ZIP archive
          </p>
          <input
            id="file-input"
            type="file"
            accept=".csv,.tsv,.txt,.zip"
            multiple
            className="hidden"
            onChange={onInputChange}
          />
        </div>

        {uploading && (
          <div className="flex items-center gap-2 text-sm text-primary">
            <Loader2 className="size-4 animate-spin" />
            Uploading and analysing schema…
          </div>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        {conflicts.length > 0 && (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-700 dark:text-amber-400">
            <p className="font-semibold">
              The updated file changed {conflicts.length === 1 ? 'a column' : 'some columns'} used in previous mapping choices — please review and remap:
            </p>
            <ul className="mt-1.5 list-disc pl-5">
              {conflicts.map(c => (
                <li key={c.column}>
                  <span className="font-mono">{c.column}</span> — {c.reason}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Per-file cards */}
        {hasSource && (
          <div className="flex flex-col gap-3">
            <p className="text-sm font-semibold text-foreground">
              {sourceFiles.length === 1 ? '1 source file' : `${sourceFiles.length} source files`}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                — give each file a short logical name; downstream steps use these names to reference columns
              </span>
            </p>
            {sourceFiles.map((sf, idx) => (
              <SourceFileCard
                key={sf.filename + idx}
                file={sf}
                index={idx}
                deleting={deletingIndex === idx}
                onDelete={handleDelete}
                onView={handleView}
              />
            ))}
          </div>
        )}

        {/* OMOP table picker */}
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

        {/* Dataset structure */}
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

        {/* OMOP vocabulary */}
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

      {/* Large-file warning before opening the grid editor */}
      <Dialog open={!!pendingEditFile} onOpenChange={open => { if (!open) setPendingEditFile(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Large file</DialogTitle>
            <DialogDescription>
              This file has {pendingEditFile?.row_count.toLocaleString()} rows
              ({formatFileSize(pendingEditFile?.size_bytes)}). Loading the whole thing into the editable
              grid may be slow and use significant memory. Preview the first {PREVIEW_ROW_LIMIT} rows instead,
              or open the whole file to edit it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <button
              onClick={() => setPendingEditFile(null)}
              className="px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground"
            >
              Cancel
            </button>
            <button
              onClick={() => { if (pendingEditFile) setOpenFile({ file: pendingEditFile, preview: true }); setPendingEditFile(null) }}
              className="px-3 py-1.5 text-xs border border-border rounded-md text-foreground hover:bg-secondary"
            >
              Preview first {PREVIEW_ROW_LIMIT} rows
            </button>
            <button
              onClick={() => { if (pendingEditFile) setOpenFile({ file: pendingEditFile, preview: false }); setPendingEditFile(null) }}
              className="px-3 py-1.5 text-xs bg-primary text-primary-foreground rounded-md hover:bg-primary/90"
            >
              Open whole file
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Grid editor */}
      <Dialog open={!!openFile} onOpenChange={open => { if (!open) setOpenFile(null) }}>
        <DialogContent className="max-w-6xl w-[95vw] h-[85vh] overflow-hidden flex flex-col">
          {openFile && (
            <SourceFileGridEditor
              projectId={project.id}
              filename={openFile.file.filename}
              previewRowLimit={openFile.preview ? PREVIEW_ROW_LIMIT : undefined}
              onClose={() => setOpenFile(null)}
              onSaved={onUpdate}
            />
          )}
        </DialogContent>
      </Dialog>
    </WizardLayout>
  )
}

interface SourceFileCardProps {
  file: SourceFile
  index: number
  deleting: boolean
  onDelete: (index: number) => void
  onView: (file: SourceFile) => void
}

function SourceFileCard({ file, index, deleting, onDelete, onView }: SourceFileCardProps) {
  return (
    <Card className={clsx('flex flex-col gap-3 p-4 transition-opacity', deleting && 'opacity-50 pointer-events-none')}>
      <div className="flex items-center gap-2">
        <FileText className="size-4 text-primary flex-shrink-0" />
        <span className="text-sm font-medium text-foreground truncate flex-1">{file.filename}</span>
        <button
          onClick={() => onView(file)}
          className="rounded p-1 text-muted-foreground hover:bg-secondary hover:text-foreground transition-colors"
          title="View / edit data"
        >
          <Table2 className="size-3.5" />
        </button>
        <button
          onClick={() => onDelete(index)}
          disabled={deleting}
          className="ml-1 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive transition-colors"
          title="Remove file"
        >
          {deleting ? <Loader2 className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}
        </button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-3 text-sm">
        <div className="rounded-md border border-border bg-secondary/70 p-2">
          <p className="text-xs text-muted-foreground">Delimiter</p>
          <p className="font-mono font-semibold text-foreground">{file.delimiter === '\t' ? 'TAB' : file.delimiter || 'auto'}</p>
        </div>
        <div className="rounded-md border border-border bg-secondary/70 p-2">
          <p className="text-xs text-muted-foreground">Encoding</p>
          <p className="font-mono font-semibold text-foreground">{file.encoding}</p>
        </div>
        <div className="rounded-md border border-border bg-secondary/70 p-2">
          <p className="text-xs text-muted-foreground">Rows</p>
          <p className="font-semibold text-foreground">{file.row_count.toLocaleString()}</p>
        </div>
      </div>

      {/* Columns */}
      <div>
        <p className="mb-1.5 text-xs text-muted-foreground">Columns ({file.columns.length})</p>
        <div className="flex flex-wrap gap-1.5">
          {file.columns.map(col => (
            <span key={col} className="rounded bg-secondary px-2 py-0.5 font-mono text-xs text-secondary-foreground">
              {col}
            </span>
          ))}
        </div>
      </div>
    </Card>
  )
}
