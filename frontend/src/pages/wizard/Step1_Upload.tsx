import { useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { uploadSource } from '../../api/client'
import type { Project } from '../../types'
import WizardLayout from './WizardLayout'
import { UploadCloud, FileText, Loader2 } from 'lucide-react'
import { Card } from '@/components/ui/card'
import clsx from 'clsx'

interface Props {
  project: Project
  onUpdate: (p: Project) => void
}

export default function Step1Upload({ project, onUpdate }: Props) {
  const navigate = useNavigate()
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const handleFile = async (file: File) => {
    setUploading(true)
    setError('')
    try {
      const updated = await uploadSource(project.id, file)
      onUpdate(updated)
    } catch (e: unknown) {
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

  const hasSource = !!project.source_filename

  return (
    <WizardLayout
      projectId={project.id}
      projectName={project.name}
      currentStep={1}
      generatedScripts={project.generated_scripts}
      sourceUploaded={!!project.source_filename}
      hasMappingFiles={Object.keys(project.mapping_files || {}).length > 0}
      onNext={() => navigate(`/project/${project.id}/step/2`)}
      nextDisabled={!hasSource}
      nextLabel="Next: Location →"
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
              <div className="rounded-lg bg-muted p-3">
                <p className="text-xs text-muted-foreground">Delimiter</p>
                <p className="font-mono font-semibold text-foreground">{project.source_delimiter === '\t' ? 'TAB' : project.source_delimiter || 'auto'}</p>
              </div>
              <div className="rounded-lg bg-muted p-3">
                <p className="text-xs text-muted-foreground">Encoding</p>
                <p className="font-mono font-semibold text-foreground">{project.source_encoding}</p>
              </div>
              <div className="rounded-lg bg-muted p-3">
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
      </div>
    </WizardLayout>
  )
}
